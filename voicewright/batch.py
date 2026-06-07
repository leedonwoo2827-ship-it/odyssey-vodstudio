from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from . import settings as settings_module
from .audio_io import write_wav
from .engine import Engine
from .paths import (
    chapter_audio_dir,
    chapter_srt_path,
    chapter_subtitles_dir,
    narration_filename,
    narration_path,
    resolve_chapter_id,
    srt_filename,
    srt_path,
)
from .schemas import Script
from .srt import Cue, auto_time_cues, make_multi_srt, merge_scene_cues, split_into_cues
from .voices import VoiceMap, load_voice_map

logger = logging.getLogger(__name__)

ProgressCb = Callable[[int, int, int | None], Awaitable[None] | None]
WarningCb = Callable[[str], None]


@dataclass
class BatchResult:
    chapter_id: str
    output_dir: Path
    files: list[str]
    warnings: list[str]
    started_at: datetime
    finished_at: datetime


def parse_script(raw: bytes | str) -> Script:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    return Script.model_validate(data)


async def run_batch(
    *,
    engine: Engine,
    voice_map: VoiceMap | None = None,
    script: Script,
    chapter_id_explicit: str | int | None = None,
    filename_hint: str | None = None,
    output_root: Path | None = None,
    voice_override: str | None = None,
    speed: float | None = None,
    total_step: int | None = None,
    on_progress: ProgressCb | None = None,
    flat_layout: bool = False,
) -> BatchResult:
    # flat_layout=True 일 때는 output_root 가 곧 번들 폴더이고, 결과를
    # output_root/audio·output_root/subtitles 에 직접 쓴다 (mp4maker 번들 규약).
    # 기본(False)은 voicewright 기존 동작: output_root/ch{NN}/audio·subtitles.
    s = settings_module.load()
    vmap = voice_map if voice_map is not None else load_voice_map(s.voice_map_path)
    out_root = Path(output_root) if output_root else s.workspace_root

    chapter_id = resolve_chapter_id(
        explicit=chapter_id_explicit,
        script_field=script.chapter,
        filename_hint=filename_hint,
    )

    if flat_layout:
        out_dir = out_root / "audio"
        sub_dir = out_root / "subtitles"
        chapter_srt_file = sub_dir / f"ch{chapter_id}.srt"
    else:
        out_dir = chapter_audio_dir(out_root, chapter_id)
        sub_dir = chapter_subtitles_dir(out_root, chapter_id)
        chapter_srt_file = chapter_srt_path(out_root, chapter_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    sub_dir.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc)
    warnings: list[str] = []
    seen_warnings: set[str] = set()

    def warn(msg: str) -> None:
        if msg not in seen_warnings:
            seen_warnings.add(msg)
            warnings.append(msg)
            logger.warning(msg)

    # 1) scene별 보이스 결정 (override 우선)
    scene_voice: dict[int, str] = {}
    for scene in script.scenes:
        if voice_override:
            scene_voice[scene.scene] = voice_override.upper()
        else:
            code, w = vmap.resolve(scene.voice_style)
            scene_voice[scene.scene] = code
            if w:
                warn(w)

    # 2) 같은 보이스끼리 묶어서 배치 처리 (효율 + Supertonic 단일 스타일 제약)
    voice_groups: dict[str, list[int]] = defaultdict(list)
    for scene in script.scenes:
        voice_groups[scene_voice[scene.scene]].append(scene.scene)

    chunk_size = s.batch_chunk_size
    total = len(script.scenes)
    completed = 0
    files: list[str] = []
    scene_lookup = {sc.scene: sc for sc in script.scenes}
    actual_durations: dict[int, float] = {}
    scene_cues: dict[int, list[Cue]] = {}

    for voice_code, scene_numbers in voice_groups.items():
        for i in range(0, len(scene_numbers), chunk_size):
            chunk_scenes = scene_numbers[i : i + chunk_size]
            text_list = [scene_lookup[n].narration_text for n in chunk_scenes]

            try:
                wavs = await engine.synth_batch_same_voice(
                    text_list,
                    voice_code=voice_code,
                    total_step=total_step,
                    speed=speed,
                )
            except Exception as exc:
                logger.exception("배치 합성 실패: voice=%s, scenes=%s", voice_code, chunk_scenes)
                raise RuntimeError(f"합성 실패 (voice={voice_code}, scenes={chunk_scenes}): {exc}") from exc

            for scene_num, wav in zip(chunk_scenes, wavs):
                out_path = out_dir / narration_filename(chapter_id, scene_num)
                write_wav(out_path, wav, engine.sample_rate)
                files.append(out_path.name)

                # 개별 scene SRT (멀티큐) — 자막엔 원문(srt_text)을 우선 사용,
                # ~30자 구간으로 쪼개 실측 오디오 길이에 맞춰 타임코드 부여
                actual_dur = float(len(wav)) / float(engine.sample_rate)
                actual_durations[scene_num] = actual_dur
                scene_obj = scene_lookup[scene_num]
                srt_body = scene_obj.srt_text or scene_obj.narration_text
                cues = auto_time_cues(split_into_cues(srt_body), actual_dur)
                scene_cues[scene_num] = cues
                srt_p = sub_dir / srt_filename(chapter_id, scene_num)
                srt_p.write_text(make_multi_srt(cues), encoding="utf-8")

                completed += 1
                if on_progress is not None:
                    res = on_progress(completed, total, scene_num)
                    if hasattr(res, "__await__"):
                        await res

    # 챕터 전체 SRT — scene별 멀티큐를 누적 offset으로 병합
    sorted_scenes = sorted(script.scenes, key=lambda sc: sc.scene)
    scene_seq = [
        (scene_cues.get(sc.scene, []), actual_durations.get(sc.scene, 0.0))
        for sc in sorted_scenes
    ]
    chapter_srt_text = merge_scene_cues(scene_seq)
    chapter_srt_file.write_text(chapter_srt_text, encoding="utf-8")

    files.sort()
    return BatchResult(
        chapter_id=chapter_id,
        output_dir=out_dir,
        files=files,
        warnings=warnings,
        started_at=started,
        finished_at=datetime.now(timezone.utc),
    )

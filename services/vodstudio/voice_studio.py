"""voicewright 어댑터 — 번들 폴더 하나를 받아 음성/자막을 만든다.

voicewright는 원래 workspace/ch{NN}/audio 레이아웃으로 출력하지만, mp4maker는
번들 직속 audio/·subtitles/ 를 읽는다. 여기서 run_batch(flat_layout=True)로
번들에 직접 쓰도록 맞춘다.

공개 함수:
    synthesize(bundle_dir, only=None, ...)  → 전체 또는 특정 씬만 합성
    rebuild_chapter_srt(bundle_dir)         → 디스크의 per-scene SRT/WAV로 통합 SRT 재생성
"""
from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path

import soundfile as sf

from voicewright import settings as settings_module
from voicewright.audio_io import write_wav
from voicewright.batch import parse_script, run_batch
from voicewright.engine import Engine
from voicewright.paths import narration_filename, normalize_chapter_id, srt_filename
from voicewright.srt import (
    Cue,
    auto_time_cues,
    make_multi_srt,
    merge_scene_cues,
    parse_srt_cues,
    split_into_cues,
)
from voicewright.voices import ALL_VOICE_CODES, load_voice_map

_PER_SCENE_SRT_RE = re.compile(r"^ch[^_]+_(\d+)_narration\.srt$")


def _bundle_chapter_id(bundle_dir: Path) -> str | None:
    """번들 폴더 이름(ch90_bundle)에서 챕터 id('90')를 추출."""
    return normalize_chapter_id(bundle_dir.name.replace("_bundle", ""))


def find_script(bundle_dir: Path) -> Path:
    script_dir = bundle_dir / "script"
    hits = sorted(script_dir.glob("*_script.json"))
    if not hits:
        raise FileNotFoundError(f"대본 JSON이 없습니다: {script_dir}\\*_script.json")
    return hits[0]


async def synthesize(
    bundle_dir: str | Path,
    *,
    only: list[int] | None = None,
    voice_override: str | None = None,
    speed: float | None = None,
    total_step: int | None = None,
    on_progress=None,
) -> dict:
    """번들의 대본으로 음성(wav)+자막(srt)을 생성한다.

    only=None  → 전체 씬 배치
    only=[2,5] → 2,5번 씬만 재생성 (나머지는 디스크에 있던 것 유지)

    발음 교정은 config/pronunciation_map.yaml(웹 UI에서 편집) 를 합성 직전에
    자동 적용한다(engine 내부, 핫리로드). 단어 추가 후 그 씬만 재생성하면 반영됨.
    """
    bundle = Path(bundle_dir).resolve()
    script_path = find_script(bundle)
    script = parse_script(script_path.read_bytes())

    if only:
        wanted = set(int(n) for n in only)
        filtered = deepcopy(script)
        filtered.scenes = [sc for sc in script.scenes if sc.scene in wanted]
        if not filtered.scenes:
            raise ValueError(f"--only {sorted(wanted)} 에 해당하는 씬이 대본에 없습니다.")
        run_script = filtered
    else:
        run_script = script

    engine = await Engine.get()
    result = await run_batch(
        engine=engine,
        script=run_script,
        chapter_id_explicit=_bundle_chapter_id(bundle),
        filename_hint=script_path.name,
        output_root=bundle,
        voice_override=voice_override,
        speed=speed,
        total_step=total_step,
        on_progress=on_progress,
        flat_layout=True,
    )

    # 통합 SRT는 항상 디스크의 모든 per-scene SRT/WAV 기준으로 다시 만든다.
    # (부분 재생성 시 run_batch는 그 씬들만으로 통합 SRT를 만들기 때문에 보정 필요.
    #  사용자가 검수 탭에서 손본 per-scene SRT 타임코드도 이때 반영된다.)
    chapter_srt = rebuild_chapter_srt(bundle)

    return {
        "chapter": result.chapter_id,
        "bundle": str(bundle),
        "audio_dir": str(bundle / "audio"),
        "subtitles_dir": str(bundle / "subtitles"),
        "files": result.files,
        "chapter_srt": str(chapter_srt) if chapter_srt else None,
        "warnings": result.warnings,
        "scenes_done": [sc.scene for sc in run_script.scenes],
    }


def _wav_duration(path: Path) -> float:
    info = sf.info(str(path))
    return info.frames / float(info.samplerate)


def rebuild_chapter_srt(bundle_dir: str | Path) -> Path | None:
    """번들의 audio/*.wav + subtitles/*_narration.srt 를 모아 통합 chNN.srt 재생성.

    per-scene SRT(멀티큐)를 실측 오디오 길이만큼 누적 offset으로 병합한다.
    audio가 없는 씬은 통합 SRT에 넣지 못하므로 건너뛴다.
    """
    bundle = Path(bundle_dir).resolve()
    sub_dir = bundle / "subtitles"
    audio_dir = bundle / "audio"
    chapter_id = _bundle_chapter_id(bundle)
    if not sub_dir.exists() or chapter_id is None:
        return None

    scene_data: list[tuple[int, list, float]] = []
    for srt_p in sorted(sub_dir.glob("*_narration.srt")):
        m = _PER_SCENE_SRT_RE.match(srt_p.name)
        if not m:
            continue
        scene_num = int(m.group(1))
        wav_p = audio_dir / narration_filename(chapter_id, scene_num)
        if not wav_p.exists():
            continue
        cues = parse_srt_cues(srt_p.read_text(encoding="utf-8"))
        scene_data.append((scene_num, cues, _wav_duration(wav_p)))

    if not scene_data:
        return None

    scene_data.sort(key=lambda t: t[0])
    text = merge_scene_cues([(cues, dur) for _, cues, dur in scene_data])
    out = sub_dir / f"ch{chapter_id}.srt"
    out.write_text(text, encoding="utf-8")
    return out


def _resolve_voice(bundle: Path, scene: int, voice: str | None) -> str:
    """씬 보이스 결정: 명시값 → 대본의 voice_style → 기본."""
    s = settings_module.load()
    vmap = load_voice_map(s.voice_map_path)
    if voice:
        code = voice.upper()
        if code not in ALL_VOICE_CODES:
            raise ValueError(f"알 수 없는 보이스: {voice}")
        return code
    style = None
    sp = find_script(bundle)
    if sp:
        try:
            sc = parse_script(sp.read_bytes())
            for x in sc.scenes:
                if x.scene == int(scene):
                    style = x.voice_style
                    break
        except Exception:
            pass
    code, _ = vmap.resolve(style)
    return code


def set_voices(bundle_dir: str | Path, voice: str | None, only: list[int] | None = None) -> dict:
    """대본 JSON의 씬 voice_style을 일괄(또는 일부) 변경한다.

    voice=코드(M1..F5)/스타일명 → 저장, voice 비어있음 → 항목 제거(전체 기본값으로 복귀).
    only=None 이면 전체 씬, only=[2,5] 이면 해당 씬만.
    """
    bundle = Path(bundle_dir).resolve()
    sp = find_script(bundle)
    data = _json.loads(sp.read_text(encoding="utf-8"))
    val = (voice or "").strip()
    code = val.upper() if val else None
    if code and code not in ALL_VOICE_CODES:
        raise ValueError(f"알 수 없는 보이스: {voice}")
    want = {int(n) for n in only} if only else None
    changed = 0
    for pos, sc in enumerate(data.get("scenes") or []):
        idx = int(sc.get("scene") or sc.get("scene_number") or pos + 1)
        if want is not None and idx not in want:
            continue
        if code:
            sc["voice_style"] = code
        else:
            sc.pop("voice_style", None)
        changed += 1
    sp.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"changed": changed, "voice": code}


def set_scene_texts(
    bundle_dir: str | Path,
    scene: int,
    *,
    narration_text: str | None = None,
    srt_text: str | None = None,
) -> dict:
    """씬의 발음(narration_text)/자막(srt_text)을 대본 JSON에 저장한다.

    None인 필드는 건드리지 않는다. 이렇게 저장해 둬야 새로고침/전체 음성 생성/재생성 후에도
    발음이 자막으로 원복되지 않고 '발음은 발음대로' 유지된다.
    """
    bundle = Path(bundle_dir).resolve()
    sp = find_script(bundle)
    data = _json.loads(sp.read_text(encoding="utf-8"))
    saved = False
    for pos, sc in enumerate(data.get("scenes") or []):
        idx = int(sc.get("scene") or sc.get("scene_number") or pos + 1)
        if idx != int(scene):
            continue
        if narration_text is not None:
            sc["narration_text"] = narration_text
        if srt_text is not None:
            sc["srt_text"] = srt_text
        saved = True
        break
    if saved:
        sp.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"scene": int(scene), "saved": saved}


async def synth_scene_text(
    bundle_dir: str | Path,
    scene: int,
    text: str,
    *,
    srt_text: str | None = None,
    voice: str | None = None,
    speed: float | None = None,
    total_step: int | None = None,
    reset_subtitle: bool = False,
) -> dict:
    """한 씬만, 주어진 텍스트로 음성을 다시 만든다 (번들에 직접 기록).

    - 음성(TTS)에는 발음 사전이 자동 적용된다(엔진 내부).
    - 자막 타이밍은 **실측 음성 길이**에 맞춰 자동 재계산 → 발음변환/괄호제거로 인한
      싱크 어긋남을 보정한다.
    - reset_subtitle=False(기본): 이미 편집해 둔 per-scene 자막의 **줄 나눔(텍스트)을 유지**하고
      시간만 새 음성 길이에 맞춰 재배분 → 사용자의 자막 편집이 보존된다.
    - reset_subtitle=True: 자막을 srt_text(없으면 text)로 처음부터 새로 만든다.
    """
    bundle = Path(bundle_dir).resolve()
    chap = _bundle_chapter_id(bundle)
    if chap is None:
        raise ValueError(f"번들 이름에서 챕터를 찾지 못함: {bundle.name}")
    if not text.strip():
        raise ValueError("빈 텍스트입니다.")

    engine = await Engine.get()
    code = _resolve_voice(bundle, scene, voice)
    # 사용자가 이 씬의 보이스를 명시했으면 대본 JSON에 기록 → 새로고침/재생성 후에도 유지
    if voice:
        try:
            set_voices(bundle, code, only=[int(scene)])
        except Exception:
            pass
    # 발음(text)/자막(srt_text) 편집을 대본 JSON에 저장 → 재생성/새로고침/전체생성 후에도
    # 발음이 자막으로 원복되지 않고 편집한 발음 그대로 유지된다.
    try:
        set_scene_texts(bundle, scene, narration_text=text, srt_text=srt_text)
    except Exception:
        pass
    wav = await engine.synth(text, voice_code=code, total_step=total_step, speed=speed)

    audio_dir = bundle / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    wav_path = audio_dir / narration_filename(chap, int(scene))
    write_wav(wav_path, wav, engine.sample_rate)

    dur = float(len(wav)) / float(engine.sample_rate)
    sub_dir = bundle / "subtitles"
    sub_dir.mkdir(parents=True, exist_ok=True)
    srt_p = sub_dir / srt_filename(chap, int(scene))

    # 기존 편집 자막의 줄 나눔(텍스트) 유지 — 시간만 새 길이에 재배분
    existing_texts: list[str] = []
    if not reset_subtitle and srt_p.exists():
        existing_texts = [c.text for c in parse_srt_cues(srt_p.read_text(encoding="utf-8")) if c.text.strip()]
    if existing_texts:
        cues = auto_time_cues(existing_texts, dur)
    else:
        body = (srt_text or text).strip()
        cues = auto_time_cues(split_into_cues(body), dur)
    srt_p.write_text(make_multi_srt(cues), encoding="utf-8")
    rebuild_chapter_srt(bundle)

    return {
        "scene": int(scene),
        "voice": code,
        "duration": round(dur, 3),
        "audio_file": wav_path.name,
        "subtitle_file": srt_filename(chap, int(scene)),
        "cues": [{"text": c.text, "start": c.start, "end": c.end} for c in cues],
    }


async def synth_intro_narration(bundle_dir: str | Path, text: str,
                                voice: str | None = None) -> dict:
    """인트로용 내레이션을 새로 녹음(TTS) → draft/chNN_intro_narration.wav 로 저장.

    정상 속도로 합성하고, 빠른 재생(atempo)은 영상 렌더 단계에서 적용한다.
    voice = M1..F5 코드 또는 스타일명(비면 번들 기본 보이스).
    """
    bundle = Path(bundle_dir).resolve()
    chap = _bundle_chapter_id(bundle) or "ch"
    if not (text or "").strip():
        raise ValueError("빈 인트로 대본입니다.")
    engine = await Engine.get()
    code = voice_code_for_style(voice) if (voice or "").strip() else _resolve_voice(bundle, 1, None)
    wav = await engine.synth(text.strip(), voice_code=code)
    out_dir = bundle / "draft"
    out_dir.mkdir(parents=True, exist_ok=True)
    wav_path = out_dir / f"{chap}_intro_narration.wav"
    write_wav(wav_path, wav, engine.sample_rate)
    return {"path": str(wav_path), "voice": code,
            "duration": round(float(len(wav)) / float(engine.sample_rate), 3)}


def save_scene_cues(bundle_dir: str | Path, scene: int, cues_data: list[dict]) -> dict:
    """사용자가 편집한 자막 큐(시작/끝/텍스트)를 per-scene SRT로 저장 + 통합 SRT 갱신."""
    bundle = Path(bundle_dir).resolve()
    chap = _bundle_chapter_id(bundle)
    if chap is None:
        raise ValueError(f"번들 이름에서 챕터를 찾지 못함: {bundle.name}")
    cues: list[Cue] = []
    prev = -1.0
    for i, c in enumerate(cues_data):
        t = str(c.get("text", "")).strip()
        if not t:
            continue
        start = round(float(c.get("start", 0.0)), 3)
        end = round(float(c.get("end", 0.0)), 3)
        if start < 0 or end < start:
            raise ValueError(f"{i+1}번 자막 시간이 잘못됨 (start={start}, end={end})")
        if start < prev - 1e-3:
            raise ValueError(f"{i+1}번 자막이 앞 자막과 겹침")
        prev = end
        cues.append(Cue(text=t, start=start, end=end))
    if not cues:
        raise ValueError("저장할 자막이 없습니다.")

    sub_dir = bundle / "subtitles"
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / srt_filename(chap, int(scene))).write_text(make_multi_srt(cues), encoding="utf-8")
    rebuild_chapter_srt(bundle)
    return {"scene": int(scene), "cue_count": len(cues)}


# ── 번들 상태 (app/bundles.py 의 bundle_status 를 경로 기반으로 이식) ──────────
import json as _json

IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")
AUD_EXTS = (".wav", ".mp3", ".m4a", ".flac")


def _chap(bundle_dir: Path) -> str | None:
    m = re.search(r"(\d{1,3})", bundle_dir.name.replace("_bundle", ""))
    return f"{int(m.group(1)):02d}" if m else None


def _newest_mtime(dirs: list[Path]) -> float:
    newest = 0.0
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.is_file():
                try:
                    newest = max(newest, p.stat().st_mtime)
                except OSError:
                    pass
    return newest


def _audio_duration(path: Path | None) -> float | None:
    if path is None or not path.exists():
        return None
    try:
        info = sf.info(str(path))
        return round(info.frames / float(info.samplerate), 3)
    except Exception:
        return None


def _find_prefix_file(folder: Path, chap: str, scene: int, exts: tuple[str, ...],
                      suffix: str = "") -> Path | None:
    if not folder.is_dir():
        return None
    for pref in (f"ch{chap}_{scene:02d}", f"{int(chap)}_{scene:02d}"):
        for ext in exts:
            if suffix:
                exact = folder / f"{pref}{suffix}{ext}"
                if exact.exists():
                    return exact
            hits = sorted(folder.glob(f"{pref}*{ext}"))
            if hits:
                return hits[0]
    return None


def bundle_status(bundle_dir: str | Path) -> dict:
    """번들 폴더 하나의 단계별 상태 요약 (씬별 has_image/has_audio/audio_duration/srt_text)."""
    root = Path(bundle_dir).resolve()
    chap = _chap(root)
    script_path = find_script_opt(root) if root.is_dir() else None

    scenes_out: list[dict] = []
    title = ""
    if script_path and script_path.exists():
        try:
            data = _json.loads(script_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"bundle": root.name, "ok": False, "error": f"대본 JSON 파싱 실패: {exc}"}
        title = data.get("title") or ""
        vmap = load_voice_map(settings_module.load().voice_map_path)
        for pos, sc in enumerate(data.get("scenes") or []):
            idx = int(sc.get("scene") or sc.get("scene_number") or pos + 1)
            img = _find_prefix_file(root / "images", chap, idx, IMG_EXTS)
            aud = _find_prefix_file(root / "audio", chap, idx, AUD_EXTS, "_narration")
            sub = _find_prefix_file(root / "subtitles", chap, idx, (".srt",), "_narration")
            vstyle = sc.get("voice_style")
            vcode = vmap.resolve(vstyle)[0] if vstyle else ""
            scenes_out.append({
                "scene": idx,
                "title": sc.get("title") or "",
                "narration_text": sc.get("narration_text") or "",
                "srt_text": sc.get("srt_text"),
                "voice_style": vstyle,
                "voice_code": vcode,
                "narration_seconds": sc.get("narration_seconds"),
                "has_image": img is not None,
                "has_audio": aud is not None,
                "has_subtitle": sub is not None,
                "image_file": img.name if img else None,
                "audio_file": aud.name if aud else None,
                "subtitle_file": sub.name if sub else None,
                "audio_duration": _audio_duration(aud),
            })

    draft_mp4 = root / "draft" / f"ch{chap}_final.mp4" if chap else None
    draft_nosub = root / "draft" / f"ch{chap}_final_nosub.mp4" if chap else None
    render_stale = False
    if draft_mp4 and draft_mp4.exists():
        try:
            mp4_mtime = draft_mp4.stat().st_mtime
            newest_input = _newest_mtime([root / "audio", root / "subtitles",
                                          root / "images", root / "script"])
            render_stale = newest_input > mp4_mtime + 0.5
        except OSError:
            render_stale = False
    n = len(scenes_out)
    img_done = sum(1 for s in scenes_out if s["has_image"])
    aud_done = sum(1 for s in scenes_out if s["has_audio"])
    return {
        "bundle": root.name,
        "ok": True,
        "path": str(root),
        "chapter": chap,
        "title": title,
        "has_script": bool(script_path),
        "scene_count": n,
        "scenes": scenes_out,
        "missing_images": [s["scene"] for s in scenes_out if not s["has_image"]],
        "missing_audio": [s["scene"] for s in scenes_out if not s["has_audio"]],
        "steps": {
            "script": bool(script_path),
            "images": n > 0 and img_done == n,
            "audio": n > 0 and aud_done == n,
            "render": bool(draft_mp4 and draft_mp4.exists()) and not render_stale,
        },
        "final_mp4": str(draft_mp4) if (draft_mp4 and draft_mp4.exists()) else None,
        "final_nosub_mp4": str(draft_nosub) if (draft_nosub and draft_nosub.exists()) else None,
        "render_stale": render_stale,
    }


def find_script_opt(bundle_dir: Path) -> Path | None:
    hits = sorted((Path(bundle_dir) / "script").glob("*_script.json"))
    return hits[0] if hits else None


# ── 발음 변환 미리보기 + 보이스 매핑 + 목소리 들어보기 ────────────────────────
def to_pronunciation(text: str) -> str:
    """발음 사전 + 약어/연도/단위 변환 미리보기 ('한국어 발음 전환' 버튼용)."""
    from voicewright.pronunciation import load_pronunciation_map
    if not (text or "").strip():
        return ""
    pmap = load_pronunciation_map(settings_module.load().pronunciation_map_path)
    return pmap.apply(text, spell_unknown_acronyms=True, convert_years=True)


def voice_code_for_style(style: str | None) -> str:
    """voice_style(narrator/deep_male/...) → 엔진 보이스 코드(M1..F5). 대소문자 코드도 허용."""
    s = (style or "").strip()
    if s.upper() in ALL_VOICE_CODES:
        return s.upper()
    vmap = load_voice_map(settings_module.load().voice_map_path)
    code, _ = vmap.resolve(s or None)
    return code


def list_voices() -> list[dict]:
    """UI 보이스 목록: [{code, label, gender}]."""
    labels = {
        "M1": "남1 · 젊은", "M2": "남2 · 따뜻한", "M3": "남3 · 차분한",
        "M4": "남4 · 활기찬", "M5": "남5 · 깊은 (기본)",
        "F1": "여1 · 젊은", "F2": "여2 · 따뜻한", "F3": "여3 · 차분한",
        "F4": "여4 · 활기찬", "F5": "여5 · 성숙한",
    }
    return [{"code": c, "label": labels.get(c, c), "gender": ("male" if c[0] == "M" else "female")}
            for c in ALL_VOICE_CODES]


_PREVIEW_TEXT = "안녕하세요. 영상공방입니다. 이 목소리로 내레이션을 만듭니다."


async def preview_wav_bytes(voice_style: str | None, text: str | None = None) -> bytes:
    """선택한 목소리로 짧은 샘플을 합성해 WAV 바이트를 반환('▶ 들어보기')."""
    import io
    code = voice_code_for_style(voice_style)
    engine = await Engine.get()
    wav = await engine.synth((text or _PREVIEW_TEXT).strip(), voice_code=code)
    buf = io.BytesIO()
    sf.write(buf, wav, engine.sample_rate, format="WAV")
    return buf.getvalue()

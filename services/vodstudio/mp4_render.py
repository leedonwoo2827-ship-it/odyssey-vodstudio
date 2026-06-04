"""mp4maker 연동 — 번들 → 최종 MP4 렌더.

mp4maker 체크아웃(./mp4maker/)을 `python -m mp4maker <bundle>` 로 호출한다.
mp4maker는 씬마다 audio WAV(chNN_XX_narration.wav)를 요구하므로:
  - mode="voiced": 사용자가 SuperTonic3로 audio/ 를 채운 경우 그대로 렌더
  - mode="silent": narration_seconds 길이의 무음 WAV를 생성해 '무음 미리보기' 렌더
ffmpeg/ffprobe 는 PATH에 있어야 한다(setup 시 mp4maker --probe로 확인).
"""

import asyncio
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# mp4maker 체크아웃 위치(프로젝트 루트 기준). `python -m mp4maker` 가 패키지를
# 찾으려면 이 디렉터리를 cwd 로 둬야 한다.
MP4MAKER_DIR = Path(__file__).resolve().parents[2] / "mp4maker"

AUD_EXTS = (".wav", ".mp3", ".m4a", ".flac")


class RenderError(RuntimeError):
    pass


def _python() -> str:
    return sys.executable


def _ffmpeg() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def available() -> bool:
    return MP4MAKER_DIR.is_dir() and (MP4MAKER_DIR / "mp4maker").is_dir()


def _read_script(bundle_dir: Path) -> Dict[str, Any]:
    scripts = list((bundle_dir / "script").glob("ch*_script.json"))
    if not scripts:
        raise RenderError(f"번들에 script/ch*_script.json 이 없습니다: {bundle_dir}")
    return json.loads(scripts[0].read_text(encoding="utf-8"))


def _chapter_id(doc: Dict[str, Any]) -> str:
    return f"ch{int(doc.get('chapter') or 1):02d}"


def _scene_has_audio(audio_dir: Path, chapter_id: str, idx: int) -> bool:
    prefix = f"{chapter_id}_{idx:02d}"
    if any((audio_dir / f"{prefix}_narration{ext}").exists() for ext in AUD_EXTS):
        return True
    # NOTE: must iterate the glob results — `any(glob(...) for ext in ...)` would
    # test truthiness of generator objects (always True), not whether files exist.
    return any(p for ext in AUD_EXTS for p in audio_dir.glob(f"{prefix}*{ext}"))


def audio_status(bundle_dir: str) -> Dict[str, Any]:
    """씬별 audio 보유 현황."""
    bdir = Path(bundle_dir)
    doc = _read_script(bdir)
    chapter_id = _chapter_id(doc)
    audio_dir = bdir / "audio"
    total = 0
    missing: List[int] = []
    for sc in doc.get("scenes", []):
        idx = int(sc.get("scene") or 0)
        total += 1
        if not _scene_has_audio(audio_dir, chapter_id, idx):
            missing.append(idx)
    return {"total": total, "with_audio": total - len(missing), "missing": missing}


async def _run_ffmpeg(args: List[str], timeout: float = 60.0) -> None:
    proc = await asyncio.create_subprocess_exec(
        _ffmpeg(), *args,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    if proc.returncode != 0:
        raise RenderError(f"ffmpeg 실패: {(err or b'').decode('utf-8','replace')[:200]}")


async def ensure_silent_audio(bundle_dir: str) -> int:
    """audio 가 없는 씬마다 narration_seconds 길이의 무음 WAV를 생성한다.

    이렇게 하면 SuperTonic3 음성 없이도 mp4maker가 슬라이드+자막 '무음 미리보기'를
    렌더할 수 있다. 생성한 파일 개수를 반환."""
    bdir = Path(bundle_dir)
    doc = _read_script(bdir)
    chapter_id = _chapter_id(doc)
    audio_dir = bdir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    made = 0
    for sc in doc.get("scenes", []):
        idx = int(sc.get("scene") or 0)
        if _scene_has_audio(audio_dir, chapter_id, idx):
            continue
        dur = max(2.0, float(sc.get("narration_seconds") or 0) or 2.0)
        out = audio_dir / f"{chapter_id}_{idx:02d}_narration.wav"
        await _run_ffmpeg([
            "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", f"{dur:.2f}", "-c:a", "pcm_s16le", str(out),
        ])
        made += 1
    logger.info("Generated %d silent narration WAV(s) in %s", made, audio_dir)
    return made


async def render(
    bundle_dir: str,
    *,
    resolution: str = "1920x1080",
    extra_args: Optional[List[str]] = None,
    on_line: Optional[Callable[[str], None]] = None,
    timeout: float = 1800.0,
) -> str:
    """`python -m mp4maker <bundle> --resolution ...` 실행. 최종 MP4 경로 반환."""
    if not available():
        raise RenderError(
            f"mp4maker 체크아웃을 찾을 수 없습니다: {MP4MAKER_DIR} "
            "(git clone https://github.com/leedonwoo2827-ship-it/mp4maker.git mp4maker)"
        )
    bdir = Path(bundle_dir).resolve()
    args = [
        _python(), "-m", "mp4maker", str(bdir),
        "--resolution", resolution,
    ]
    if extra_args:
        args += extra_args
    logger.info("mp4maker render: %s", " ".join(args[2:]))
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=str(MP4MAKER_DIR),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    final_path: Optional[str] = None
    assert proc.stdout is not None
    try:
        while True:
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip()
            if on_line:
                on_line(line)
            # [done]  <path>  — capture the first *_final.mp4 we see.
            if line.startswith("[done]") and "_final.mp4" in line:
                final_path = line.split("]", 1)[1].strip()
    except asyncio.TimeoutError:
        proc.kill()
        raise RenderError(f"mp4maker 렌더 타임아웃 ({timeout:.0f}s)")
    await proc.wait()
    if proc.returncode != 0:
        raise RenderError(f"mp4maker 종료코드 {proc.returncode}")

    # Prefer the parsed [done] path; fall back to draft/chNN_final.mp4.
    if final_path and Path(final_path).exists():
        return final_path
    doc = _read_script(bdir)
    guess = bdir / "draft" / f"{_chapter_id(doc)}_final.mp4"
    if guess.exists():
        return str(guess)
    raise RenderError("렌더는 끝났지만 최종 MP4를 찾지 못했습니다.")

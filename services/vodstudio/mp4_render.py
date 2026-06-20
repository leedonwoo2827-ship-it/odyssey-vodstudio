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
    no_subtitles: bool = False,
    dry_run: bool = False,
    extra_args: Optional[List[str]] = None,
    on_line: Optional[Callable[[str], None]] = None,
    timeout: float = 1800.0,
) -> str:
    """`python -m mp4maker <bundle> --resolution ...` 실행. 최종 MP4 경로 반환.

    no_subtitles=True → 자막을 굽지 않은 클린본(chNN_final_nosub.mp4) + .srt 사이드카.
    dry_run=True → 검증만(ffmpeg 미실행). 최종 MP4 없음 → "" 반환.
    """
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
    if no_subtitles:
        args.append("--no-subtitles")
    if dry_run:
        args.append("--dry-run")
    if extra_args:
        args += extra_args
    final_token = "_final_nosub.mp4" if no_subtitles else "_final.mp4"
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
            # [done]  <path>  — capture the matching *_final(.|_nosub.)mp4 we see.
            if line.startswith("[done]") and final_token in line:
                final_path = line.split("]", 1)[1].strip()
    except asyncio.TimeoutError:
        proc.kill()
        raise RenderError(f"mp4maker 렌더 타임아웃 ({timeout:.0f}s)")
    await proc.wait()
    if proc.returncode != 0:
        raise RenderError(f"mp4maker 종료코드 {proc.returncode}")

    if dry_run:
        return ""  # 검증만 — 최종 MP4 없음

    # Prefer the parsed [done] path; fall back to draft/chNN_final.mp4.
    if final_path and Path(final_path).exists():
        return final_path
    doc = _read_script(bdir)
    guess = bdir / "draft" / f"{_chapter_id(doc)}{final_token}"
    if guess.exists():
        return str(guess)
    raise RenderError("렌더는 끝났지만 최종 MP4를 찾지 못했습니다.")


async def render_shorts(
    bundle_dir: str,
    *,
    original_url: str = "",
    duration: float = 30.0,
    bottom_mode: str = "subtitle",
    on_line: Optional[Callable[[str], None]] = None,
    timeout: float = 1800.0,
) -> str:
    """`python -m mp4maker <bundle> --shorts ...` 실행. 세로 9:16 쇼츠 MP4 경로 반환.

    오프닝 나레이션을 오디오 베드로, 핵심 장면 이미지를 3분할 변동 레이아웃으로 합성한다.
    """
    if not available():
        raise RenderError(
            f"mp4maker 체크아웃을 찾을 수 없습니다: {MP4MAKER_DIR}"
        )
    bdir = Path(bundle_dir).resolve()
    args = [
        _python(), "-m", "mp4maker", str(bdir),
        "--shorts",
        "--duration", f"{duration:g}",
        "--bottom", bottom_mode,
    ]
    if (original_url or "").strip():
        args += ["--original-url", original_url.strip()]
    final_token = "_shorts.mp4"
    logger.info("mp4maker shorts: %s", " ".join(args[2:]))
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
            if line.startswith("[done]") and final_token in line:
                final_path = line.split("]", 1)[1].strip()
    except asyncio.TimeoutError:
        proc.kill()
        raise RenderError(f"mp4maker 쇼츠 렌더 타임아웃 ({timeout:.0f}s)")
    await proc.wait()
    if proc.returncode != 0:
        raise RenderError(f"mp4maker 종료코드 {proc.returncode}")

    if final_path and Path(final_path).exists():
        return final_path
    doc = _read_script(bdir)
    guess = bdir / "draft" / f"{_chapter_id(doc)}{final_token}"
    if guess.exists():
        return str(guess)
    raise RenderError("쇼츠 렌더는 끝났지만 MP4를 찾지 못했습니다.")


async def render_intro(
    bundle_dir: str,
    *,
    duration: float = 15.0,
    speed: float = 1.15,
    resolution: str = "1920x1080",
    backdrop: str = "plain",
    order: str = "reverse",
    sfx: str = "both",
    audio_path: str = "",
    script_text: str = "",
    on_line: Optional[Callable[[str], None]] = None,
    timeout: float = 1800.0,
) -> str:
    """`python -m mp4maker <bundle> --intro ...` 실행. 가로 16:9 인트로 MP4 경로 반환.

    본편 앞에 붙는 '목차/요약' 티저. 전체화면 켄번스 빠른 컷 + 빠른 나레이션(atempo).
    """
    if not available():
        raise RenderError(f"mp4maker 체크아웃을 찾을 수 없습니다: {MP4MAKER_DIR}")
    bdir = Path(bundle_dir).resolve()
    args = [
        _python(), "-m", "mp4maker", str(bdir),
        "--intro",
        "--duration", f"{duration:g}",
        "--speed", f"{speed:g}",
        "--resolution", resolution,
        "--backdrop", backdrop,
        "--intro-order", order,
        "--sfx", sfx,
    ]
    if (audio_path or "").strip():
        args += ["--audio", audio_path.strip()]
    # 캡션용 인트로 대본은 파일로 전달(긴 텍스트/줄바꿈 안전)
    if (script_text or "").strip():
        try:
            sp = bdir / "draft" / "_intro_script.txt"
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(script_text.strip(), encoding="utf-8")
            args += ["--intro-script", str(sp)]
        except Exception:
            pass
    final_token = "_intro.mp4"
    logger.info("mp4maker intro: %s", " ".join(args[2:]))
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
            if line.startswith("[done]") and final_token in line:
                final_path = line.split("]", 1)[1].strip()
    except asyncio.TimeoutError:
        proc.kill()
        raise RenderError(f"mp4maker 인트로 렌더 타임아웃 ({timeout:.0f}s)")
    await proc.wait()
    if proc.returncode != 0:
        raise RenderError(f"mp4maker 종료코드 {proc.returncode}")

    if final_path and Path(final_path).exists():
        return final_path
    doc = _read_script(bdir)
    guess = bdir / "draft" / f"{_chapter_id(doc)}{final_token}"
    if guess.exists():
        return str(guess)
    raise RenderError("인트로 렌더는 끝났지만 MP4를 찾지 못했습니다.")


# ---- 🔗 인트로 + 본편 합치기 (원본 보존 · chNN_with_intro.mp4) -------------------
async def _stream_ffmpeg(args: list, on_line: Optional[Callable[[str], None]], timeout: float) -> int:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    assert proc.stdout is not None
    try:
        while True:
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip()
            if on_line and line:
                on_line(line)
    except asyncio.TimeoutError:
        proc.kill()
        raise RenderError("합치기 타임아웃")
    await proc.wait()
    return proc.returncode or 0


async def _ffprobe_streams(path: Path) -> dict:
    args = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)]
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    try:
        data = json.loads(out.decode("utf-8", "replace") or "{}")
    except Exception:
        data = {}
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {"v": v, "a": a}


async def merge_intro(bundle_dir: str, *, on_line: Optional[Callable[[str], None]] = None,
                      timeout: float = 3600.0) -> str:
    """인트로(chNN_intro.mp4)를 본편 최종영상 앞에 붙여 chNN_with_intro.mp4 생성.

    원본(chNN_final*.mp4)은 절대 건드리지 않는다. 인트로만 본편 규격에 맞춰 재인코딩 후
    concat 데먹서(-c copy)로 즉시 결합(빠름). 실패 시 재인코딩 폴백.
    """
    bdir = Path(bundle_dir).resolve()
    doc = _read_script(bdir)
    chap = _chapter_id(doc)
    draft = bdir / "draft"
    intro = draft / f"{chap}_intro.mp4"
    if not intro.exists():
        raise RenderError("인트로가 없습니다 — 먼저 '🎬 인트로 생성'을 하세요.")
    main = None
    for name in (f"{chap}_final_nosub.mp4", f"{chap}_final.mp4"):
        if (draft / name).exists():
            main = draft / name
            break
    if main is None:
        raise RenderError("본편 최종영상(chNN_final*.mp4)이 없습니다 — 먼저 ④ 풀 렌더를 하세요.")

    def _line(l: str) -> None:
        if on_line:
            on_line(l)

    _line(f"[merge] 본편={main.name} · 인트로={intro.name} (원본 보존)")
    info = await _ffprobe_streams(main)
    v, a = info["v"], info["a"]
    W = int(v.get("width") or 1920)
    H = int(v.get("height") or 1080)
    rfr = str(v.get("r_frame_rate") or "30/1")
    try:
        num, den = rfr.split("/")
        fps = int(round(float(num) / float(den))) if float(den) else 30
    except Exception:
        fps = 30
    try:
        ts = int(str(v.get("time_base") or "1/15360").split("/")[1])
    except Exception:
        ts = 15360
    sar = str(v.get("sample_aspect_ratio") or "1:1")
    if sar in ("N/A", "0:1", ""):
        sar = "1:1"
    ar = int(a.get("sample_rate") or 48000)
    ac = int(a.get("channels") or 2)

    matched = draft / "_intro_matched.mp4"
    vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar={sar.replace(':', '/')},"
          f"fps={fps},format=yuv420p")
    _line(f"[merge] 인트로 규격 맞춤 {W}x{H}@{fps} (ts={ts}, sar={sar})…")
    rc = await _stream_ffmpeg(
        ["ffmpeg", "-y", "-i", str(intro), "-vf", vf,
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-video_track_timescale", str(ts),
         "-c:a", "aac", "-ar", str(ar), "-ac", str(ac), "-b:a", "192k",
         "-movflags", "+faststart", str(matched)], _line, timeout)
    if rc != 0 or not matched.exists():
        raise RenderError("인트로 규격 맞춤 실패")

    out = draft / f"{chap}_with_intro.mp4"
    listf = draft / "_merge_list.txt"
    listf.write_text(f"file '{matched.resolve().as_posix()}'\nfile '{main.resolve().as_posix()}'\n",
                     encoding="utf-8")
    _line("[merge] 합치는 중 (copy)…")
    rc = await _stream_ffmpeg(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
         "-c", "copy", "-movflags", "+faststart", str(out)], _line, timeout)
    if rc != 0 or not out.exists():
        _line("[merge] copy 실패 → 재인코딩 폴백(시간 걸릴 수 있음)…")
        rc = await _stream_ffmpeg(
            ["ffmpeg", "-y", "-i", str(matched), "-i", str(main),
             "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]",
             "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-ar", str(ar), "-ac", str(ac), "-b:a", "192k",
             "-movflags", "+faststart", str(out)], _line, timeout)
    for tmp in (listf, matched):
        try:
            tmp.unlink()
        except Exception:
            pass
    if rc != 0 or not out.exists():
        raise RenderError("합치기 실패 — 출력 파일이 생성되지 않았습니다.")
    _line(f"[done] {out}")
    return str(out)

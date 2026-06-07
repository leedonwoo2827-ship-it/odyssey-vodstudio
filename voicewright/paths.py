from __future__ import annotations

import re
from pathlib import Path

CHAPTER_RE = re.compile(r"ch(\d{1,3})", re.IGNORECASE)


def normalize_chapter_id(raw: str | int | None) -> str | None:
    """문자열/숫자 chapter id를 zero-padded 2자리 (또는 그 이상) 문자열로 정규화."""
    if raw is None:
        return None
    if isinstance(raw, int):
        return f"{raw:02d}"
    s = str(raw).strip()
    if not s:
        return None
    m = CHAPTER_RE.search(s)
    if m:
        s = m.group(1)
    if not s.isdigit():
        return None
    return f"{int(s):02d}"


def resolve_chapter_id(
    *,
    explicit: str | int | None,
    script_field: str | int | None,
    filename_hint: str | None,
) -> str:
    """챕터 ID 우선순위: 명시값 → JSON 필드 → 파일명 → 에러."""
    for candidate in (explicit, script_field, filename_hint):
        norm = normalize_chapter_id(candidate)
        if norm is not None:
            return norm
    raise ValueError(
        "챕터 ID를 결정할 수 없습니다. 폼 필드 chapter, JSON 최상위 chapter, "
        "또는 파일명(ch{NN}_*) 중 하나가 필요합니다."
    )


def chapter_audio_dir(workspace_root: Path, chapter_id: str) -> Path:
    return Path(workspace_root) / f"ch{chapter_id}" / "audio"


def chapter_subtitles_dir(workspace_root: Path, chapter_id: str) -> Path:
    return Path(workspace_root) / f"ch{chapter_id}" / "subtitles"


def narration_filename(chapter_id: str, scene: int) -> str:
    return f"ch{chapter_id}_{scene:02d}_narration.wav"


def narration_path(workspace_root: Path, chapter_id: str, scene: int) -> Path:
    return chapter_audio_dir(workspace_root, chapter_id) / narration_filename(chapter_id, scene)


def srt_filename(chapter_id: str, scene: int) -> str:
    return f"ch{chapter_id}_{scene:02d}_narration.srt"


def srt_path(workspace_root: Path, chapter_id: str, scene: int) -> Path:
    return chapter_subtitles_dir(workspace_root, chapter_id) / srt_filename(chapter_id, scene)


def chapter_srt_path(workspace_root: Path, chapter_id: str) -> Path:
    return chapter_subtitles_dir(workspace_root, chapter_id) / f"ch{chapter_id}.srt"

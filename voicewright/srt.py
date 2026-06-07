from __future__ import annotations

import re
from dataclasses import dataclass


def format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_timestamp(s: str) -> float:
    """`HH:MM:SS,mmm` (또는 `.mmm`) → 초. 잘못된 형식이면 0.0."""
    s = s.strip().replace(".", ",")
    m = re.match(r"^(\d{1,2}):(\d{2}):(\d{2})(?:,(\d{1,3}))?$", s)
    if not m:
        return 0.0
    h, mi, se, ms = m.groups()
    ms = (ms or "0").ljust(3, "0")[:3]
    return int(h) * 3600 + int(mi) * 60 + int(se) + int(ms) / 1000.0


def make_single_srt(text: str, duration: float, *, index: int = 1) -> str:
    end = format_timestamp(max(duration, 0.1))
    body = (text or "").strip()
    return f"{index}\n00:00:00,000 --> {end}\n{body}\n"


# ---------------------------------------------------------------------------
# 멀티큐 자막 — scene 텍스트를 ~30자 구간(cue)으로 쪼개고 구간별 타임코드를 부여
# ---------------------------------------------------------------------------

_CUE_SENT_END_RE = re.compile(r"(?<=[.!?。！？…])\s+")
_CUE_COMMA_RE = re.compile(r"(?<=[,，、])\s+")
# 자막 한 줄(cue) 최대 길이. 문장 단위를 우선 유지하고, 이 길이를 넘는 긴 문장만
# 쉼표 기준으로 추가 분할한다 (너무 자잘하게 쪼개지지 않도록 문장 길이에 맞춤).
_CUE_MAX_CHARS = 55


@dataclass
class Cue:
    text: str
    start: float
    end: float


def _hard_wrap(piece: str, max_chars: int) -> list[str]:
    """공백 경계를 우선 활용해 max_chars 이하 조각으로 자른다. 공백이 없으면 글자 단위."""
    piece = piece.strip()
    if len(piece) <= max_chars:
        return [piece] if piece else []
    out: list[str] = []
    cur = ""
    for word in piece.split(" "):
        if not word:
            continue
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= max_chars:
            cur = cur + " " + word
        else:
            out.append(cur)
            cur = word
        # 단어 하나가 max_chars보다 길면 글자 단위로 강제 분할
        while len(cur) > max_chars:
            out.append(cur[:max_chars])
            cur = cur[max_chars:]
    if cur:
        out.append(cur)
    return out


def split_into_cues(text: str, max_chars: int = _CUE_MAX_CHARS) -> list[str]:
    """자막 표시용 구간 분할: 문장끝 → 쉼표 순으로 쪼개고 ~max_chars까지 묶음.

    한 절이 max_chars를 넘으면 공백/글자 경계에서 hard-wrap 한다.
    """
    text = (text or "").strip()
    if not text:
        return []
    cues: list[str] = []
    for sent in _CUE_SENT_END_RE.split(text):
        sent = sent.strip()
        if not sent:
            continue
        parts = [p.strip() for p in _CUE_COMMA_RE.split(sent) if p.strip()]
        cur = ""
        for p in parts:
            if not cur:
                cur = p
            elif len(cur) + 1 + len(p) <= max_chars:
                cur = cur + " " + p
            else:
                cues.extend(_hard_wrap(cur, max_chars))
                cur = p
        if cur:
            cues.extend(_hard_wrap(cur, max_chars))
    return [c for c in cues if c]


def auto_time_cues(cue_texts: list[str], total_duration: float) -> list[Cue]:
    """글자 수에 비례해 total_duration을 분배하고 연속(끝=다음 시작)으로 채운다.

    자동 baseline일 뿐 — 사용자가 이후 UI에서 미세 조정한다.
    """
    texts = [t for t in (ct.strip() for ct in cue_texts) if t]
    if not texts:
        return []
    total = max(float(total_duration), 0.1)
    weights = [max(len(t), 1) for t in texts]
    weight_sum = sum(weights)
    cues: list[Cue] = []
    cursor = 0.0
    for i, (t, w) in enumerate(zip(texts, weights)):
        if i == len(texts) - 1:
            end = total  # 마지막 구간은 누적 오차 없이 끝에 딱 맞춤
        else:
            end = cursor + total * (w / weight_sum)
        cues.append(Cue(text=t, start=round(cursor, 3), end=round(end, 3)))
        cursor = end
    return cues


def make_multi_srt(cues: list[Cue], *, base_index: int = 1) -> str:
    """Cue 목록 → 표준 SRT 문자열. 빈 목록이면 빈 블록 대신 빈 문자열."""
    parts: list[str] = []
    idx = base_index
    for c in cues:
        body = (c.text or "").strip()
        if not body:
            continue
        start = max(c.start, 0.0)
        end = max(c.end, start + 0.001)
        parts.append(str(idx))
        parts.append(f"{format_timestamp(start)} --> {format_timestamp(end)}")
        parts.append(body)
        parts.append("")
        idx += 1
    return ("\n".join(parts).rstrip() + "\n") if parts else ""


_SRT_TIME_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
)


def parse_srt_cues(text: str) -> list[Cue]:
    """SRT 문자열 → Cue 목록. 단일/멀티 블록 모두 처리.

    index 줄은 무시하고 `time --> time` 줄을 만나면 다음 비어있지 않은
    줄들을 다음 타임코드(또는 EOF) 전까지 본문으로 모은다.
    """
    cues: list[Cue] = []
    blocks = re.split(r"\n\s*\n", (text or "").strip())
    for block in blocks:
        lines = [ln for ln in block.splitlines()]
        tm = None
        body_lines: list[str] = []
        for ln in lines:
            m = _SRT_TIME_RE.search(ln)
            if m and tm is None:
                tm = (parse_timestamp(m.group(1)), parse_timestamp(m.group(2)))
                continue
            if tm is not None:
                body_lines.append(ln)
            # tm 이전의 index 줄은 그냥 건너뜀
        if tm is None:
            continue
        body = "\n".join(body_lines).strip()
        if body:
            cues.append(Cue(text=body, start=tm[0], end=tm[1]))
    return cues


def merge_scene_cues(scenes: list[tuple[list[Cue], float]]) -> str:
    """scene별 (큐목록, scene오디오길이)을 누적 offset으로 이어붙여 챕터 SRT 생성.

    각 scene의 큐는 scene 내부 기준(0부터)이라 가정하고, 앞선 scene들의
    오디오 길이 합만큼 밀어서 전역 타임코드로 변환 + 전역 재번호.
    """
    merged: list[Cue] = []
    cursor = 0.0
    for cues, scene_dur in scenes:
        for c in cues:
            merged.append(Cue(text=c.text, start=cursor + c.start, end=cursor + c.end))
        cursor += max(float(scene_dur), 0.0)
    return make_multi_srt(merged)


@dataclass
class SrtEntry:
    scene: int
    text: str
    duration: float


def make_chapter_srt(entries: list[SrtEntry]) -> str:
    cursor = 0.0
    parts: list[str] = []
    for i, e in enumerate(entries, 1):
        dur = max(e.duration, 0.1)
        start = cursor
        end = cursor + dur
        parts.append(str(i))
        parts.append(f"{format_timestamp(start)} --> {format_timestamp(end)}")
        parts.append((e.text or "").strip())
        parts.append("")
        cursor = end
    return "\n".join(parts).rstrip() + "\n"

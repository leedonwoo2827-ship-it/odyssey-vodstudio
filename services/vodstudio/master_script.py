"""2단계 마스터 대본 텍스트 → 구조화된 Slide 리스트 파서.

NotebookLM이 돌려주는 대본은 아래 형식의 블록이 슬라이드마다 반복된다:

    슬라이드 번호: 1
    제목: ...
    화면 텍스트: ...
    상세 대본: ...

LLM 출력은 라벨 변형(콜론 종류, 굵게 마크다운, 영어 라벨 등)이 흔하므로 느슨하게 파싱한다.
"""

import re
from dataclasses import dataclass, field
from typing import List


@dataclass
class Slide:
    number: int
    title: str = ""
    screen_text: str = ""   # 화면 텍스트 — 슬라이드 검수 기준
    narration: str = ""     # 상세 대본 — mp4maker narration_text 원천
    voice_style: str = ""   # (선택) VoiceWright voice_style — 비면 전역값 사용

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "title": self.title,
            "screen_text": self.screen_text,
            "narration": self.narration,
        }


# 라벨 매칭 — 한/영 변형 허용. "**제목**:", "제목 :", "Title:" 등.
_LABELS = {
    "number": [r"슬라이드\s*번호", r"slide\s*(?:number|no\.?)", r"페이지\s*번호"],
    "title": [r"제목", r"title", r"헤드라인", r"headline"],
    "screen_text": [r"화면\s*텍스트", r"screen\s*text", r"on[-\s]?screen", r"본문\s*텍스트"],
    "narration": [r"상세\s*대본", r"대본", r"narration", r"script", r"speaker\s*notes?"],
}


def _label_regex(patterns: List[str]) -> re.Pattern:
    # optional leading markdown bold/asterisks/spaces, the label, optional bold close, then : or ：
    body = "|".join(patterns)
    return re.compile(rf"^\s*[*_#>\s]*(?:{body})\s*[*_]*\s*[:：]\s*(.*)$", re.IGNORECASE)


_NUMBER_RE = _label_regex(_LABELS["number"])
_TITLE_RE = _label_regex(_LABELS["title"])
_SCREEN_RE = _label_regex(_LABELS["screen_text"])
_NARR_RE = _label_regex(_LABELS["narration"])

# Header-style slide boundary: a line that is essentially just "슬라이드 1",
# "**슬라이드 1**", "## 슬라이드 1", "Slide 1", "페이지 1:" — i.e. the keyword +
# a number and nothing else. This is the form Gemini/ChatGPT usually emit, as
# opposed to the labeled "슬라이드 번호: 1" form.
_SLIDE_HEADER_RE = re.compile(
    r"^\s*[#>*_\s]*(?:슬라이드|slide|페이지|page)\s*0*(\d+)\s*[*_]*\s*[:：.)]?\s*$",
    re.IGNORECASE,
)


def _clean(value: str) -> str:
    """Strip surrounding markdown emphasis (**bold**, _italic_) from a label value."""
    return (value or "").strip().strip("*_ ").strip()


def parse_master_script(text: str) -> List[Slide]:
    """Parse the 2단계 output into ordered Slides.

    Strategy: scan line by line. A "슬라이드 번호:" line opens a new slide;
    label lines set the current field; unlabeled lines append to the field
    most recently opened (so multi-line 상세 대본 is preserved).
    """
    slides: List[Slide] = []
    current: Slide | None = None
    active_field: str | None = None

    def has_content(s: Slide | None) -> bool:
        # A slide is worth keeping only if it carries real text — a bare
        # "슬라이드 번호" with nothing else (or stray prose) is dropped.
        return s is not None and bool(s.title or s.screen_text or s.narration)

    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            # blank line ends the current free-text run but keeps the slide open
            active_field = None
            continue

        # Header-style boundary first ("슬라이드 1", "**슬라이드 1**", "Slide 1").
        mh = _SLIDE_HEADER_RE.match(line)
        if mh:
            if has_content(current):
                slides.append(current)  # type: ignore[arg-type]
            current = Slide(number=int(mh.group(1)))
            active_field = None
            continue

        m = _NUMBER_RE.match(line)
        if m:
            if has_content(current):
                slides.append(current)  # type: ignore[arg-type]
            num_match = re.search(r"\d+", m.group(1).strip())
            num = int(num_match.group()) if num_match else (len(slides) + 1)
            current = Slide(number=num)
            active_field = None
            continue

        # Field label lines (제목 / 화면 텍스트 / 상세 대본 ...). The first one
        # seen also implicitly opens a slide if "슬라이드 번호" was omitted.
        matched_field = False
        for field_name, regex in (
            ("title", _TITLE_RE),
            ("screen_text", _SCREEN_RE),
            ("narration", _NARR_RE),
        ):
            m = regex.match(line)
            if m:
                if current is None:
                    current = Slide(number=len(slides) + 1)
                setattr(current, field_name, _clean(m.group(1)))
                active_field = field_name
                matched_field = True
                break
        if matched_field:
            continue

        # Continuation line for the active field (e.g. multi-line 상세 대본).
        # Stray prose before any slide/label (current is None) is ignored.
        if current is not None and active_field:
            prev = getattr(current, active_field)
            setattr(current, active_field, (prev + "\n" + line.strip()).strip())

    if has_content(current):
        slides.append(current)  # type: ignore[arg-type]

    # Renumber sequentially 1..N to guarantee a clean ordering for the bundle,
    # regardless of any gaps/dupes in the model's numbering.
    for i, s in enumerate(slides, start=1):
        s.number = i
    return slides


def slides_from_plain_text(text: str) -> List[Slide]:
    """Fallback for pasted text that ISN'T in the labeled (슬라이드 번호/제목/대본)
    format: split into blocks on blank lines and treat each block as one slide's
    narration (first line becomes the title). Used by the manual paste mode.
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text or "") if b.strip()]
    slides: List[Slide] = []
    for i, block in enumerate(blocks, start=1):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        title = lines[0][:80] if lines else f"Slide {i}"
        slides.append(Slide(number=i, title=title, narration=block))
    return slides


def parse_or_split(text: str) -> List[Slide]:
    """Use the labeled parser when the text looks structured; otherwise fall
    back to plain-text block splitting. Lets the manual mode accept either the
    NotebookLM 'Master Script' format or any pasted prose."""
    slides = parse_master_script(text)
    if len(slides) >= 2:
        return slides
    plain = slides_from_plain_text(text)
    # Prefer whichever yielded more scenes (structured single-block stays as-is).
    return plain if len(plain) > len(slides) else (slides or plain)


def estimate_narration_seconds(text: str) -> int:
    """Rough Korean TTS duration estimate from character count.

    ~4 Korean chars/sec is a conservative narration pace; clamp to a sane
    floor so very short lines still get screen time. mp4maker re-measures the
    real audio later, so this only seeds total_duration + per-scene hints.
    """
    chars = len((text or "").replace(" ", "").replace("\n", ""))
    return max(2, round(chars / 4.0))

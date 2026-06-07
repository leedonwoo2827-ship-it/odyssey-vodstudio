from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


_LETTER_HANGUL = {
    "A": "에이", "B": "비", "C": "씨", "D": "디", "E": "이", "F": "에프",
    "G": "지", "H": "에이치", "I": "아이", "J": "제이", "K": "케이", "L": "엘",
    "M": "엠", "N": "엔", "O": "오", "P": "피", "Q": "큐", "R": "알",
    "S": "에스", "T": "티", "U": "유", "V": "브이", "W": "더블유",
    "X": "엑스", "Y": "와이", "Z": "제트",
}
_ACRONYM_RE = re.compile(r"(?<![A-Za-z])[A-Z]{2,}(?![A-Za-z])")

_DIGIT_HANGUL_SINO = ["", "일", "이", "삼", "사", "오", "육", "칠", "팔", "구"]

# 한자어 수사 + 단위 변환.
#   - 연도(3~4자리 + "년")는 별도 처리 — "14년" 같은 기간 표기를 건드리지 않기 위해.
#   - 그 외 단위는 1~4자리 + 단위 매칭.
#   - 단위는 띄어쓰기 정책에 따라 두 그룹.
#       TIGHT  : 숫자에 붙여 읽음 (예: "27분" → "이십칠분")
#       SPACED : 한 칸 띄움      (예: "27킬로미터" → "이십칠 킬로미터")
#   - 시(시간), 살, 명, 개, 마리 등 고유어 수사를 쓰는 단위, 6월/10월 같은
#     특수 발음(유월/시월) 단위는 의도적으로 제외.
_YEAR_RE = re.compile(r"(?<!\d)(\d{3,4})년")
_SINO_TIGHT_UNITS = ["분", "초", "도", "원", "일"]
_SINO_SPACED_UNITS = ["킬로미터", "센티미터", "밀리미터", "킬로그램", "퍼센트", "미터", "그램"]
_SPACED_UNIT_SET = set(_SINO_SPACED_UNITS)
# 긴 단위부터 매칭해야 "킬로미터"가 "미터"보다 먼저 잡힌다.
_SINO_UNIT_RE = re.compile(
    r"(?<!\d)(\d{1,4})("
    + "|".join(sorted(_SINO_TIGHT_UNITS + _SINO_SPACED_UNITS, key=len, reverse=True))
    + r")"
)


def _spell_acronym(word: str) -> str:
    return "".join(_LETTER_HANGUL.get(c, c) for c in word)


def _sino_number(n: int) -> str:
    """1~9999 정수를 한자어 수사로 변환 (천/백/십 앞 1은 생략)."""
    if n == 0:
        return "영"
    digits = [int(c) for c in str(n)]
    units = ["천", "백", "십", ""][4 - len(digits):]
    parts: list[str] = []
    for d, u in zip(digits, units):
        if d == 0:
            continue
        if d == 1 and u in ("천", "백", "십"):
            parts.append(u)
        else:
            parts.append(_DIGIT_HANGUL_SINO[d] + u)
    return "".join(parts)


def _convert_years(text: str) -> str:
    return _YEAR_RE.sub(lambda m: _sino_number(int(m.group(1))) + "년", text)


def _convert_sino_units(text: str) -> str:
    def repl(m: "re.Match[str]") -> str:
        n = int(m.group(1))
        unit = m.group(2)
        sep = " " if unit in _SPACED_UNIT_SET else ""
        return f"{_sino_number(n)}{sep}{unit}"
    return _SINO_UNIT_RE.sub(repl, text)


@dataclass
class PronunciationMap:
    """약자/외래어를 한국어 발음으로 치환하는 사전.

    SRT 자막에는 적용하지 않는다 — 합성용 텍스트만 변환.
    """
    rules: dict[str, str] = field(default_factory=dict)
    _pattern: "re.Pattern[str] | None" = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._compile()

    def _compile(self) -> None:
        if not self.rules:
            self._pattern = None
            return
        # 긴 키부터 매칭 (e.g., HTTPS가 HTTP보다 먼저 잡혀야 함)
        keys = sorted(self.rules.keys(), key=len, reverse=True)
        escaped = [re.escape(k) for k in keys]
        # 라틴 알파벳 기준 경계 — `\b`를 쓰면 Python regex가 한글도 \w로 취급해서
        # "CERN의" 같은 조사 결합형이 매칭에서 누락된다. lookaround로 인접한
        # 라틴 글자만 차단해서 한글/숫자/공백은 모두 경계로 인정한다.
        self._pattern = re.compile(
            r"(?<![A-Za-z])(?:" + "|".join(escaped) + r")(?![A-Za-z])"
        )

    def apply(
        self,
        text: str,
        *,
        spell_unknown_acronyms: bool = False,
        convert_years: bool = False,
    ) -> str:
        if not text:
            return text
        if self._pattern is not None:
            text = self._pattern.sub(lambda m: self.rules[m.group(0)], text)
        if spell_unknown_acronyms:
            # 사전에 없는 2글자 이상 영문 대문자 약어는 알파벳 단위로 음역
            text = _ACRONYM_RE.sub(lambda m: _spell_acronym(m.group(0)), text)
        if convert_years:
            text = _convert_years(text)
            text = _convert_sino_units(text)
        return text


def load_pronunciation_map(path: Path) -> PronunciationMap:
    if not path.exists():
        return PronunciationMap(rules={})
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("pronunciation_map.yaml 로드 실패 (%s): %s", path, exc)
        return PronunciationMap(rules={})

    raw = data.get("rules") or {}
    rules: dict[str, str] = {}
    for k, v in raw.items():
        key = str(k).strip()
        val = str(v).strip()
        if key and val:
            rules[key] = val
    return PronunciationMap(rules=rules)

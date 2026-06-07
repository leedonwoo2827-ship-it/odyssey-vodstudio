from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

ALL_VOICE_CODES = ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]


@dataclass(frozen=True)
class VoiceInfo:
    code: str
    gender: str
    preset_path: Path


def list_voices(voice_styles_dir: Path) -> list[VoiceInfo]:
    voices = []
    for code in ALL_VOICE_CODES:
        p = voice_styles_dir / f"{code}.json"
        gender = "male" if code.startswith("M") else "female"
        voices.append(VoiceInfo(code=code, gender=gender, preset_path=p))
    return voices


@dataclass
class VoiceMap:
    default: str
    styles: dict[str, str]                # lowercase 키

    def resolve(self, voice_style: str | None) -> tuple[str, str | None]:
        """returns (resolved_code, warning_or_none)."""
        if not voice_style:
            return self.default, None

        key = voice_style.strip().lower()
        if not key:
            return self.default, None

        # 원시 보이스 코드(M1..F5)는 그대로 사용 — UI에서 코드를 직접 저장/전달하는 경로 지원
        code_up = voice_style.strip().upper()
        if code_up in ALL_VOICE_CODES:
            return code_up, None

        if key in self.styles:
            return self.styles[key], None

        if any(t in key for t in ("female", "여성")):
            return "F2", f"unmapped voice_style '{voice_style}' → F2 (female heuristic)"
        if any(t in key for t in ("male", "남성")):
            return "M3", f"unmapped voice_style '{voice_style}' → M3 (male heuristic)"

        return self.default, f"unmapped voice_style '{voice_style}' → {self.default} (default)"


def load_voice_map(path: Path) -> VoiceMap:
    if not path.exists():
        logger.warning("voice_map.yaml을 찾을 수 없습니다 (%s). 기본값 F2로 동작합니다.", path)
        return VoiceMap(default="F2", styles={})

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    default = str(data.get("default", "M5")).upper()
    if default not in ALL_VOICE_CODES:
        logger.warning("voice_map.yaml의 default '%s'가 유효하지 않아 M5로 대체합니다.", default)
        default = "M5"

    styles: dict[str, str] = {}
    for k, v in (data.get("styles") or {}).items():
        code = str(v).upper()
        if code not in ALL_VOICE_CODES:
            logger.warning("voice_map.yaml의 styles.%s='%s'가 유효하지 않아 무시합니다.", k, v)
            continue
        styles[str(k).strip().lower()] = code

    return VoiceMap(default=default, styles=styles)


def voice_preset_path(voice_styles_dir: Path, code: str) -> Path:
    code = code.upper()
    if code not in ALL_VOICE_CODES:
        raise ValueError(f"알 수 없는 보이스 코드: {code}. 사용 가능: {ALL_VOICE_CODES}")
    p = voice_styles_dir / f"{code}.json"
    if not p.exists():
        raise FileNotFoundError(f"보이스 프리셋이 없습니다: {p}")
    return p

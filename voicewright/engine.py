from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import numpy as np

from . import settings as settings_module
from ._assets_check import check_assets
from ._vendor.supertonic_helper import (
    Style,
    TextToSpeech,
    load_text_to_speech_with_providers,
    load_voice_style,
)
from .pronunciation import PronunciationMap, load_pronunciation_map
from .voices import voice_preset_path

logger = logging.getLogger(__name__)


# 한국어 긴 문장에서 Supertonic alignment가 흔들려 중간 단어를 통째로 누락하는
# 증상이 보고됨 (예: 4문장·115자 한 덩어리 입력에서 "로부터", "지식의" 드롭).
# 벤더된 helper의 chunk_text(max_len=120)는 너무 관대해서 한국어 음절 밀도엔
# 부족하다. 우리 레이어에서 항상 문장 단위로 자르고, 한 문장이 길면 쉼표
# 기준으로 추가 분할한 뒤 조각별로 따로 합성해 붙인다.
_SENT_END_RE = re.compile(r"(?<=[.!?。！？…])\s+")
_COMMA_RE = re.compile(r"(?<=[,，、])\s+")
_TTS_MAX_CHARS = 60
_INTER_PIECE_SILENCE_SEC = 0.18


def _split_for_tts(text: str, max_chars: int = _TTS_MAX_CHARS) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    out: list[str] = []
    for sent in _SENT_END_RE.split(text):
        sent = sent.strip()
        if not sent:
            continue
        if len(sent) <= max_chars:
            out.append(sent)
            continue
        parts = [p.strip() for p in _COMMA_RE.split(sent) if p.strip()]
        cur = ""
        for p in parts:
            if not cur:
                cur = p
            elif len(cur) + 1 + len(p) <= max_chars:
                cur = cur + " " + p
            else:
                out.append(cur)
                cur = p
        if cur:
            out.append(cur)
    return out


def _select_providers(use_gpu: bool) -> list[str]:
    if not use_gpu:
        return ["CPUExecutionProvider"]
    try:
        import onnxruntime as ort
        available = set(ort.get_available_providers())
    except Exception:
        available = set()

    preferred = ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
    chosen = [p for p in preferred if p in available] if available else ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if "CPUExecutionProvider" not in chosen:
        chosen.append("CPUExecutionProvider")
    return chosen


class Engine:
    _instance: "Engine | None" = None
    _init_lock = asyncio.Lock()

    def __init__(self, onnx_dir: Path, voice_styles_dir: Path, use_gpu: bool):
        check_assets(onnx_dir, voice_styles_dir)
        providers = _select_providers(use_gpu)
        logger.info("Loading Supertonic engine from %s with providers=%s", onnx_dir, providers)
        self._tts: TextToSpeech = load_text_to_speech_with_providers(str(onnx_dir), providers)
        self._infer_lock = asyncio.Lock()
        self.providers = providers
        self.use_gpu_active = "CUDAExecutionProvider" in providers or "DmlExecutionProvider" in providers
        self.sample_rate: int = int(self._tts.sample_rate)
        self._voice_styles_dir = voice_styles_dir
        self._style_cache: dict[str, Style] = {}
        self._pmap: PronunciationMap | None = None
        self._pmap_mtime: float = -1.0

    def _get_pmap(self) -> PronunciationMap:
        s = settings_module.load()
        path = s.pronunciation_map_path
        mtime = path.stat().st_mtime if path.exists() else 0.0
        if self._pmap is None or mtime != self._pmap_mtime:
            self._pmap = load_pronunciation_map(path)
            self._pmap_mtime = mtime
            if self._pmap.rules:
                logger.info("pronunciation_map loaded: %d rules", len(self._pmap.rules))
        return self._pmap

    @classmethod
    async def get(cls) -> "Engine":
        if cls._instance is None:
            async with cls._init_lock:
                if cls._instance is None:
                    s = settings_module.load()
                    cls._instance = cls(s.onnx_dir, s.voice_styles_dir, s.resolve_use_gpu())
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def _style_for(self, voice_code: str) -> Style:
        code = voice_code.upper()
        if code not in self._style_cache:
            path = voice_preset_path(self._voice_styles_dir, code)
            self._style_cache[code] = load_voice_style([str(path)])
        return self._style_cache[code]

    def _styles_for(self, voice_codes: list[str]) -> Style:
        paths = [str(voice_preset_path(self._voice_styles_dir, c.upper())) for c in voice_codes]
        return load_voice_style(paths)

    def _trim_wav(self, wav: np.ndarray, dur: np.ndarray, idx: int = 0) -> np.ndarray:
        """trailing silence만 잘라낸다 (텍스트 잘림 방지).

        예전엔 duration_predictor가 예측한 dur로 단순 슬라이싱했는데, 그
        값이 실제 발화 길이보다 짧을 때 마지막 단어가 잘려 들어가지 않는
        현상이 있었음. 이제는 신호 진폭으로 trailing silence를 찾아
        그 직후까지만 자른다. 말미에 150ms 여유 버퍼를 둔다.
        """
        full = wav[idx] if wav.ndim == 2 else wav
        if full.size == 0:
            return full

        threshold = 0.01  # |sample| <= 0.01 (-40dB) → silence
        nonzero = np.where(np.abs(full) > threshold)[0]
        if len(nonzero) == 0:
            # 통째로 무음이면 dur로 잘라 padding 제거 (fallback)
            n = int(self.sample_rate * float(dur[idx]))
            return full[: max(0, min(n, full.shape[-1]))]

        tail = int(self.sample_rate * 0.15)
        end = min(int(nonzero[-1]) + tail, full.shape[-1])
        return full[:end]

    async def synth(
        self,
        text: str,
        *,
        voice_code: str,
        lang: str = "ko",
        total_step: int | None = None,
        speed: float | None = None,
    ) -> np.ndarray:
        s = settings_module.load()
        ts = total_step if total_step is not None else s.default_total_step
        sp = speed if speed is not None else s.default_speed
        # 합성 직전 항상 전체 발음 변환: 발음사전 + 영문 약어 음역 + 연도(1989년→천구백…)
        # + 숫자·단위. (자막 SRT에는 적용 안 됨 — 원문 유지) 모든 합성 경로가 이걸 거친다.
        text = self._get_pmap().apply(text, spell_unknown_acronyms=True, convert_years=True)
        style = self._style_for(voice_code)

        pieces = _split_for_tts(text)
        if not pieces:
            return np.zeros(0, dtype=np.float32)

        silence = np.zeros(int(self.sample_rate * _INTER_PIECE_SILENCE_SEC), dtype=np.float32)
        parts: list[np.ndarray] = []
        async with self._infer_lock:
            for i, piece in enumerate(pieces):
                wav, dur = await asyncio.to_thread(self._tts, piece, lang, style, ts, sp)
                parts.append(self._trim_wav(wav, dur, 0))
                if i < len(pieces) - 1:
                    parts.append(silence)
        return np.concatenate(parts) if parts else np.zeros(0, dtype=np.float32)

    async def synth_batch_same_voice(
        self,
        text_list: list[str],
        *,
        voice_code: str,
        lang: str = "ko",
        total_step: int | None = None,
        speed: float | None = None,
    ) -> list[np.ndarray]:
        # 각 scene을 chunked synth()로 처리해 alignment dropout을 막는다.
        # 벤더 batch ONNX 호출은 텍스트별 alignment 이슈를 그대로 가지고 있어
        # 정확성을 위해 직렬 호출로 전환했다.
        out: list[np.ndarray] = []
        for t in text_list:
            wav = await self.synth(
                t,
                voice_code=voice_code,
                lang=lang,
                total_step=total_step,
                speed=speed,
            )
            out.append(wav)
        return out

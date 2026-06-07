from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import soundfile as sf


def _to_1d_float32(samples) -> np.ndarray:
    arr = np.asarray(samples)
    if arr.dtype != np.float32:
        arr = arr.astype(np.float32)
    arr = np.squeeze(arr)
    if arr.ndim != 1:
        raise ValueError(f"WAV writer는 1D mono 샘플만 받습니다. shape={arr.shape}")
    return arr


def write_wav(path: Path, samples, sample_rate: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), _to_1d_float32(samples), sample_rate, subtype="PCM_16")


def to_wav_bytes(samples, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, _to_1d_float32(samples), sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()

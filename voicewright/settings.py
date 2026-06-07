from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _project_root() -> Path:
    # 영상공방(260604) 레이아웃: <repo>/voicewright/settings.py → <repo> = parents[1].
    # (assets/ 와 config/ 는 repo 루트에 위치.)
    return Path(__file__).resolve().parents[1]


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser().resolve() if raw else default


@dataclass(frozen=True)
class Settings:
    project_root: Path
    onnx_dir: Path
    voice_styles_dir: Path
    voice_map_path: Path
    pronunciation_map_path: Path
    workspace_root: Path
    use_gpu_mode: str            # "auto" | "1" | "0" (true/false도 허용)
    default_speed: float
    default_total_step: int
    batch_chunk_size: int
    host: str
    port: int

    def resolve_use_gpu(self) -> bool:
        mode = self.use_gpu_mode.strip().lower()
        if mode in ("1", "true", "yes", "on"):
            return True
        if mode in ("0", "false", "no", "off"):
            return False
        try:
            import onnxruntime as ort
            return ort.get_device().upper() == "GPU"
        except Exception:
            return False


def load() -> Settings:
    root = _project_root()
    assets = _env_path("VOICEWRIGHT_ASSETS_DIR", root / "assets")
    # supertonic-3 레이아웃: assets/onnx/*.onnx + tts.json + unicode_indexer.json,
    # assets/voice_styles/*.json
    onnx_dir = assets / "onnx" if (assets / "onnx").exists() else assets
    return Settings(
        project_root=root,
        onnx_dir=onnx_dir,
        voice_styles_dir=assets / "voice_styles",
        voice_map_path=_env_path("VOICEWRIGHT_VOICE_MAP", root / "config" / "voice_map.yaml"),
        pronunciation_map_path=_env_path("VOICEWRIGHT_PRONUNCIATION_MAP", root / "config" / "pronunciation_map.yaml"),
        workspace_root=_env_path("VOICEWRIGHT_WORKSPACE", root / "workspace"),
        use_gpu_mode=_env_str("VOICEWRIGHT_USE_GPU", "auto"),
        default_speed=float(_env_str("VOICEWRIGHT_DEFAULT_SPEED", "1.00")),
        default_total_step=_env_int("VOICEWRIGHT_TOTAL_STEP", 8),
        batch_chunk_size=_env_int("VOICEWRIGHT_BATCH_CHUNK_SIZE", 4),
        host=_env_str("VOICEWRIGHT_HOST", "0.0.0.0"),
        port=_env_int("VOICEWRIGHT_PORT", 7878),
    )

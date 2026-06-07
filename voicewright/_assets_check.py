from __future__ import annotations

from pathlib import Path

REQUIRED_VOICE_CODES = ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]

SETUP_HINT = (
    "assets/ 디렉토리가 비어있거나 모델 파일이 빠졌습니다.\n"
    "Windows: powershell .\\scripts\\setup_assets.ps1\n"
    "Linux/macOS/WSL: bash ./scripts/setup_assets.sh"
)


def check_assets(onnx_dir: Path, voice_styles_dir: Path) -> None:
    if not onnx_dir.exists():
        raise RuntimeError(f"{onnx_dir}가 존재하지 않습니다.\n{SETUP_HINT}")

    onnx_files = list(onnx_dir.glob("*.onnx"))
    if not onnx_files:
        raise RuntimeError(
            f"{onnx_dir}에 ONNX 모델 파일이 없습니다 (*.onnx).\n{SETUP_HINT}"
        )

    if not voice_styles_dir.exists():
        raise RuntimeError(
            f"{voice_styles_dir}가 존재하지 않습니다.\n{SETUP_HINT}"
        )

    missing = [c for c in REQUIRED_VOICE_CODES if not (voice_styles_dir / f"{c}.json").exists()]
    if missing:
        raise RuntimeError(
            f"voice_styles/ 안에 누락된 보이스: {', '.join(missing)}\n{SETUP_HINT}"
        )

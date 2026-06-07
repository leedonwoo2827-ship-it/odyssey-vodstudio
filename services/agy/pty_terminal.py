"""크로스플랫폼 PTY(가상 터미널) 세션 — 웹 내장 터미널용.

웹 화면(xterm.js) ↔ WebSocket ↔ 이 PTY 세션 ↔ 셸(cmd/bash) 안에서 사용자가
`agy` 명령(로그인/로그아웃·계정전환, 모델 변경, 사용량 조회 등)을 직접 입력한다.

- Windows: pywinpty(`winpty.PtyProcess`)
- POSIX  : ptyprocess(`ptyprocess.PtyProcess`)
두 라이브러리는 의도적으로 동일한 API(spawn/read/write/isalive/setwinsize)를 제공한다.

보안: 이 터미널은 호스트 셸 접근을 의미하므로, 라우트에서 반드시 loopback(127.0.0.1) 호출로
제한한다. (각 PC 로컬 단독 사용 전제)
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional

# ── PtyProcess 구현체 선택 ───────────────────────────────────────────────
_IMPORT_ERROR = None
try:  # Windows
    from winpty import PtyProcess  # type: ignore
    _BACKEND = "winpty"
except Exception as _e_win:  # POSIX — Unicode 변형으로 str I/O 통일
    try:
        from ptyprocess import PtyProcessUnicode as PtyProcess  # type: ignore
        _BACKEND = "ptyprocess"
    except Exception as _e_posix:  # pragma: no cover
        PtyProcess = None  # type: ignore
        _BACKEND = None
        _IMPORT_ERROR = (
            f"winpty: {_e_win!r} / ptyprocess: {_e_posix!r}"
        )


def backend_available() -> bool:
    return PtyProcess is not None


def diag() -> dict:
    """진단용: PTY 백엔드/설치 상태."""
    from .runner import agy_path
    return {
        "platform": sys.platform,
        "pty_backend": _BACKEND,
        "pty_available": PtyProcess is not None,
        "pty_import_error": _IMPORT_ERROR,
        "shell": default_shell(),
        "agy_path": agy_path(),
        "agy_installed": agy_path() is not None,
    }


def default_shell() -> List[str]:
    """기본 셸. 파워셸 대신 Windows=cmd, POSIX=bash 선호(.env 로 덮어쓰기 가능)."""
    override = os.environ.get("AGY_TERMINAL_SHELL")
    if override:
        return override.split()
    if sys.platform == "win32":
        return [os.environ.get("COMSPEC", "cmd.exe")]
    return [os.environ.get("SHELL", "/bin/bash")]


class PtySession:
    """단일 PTY 세션 래퍼."""

    def __init__(self, argv: Optional[List[str]] = None, cols: int = 120, rows: int = 30):
        if not backend_available():
            raise RuntimeError(
                "PTY 백엔드가 없습니다. requirements 설치 필요: "
                "pywinpty(Windows) 또는 ptyprocess(POSIX)."
            )
        self.argv = argv or default_shell()
        self._proc = PtyProcess.spawn(self.argv, dimensions=(rows, cols))

    def read(self, size: int = 65536) -> str:
        """블로킹 읽기. EOF/종료 시 예외 → 호출측에서 세션 종료 처리."""
        return self._proc.read(size)

    def write(self, data: str) -> None:
        self._proc.write(data)

    def setwinsize(self, rows: int, cols: int) -> None:
        try:
            self._proc.setwinsize(rows, cols)
        except Exception:
            pass

    def isalive(self) -> bool:
        try:
            return self._proc.isalive()
        except Exception:
            return False

    def terminate(self, force: bool = True) -> None:
        try:
            self._proc.terminate(force)
        except Exception:
            pass

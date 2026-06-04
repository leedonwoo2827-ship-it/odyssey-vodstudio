"""Gemini CLI 연동 — 구글 계정 OAuth 로그인(키 없음, 무료 티어)로 Gemini 사용.

google-gemini/gemini-cli(`gemini`)를 subprocess로 래핑한다. nlm/mp4maker와
동일한 패턴. 각 사용자가 자기 PC에서 `gemini` 를 한 번 실행해 구글 계정으로
로그인하면(API 키 불필요), 이 서비스가 비대화식 모드로 호출해 텍스트를 생성한다.

전제: Node.js + `npm i -g @google/gemini-cli` 설치, `gemini` 최초 1회 로그인.
무료 OAuth 티어는 개인 Google 계정 기준 쿼터 제한이 있고, 회사(Workspace)
계정은 Code Assist 라이선스가 필요할 수 있다(조직 정책에 따름).
"""

import asyncio
import logging
import os
import shutil
from typing import List, Optional

logger = logging.getLogger(__name__)


class GeminiCliError(RuntimeError):
    pass


def gemini_executable() -> Optional[str]:
    """Resolve the `gemini` CLI (global npm bin). Returns None if not installed."""
    override = os.getenv("GEMINI_BIN")
    if override and shutil.which(override):
        return shutil.which(override)
    # npm on Windows installs gemini.cmd / gemini.ps1; shutil.which finds them via PATHEXT.
    for name in ("gemini", "gemini.cmd", "gemini.exe"):
        found = shutil.which(name)
        if found:
            return found
    return None


def available() -> bool:
    return gemini_executable() is not None


async def _run(args: List[str], *, stdin_text: Optional[str] = None, timeout: float = 180.0):
    exe = gemini_executable()
    if not exe:
        raise GeminiCliError(
            "`gemini` CLI가 설치되어 있지 않습니다. Node.js 설치 후 "
            "`npm i -g @google/gemini-cli` 그리고 `gemini` 로 구글 로그인하세요."
        )
    # Windows: npm installs `gemini.cmd`, and CreateProcess can't exec a .cmd/.bat
    # directly — must go through cmd.exe. Prompt is passed via stdin (below), so
    # there are no multi-line-arg quoting issues through cmd /c.
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        cmd = ["cmd", "/c", exe, *args]
    else:
        cmd = [exe, *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(input=stdin_text.encode("utf-8") if stdin_text is not None else None),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise GeminiCliError(f"gemini 호출 시간 초과 ({timeout:.0f}s)")
    return proc.returncode, (out or b"").decode("utf-8", "replace"), (err or b"").decode("utf-8", "replace")


async def version() -> Optional[str]:
    if not available():
        return None
    try:
        rc, out, err = await _run(["--version"], timeout=30.0)
        return (out or err).strip().splitlines()[0] if (out or err).strip() else "installed"
    except GeminiCliError:
        return None


async def generate(prompt: str, *, model: Optional[str] = None, timeout: float = 240.0) -> str:
    """비대화식 1회 호출. `gemini -p "<prompt>"` → 모델 응답 텍스트 반환.

    인증 만료/미로그인 등은 stderr로 드러나며 GeminiCliError로 변환한다.
    """
    # Pass the (multi-line) prompt via stdin rather than -p, so it survives the
    # Windows `cmd /c` hop without quoting/newline breakage. Gemini CLI runs
    # once on piped stdin and exits (non-interactive).
    args: List[str] = []
    if model:
        args += ["-m", model]
    rc, out, err = await _run(args, stdin_text=prompt, timeout=timeout)
    text = (out or "").strip()
    if rc != 0 or not text:
        blob = (out + "\n" + err).lower()
        if any(k in blob for k in ("login", "auth", "oauth", "unauthor", "sign in", "credential")):
            raise GeminiCliError(
                "Gemini 인증이 필요합니다. 터미널에서 `gemini` 를 실행해 구글 계정으로 "
                "로그인(키 없음)한 뒤 다시 시도하세요."
            )
        raise GeminiCliError(f"gemini 호출 실패 (rc={rc}): {(err or out)[:300]}")
    return text

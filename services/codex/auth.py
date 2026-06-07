"""codex(OpenAI Codex CLI) 인증 상태 헬퍼.

Codex 는 "Sign in with ChatGPT"(OAuth) 로 로그인하면 API 키 없이 ChatGPT 구독
할당량으로 GPT 모델을 쓴다. 로그인은 `codex login`, 로그아웃 `codex logout`,
상태는 `codex login status`(로그인 시 exit 0). 자격증명은 ~/.codex/auth.json
(또는 OS 키링) 에 저장된다.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
AUTH_PATH = os.environ.get("CODEX_AUTH_PATH", str(Path.home() / ".codex" / "auth.json"))

_FALLBACK_CODEX_PATHS = [
    os.path.expanduser("~/.local/bin/codex"),
    os.path.expandvars(r"%APPDATA%\npm\codex.cmd"),
    os.path.expandvars(r"%APPDATA%\npm\codex"),
]


def codex_path() -> Optional[str]:
    if os.path.sep in CODEX_BIN or (os.path.altsep and os.path.altsep in CODEX_BIN):
        return CODEX_BIN if os.path.isfile(CODEX_BIN) else None
    found = shutil.which(CODEX_BIN)
    if found:
        return found
    for p in _FALLBACK_CODEX_PATHS:
        if p and os.path.isfile(p):
            return p
    return None


def is_installed() -> bool:
    return codex_path() is not None


def is_authenticated() -> bool:
    """`codex login status` exit 0 이면 로그인된 것으로 판정(키링 저장 대비 가장 신뢰)."""
    path = codex_path()
    if not path:
        return False
    try:
        p = subprocess.run([path, "login", "status"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20)
        return p.returncode == 0
    except Exception:
        return False


def _decode_jwt_email(token: str) -> Optional[str]:
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
        for k in ("email", "https://api.openai.com/profile", "preferred_username"):
            v = payload.get(k)
            if isinstance(v, str) and "@" in v:
                return v.strip().lower()
            if isinstance(v, dict) and isinstance(v.get("email"), str):
                return v["email"].strip().lower()
    except Exception:
        pass
    return None


def _deep_find(obj, keys) -> Optional[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and isinstance(v, str) and v.strip():
                return v.strip()
        for v in obj.values():
            r = _deep_find(v, keys)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _deep_find(v, keys)
            if r:
                return r
    return None


def get_account_email() -> Optional[str]:
    """~/.codex/auth.json 에서 ChatGPT 계정 email best-effort 추출(키링 저장 시 None 가능)."""
    try:
        if os.path.isfile(AUTH_PATH):
            with open(AUTH_PATH, "r", encoding="utf-8") as f:
                obj = json.load(f)
            email = _deep_find(obj, {"email", "account_email", "preferred_username"})
            if email and "@" in email:
                return email.strip().lower()
            idt = _deep_find(obj, {"id_token", "idToken", "access_token", "accessToken"})
            if idt and idt.count(".") >= 2:
                e = _decode_jwt_email(idt)
                if e:
                    return e
    except Exception:
        pass
    return None


def logout() -> bool:
    """`codex logout` 로 로그아웃(계정 전환 준비). 성공/시도하면 True."""
    path = codex_path()
    if not path:
        return False
    try:
        subprocess.run([path, "logout"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=20)
        return True
    except Exception:
        # 폴백: 자격증명 파일 삭제
        try:
            if os.path.isfile(AUTH_PATH):
                os.remove(AUTH_PATH)
                return True
        except Exception:
            pass
        return False


def login_terminal_cmd() -> List[str]:
    """로그인용 터미널 명령(브라우저 OAuth)."""
    return [CODEX_BIN, "login"]


def status() -> dict:
    installed = is_installed()
    authed = is_authenticated() if installed else False
    return {
        "provider": "codex",
        "label": "OpenAI (ChatGPT)",
        "installed": installed,
        "authenticated": authed,
        "email": (get_account_email() if authed else None),
    }

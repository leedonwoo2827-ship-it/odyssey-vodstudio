"""LLM 공급자 디스패처 — Gemini(agy) ↔ OpenAI(codex) 토글.

활성 공급자를 data/llm_provider.json 에 저장하고, 상위(studio/llm.py, 라우트)에서
공급자와 무관하게 active_* 로 접근한다. 각 공급자(services/agy, services/codex)는
동일 인터페이스(client.chat / auth.is_installed·is_authenticated·get_account_email·logout
/ runner.list_models·get_model·set_model)를 제공한다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# codex(OpenAI/ChatGPT) 우선 — 만료되는 gemini CLI 대체. 순서·기본값 모두 codex 먼저.
VALID = ("codex", "agy")
LABELS = {"codex": "OpenAI (ChatGPT)", "agy": "Gemini (Google)"}

_PROVIDER_FILE = Path(__file__).resolve().parents[1] / "data" / "llm_provider.json"
_DEFAULT = (os.environ.get("LLM_PROVIDER", "codex").strip() or "codex")


def get_provider() -> str:
    try:
        if _PROVIDER_FILE.is_file():
            p = json.loads(_PROVIDER_FILE.read_text(encoding="utf-8")).get("provider", "")
            if p in VALID:
                return p
    except Exception:
        pass
    return _DEFAULT if _DEFAULT in VALID else "codex"


def set_provider(name: str) -> bool:
    name = (name or "").strip()
    if name not in VALID:
        return False
    try:
        _PROVIDER_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PROVIDER_FILE.write_text(json.dumps({"provider": name}, ensure_ascii=False),
                                  encoding="utf-8")
        return True
    except Exception:
        return False


def _modules(name: str):
    """(runner, auth, login_cmd) 반환. 지연 import."""
    if name == "codex":
        from services.codex import runner as r
        from services.codex import auth as a
        return r, a, a.login_terminal_cmd()
    from services.agy import runner as r
    from services.agy import auth as a
    return r, a, [os.environ.get("AGY_BIN", "agy")]  # agy 로그인 = `agy` 실행


def active_client():
    r, _a, _c = _modules(get_provider())
    return r.client


def active_runner():
    r, _a, _c = _modules(get_provider())
    return r


def active_auth():
    _r, a, _c = _modules(get_provider())
    return a


def login_cmd(name: Optional[str] = None) -> List[str]:
    """로그인 명령. PATH가 stale 해도 동작하도록 실행 파일 풀경로로 해석한다."""
    name = name or get_provider()
    if name == "codex":
        from services.codex import auth as a
        return [a.codex_path() or os.environ.get("CODEX_BIN", "codex"), "login"]
    from services.agy import runner as r
    return [r.agy_path() or os.environ.get("AGY_BIN", "agy")]


def _status_one(name: str) -> Dict[str, Any]:
    try:
        _r, a, _c = _modules(name)
        installed = a.is_installed()
        authed = a.is_authenticated() if installed else False
        email = a.get_account_email() if authed else None
    except Exception:
        installed = authed = False
        email = None
    return {"provider": name, "label": LABELS.get(name, name),
            "installed": installed, "authenticated": authed, "email": email}


def status_all() -> Dict[str, Any]:
    """모든 공급자 상태 + 현재 활성 공급자."""
    cur = get_provider()
    return {
        "provider": cur,
        "label": LABELS.get(cur, cur),
        "providers": {n: _status_one(n) for n in VALID},
        "active": _status_one(cur),
    }


# 활성 공급자 기준 모델 목록/선택
def list_models() -> List[str]:
    return active_runner().list_models()


def get_model() -> str:
    return active_runner().get_model()


def set_model(name: str) -> None:
    active_runner().set_model(name)

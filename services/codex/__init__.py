"""OpenAI Codex CLI(`codex`) 연동 패키지.

API 키 없이 `codex login`(ChatGPT OAuth) 으로 로그인해, `codex exec` 로 GPT 모델을 호출한다.
services/agy 와 동일한 인터페이스(client.chat / auth.* / list_models·get_model·set_model)를 제공.
"""
from .runner import (
    CodexClient,
    CodexError,
    CodexNotInstalled,
    CodexNotAuthenticated,
    CodexQuotaExceeded,
    client,
)

__all__ = [
    "CodexClient",
    "CodexError",
    "CodexNotInstalled",
    "CodexNotAuthenticated",
    "CodexQuotaExceeded",
    "client",
]

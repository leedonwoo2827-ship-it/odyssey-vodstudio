"""LLM 공급자 공통 예외.

agy/codex 등 공급자별 예외가 이 베이스를 상속해, studio/llm.py 등 상위에서
공급자와 무관하게 '미설치/미로그인/할당량' 을 잡을 수 있게 한다.
"""
from __future__ import annotations


class LLMError(RuntimeError):
    """LLM 호출 일반 오류(공급자 공통)."""


class LLMNotInstalled(LLMError):
    """공급자 CLI(agy/codex)가 설치되어 있지 않음."""


class LLMNotAuthenticated(LLMError):
    """공급자에 로그인되어 있지 않음."""


class LLMQuotaExceeded(LLMError):
    """계정 할당량(quota) 초과."""

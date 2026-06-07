"""Antigravity CLI(agy) 연동 패키지.

회사 LiteLLM 프록시(ubion_llm) 대체. API 키 없이 구글 공식 CLI `agy` 를
subprocess 로 호출해, agy 에 로그인된 Google 계정 할당량으로 Gemini 모델을 사용한다.

- runner.AgyClient : LLM 호출 (ubion_llm.UbionClient 의 .chat 시그니처를 그대로 재현한 드롭인)
- auth            : agy 인증 상태/로그인 email 헬퍼
"""
from .runner import (
    AgyClient,
    AgyError,
    AgyNotInstalled,
    AgyNotAuthenticated,
    AgyQuotaExceeded,
    client,
)

__all__ = [
    "AgyClient",
    "AgyError",
    "AgyNotInstalled",
    "AgyNotAuthenticated",
    "AgyQuotaExceeded",
    "client",
]

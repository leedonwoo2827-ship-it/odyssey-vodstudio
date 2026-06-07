"""CodexClient — OpenAI Codex CLI(`codex`)를 비대화식으로 호출하는 LLM 래퍼.

설계 의도:
- API 키를 쓰지 않는다. 인증/할당량은 `codex` 가 담당(사용자가 `codex login` 1회, ChatGPT OAuth).
- agy 의 AgyClient 와 동일한 `.chat(model, messages, max_tokens) -> resp(.text)` 시그니처(드롭인).
- `codex exec` 는 최종 답을 stdout 으로 내보내므로 PTY 불필요(일반 subprocess 캡처).
- 코딩 에이전트이므로 `-s read-only -a never` + 가드 프롬프트로 파일변경/도구사용을 막는다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.llm_errors import (
    LLMError, LLMNotInstalled, LLMNotAuthenticated, LLMQuotaExceeded,
)
from .auth import CODEX_BIN, codex_path

CODEX_TIMEOUT = int(os.environ.get("CODEX_EXEC_TIMEOUT", "300"))
CODEX_MODEL_ENV = os.environ.get("CODEX_MODEL", "").strip()
_MODEL_FILE = Path(__file__).resolve().parents[2] / "data" / "codex_model.json"

_GUARD_SYSTEM = (
    "You are a text and document generation assistant. "
    "Do NOT run shell commands, do NOT read or write files, do NOT use any tools, "
    "and do NOT perform any coding or repository actions. "
    "Respond with ONLY the requested content as your message — nothing else."
)


class CodexError(LLMError):
    pass


class CodexNotInstalled(LLMNotInstalled):
    pass


class CodexNotAuthenticated(LLMNotAuthenticated):
    pass


class CodexQuotaExceeded(LLMQuotaExceeded):
    pass


class CodexResult:
    __slots__ = ("text", "raw")

    def __init__(self, text: str, raw: Any = None):
        self.text = text
        self.raw = raw


# ── 모델 선택 저장 ───────────────────────────────────────────────────────────
def get_model() -> str:
    try:
        if _MODEL_FILE.is_file():
            m = json.loads(_MODEL_FILE.read_text(encoding="utf-8")).get("model", "")
            if isinstance(m, str) and m.strip():
                return m.strip()
    except Exception:
        pass
    return CODEX_MODEL_ENV


def set_model(name: str) -> None:
    try:
        _MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MODEL_FILE.write_text(json.dumps({"model": (name or "").strip()}, ensure_ascii=False),
                               encoding="utf-8")
    except Exception:
        pass


_MODELS_CACHE: Optional[List[str]] = None
_MODEL_ID_RE = re.compile(r"^(gpt|o\d|chatgpt|codex)[A-Za-z0-9.\-_]*$", re.IGNORECASE)


def _parse_models(out: str) -> List[str]:
    """`codex debug models` 출력 파싱. 최신 codex 는 JSON({"models":[{slug,..}]})을 낸다.
    실패하면 줄 단위 휴리스틱으로 폴백. visibility='hide' 인 내부 모델은 제외."""
    out = (out or "").strip()
    # 1) JSON 우선 (출력 앞뒤 잡음 대비해 첫 '{' ~ 마지막 '}' 만 잘라 시도)
    if "{" in out and '"models"' in out:
        try:
            blob = out[out.index("{"): out.rindex("}") + 1]
            data = json.loads(blob)
            ids: List[str] = []
            for m in data.get("models", []):
                if not isinstance(m, dict):
                    continue
                if str(m.get("visibility", "")).lower() == "hide":
                    continue
                slug = (m.get("slug") or m.get("id") or m.get("name") or "").strip()
                if slug and slug not in ids:
                    ids.append(slug)
            if ids:
                return ids
        except Exception:
            pass
    # 2) 폴백: 줄 단위에서 모델 ID 형태 토큰 추출
    models: List[str] = []
    for line in out.splitlines():
        s = line.strip().strip("-*•> \t")
        tok = s.split()[0] if s.split() else ""
        if _MODEL_ID_RE.match(tok) and tok not in models:
            models.append(tok)
    return models


def list_models(force: bool = False) -> List[str]:
    """`codex debug models` 출력에서 모델 ID 추출(캐시). 실패 시 빈 목록."""
    global _MODELS_CACHE
    if _MODELS_CACHE is not None and not force:
        return _MODELS_CACHE
    path = codex_path()
    if not path:
        return []
    models: List[str] = []
    try:
        p = subprocess.run([path, "debug", "models"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=45)
        models = _parse_models((p.stdout or "") + "\n" + (p.stderr or ""))
    except Exception:
        pass
    if models:
        _MODELS_CACHE = models
    return models


def _compose_prompt(messages: List[Dict[str, str]]) -> str:
    parts: List[str] = [_GUARD_SYSTEM]
    for m in messages:
        role = (m.get("role") or "user").lower()
        content = m.get("content") or ""
        if not content:
            continue
        if role == "system":
            parts.append(f"[지침]\n{content}")
        elif role == "assistant":
            parts.append(f"[이전 답변]\n{content}")
        else:
            parts.append(f"[요청]\n{content}")
    return "\n\n".join(parts)


def _classify_error(stdout: str, stderr: str, rc: int) -> LLMError:
    blob = f"{stdout}\n{stderr}".lower()
    if any(k in blob for k in ("not logged in", "please run", "codex login", "unauthorized",
                               "401", "authenticate", "sign in")):
        return CodexNotAuthenticated(
            "codex 에 ChatGPT 로그인이 필요합니다. 터미널에서 `codex login` 을 실행해 로그인하세요."
        )
    if any(k in blob for k in ("quota", "rate limit", "429", "too many requests",
                               "usage limit", "exceeded")):
        return CodexQuotaExceeded(
            "ChatGPT 계정 사용 한도를 초과했습니다. 잠시 후 다시 시도하거나 상위 구독(Plus/Pro)을 사용하세요."
        )
    snippet = (stderr or stdout or "").strip()[:300]
    return CodexError(f"codex 호출 실패(exit={rc}): {snippet}")


class CodexClient:
    def __init__(self, bin_path: Optional[str] = None, default_model: Optional[str] = None,
                 timeout: Optional[int] = None):
        self.bin = bin_path or CODEX_BIN
        self.default_model = default_model
        self.timeout = timeout or CODEX_TIMEOUT

    def chat(self, model: Optional[str], messages: List[Dict[str, str]],
             max_tokens: int = 4000) -> CodexResult:
        prompt = _compose_prompt(messages)
        text = self._run(prompt, model)
        return CodexResult(text)

    def quick(self, prompt: str, model: Optional[str] = None) -> str:
        return self._run(_compose_prompt([{"role": "user", "content": prompt}]), model)

    def _run(self, prompt: str, model: Optional[str]) -> str:
        path = codex_path() if self.bin == CODEX_BIN else (
            self.bin if os.path.isfile(self.bin) else shutil.which(self.bin))
        if not path:
            raise CodexNotInstalled(
                "OpenAI Codex CLI(`codex`)가 설치되어 있지 않습니다. "
                "docs/openai-codex/install.md 를 참고해 설치 후 `codex login` 으로 로그인하세요."
            )
        sel = (model or "").strip() or get_model()
        model_opt = (["-m", sel] if sel else [])
        out_path = None
        # 프롬프트는 argv 가 아니라 stdin 으로 전달한다('exec -'). codex 가 codex.cmd 래퍼로
        # 실행되면 Windows cmd 명령줄 한계(8KB)에 걸려 "command line is too long" 이 나기 때문.
        # 이 codex 버전의 exec 에는 -a(approval) 옵션이 없다 → 읽기전용 샌드박스(-s read-only)만 사용.
        cmd = [path, "exec", "-", "-s", "read-only", *model_opt]
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=self.timeout + 30)
        except FileNotFoundError as e:
            raise CodexNotInstalled(f"codex 실행 실패: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise CodexError(f"codex 응답 시간 초과({self.timeout}s).") from e
        finally:
            pass

        text = (proc.stdout or "").strip()
        if not text and out_path:
            try:
                with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read().strip()
            except Exception:
                pass
        if out_path:
            try:
                os.remove(out_path)
            except Exception:
                pass

        if proc.returncode != 0 and not text:
            raise _classify_error(proc.stdout or "", proc.stderr or "", proc.returncode)
        if not text:
            raise _classify_error(proc.stdout or "", proc.stderr or "", proc.returncode)
        return text


client = CodexClient()

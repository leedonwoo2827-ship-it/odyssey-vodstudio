"""AgyClient — 구글 공식 Antigravity CLI(`agy`) 를 subprocess 로 호출하는 LLM 래퍼.

설계 의도(중요):
- API 키를 쓰지 않는다. 인증/할당량은 전적으로 `agy` 가 담당한다(사용자가 `agy` 에 1회
  Google 로그인). 우리 앱은 비대화식 호출(`agy --print ...`)의 결과(평문)만 받는다.
- 회사 LiteLLM 프록시(ubion_llm.UbionClient)의 `.chat(model, messages, max_tokens)` 시그니처를
  그대로 재현하여, services/studio/llm.py 의 호출부를 거의 손대지 않고 교체할 수 있게 한다.

agy 는 본래 "코딩 에이전트" 이므로, 문서/텍스트 생성 용도로 쓸 때는 파일 수정·도구 실행을
하지 않고 요청한 내용만 출력하도록 가드 프롬프트를 앞에 붙인다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ANSI 이스케이프(색상/커서 등) 제거용
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[@-Z\\-_]")


def _strip_ansi(s: str) -> str:
    s = _ANSI_RE.sub("", s or "")
    return s.replace("\r\n", "\n").replace("\r", "\n")

# ── 설정(.env 로 덮어쓰기 가능) ──────────────────────────────────────────────
AGY_BIN = os.environ.get("AGY_BIN", "agy")
# agy --print 단일 호출 타임아웃(초).
AGY_TIMEOUT = int(os.environ.get("AGY_PRINT_TIMEOUT", "300"))
DEFAULT_MODEL = os.environ.get("STUDIO_DEFAULT_MODEL", "gemini-3-pro")
# agy 에 --model 로 넘길 모델명(환경변수 기본값). 비우면 화면에서 고른 값을 사용.
AGY_MODEL = os.environ.get("AGY_MODEL", "").strip()

# 화면에서 고른 모델을 저장하는 파일. agy `models` 가 출력하는 이름을 그대로 저장한다
# (예: "Gemini 3.1 Pro (High)"). 빈 값이면 agy 기본 모델 사용.
_MODEL_FILE = Path(__file__).resolve().parents[2] / "data" / "agy_model.json"


def get_model() -> str:
    """현재 사용할 모델명. 우선순위: 화면 저장값 > 환경변수 AGY_MODEL > '' (agy 기본)."""
    try:
        if _MODEL_FILE.is_file():
            m = json.loads(_MODEL_FILE.read_text(encoding="utf-8")).get("model", "")
            if isinstance(m, str) and m.strip():
                return m.strip()
    except Exception:
        pass
    return AGY_MODEL


def set_model(name: str) -> None:
    """화면에서 고른 모델 저장(빈 값이면 agy 기본 모델)."""
    try:
        _MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MODEL_FILE.write_text(
            json.dumps({"model": (name or "").strip()}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass

# agy 가 PATH 에 없을 때 확인할 기본 설치 위치(uvicorn 프로세스 PATH 누락 대비).
# 실제 설치 위치는 %LOCALAPPDATA%\agy\bin\agy.exe (Antigravity 본체와 별도).
_FALLBACK_AGY_PATHS = [
    os.path.expandvars(r"%LOCALAPPDATA%\agy\bin\agy.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\agy\bin\agy.cmd"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\agy\bin\agy.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Antigravity\agy.exe"),
    os.path.expanduser("~/.local/bin/agy"),
    os.path.expanduser("~/.agy/bin/agy"),
]

# 문서 생성기로 쓰기 위한 가드(코딩 에이전트의 도구사용/파일조작 억제).
_GUARD_SYSTEM = (
    "You are a text and document generation assistant. "
    "Do NOT run shell commands, do NOT read or write files, do NOT use any tools, "
    "and do NOT perform any coding or repository actions. "
    "Respond with ONLY the requested content as your message — nothing else."
)


from services.llm_errors import (
    LLMError as _LLMError, LLMNotInstalled as _LLMNotInstalled,
    LLMNotAuthenticated as _LLMNotAuthenticated, LLMQuotaExceeded as _LLMQuotaExceeded,
)


class AgyError(_LLMError):
    """agy 호출 일반 오류."""


class AgyNotInstalled(_LLMNotInstalled, AgyError):
    """`agy` 실행 파일을 찾을 수 없음."""


class AgyNotAuthenticated(_LLMNotAuthenticated, AgyError):
    """agy 에 Google 로그인이 안 되어 있음."""


class AgyQuotaExceeded(_LLMQuotaExceeded, AgyError):
    """Google 계정 할당량(quota) 초과."""


class AgyResult:
    """ubion_llm 의 응답 객체와 호환되도록 `.text` 만 제공."""

    __slots__ = ("text", "raw")

    def __init__(self, text: str, raw: Any = None):
        self.text = text
        self.raw = raw


def agy_path() -> Optional[str]:
    """PATH 또는 AGY_BIN 으로 agy 실행 파일 경로를 찾는다. 없으면 None."""
    # 절대경로를 직접 지정한 경우
    if os.path.sep in AGY_BIN or (os.path.altsep and os.path.altsep in AGY_BIN):
        return AGY_BIN if os.path.isfile(AGY_BIN) else None
    found = shutil.which(AGY_BIN)
    if found:
        return found
    # PATH 에 없으면 알려진 설치 위치 확인(uvicorn 프로세스 PATH 누락 대비)
    for p in _FALLBACK_AGY_PATHS:
        if p and os.path.isfile(p):
            return p
    return None


def is_installed() -> bool:
    return agy_path() is not None


def _compose_prompt(messages: List[Dict[str, str]]) -> str:
    """OpenAI 형식 messages 를 agy --print 용 단일 프롬프트 문자열로 합성."""
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
        else:  # user
            parts.append(f"[요청]\n{content}")
    return "\n\n".join(parts)


def _classify_error(stdout: str, stderr: str, returncode: int) -> AgyError:
    blob = f"{stdout}\n{stderr}".lower()
    if any(k in blob for k in ("not authenticated", "not logged in", "login required",
                               "please log in", "unauthenticated", "sign in", "auth")):
        # 인증 관련 키워드가 보이면 로그인 문제로 안내(오탐 허용 — 메시지가 친절함)
        if "auth" in blob or "login" in blob or "sign in" in blob:
            return AgyNotAuthenticated(
                "agy 에 Google 로그인이 필요합니다. 터미널에서 `agy` 를 한 번 실행해 "
                "Google 계정으로 로그인한 뒤 다시 시도하세요."
            )
    if any(k in blob for k in ("quota", "resource_exhausted", "rate limit",
                               "429", "too many requests", "exceeded")):
        return AgyQuotaExceeded(
            "Google 계정의 사용 할당량(quota)을 초과했습니다. 잠시 후 다시 시도하거나, "
            "더 큰 할당량의 Google AI Pro/Ultra 계정으로 `agy` 에 로그인하세요."
        )
    snippet = (stderr or stdout or "").strip()[:300]
    return AgyError(f"agy 호출 실패(exit={returncode}): {snippet}")


def _extract_text(stdout: str) -> str:
    """agy --print stdout(평문, 드물게 JSON)에서 모델 응답 텍스트를 추출.

    agy --print 는 평문을 출력한다. 혹시 JSON 이면 여러 후보 키를 관용적으로 탐색하고,
    아니면 stdout 평문을 그대로 사용한다.
    """
    raw = (stdout or "").strip()
    if not raw:
        return ""
    # 1) 전체가 JSON 인 경우
    try:
        obj = json.loads(raw)
        return _text_from_obj(obj) or raw
    except Exception:
        pass
    # 2) 마지막 줄이 JSON(JSONL) 인 경우 — 뒤에서부터 탐색
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line or line[0] not in "{[":
            continue
        try:
            obj = json.loads(line)
            t = _text_from_obj(obj)
            if t:
                return t
        except Exception:
            continue
    # 3) JSON 이 아니면 plain text 로 간주
    return raw


def _text_from_obj(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        for key in ("response", "output", "text", "result", "content", "message"):
            v = obj.get(key)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, dict):
                inner = _text_from_obj(v)
                if inner:
                    return inner
    return ""


def _pty_capture(argv: List[str], timeout: int = 120, idle_break: int = 30) -> Optional[str]:
    """agy 를 PTY 로 실행해 콘솔 출력을 캡처(ANSI 제거). PTY 백엔드 없으면 None.

    agy 는 응답을 stdout 파이프가 아니라 '콘솔'에 직접 쓰므로 일반 subprocess 로는
    빈 문자열만 잡힌다 → PTY(가상 콘솔)로 실행해야 캡처된다.
    """
    try:
        from .pty_terminal import PtyProcess, backend_available
    except Exception:
        return None
    if not backend_available() or PtyProcess is None:
        return None
    try:
        proc = PtyProcess.spawn(argv, dimensions=(60, 220))
    except Exception:
        return None
    chunks: List[str] = []
    deadline = time.monotonic() + timeout
    last_data = time.monotonic()
    try:
        while True:
            if time.monotonic() > deadline:
                break
            try:
                data = proc.read(65536)
            except EOFError:
                break
            except Exception:
                break
            if data:
                chunks.append(data)
                last_data = time.monotonic()
            else:
                if not proc.isalive():
                    break
                if chunks and (time.monotonic() - last_data) > idle_break:
                    break
                time.sleep(0.05)
    finally:
        try:
            if proc.isalive():
                proc.terminate(force=True)
        except Exception:
            pass
    return _strip_ansi("".join(chunks))


_MODELS_CACHE: Optional[List[str]] = None

# 'Gemini 3.1 Pro (High)' 같은 줄만 매칭(공급자 접두 + 영숫자/공백/()/.-/ 만 허용)
_MODEL_LINE_RE = re.compile(r"^(Gemini|Claude|GPT|gemini|claude|gpt)[A-Za-z0-9 .\-()/]*$")


def _parse_models(out: str) -> List[str]:
    models: List[str] = []
    for line in (out or "").splitlines():
        s = _strip_ansi(line).strip()
        # 앞쪽 선택 마커(> ● * - 숫자. 등) 제거
        s = re.sub(r"^[>\-\*●•‣→\s\d.\)]+", "", s).strip()
        if _MODEL_LINE_RE.match(s) and s not in models:
            models.append(s)
    return models


def list_models(force: bool = False) -> List[str]:
    """`agy models` 출력에서 모델 이름 목록 추출(캐시). 실패 시 폴백 목록."""
    global _MODELS_CACHE
    if _MODELS_CACHE is not None and not force:
        return _MODELS_CACHE
    path = agy_path()
    if not path:
        return list(_FALLBACK_MODELS)
    out = _pty_capture([path, "models"], timeout=30, idle_break=6) or ""
    models = _parse_models(out)
    if not models:
        try:
            p = subprocess.run([path, "models"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
            models = _parse_models((p.stdout or "") + "\n" + (p.stderr or ""))
        except Exception:
            pass
    if not models:
        models = list(_FALLBACK_MODELS)  # 파싱 실패 → 확인된 목록 사용
    _MODELS_CACHE = models
    return models


class AgyClient:
    """ubion_llm.UbionClient 드롭인 대체.

    사용:
        client = AgyClient()
        resp = client.chat("gemini-3-pro", messages, max_tokens=4000)
        text = resp.text
    """

    def __init__(self, bin_path: Optional[str] = None,
                 default_model: Optional[str] = None,
                 timeout: Optional[int] = None):
        self.bin = bin_path or AGY_BIN
        self.default_model = default_model or DEFAULT_MODEL
        self.timeout = timeout or AGY_TIMEOUT

    def chat(self, model: Optional[str], messages: List[Dict[str, str]],
             max_tokens: int = 4000) -> AgyResult:
        # max_tokens 는 agy 가 자체 제어하므로 인터페이스 호환용으로만 받는다.
        prompt = _compose_prompt(messages)
        text = self._run(prompt, model or self.default_model)
        return AgyResult(text)

    # 단순 텍스트 1회 호출(헬스체크 등)
    def quick(self, prompt: str, model: Optional[str] = None) -> str:
        return self._run(_compose_prompt([{"role": "user", "content": prompt}]),
                         model or self.default_model)

    def _candidate_cmds(self, path: str, prompt: str) -> List[List[str]]:
        """agy 버전/플래그 지원 차이에 견고하도록 여러 변형을 우선순위대로 시도.

        agy 의 모델 ID / 플래그가 버전마다 다를 수 있어, 옵션이 많은 명령부터 시도하고
        실패하면 점점 단순한 명령으로 폴백한다. --model 은 AGY_MODEL 이 설정된 경우만 넘긴다
        (미설정 시 agy 기본 모델 사용 — 가장 호환성 높음).
        """
        # NOTE: agy(>=1.0.5)에는 --output-format 옵션이 없다. --print 는 평문으로 응답한다.
        # 유효 옵션: --print/-p, --model, --dangerously-skip-permissions, --print-timeout.
        sel = get_model()
        model_opt = (["--model", sel] if sel else [])
        variants: List[List[str]] = []
        # 1) skip-permissions + (model) — 비대화식에서 권한 프롬프트로 멈추지 않게
        variants.append([path, "--print", prompt, "--dangerously-skip-permissions", *model_opt])
        # 2) (model)
        variants.append([path, "--print", prompt, *model_opt])
        # 3) 최소 형태
        variants.append([path, "--print", prompt])
        # 4) -p 별칭
        variants.append([path, "-p", prompt])
        # 중복 제거(순서 유지)
        seen = set()
        uniq = []
        for v in variants:
            key = tuple(v)
            if key not in seen:
                seen.add(key)
                uniq.append(v)
        return uniq

    def _run_via_pty(self, argv: List[str]) -> Optional[str]:
        return _pty_capture(argv, timeout=self.timeout, idle_break=30)

    def _run(self, prompt: str, model: str) -> str:
        path = agy_path() if self.bin == AGY_BIN else (
            self.bin if os.path.isfile(self.bin) else shutil.which(self.bin))
        if not path:
            raise AgyNotInstalled(
                "Antigravity CLI(`agy`)가 설치되어 있지 않습니다. "
                "docs/antigravity/install.md 를 참고해 설치 후 `agy` 로 Google 로그인하세요."
            )

        # 1) PTY 캡처 우선 (agy 는 콘솔에 직접 출력하므로 파이프로는 안 잡힘)
        sel = get_model()
        model_opt = (["--model", sel] if sel else [])
        argv = [path, "--print", prompt, "--dangerously-skip-permissions", *model_opt]
        out = self._run_via_pty(argv)
        if out is not None:
            text = _extract_text(out)
            if text.strip():
                return text
            err = _classify_error(out, "", 0)
            if isinstance(err, (AgyNotAuthenticated, AgyQuotaExceeded)):
                raise err
            # PTY 로 잡았는데 비었으면 아래 subprocess 폴백 시도

        # 2) subprocess 폴백 (PTY 백엔드 없음/POSIX 등)
        last_err: Optional[Exception] = None
        for cmd in self._candidate_cmds(path, prompt):
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout + 30,
                )
            except FileNotFoundError as e:
                raise AgyNotInstalled(f"agy 실행 실패: {e}") from e
            except subprocess.TimeoutExpired as e:
                last_err = AgyError(f"agy 응답 시간 초과({self.timeout}s). 모델/프롬프트를 줄여보세요.")
                continue

            if proc.returncode != 0:
                # 인증/할당량 문제면 즉시 그 에러로 종료(폴백해도 동일하게 실패)
                err = _classify_error(proc.stdout or "", proc.stderr or "", proc.returncode)
                if isinstance(err, (AgyNotAuthenticated, AgyQuotaExceeded)):
                    raise err
                last_err = err  # 플래그 미지원 등 → 다음 변형 시도
                continue

            text = _extract_text(proc.stdout or "")
            if text.strip():
                return text
            last_err = _classify_error(proc.stdout or "", proc.stderr or "", proc.returncode)

        raise last_err or AgyError("agy 호출이 모든 방식에서 실패했습니다.")


# 모듈 전역 싱글톤(가벼우므로 import 시 생성해도 무방 — 실제 호출 전엔 agy 를 건드리지 않음)
client = AgyClient()

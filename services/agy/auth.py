"""agy(Antigravity CLI) 인증 상태 헬퍼.

agy 가 Google OAuth 를 전담하고 토큰을 `~/.antigravity/oauth_creds.json` 에 저장한다.
앱은 이 파일에서 로그인된 Google email 을 읽어 앱 신원/세션의 근거로 삼는다.
(앱 자체 OAuth 클라이언트를 만들지 않으므로 추가 등록/미검증 경고가 없다.)
"""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path
from typing import Optional

from .runner import is_installed  # re-export 편의

# agy 인증정보 파일(있으면 최우선). 환경변수로 명시 지정 가능.
CREDS_PATH = os.environ.get("AGY_CREDS_PATH", "").strip()

# ── Windows 자격증명 관리자 ───────────────────────────────────────────────
# agy(1.0.5)는 토큰을 파일이 아니라 Windows 자격증명 관리자에 저장한다.
#   cmdkey: LegacyGeneric:target=gemini:antigravity  (사용자: antigravity)
# 환경변수 AGY_CRED_TARGET 로 대상 이름을 덮어쓸 수 있다.
_WIN = sys.platform == "win32"
WIN_CRED_TARGET = os.environ.get("AGY_CRED_TARGET", "").strip()
_WIN_CRED_KEYWORDS = ("antigravity", "gemini", "cloudcode")


def _win_cred_targets() -> list[str]:
    """agy 관련 Windows 자격증명 대상 이름 목록."""
    if not _WIN:
        return []
    if WIN_CRED_TARGET:
        return [WIN_CRED_TARGET]
    targets: list[str] = []
    try:
        import win32cred
        for c in win32cred.CredEnumerate(None, 0):
            t = (c.get("TargetName") or "")
            if any(s in t.lower() for s in _WIN_CRED_KEYWORDS):
                targets.append(t)
    except Exception:
        pass
    if "gemini:antigravity" not in targets:
        targets.append("gemini:antigravity")  # cmdkey 로 확인된 기본값
    return targets


def _win_cred_blob(target: str):
    try:
        import win32cred
        c = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC)
        return c.get("CredentialBlob")
    except Exception:
        return None


def _win_cred_entries() -> list[tuple[str, bytes]]:
    """존재하는 (대상이름, blob) 목록."""
    out = []
    for t in _win_cred_targets():
        blob = _win_cred_blob(t)
        if blob is not None:
            out.append((t, blob))
    return out


def _blob_to_obj(blob) -> Optional[dict]:
    """자격증명 blob(bytes) → JSON dict (utf-8/utf-16 관용 디코드)."""
    if not blob:
        return None
    raw = bytes(blob) if not isinstance(blob, (bytes, bytearray)) else blob
    for enc in ("utf-8", "utf-16-le", "utf-16"):
        try:
            s = raw.decode(enc, errors="ignore").strip().strip("\x00").strip()
            if s and s[0] in "{[":
                return json.loads(s)
        except Exception:
            continue
    return None

# agy 1.0.5 의 자격증명 저장 위치가 확정 공개되지 않아, 가능한 디렉터리를 모두 훑는다.
def _agy_dirs() -> list[Path]:
    dirs = [Path.home() / ".antigravity", Path.home() / ".config" / "antigravity"]
    for ev in ("LOCALAPPDATA", "APPDATA"):
        base = os.environ.get(ev)
        if base:
            dirs.append(Path(base) / "Antigravity")
            dirs.append(Path(base) / "antigravity")
    # 중복 제거
    seen, out = set(), []
    for d in dirs:
        s = str(d).lower()
        if s not in seen:
            seen.add(s)
            out.append(d)
    return out


def _cred_json_files() -> list[Path]:
    """agy 설정 디렉터리들에서 자격증명 후보 .json 파일 목록(존재하는 것만)."""
    files: list[Path] = []
    if CREDS_PATH and os.path.isfile(CREDS_PATH):
        files.append(Path(CREDS_PATH))
    for d in _agy_dirs():
        try:
            if not d.is_dir():
                continue
            for p in sorted(d.rglob("*.json")):
                if p.is_file() and p not in files:
                    files.append(p)
                if len(files) > 60:  # 안전 상한
                    break
        except Exception:
            continue
    return files


def creds_exist() -> bool:
    return bool(_win_cred_entries()) or bool(_cred_json_files())


def _deep_find(obj, keys) -> Optional[str]:
    """중첩 dict/list 를 훑어 keys 중 하나에 해당하는 첫 문자열 값을 반환."""
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


def _decode_jwt_email(id_token: str) -> Optional[str]:
    """id_token(JWT) payload 에서 email 추출(서명 검증 없이 디코드만)."""
    try:
        payload_b64 = id_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # base64url padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
        email = payload.get("email")
        return email.strip().lower() if isinstance(email, str) and email else None
    except Exception:
        return None


def _userinfo_email(access_token: str) -> Optional[str]:
    """access_token 으로 Google userinfo 조회(표준 라이브러리만 사용)."""
    try:
        import urllib.request

        req = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        email = data.get("email")
        return email.strip().lower() if isinstance(email, str) and email else None
    except Exception:
        return None


def _email_from_obj(obj) -> Optional[str]:
    """파싱된 자격증명 객체에서 email 추출: 평문 email → id_token(JWT) → access_token(userinfo)."""
    email = _deep_find(obj, {"email", "user_email", "account_email", "account"})
    if email and "@" in email:
        return email.strip().lower()
    idt = _deep_find(obj, {"id_token", "idToken"})
    if idt and idt.count(".") >= 2:
        e = _decode_jwt_email(idt)
        if e:
            return e
    at = _deep_find(obj, {"access_token", "accessToken"})
    if at:
        e = _userinfo_email(at)
        if e:
            return e
    return None


def _looks_like_creds(obj) -> bool:
    """토큰/이메일을 담고 있어 '자격증명 파일'로 볼 수 있는지."""
    return bool(_deep_find(obj, {"email", "id_token", "idToken", "access_token",
                                 "accessToken", "refresh_token", "refreshToken"}))


def get_account_email() -> Optional[str]:
    """agy 에 로그인된 Google 계정 email. 못 읽으면 None(로그인 여부는 is_authenticated 로).

    1) Windows 자격증명 관리자(gemini:antigravity)의 blob 에서 추출 시도
    2) 실패 시 파일 기반(.json) 스캔
    """
    # 1) Windows 자격증명
    for _t, blob in _win_cred_entries():
        obj = _blob_to_obj(blob)
        if obj:
            email = _email_from_obj(obj)
            if email:
                return email
    # 2) 파일 기반(POSIX 등)
    for p in _cred_json_files():
        try:
            with open(p, "r", encoding="utf-8") as f:
                obj = json.load(f)
        except Exception:
            continue
        email = _email_from_obj(obj)
        if email:
            return email
    return None


def is_authenticated() -> bool:
    """agy 설치 + 로그인 여부. (Windows 자격증명 존재 = 로그인으로 간주; email 파싱 실패해도 OK)"""
    if not is_installed():
        return False
    if _win_cred_entries():
        return True
    return get_account_email() is not None


def logout() -> bool:
    """저장된 agy 자격증명을 지워 로그아웃(계정 전환 준비). 하나라도 지웠으면 True.

    agy(1.0.5)에는 `logout` 하위명령이 없어, 저장된 OAuth 자격증명을 지운다.
    Windows 는 자격증명 관리자 항목을, 그 외는 .json 파일을 삭제한다.
    이후 `agy` 를 다시 실행하면 새 Google 계정으로 로그인할 수 있다.
    """
    removed = False
    # 1) Windows 자격증명 삭제
    if _WIN:
        try:
            import win32cred
            for t in _win_cred_targets():
                try:
                    win32cred.CredDelete(t, win32cred.CRED_TYPE_GENERIC)
                    removed = True
                except Exception:
                    pass
        except Exception:
            pass
    # 2) 파일 기반 삭제
    for p in _cred_json_files():
        try:
            with open(p, "r", encoding="utf-8") as f:
                obj = json.load(f)
        except Exception:
            continue
        if _looks_like_creds(obj):
            try:
                os.remove(p)
                removed = True
            except Exception:
                pass
    return removed


def status() -> dict:
    """UI/진단용 상태 요약."""
    installed = is_installed()
    authed = is_authenticated() if installed else False
    email = get_account_email() if authed else None
    return {
        "installed": installed,
        "authenticated": authed,
        "email": email,
        "win_cred_targets": [t for t, _ in _win_cred_entries()],
        "creds_files": [str(p) for p in _cred_json_files()][:10],
    }

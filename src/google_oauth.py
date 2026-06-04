"""Google OAuth2 (OIDC) login helper — Authorization Code + PKCE flow.

Built on httpx (already a core dependency), so it needs no Authlib and no
Starlette SessionMiddleware. routes/auth_routes.py uses this to let users
"Sign in with Google".

Scope: this module only handles *app login identity*. It is intentionally
separate from the NotebookLM integration, which authenticates the same Google
account through a browser-cookie session (`nlm login`) rather than an OAuth
token — different mechanism, different purpose.

Configuration (env / .env):
  GOOGLE_OAUTH_CLIENT_ID       OAuth 2.0 client ID (required to enable)
  GOOGLE_OAUTH_CLIENT_SECRET   OAuth 2.0 client secret (required to enable)
  OAUTH_REDIRECT_BASE          public base URL, default http://127.0.0.1:7000
                               (callback = <base>/api/auth/google/callback)
  GOOGLE_OAUTH_ALLOWED_EMAILS  optional comma list of allowed emails
  GOOGLE_OAUTH_ALLOWED_DOMAIN  optional single allowed domain (e.g. ubion.co.kr)
"""

import os
import time
import base64
import hashlib
import logging
import secrets
import threading
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

# Short-lived server-side store for the per-login state + PKCE verifier.
# Keyed by the opaque `state` value we hand to Google; popped on callback.
_STATE_TTL = 600  # 10 minutes
_state_store: Dict[str, Dict[str, Any]] = {}
_state_lock = threading.Lock()


def _client_id() -> str:
    return (os.getenv("GOOGLE_OAUTH_CLIENT_ID") or "").strip()


def _client_secret() -> str:
    return (os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()


def is_enabled() -> bool:
    """True when both client credentials are present so the UI can show the button."""
    return bool(_client_id() and _client_secret())


def redirect_uri() -> str:
    base = (os.getenv("OAUTH_REDIRECT_BASE") or "http://127.0.0.1:7000").rstrip("/")
    return f"{base}/api/auth/google/callback"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _prune_locked() -> None:
    now = time.time()
    for key in [k for k, v in _state_store.items() if v.get("expiry", 0) < now]:
        _state_store.pop(key, None)


def begin_login(remember: bool = True) -> str:
    """Create a state + PKCE pair and return the Google authorize URL to redirect to."""
    verifier = _b64url(secrets.token_bytes(48))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(24)
    with _state_lock:
        _prune_locked()
        _state_store[state] = {
            "verifier": verifier,
            "expiry": time.time() + _STATE_TTL,
            "remember": bool(remember),
        }
    params = {
        "client_id": _client_id(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


def _pop_state(state: str) -> Optional[Dict[str, Any]]:
    with _state_lock:
        _prune_locked()
        return _state_store.pop(state, None)


async def complete_login(code: str, state: str) -> Dict[str, Any]:
    """Exchange `code` for a token and return verified userinfo.

    Returns {"email", "name", "remember", "raw"}. Raises ValueError on any
    failure (unknown/expired state, token error, unverified email).
    """
    st = _pop_state(state)
    if not st:
        raise ValueError("Invalid or expired login state")
    data = {
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "code": code,
        "code_verifier": st["verifier"],
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri(),
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        tr = await client.post(GOOGLE_TOKEN_URL, data=data)
        if tr.status_code != 200:
            raise ValueError(f"Token exchange failed (HTTP {tr.status_code}): {tr.text[:200]}")
        access_token = (tr.json() or {}).get("access_token")
        if not access_token:
            raise ValueError("Token response had no access_token")
        ur = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if ur.status_code != 200:
            raise ValueError(f"Userinfo failed (HTTP {ur.status_code}): {ur.text[:200]}")
        info = ur.json() or {}
    email = (info.get("email") or "").strip().lower()
    if not email:
        raise ValueError("Google account returned no email")
    # email_verified comes back as a real bool from the OIDC userinfo endpoint.
    if not info.get("email_verified", False):
        raise ValueError("Google email is not verified")
    return {
        "email": email,
        "name": info.get("name") or email,
        "remember": st.get("remember", True),
        "raw": info,
    }


def _allowlist():
    emails = [
        e.strip().lower()
        for e in (os.getenv("GOOGLE_OAUTH_ALLOWED_EMAILS") or "").split(",")
        if e.strip()
    ]
    domain = (os.getenv("GOOGLE_OAUTH_ALLOWED_DOMAIN") or "").strip().lower().lstrip("@")
    return emails, domain


def allowlist_configured() -> bool:
    emails, domain = _allowlist()
    return bool(emails or domain)


def email_matches_allowlist(email: str) -> bool:
    """True only when an allowlist/domain IS configured and `email` matches it."""
    email = (email or "").strip().lower()
    emails, domain = _allowlist()
    if emails and email in emails:
        return True
    if domain and email.endswith("@" + domain):
        return True
    return False

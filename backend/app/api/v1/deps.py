"""Request authentication.

Two credentials are accepted, for two genuinely different callers:

  * an `x-api-key` header — scripts, CI, curl, and local development
    where the frontend runs on its own Vite origin;
  * a signed session cookie — the browser.

The cookie exists so the API key never reaches JavaScript. A key baked
into a frontend bundle with Vite's `VITE_` prefix is readable by every
visitor: it is in the served JS, in the network tab, and in any archive
of the page. That is acceptable for nothing, and it is the same secret
the deployment uses elsewhere.

The cookie is HMAC-signed with the API key rather than being the key, so
a stolen cookie grants a session and not a credential — it cannot be
replayed against another environment, and it cannot be turned back into
the key.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

from fastapi import Cookie, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.base import get_db

SESSION_COOKIE = "ba_session"
SESSION_TTL_SECONDS = 12 * 60 * 60


def _sign(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def issue_session(response: Response, settings: Settings, secure: bool) -> None:
    """Attaches a signed, httpOnly session cookie to the response."""
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
    payload = str(expires_at)
    response.set_cookie(
        SESSION_COOKIE,
        f"{payload}.{_sign(payload, settings.api_key)}",
        max_age=SESSION_TTL_SECONDS,
        httponly=True,      # unreadable from JavaScript, so XSS cannot exfiltrate it
        samesite="lax",     # not sent on cross-site POSTs
        secure=secure,      # HTTPS only in deployment; off for local http
        path="/",
    )


def _session_is_valid(cookie: str | None, secret: str) -> bool:
    if not cookie or "." not in cookie:
        return False
    payload, _, signature = cookie.rpartition(".")
    # compare_digest, not ==, so a wrong signature cannot be discovered
    # one byte at a time by timing the response.
    if not hmac.compare_digest(signature, _sign(payload, secret)):
        return False
    try:
        return int(payload) > time.time()
    except ValueError:
        return False


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
    ba_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if x_api_key is not None:
        if hmac.compare_digest(x_api_key, settings.api_key):
            return
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid API key")

    if _session_is_valid(ba_session, settings.api_key):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing credentials: send an x-api-key header, or load the app to obtain a session",
    )


DbDep = Depends(get_db)
AuthDep = Depends(require_api_key)

"""Simple single-user auth: HMAC-signed cookie, credentials from env.

Not multi-user or role-based — just a gate in front of the tailnet-private app.
The cookie token is derived from the credentials + SECRET_KEY, so changing either
invalidates existing sessions.
"""
from __future__ import annotations

import hashlib
import hmac
import os

AUTH_USER = os.environ.get("AUTH_USERNAME", "admin")
AUTH_PASS = os.environ.get("AUTH_PASSWORD", "change-me")
SECRET = os.environ.get("SECRET_KEY", "change-me-to-a-long-random-string").encode()

COOKIE_NAME = "expenses_auth"
MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _token() -> str:
    msg = f"{AUTH_USER}:{AUTH_PASS}".encode()
    return hmac.new(SECRET, msg, hashlib.sha256).hexdigest()


TOKEN = _token()


def verify_credentials(username: str | None, password: str | None) -> bool:
    ok_user = hmac.compare_digest(username or "", AUTH_USER)
    ok_pass = hmac.compare_digest(password or "", AUTH_PASS)
    return ok_user and ok_pass


def is_authed(request) -> bool:
    return hmac.compare_digest(request.cookies.get(COOKIE_NAME, ""), TOKEN)


def set_cookie(response) -> None:
    response.set_cookie(
        COOKIE_NAME, TOKEN,
        max_age=MAX_AGE, httponly=True, samesite="lax", path="/",
    )


def clear_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")

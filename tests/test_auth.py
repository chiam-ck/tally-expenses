"""Auth-gate tests — focused on the /api/* API-key bypass surface.

Two layers:
  * Unit tests for ``auth.is_api_authed`` — stdlib only, run anywhere (this is the
    security-critical logic: which presented secret does/doesn't open the gate).
  * One integration test that drives the real middleware via FastAPI's TestClient;
    auto-skips where fastapi isn't installed (runs in CI / on the app host).

Run the whole suite with pytest, or just the unit layer with plain python:
    pytest tests/test_auth.py
    python tests/test_auth.py        # no pytest/fastapi needed
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the app importable and pin auth config BEFORE importing app modules
# (auth reads API_KEY / DATABASE_URL at import time).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "postgresql://x:y@localhost/z")
KEY = "tally_sk_testkey"
os.environ["API_KEY"] = KEY

from app import auth  # noqa: E402


class FakeRequest:
    """Minimal stand-in: the gate only ever reads .headers and .cookies."""

    def __init__(self, headers: dict | None = None):
        # Starlette headers are case-insensitive; lower-case keys here to match.
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.cookies = {}


# ── unit: is_api_authed ──────────────────────────────────────────────────────

def test_bearer_token_authorizes():
    assert auth.is_api_authed(FakeRequest({"Authorization": f"Bearer {KEY}"})) is True


def test_x_api_key_header_authorizes():
    assert auth.is_api_authed(FakeRequest({"X-API-Key": KEY})) is True


def test_bearer_scheme_is_case_insensitive():
    assert auth.is_api_authed(FakeRequest({"Authorization": f"bearer {KEY}"})) is True


def test_wrong_key_rejected():
    assert auth.is_api_authed(FakeRequest({"Authorization": "Bearer nope"})) is False
    assert auth.is_api_authed(FakeRequest({"X-API-Key": "nope"})) is False


def test_no_credentials_rejected():
    assert auth.is_api_authed(FakeRequest({})) is False


def test_unset_api_key_never_authorizes():
    """The critical case: a blank server-side key must not be bypassable with a
    blank/absent presented secret."""
    saved = auth.API_KEY
    auth.API_KEY = ""
    try:
        assert auth.is_api_authed(FakeRequest({})) is False
        assert auth.is_api_authed(FakeRequest({"Authorization": "Bearer "})) is False
        assert auth.is_api_authed(FakeRequest({"X-API-Key": ""})) is False
    finally:
        auth.API_KEY = saved


# ── integration: the middleware actually gates /api/* ────────────────────────

def test_api_middleware_gate():
    import pytest

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from app import queries
    import app.main as main

    # /api/reference is the lightest write-free endpoint; stub its DB reads so
    # the test needs no live pool. TestClient without `with` skips the lifespan,
    # so no connection pool / scheduler is started.
    orig_accounts, orig_categories = queries.accounts, queries.categories
    queries.accounts = lambda *a, **k: []
    queries.categories = lambda *a, **k: []
    try:
        client = TestClient(main.app)
        assert client.get("/api/reference").status_code == 401
        assert client.get("/api/reference", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get("/api/reference", headers={"Authorization": f"Bearer {KEY}"}).status_code == 200
        assert client.get("/api/reference", headers={"X-API-Key": KEY}).status_code == 200
    finally:
        queries.accounts, queries.categories = orig_accounts, orig_categories


if __name__ == "__main__":
    # Zero-dependency runner for the unit layer; skips anything needing extras.
    passed = failed = skipped = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        try:
            fn()
            passed += 1
            print(f"PASS {name}")
        except ImportError as exc:
            skipped += 1
            print(f"SKIP {name} ({exc})")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    sys.exit(1 if failed else 0)

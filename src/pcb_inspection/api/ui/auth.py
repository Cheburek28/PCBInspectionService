"""Password login for the web console: a signed, expiring cookie. No user accounts.

The signing key is derived from the password, so changing ``PCBIS_UI_PASSWORD`` logs everybody out.
"""

from __future__ import annotations

import hashlib
import hmac
import time

COOKIE = "pcbis_ui"


def _key(password: str) -> bytes:
    return hashlib.sha256(b"pcbis-ui-session:" + password.encode()).digest()


def check_password(expected: str, given: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(expected.encode()).digest(), hashlib.sha256(given.encode()).digest()
    )


def make_token(password: str, hours: int, now: float | None = None) -> str:
    expires = int((now or time.time()) + hours * 3600)
    sig = hmac.new(_key(password), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def token_valid(password: str, token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    expires_raw, sig = token.split(".", 1)
    if not expires_raw.isdigit() or int(expires_raw) < (now or time.time()):
        return False
    expected = hmac.new(_key(password), expires_raw.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)

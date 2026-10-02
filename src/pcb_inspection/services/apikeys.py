"""API keys: ``pcbis_<prefix>_<secret>``. Only a SHA-256 of the secret is stored."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime

from sqlalchemy import select

from pcb_inspection.db.models import ApiKey
from pcb_inspection.domain.errors import NotFound, Unauthorized
from pcb_inspection.services.context import ServiceContext

KEY_PREFIX = "pcbis"


def _hash(prefix: str, secret: str) -> str:
    return hashlib.sha256(f"{prefix}:{secret}".encode()).hexdigest()


def create_api_key(ctx: ServiceContext, station: str) -> tuple[ApiKey, str]:
    """Returns the stored row and the raw key. The raw key is shown once and never stored."""
    prefix = secrets.token_hex(6)
    secret = secrets.token_urlsafe(32)
    with ctx.db.session() as s:
        key = ApiKey(station=station, prefix=prefix, key_hash=_hash(prefix, secret))
        s.add(key)
        s.flush()
    return key, f"{KEY_PREFIX}_{prefix}_{secret}"


def authenticate(ctx: ServiceContext, raw: str | None) -> ApiKey:
    if not raw:
        raise Unauthorized("send 'Authorization: Bearer <api key>'")
    parts = raw.split("_", 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        raise Unauthorized("malformed API key")
    _, prefix, secret = parts
    with ctx.db.session() as s:
        key = s.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
    if (
        key is None
        or key.revoked_at is not None
        or not hmac.compare_digest(key.key_hash, _hash(prefix, secret))
    ):
        raise Unauthorized("invalid or revoked API key")
    return key


def list_api_keys(ctx: ServiceContext) -> list[ApiKey]:
    with ctx.db.session() as s:
        return list(s.scalars(select(ApiKey).order_by(ApiKey.created_at)))


def revoke_api_key(ctx: ServiceContext, prefix: str) -> ApiKey:
    with ctx.db.session() as s:
        key = s.scalar(select(ApiKey).where(ApiKey.prefix == prefix))
        if key is None:
            raise NotFound(f"API key with prefix {prefix!r} not found")
        key.revoked_at = datetime.now(UTC)
    return key

"""Refresh token issuance, rotation, and family-based reuse detection.

Implements the OAuth 2.0 refresh token rotation pattern with reuse
detection (IETF draft `oauth-security-topics`, Auth0/Okta defaults).

Each refresh chain shares a family_id. On rotation we mark the consumed
row's used_at and insert a new row with parent_id pointing back at it.
If a refresh request arrives for a row whose used_at is already set,
the token was either replayed or leaked — we revoke the entire family
and refuse the request, forcing a fresh interactive login.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import asyncpg

logger = logging.getLogger(__name__)

REFRESH_TOKEN_LIFETIME_DAYS = 7
_TOKEN_BYTES = 32


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


async def issue_initial_refresh_token(
    conn: asyncpg.Connection,
    *,
    user_id: str,
    tenant_id: Optional[str] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> tuple[str, UUID, UUID]:
    """Issue the first refresh token in a new family. Called at login/signup.

    Returns ``(raw_token, token_id, family_id)``. The raw token is returned
    to the caller exactly once and must be placed in the httpOnly refresh
    cookie. Only the sha256 hash is persisted.
    """
    raw = _generate_token()
    token_hash = _hash_token(raw)
    now = _now_utc()
    expires_at = now + timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS)

    row = await conn.fetchrow(
        """
        INSERT INTO refresh_tokens
            (family_id, user_id, tenant_id, token_hash, parent_id,
             issued_at, expires_at, ip, user_agent)
        VALUES (uuid_generate_v4(), $1, $2, $3, NULL, $4, $5, $6, $7)
        RETURNING id, family_id
        """,
        user_id,
        tenant_id,
        token_hash,
        now,
        expires_at,
        ip,
        user_agent,
    )
    return raw, row["id"], row["family_id"]


async def rotate_refresh_token(
    conn: asyncpg.Connection,
    *,
    presented_token: str,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Optional[tuple[str, dict]]:
    """Validate and rotate a refresh token.

    Returns ``(new_raw_token, {"user_id": ..., "tenant_id": ..., "family_id": ...})``
    on success, or ``None`` on any failure (expired, revoked, unknown, or
    reuse detected — family revoked as a side effect of reuse).
    """
    presented_hash = _hash_token(presented_token)
    now = _now_utc()

    row = await conn.fetchrow(
        """
        SELECT id, family_id, user_id, tenant_id, expires_at, used_at, revoked_at
        FROM refresh_tokens
        WHERE token_hash = $1
        """,
        presented_hash,
    )
    if row is None:
        logger.warning("refresh.unknown_token_presented")
        return None

    # Reuse detection runs first: a token whose used_at is set has already
    # been rotated. Seeing it again means either a replay or a leaked
    # successor chain — revoke the whole family.
    if row["used_at"] is not None:
        await conn.execute(
            """
            UPDATE refresh_tokens
            SET revoked_at = $2, revoked_reason = 'reuse_detected'
            WHERE family_id = $1 AND revoked_at IS NULL
            """,
            row["family_id"],
            now,
        )
        logger.error(
            "refresh.reuse_detected family=%s user=%s — family revoked",
            row["family_id"],
            row["user_id"],
        )
        return None

    if row["revoked_at"] is not None:
        logger.warning(
            "refresh.revoked_token_presented family=%s user=%s",
            row["family_id"],
            row["user_id"],
        )
        return None

    if row["expires_at"] <= now:
        await conn.execute(
            """
            UPDATE refresh_tokens
            SET revoked_at = $2, revoked_reason = 'expired'
            WHERE id = $1 AND revoked_at IS NULL
            """,
            row["id"],
            now,
        )
        return None

    # Happy path: mark consumed and issue successor. We set used_at only —
    # leaving revoked_at NULL so the reuse-detection branch above can
    # distinguish "rotated" from "revoked-for-other-reason".
    new_raw = _generate_token()
    new_hash = _hash_token(new_raw)
    expires_at = now + timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS)

    async with conn.transaction():
        await conn.execute(
            """
            UPDATE refresh_tokens
            SET used_at = $2
            WHERE id = $1
            """,
            row["id"],
            now,
        )
        await conn.execute(
            """
            INSERT INTO refresh_tokens
                (family_id, user_id, tenant_id, token_hash, parent_id,
                 issued_at, expires_at, ip, user_agent)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            row["family_id"],
            row["user_id"],
            row["tenant_id"],
            new_hash,
            row["id"],
            now,
            expires_at,
            ip,
            user_agent,
        )

    return new_raw, {
        "user_id": str(row["user_id"]),
        "tenant_id": str(row["tenant_id"]) if row["tenant_id"] else None,
        "family_id": str(row["family_id"]),
    }


async def revoke_family_by_token(
    conn: asyncpg.Connection,
    *,
    presented_token: str,
    reason: str = "logout",
) -> None:
    """Revoke every refresh token sharing a family with the presented one.

    Idempotent — silently no-ops if the token is unknown or the family is
    already revoked. Used by /auth/logout.
    """
    presented_hash = _hash_token(presented_token)
    now = _now_utc()
    await conn.execute(
        """
        UPDATE refresh_tokens
        SET revoked_at = $2, revoked_reason = $3
        WHERE family_id = (
            SELECT family_id FROM refresh_tokens WHERE token_hash = $1
        )
        AND revoked_at IS NULL
        """,
        presented_hash,
        now,
        reason,
    )


async def revoke_all_user_refresh_tokens(
    conn: asyncpg.Connection,
    user_id: str,
    *,
    reason: str,
    exclude_family_id: Optional[str] = None,
) -> int:
    """
    Revoke every active refresh token (across all families) for *user_id*.

    Used by password change + MFA disable to force every device to
    re-authenticate. The current session's access JWT keeps working
    until it expires (15 min) — by then the user has been informed of
    the password change in the UI and will sign in again normally.

    Setting ``exclude_family_id`` keeps a single family alive. Pass the
    current request's family_id when you want the current device to
    continue without interruption.

    Returns the number of token rows revoked. Idempotent.
    """
    now = _now_utc()
    if exclude_family_id:
        result = await conn.execute(
            """
            UPDATE refresh_tokens
            SET revoked_at = $2, revoked_reason = $3
            WHERE user_id = $1
              AND family_id <> $4
              AND revoked_at IS NULL
            """,
            user_id, now, reason, exclude_family_id,
        )
    else:
        result = await conn.execute(
            """
            UPDATE refresh_tokens
            SET revoked_at = $2, revoked_reason = $3
            WHERE user_id = $1
              AND revoked_at IS NULL
            """,
            user_id, now, reason,
        )
    try:
        return int(result.split(" ")[-1]) if result else 0
    except Exception:
        return 0

"""Pending team invites (QA F1-15 / D4-2), kept in Redis.

Owner decisions, 11 Nov 2026: an email that already has an account is
refused, an invite lasts 7 days, and any role up to tenant admin may be
invited. Nothing is written to Postgres until the invite is accepted, so a
withdrawn or expired invite leaves no half-made account that would block
the email later.

Only a SHA-256 of the token is stored; the token itself travels only in the
email link. Each invite has two keys: the token key the accept page looks
up, which expires with the invite, and an entry in its workspace's list,
which the Team page reads. Deleting the token key is the one atomic step
that withdraws or spends an invite.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Optional

INVITE_LIFETIME = timedelta(days=7)
_TOKEN_KEY = "team-invite:token:{}"
_WORKSPACE_KEY = "team-invite:tenant:{}"
_RECIPIENT_KEY = "team-invite:recipient:{}"


@dataclass(frozen=True)
class Invite:
    id: str
    tenant_id: str
    email: str
    role_name: str
    invited_by: str
    created_at: str
    expires_at: str
    token_hash: str

    def expired(self, now: datetime) -> bool:
        return datetime.fromisoformat(self.expires_at) <= now


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _text(value) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else value


def _load(raw) -> Optional[Invite]:
    if not raw:
        return None
    try:
        return Invite(**json.loads(_text(raw)))
    except (TypeError, ValueError):
        return None


async def pending(redis, tenant_id: str, *, now: datetime) -> list[Invite]:
    """The workspace's open invites, newest first; expired ones are dropped."""
    key = _WORKSPACE_KEY.format(tenant_id)
    invites, stale = [], []
    for field, raw in (await redis.hgetall(key)).items():
        invite = _load(raw)
        if invite is None or invite.tenant_id != tenant_id or invite.expired(now):
            stale.append(field)
        else:
            invites.append(invite)
    if stale:
        await redis.hdel(key, *stale)
    return sorted(invites, key=lambda invite: invite.created_at, reverse=True)


async def find(redis, tenant_id: str, invite_id: str, *, now: datetime) -> Optional[Invite]:
    return next((invite for invite in await pending(redis, tenant_id, now=now) if invite.id == invite_id), None)


async def issue(redis, *, tenant_id: str, email: str, role_name: str, invited_by: str,
                now: datetime) -> tuple[Invite, str, Optional[Invite]]:
    """Store a new invite and return it with its token.

    Inviting the same email again replaces the earlier invite, whose link
    stops working, and returns it as the third value.
    """
    previous = next((invite for invite in await pending(redis, tenant_id, now=now) if invite.email == email), None)
    if previous is not None:
        await withdraw(redis, previous)
    token = secrets.token_urlsafe(32)
    invite = Invite(
        id=str(uuid.uuid4()), tenant_id=tenant_id, email=email, role_name=role_name,
        invited_by=invited_by, created_at=now.isoformat(),
        expires_at=(now + INVITE_LIFETIME).isoformat(), token_hash=token_hash(token),
    )
    payload = json.dumps(asdict(invite))
    seconds = int(INVITE_LIFETIME.total_seconds())
    await redis.set(_TOKEN_KEY.format(invite.token_hash), payload, ex=seconds)
    await redis.hset(_WORKSPACE_KEY.format(tenant_id), invite.id, payload)
    await redis.expire(_WORKSPACE_KEY.format(tenant_id), seconds)
    return invite, token, previous


async def by_token(redis, token: str, *, now: datetime) -> Optional[Invite]:
    invite = _load(await redis.get(_TOKEN_KEY.format(token_hash(token))))
    return None if invite is None or invite.expired(now) else invite


async def spend(redis, invite: Invite) -> bool:
    """Use the token once. False when it was withdrawn or used meanwhile."""
    return bool(await redis.delete(_TOKEN_KEY.format(invite.token_hash)))


async def withdraw(redis, invite: Invite) -> bool:
    """The link stops working at once and the invite leaves the list."""
    spent = await spend(redis, invite)
    await forget(redis, invite)
    return spent


async def forget(redis, invite: Invite) -> None:
    await redis.hdel(_WORKSPACE_KEY.format(invite.tenant_id), invite.id)


async def admit_workspace(redis, email: str, tenant_id: str, *, limit: int) -> bool:
    """Whether this workspace may invite this person today.

    At most ``limit`` different workspaces may invite one person in a day; a
    workspace already among them may invite again, within its own caps. The
    key holds a hash of the email, never the address itself.
    """
    key = _RECIPIENT_KEY.format(hashlib.sha256(email.encode("utf-8")).hexdigest())
    if await redis.sadd(key, tenant_id) and await redis.scard(key) > limit:
        await redis.srem(key, tenant_id)
        return False
    await redis.expire(key, 24 * 60 * 60)
    return True

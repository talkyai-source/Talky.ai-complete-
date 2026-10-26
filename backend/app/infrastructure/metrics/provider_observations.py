"""Bounded observations of real provider attempts, never active health probes.

Only allowlisted outcome metadata is retained. Redis is best effort and is
never awaited by audio/token consumers. Credential revisions are keyed HMACs,
not key fragments; observations are scoped to the credential actually used.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

TTL_SECONDS = 600
MAX_RECORDS = 128
MAX_PENDING_WRITES = 32
IO_TIMEOUT_SECONDS = 0.25
RECORDS_KEY = "talky:{provider-observations}:records:v1"
INDEX_KEY = "talky:{provider-observations}:index:v1"
_pending: set[asyncio.Task] = set()
_COMPONENTS = frozenset({"stt", "llm", "tts", "realtime"})
_OUTCOMES = frozenset({"completed", "recognized", "failed", "cancelled", "incomplete", "connected"})
_ERRORS = frozenset({"authentication", "rate_limit", "timeout", "transport", "provider", "request", "none"})
_SAFE_NAME = re.compile(r"[a-z][a-z0-9_-]{0,39}\Z")
_REFERENCE = re.compile(r"[0-9a-f]{32}\Z")

# Both keys share a Redis cluster slot. Reject older deliveries, expire records
# individually and cap the complete store, including its index, in one command.
_WRITE = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000000 + tonumber(clock[2])
local stamp = tonumber(ARGV[2])
local ttl = tonumber(ARGV[4])
if stamp > now + 30000000 or stamp < now - ttl * 1000000 then return 0 end
local old = redis.call('ZSCORE', KEYS[2], ARGV[1])
if old and tonumber(old) >= stamp then return 0 end
local expired = redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now - ttl * 1000000)
for _, id in ipairs(expired) do
  redis.call('HDEL', KEYS[1], id)
  redis.call('ZREM', KEYS[2], id)
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[3])
redis.call('ZADD', KEYS[2], stamp, ARGV[1])
local excess = redis.call('ZCARD', KEYS[2]) - tonumber(ARGV[5])
if excess > 0 then
  local trimmed = redis.call('ZRANGE', KEYS[2], 0, excess - 1)
  for _, id in ipairs(trimmed) do
    redis.call('HDEL', KEYS[1], id)
    redis.call('ZREM', KEYS[2], id)
  end
end
redis.call('EXPIRE', KEYS[1], ttl)
redis.call('EXPIRE', KEYS[2], ttl)
return 1
"""


def _redis_client():
    try:
        from app.core.container import get_container
        container = get_container()
        return getattr(container, "redis", None) if container.is_initialized else None
    except Exception:
        return None


def _signing_key() -> bytes | None:
    try:
        from app.core.config import get_settings
        key = get_settings().effective_jwt_secret
        return key.encode() if isinstance(key, str) and key else None
    except Exception:
        return None


def credential_revision(provider: str, api_key: str) -> str | None:
    """Stable across workers sharing configured signing material, never logged."""
    secret = _signing_key()
    if secret is None or not isinstance(api_key, str) or not api_key:
        return None
    payload = b"provider-observation:v1\0" + provider.encode() + b"\0" + api_key.encode()
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()[:32]


def _error_category(error: BaseException) -> str:
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(error, "status", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    if status in (401, 403):
        return "authentication"
    if status == 429:
        return "rate_limit"
    if isinstance(status, int) and 400 <= status < 500:
        return "request"
    if isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower():
        return "timeout"
    if isinstance(error, (ConnectionError, OSError)):
        return "transport"
    return "provider"


@dataclass(frozen=True)
class ProviderObservation:
    reference: str
    component: str
    provider: str
    credential_revision: str
    credential_source: str
    credential_id: str | None
    tenant_id: str | None
    observed_at_us: int
    outcome: str
    error_category: str
    first_output_ms: int | None
    elapsed_ms: int
    output_seen: bool
    protocol_accepted: bool

    def to_json(self) -> str:
        from dataclasses import asdict
        return json.dumps(asdict(self), separators=(",", ":"), allow_nan=False)

    @property
    def observed_at(self) -> str:
        return datetime.fromtimestamp(self.observed_at_us / 1_000_000, timezone.utc).isoformat()


async def _write_observation(client, observation: ProviderObservation) -> None:
    try:
        await asyncio.wait_for(client.eval(
            _WRITE, 2, RECORDS_KEY, INDEX_KEY, observation.reference,
            observation.observed_at_us, observation.to_json(), TTL_SECONDS, MAX_RECORDS,
        ), IO_TIMEOUT_SECONDS)
    except Exception:
        # Do not log provider keys, exception bodies or request/response content.
        pass


def publish(observation: ProviderObservation) -> bool:
    """Bounded best-effort dispatch. No database, new queue or call dependency."""
    if len(_pending) >= MAX_PENDING_WRITES:
        return False
    client = _redis_client()
    if client is None:
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    task = loop.create_task(_write_observation(client, observation))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return True


class ProviderAttempt:
    """One actual provider attempt; finish only after protocol completion.

    first_output() is an in-memory timestamp only. The single final write is
    scheduled after completion, error or cancellation; no token/audio content
    is accepted by this interface.
    """

    def __init__(self, component: str, provider: str, api_key: str | None,
                 context: dict[str, Any] | None = None):
        self.component = component
        self.provider = provider
        self.context = context or {}
        try:
            self.revision = credential_revision(provider, api_key) if api_key else None
        except Exception:
            self.revision = None
        self.started = time.perf_counter()
        self.first_output_ms: int | None = None
        self.output_seen = False
        self.protocol_accepted = False
        self.completed = False
        self.closed = False

    def accepted(self) -> None:
        self.protocol_accepted = True

    def first_output(self) -> None:
        self.output_seen = True
        if self.first_output_ms is None:
            self.first_output_ms = max(0, round((time.perf_counter() - self.started) * 1000))

    def recognized(self) -> None:
        """An actual nonempty final STT result, without user-speech duration."""
        if self.component == "stt":
            self.output_seen = True

    def complete(self) -> None:
        self.completed = True

    def finish(self, error: BaseException | None = None, *, outcome: str | None = None) -> None:
        try:
            self._finish(error, outcome=outcome)
        except Exception:
            # Telemetry failures cannot replace the provider result or error.
            pass

    def _finish(self, error: BaseException | None = None, *, outcome: str | None = None) -> None:
        if self.closed:
            return
        self.closed = True
        if (self.component not in _COMPONENTS or not _SAFE_NAME.fullmatch(self.provider)
                or self.revision is None):
            return
        if isinstance(error, (asyncio.CancelledError, GeneratorExit)):
            actual_outcome = "cancelled"
        elif error is not None:
            actual_outcome = "failed"
        else:
            actual_outcome = outcome or ("completed" if self.completed and self.output_seen else "incomplete")
        if actual_outcome not in _OUTCOMES:
            return
        source = self.context.get("source", "unattributed")
        if source not in {"tenant", "platform", "unattributed"}:
            source = "unattributed"
        # Identity is server-owned metadata, never free-form model input.
        from uuid import UUID
        def identity(value):
            try:
                return str(UUID(str(value))) if value else None
            except (ValueError, TypeError, AttributeError):
                return None
        tenant_id = identity(self.context.get("tenant_id"))
        credential_id = identity(self.context.get("credential_id")) if source == "tenant" else None
        if source == "tenant" and (tenant_id is None or credential_id is None):
            source, credential_id = "unattributed", None
        if actual_outcome in {"completed", "recognized"} and not self.output_seen:
            actual_outcome = "incomplete"
        material = json.dumps([self.component, self.provider, source, tenant_id,
                               credential_id, self.revision], separators=(",", ":"))
        observation = ProviderObservation(
            reference=hashlib.sha256(material.encode()).hexdigest()[:32],
            component=self.component, provider=self.provider,
            credential_revision=self.revision, credential_source=source,
            credential_id=credential_id, tenant_id=tenant_id,
            observed_at_us=time.time_ns() // 1000, outcome=actual_outcome,
            error_category=_error_category(error) if error is not None and actual_outcome == "failed" else "none",
            first_output_ms=self.first_output_ms,
            elapsed_ms=max(0, round((time.perf_counter() - self.started) * 1000)),
            output_seen=self.output_seen, protocol_accepted=self.protocol_accepted,
        )
        publish(observation)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.finish(exc)
        return False


def _decode(raw, reference: str, now_us: int) -> ProviderObservation:
    value = json.loads(raw)
    item = ProviderObservation(**value)
    if (item.reference != reference or not _REFERENCE.fullmatch(item.reference)
            or not _REFERENCE.fullmatch(item.credential_revision)
            or item.component not in _COMPONENTS or not _SAFE_NAME.fullmatch(item.provider)
            or item.credential_source not in {"tenant", "platform", "unattributed"}
            or item.outcome not in _OUTCOMES or item.error_category not in _ERRORS
            or type(item.observed_at_us) is not int
            or not now_us - TTL_SECONDS * 1_000_000 <= item.observed_at_us <= now_us + 30_000_000
            or type(item.output_seen) is not bool or type(item.protocol_accepted) is not bool):
        raise ValueError("Invalid provider observation")
    from uuid import UUID
    for identifier in (item.credential_id, item.tenant_id):
        if identifier is not None and str(UUID(identifier)) != identifier:
            raise ValueError("Invalid observation identity")
    for latency in (item.first_output_ms, item.elapsed_ms):
        if latency is not None and (type(latency) is not int or latency < 0 or not math.isfinite(latency)):
            raise ValueError("Invalid observation latency")
    if (item.elapsed_ms is None
            or item.first_output_ms is not None and not item.output_seen
            or item.output_seen and item.first_output_ms is None and item.component != "stt"):
        raise ValueError("Inconsistent observation")
    if (item.credential_source == "tenant" and (item.tenant_id is None or item.credential_id is None)
            or item.credential_source != "tenant" and item.credential_id is not None
            or item.outcome in {"completed", "recognized"} and not item.output_seen
            or item.outcome != "failed" and item.error_category != "none"):
        raise ValueError("Inconsistent observation outcome or scope")
    material = json.dumps([item.component, item.provider, item.credential_source, item.tenant_id,
                           item.credential_id, item.credential_revision], separators=(",", ":"))
    if hashlib.sha256(material.encode()).hexdigest()[:32] != item.reference:
        raise ValueError("Observation reference does not match its scope")
    if item.first_output_ms is not None and item.first_output_ms > item.elapsed_ms:
        raise ValueError("Output latency exceeds complete observation")
    return item


async def read_recent(client=None) -> list[ProviderObservation] | None:
    """None means unavailable/corrupt; an empty list means no recent evidence."""
    if client is None:
        client = _redis_client()
    if client is None:
        return None
    try:
        references = await asyncio.wait_for(client.zrevrange(INDEX_KEY, 0, MAX_RECORDS - 1), IO_TIMEOUT_SECONDS)
        if not references:
            return []
        raw_items = await asyncio.wait_for(client.hmget(RECORDS_KEY, references), IO_TIMEOUT_SECONDS)
        now_us = time.time_ns() // 1000
        items = []
        for reference, raw in zip(references, raw_items, strict=True):
            if raw is None:  # A concurrent expiry/trim removed this entry.
                continue
            reference = reference.decode() if isinstance(reference, bytes) else reference
            value = json.loads(raw)
            stamp = value.get("observed_at_us") if isinstance(value, dict) else None
            if type(stamp) is int and stamp < now_us - TTL_SECONDS * 1_000_000:
                continue
            items.append(_decode(raw, reference, now_us))
        return sorted(items, key=lambda item: item.observed_at_us, reverse=True)
    except Exception:
        return None

"""Redis durability inspection (T2.4).

Before this module, nothing checked whether the Redis instance
backing the dialer queue had persistence enabled. A Redis restart
wipes in-flight `DialerJob` entries silently — 100 campaign calls
just vanish and nobody notices until retention-dashboards miss the
expected volume.

What we do
----------
- At startup and on `/health`, inspect `CONFIG GET` for the two
  knobs that matter: `appendonly` (AOF) and `save` (RDB
  snapshotting).
- In production, WARN loudly when both are disabled (the "pure
  in-memory" config that ships by default with upstream Redis
  Docker images).
- Expose the result via `redis_durability_status()` so the health
  endpoint can surface it.

Not enforced at boot — a misconfigured Redis is a production issue
but not a safety-critical one like a default password. We log,
flag, and let the operator fix rather than refusing to start.
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class DurabilityStatus:
    """Snapshot of Redis persistence configuration."""
    probed: bool
    aof_enabled: bool = False
    rdb_snapshots_enabled: bool = False
    rdb_save_rules: str = ""
    warning: Optional[str] = None
    raw_error: Optional[str] = None

    def is_durable(self) -> bool:
        """True if at least one persistence mechanism is live."""
        return self.aof_enabled or self.rdb_snapshots_enabled

    def to_dict(self) -> dict:
        return asdict(self)


async def probe_redis_durability(redis_client: Any) -> DurabilityStatus:
    """Read persistence settings from the live Redis.

    Returns a populated DurabilityStatus on success. On any RPC
    failure we return an un-probed stub with `raw_error` populated —
    callers should treat 'unknown' as 'unsafe to assume durable'.
    """
    if redis_client is None:
        return DurabilityStatus(probed=False, raw_error="redis_client_is_none")

    try:
        appendonly = await redis_client.config_get("appendonly")
        save = await redis_client.config_get("save")
    except Exception as exc:
        logger.debug("redis_durability_probe_failed err=%s", exc)
        return DurabilityStatus(probed=False, raw_error=str(exc))

    # Redis returns {"appendonly": "yes" | "no"} or {"appendonly": b"yes"}.
    def _v(result: Any, key: str) -> str:
        if not result:
            return ""
        raw = result.get(key) if isinstance(result, dict) else None
        if raw is None:
            return ""
        return raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)

    aof_val = _v(appendonly, "appendonly").strip().lower()
    save_val = _v(save, "save").strip()

    status = DurabilityStatus(
        probed=True,
        aof_enabled=(aof_val == "yes"),
        # Empty string OR the "no snapshots" sentinel both mean
        # snapshotting is off. Anything else (e.g. "3600 1 300 100 60
        # 10000") is a real save rule.
        rdb_snapshots_enabled=bool(save_val) and save_val not in ("", '""'),
        rdb_save_rules=save_val,
    )
    environment = (os.getenv("ENVIRONMENT") or "development").strip().lower()
    if environment == "production" and not status.is_durable():
        status.warning = (
            "Redis has NO persistence configured (AOF off, no RDB save "
            "rules). In-flight dialer jobs will be lost on any Redis "
            "restart. Enable AOF everysec on the dialer Redis or set a "
            "`save` schedule. See backend/docs/telephony/redis-durability.md."
        )
        logger.warning("redis_durability_none_in_production — %s", status.warning)
    elif not status.is_durable():
        logger.info(
            "redis_durability_check environment=%s durable=False aof=%s rdb=%r "
            "— ok for dev; set AOF before going to prod",
            environment, status.aof_enabled, status.rdb_save_rules,
        )
    else:
        logger.info(
            "redis_durability_check environment=%s aof=%s rdb=%r",
            environment, status.aof_enabled, status.rdb_save_rules,
        )
    return status

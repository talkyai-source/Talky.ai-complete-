"""Per-tenant voice-pipeline tuning (Tier 3.9 + Tier 4-C3).

Different customers run different conversation patterns. A B2B medical
intake line wants generous pauses (a caller pulling out their insurance
card is not a turn-end); a high-volume sales call-centre wants tight EOT
cuts so the agent can keep up. Hard-coding one set of timings means every
tenant lives with the wrong rhythm.

This module is the single source of truth for those tunable values:

* ``stt_eot_threshold``       — Flux end-of-turn confidence
* ``stt_eager_eot_threshold`` — Flux speculative-LLM trigger
* ``stt_eot_timeout_ms``      — Flux silence timeout
* ``turn_0_min_confidence``   — Reject a turn-0 transcript below this
* ``turn_0_min_alpha_chars``  — Reject a turn-0 transcript shorter than this

Resolution priority (highest first, T4-C3):

1. **Per-tenant DB override** — ``tenant_ai_configs.voice_tuning JSONB``,
   wired via :meth:`VoiceTuningResolver.set_db_lookup` at app startup.
   Cache-bypassed: every async lookup hits the DB so UI edits land on
   the next call without a restart.
2. Per-tenant env override — ``TELEPHONY_TUNING_OVERRIDES_JSON`` keyed
   by tenant_id. The legacy power-user path; still works.
3. Global env default — ``TELEPHONY_TUNING_DEFAULT_JSON``.
4. Hard-coded defaults matching pre-T3.9 production values.

Bad JSON, unknown keys, and wrong types are logged and skipped — the
resolver never raises. A misconfigured DB row, env var, or code default
must not take a tenant's calls offline.

Example operator setups:

    # Self-serve UI (T4-C3):
    POST /api/v1/ai-options/config
    {"voice_tuning": {"stt_eot_timeout_ms": 1500}, ...}

    # Power-user env (T3.9):
    TELEPHONY_TUNING_DEFAULT_JSON='{"stt_eot_timeout_ms": 750}'
    TELEPHONY_TUNING_OVERRIDES_JSON='{
        "11111111-1111-1111-1111-111111111111": {
            "stt_eot_timeout_ms": 1500,
            "turn_0_min_confidence": 0.55
        }
    }'
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)


# A pluggable lookup that returns a partial voice-tuning dict for a
# given tenant_id, or ``None`` when the tenant has no per-tenant DB
# row. Wired at app startup; tests inject a mock or leave it unset to
# fall back to env-only behaviour.
DBLookup = Callable[[str], Awaitable[Optional[Dict[str, Any]]]]


@dataclass(frozen=True)
class VoiceTuning:
    """Tunable values for a single voice session.

    Frozen so a tenant's resolved tuning cannot be mutated mid-call by
    accident. The defaults below match the values
    :func:`build_telephony_session_config` was already producing for
    production telephony before this module existed; tenants without an
    override see no behaviour change.
    """

    stt_eot_threshold: float = 0.85
    stt_eager_eot_threshold: Optional[float] = 0.7
    stt_eot_timeout_ms: int = 500
    turn_0_min_confidence: float = 0.4
    turn_0_min_alpha_chars: int = 2


_DEFAULT_TUNING_DICT: Dict[str, Any] = asdict(VoiceTuning())


# The set of fields the resolver knows how to coerce. Anything outside
# this set in operator JSON is logged and ignored — protects against a
# typo silently taking effect when a future field name lands.
_FIELD_COERCERS: Dict[str, Any] = {
    "stt_eot_threshold": float,
    "stt_eager_eot_threshold": lambda v: None if v is None else float(v),
    "stt_eot_timeout_ms": int,
    "turn_0_min_confidence": float,
    "turn_0_min_alpha_chars": int,
}


class VoiceTuningResolver:
    """Resolves per-tenant voice tuning across DB, env, and code defaults.

    The env-driven layer caches its parsed JSON so the per-call lookup
    is a dict access; the DB layer (T4-C3) bypasses that cache because
    UI edits should land on the next call without a service restart.

    Thread-safe: the env cache is populated lazily under a lock the
    first time any tenant is resolved. The DB lookup callback is set
    once at app startup and read without locking afterwards.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cached_defaults: Optional[Dict[str, Any]] = None
        self._cached_overrides: Optional[Dict[str, Dict[str, Any]]] = None
        # T4-C3 — async DB lookup. ``None`` means env-only resolution
        # (the T3.9 behaviour). Set via :meth:`set_db_lookup` at app
        # startup; tests can leave it unset or wire a mock.
        self._db_lookup: Optional[DBLookup] = None

    def set_db_lookup(self, lookup: Optional[DBLookup]) -> None:
        """Wire (or clear) the per-tenant DB lookup callback.

        Called once at app startup with a function that fetches
        ``tenant_ai_configs.voice_tuning`` for a given tenant_id. Pass
        ``None`` to revert to env-only resolution — useful for tests
        that don't want a DB pool.
        """
        with self._lock:
            self._db_lookup = lookup

    def for_tenant(self, tenant_id: Optional[str]) -> VoiceTuning:
        """Return the tuning for ``tenant_id`` from env + code defaults.

        Sync path. ``None`` or unknown tenant_id falls back to the
        global default. Does NOT consult the DB lookup — synchronous
        callers (tests, browser sessions, legacy code paths) get the
        T3.9 env-only behaviour, which is the safe pre-C3 default.

        For production-grade per-tenant lookups, prefer
        :meth:`for_tenant_async` which adds the DB layer on top.
        """
        defaults, overrides = self._ensure_loaded()

        merged = dict(defaults)
        if tenant_id:
            tenant_partial = overrides.get(str(tenant_id))
            if tenant_partial:
                merged.update(tenant_partial)

        return VoiceTuning(**merged)

    async def for_tenant_async(self, tenant_id: Optional[str]) -> VoiceTuning:
        """Production resolution path: DB → env override → env default → code.

        DB results are not cached — operators editing voice tuning in
        the UI expect the change to take effect on the very next call,
        not after a service restart. The DB query is one indexed lookup
        on a small table; the round-trip is a few milliseconds.

        Falls back gracefully:

        * No DB lookup wired → env-only path (matches :meth:`for_tenant`).
        * Lookup raises → log a warning, env-only fallback. Voice
          tuning must NEVER block a call from going out.
        * Lookup returns ``None`` (no row, or the JSONB is empty) →
          env-only fallback.
        """
        defaults, overrides = self._ensure_loaded()
        merged = dict(defaults)

        # Env-driven per-tenant override (T3.9 layer) lands first so
        # the DB layer can shadow specific fields without losing the
        # operator's other env-driven settings.
        if tenant_id:
            env_partial = overrides.get(str(tenant_id))
            if env_partial:
                merged.update(env_partial)

        # DB layer (T4-C3) — only fires when both a lookup is wired
        # and a tenant_id is present. The lookup runs OUTSIDE the
        # resolver's lock so a slow DB round-trip can't stall other
        # tenant resolutions.
        lookup = self._db_lookup
        if lookup is not None and tenant_id:
            try:
                db_partial = await lookup(str(tenant_id))
            except Exception as exc:  # noqa: BLE001 — never block a call
                logger.warning(
                    "voice_tuning_db_lookup_failed tenant=%s err=%s "
                    "— falling back to env+defaults",
                    tenant_id, exc,
                )
                db_partial = None
            if db_partial:
                # Coerce the same way env values are coerced — single
                # validator across both paths means a future field
                # rename only has to happen in one place.
                coerced = self._coerce_partial(
                    db_partial, scope=f"db tenant {tenant_id}",
                )
                if coerced:
                    merged.update(coerced)

        return VoiceTuning(**merged)

    def reset_cache(self) -> None:
        """Clear cached env parses. Tests use this; production rarely
        needs it (env changes require a service restart anyway)."""
        with self._lock:
            self._cached_defaults = None
            self._cached_overrides = None

    def coerce_user_partial(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Public entry point for validating a partial tuning dict
        coming from the API or another untrusted source. Drops unknown
        keys and clamps wrong-typed values the same way env loads do —
        callers can hand the result straight to a SQL INSERT without
        worrying about malformed JSON poisoning the DB.
        """
        return self._coerce_partial(data, scope="api")

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
        with self._lock:
            if self._cached_defaults is None:
                self._cached_defaults = self._load_default_dict()
            if self._cached_overrides is None:
                self._cached_overrides = self._load_overrides_dict()
            return self._cached_defaults, self._cached_overrides

    def _load_default_dict(self) -> Dict[str, Any]:
        merged = dict(_DEFAULT_TUNING_DICT)
        # Read through TelephonySettings (T4-C5) so env access has one
        # canonical home. Local import avoids a startup-order issue:
        # TelephonySettings.from_env() may be called before this module
        # is fully imported in worker processes.
        from app.core.telephony_settings import get_telephony_settings
        raw = get_telephony_settings().voice_tuning.default_json
        if not raw or not raw.strip():
            return merged
        partial = self._parse_partial(raw, scope="default")
        merged.update(partial)
        return merged

    def _load_overrides_dict(self) -> Dict[str, Dict[str, Any]]:
        from app.core.telephony_settings import get_telephony_settings
        raw = get_telephony_settings().voice_tuning.overrides_json
        if not raw or not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "voice_tuning_overrides_parse_failed err=%s — no per-tenant overrides",
                exc,
            )
            return {}
        if not isinstance(parsed, dict):
            logger.warning(
                "voice_tuning_overrides_invalid_shape — expected JSON object keyed by tenant_id"
            )
            return {}

        result: Dict[str, Dict[str, Any]] = {}
        for tenant_id, partial in parsed.items():
            if not isinstance(partial, dict):
                logger.warning(
                    "voice_tuning_override_skipped tenant_id=%s reason=non_dict_value",
                    tenant_id,
                )
                continue
            coerced = self._coerce_partial(partial, scope=f"tenant {tenant_id}")
            if coerced:
                result[str(tenant_id)] = coerced
        return result

    def _parse_partial(self, raw: str, *, scope: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "voice_tuning_%s_parse_failed err=%s — using base defaults",
                scope, exc,
            )
            return {}
        if not isinstance(parsed, dict):
            logger.warning(
                "voice_tuning_%s_invalid_shape — expected JSON object", scope,
            )
            return {}
        return self._coerce_partial(parsed, scope=scope)

    def _coerce_partial(self, data: Dict[str, Any], *, scope: str) -> Dict[str, Any]:
        coerced: Dict[str, Any] = {}
        for key, value in data.items():
            coercer = _FIELD_COERCERS.get(key)
            if coercer is None:
                logger.warning(
                    "voice_tuning_unknown_field scope=%s name=%s — ignored", scope, key,
                )
                continue
            try:
                coerced[key] = coercer(value)
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "voice_tuning_field_skipped scope=%s name=%s value=%r err=%s",
                    scope, key, value, exc,
                )
        return coerced


# ---------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------

_resolver: Optional[VoiceTuningResolver] = None
_resolver_lock = threading.Lock()


def get_voice_tuning_resolver() -> VoiceTuningResolver:
    """Return the process-wide tuning resolver singleton."""
    global _resolver
    if _resolver is None:
        with _resolver_lock:
            if _resolver is None:
                _resolver = VoiceTuningResolver()
    return _resolver


def reset_voice_tuning_resolver() -> None:
    """Drop the singleton. Tests use this between cases that mutate env;
    production code should not call it during normal operation."""
    global _resolver
    with _resolver_lock:
        _resolver = None

"""Typed source of truth for telephony env knobs (T4-C5).

The voice agent grew a steady accumulation of env variables across Tier
1-4: Flux EOT timing, user-first call-flow knobs, mute-during-TTS,
per-tenant tuning JSON. Each was parsed inline with bespoke helpers
and slightly different falsy-value sets — operators had to read source
to know which env var existed, what defaults it had, and whether
"off" was a valid disable string.

This module gives them one place to look and one place to override.
The class:

* Reads every Tier-4 env var at startup with strict typing.
* Preserves the EXACT parsing semantics of the original inline helpers
  (truthy/falsy sets, min/max clamps, default values) — locked in
  tests so future refactors can't silently drift.
* Exposes nested dataclasses (``FluxSettings``, ``UserFirstSettings``,
  ``VoiceTuningSettings``) so consumers read typed fields, not raw
  os.getenv calls.
* Singleton-cached so re-imports during dev hot-reload don't re-parse.
* ``reset_telephony_settings()`` for tests that mutate env between cases.

Pre-Tier-4 telephony env knobs (TELEPHONY_ADAPTER, TELEPHONY_LOCAL_DEV,
TELEPHONY_PREWARM_TIMEOUT_S, etc.) are intentionally NOT migrated here.
They're stable, well-understood, and migrating them is busywork that
risks breaking working code. C5's scope is the noisy Tier-4 surface.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# --- parsing primitives ----------------------------------------------
#
# Kept as module-level functions (not methods) so they're trivially
# unit-testable and so consumers can call them directly when they need
# to parse a value that didn't quite earn its own settings field yet.

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off"})


def _norm(value: Optional[str]) -> str:
    """Lower-strip an env value. Returns '' when the var is missing."""
    return (value or "").strip().lower()


def parse_bool_env(name: str, *, default: bool) -> bool:
    """Parse a TELEPHONY_* boolean env var.

    The codebase uses two slightly different conventions:
    * Default-OFF: only explicit truthy values ({1,true,yes,on}) flip ON.
    * Default-ON: only explicit falsy values ({0,false,no,off}) flip OFF.

    This single helper preserves both by branching on ``default``. The
    historical behaviour of every Tier-4 boolean knob is locked in
    tests so changing this implementation can't silently flip a flag.
    """
    raw = _norm(os.getenv(name))
    if not raw:
        return default
    if default:
        return raw not in _FALSY
    return raw in _TRUTHY


def parse_int_env(
    name: str, *, default: int, min_: Optional[int] = None, max_: Optional[int] = None,
) -> int:
    """Parse a clamped int env var. Out-of-range or unparseable values
    log a warning and fall back to default — never raise."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "telephony_settings_parse_failed name=%s value=%r reason=int_parse "
            "— using default %d",
            name, raw, default,
        )
        return default
    if min_ is not None and value < min_:
        logger.warning(
            "telephony_settings_clamp name=%s value=%d min=%d", name, value, min_,
        )
        return default
    if max_ is not None and value > max_:
        logger.warning(
            "telephony_settings_clamp name=%s value=%d max=%d", name, value, max_,
        )
        return default
    return value


def parse_float_env(
    name: str, *, default: float, min_: Optional[float] = None, max_: Optional[float] = None,
) -> float:
    """Parse a clamped float env var. Same fall-back-to-default
    semantics as parse_int_env."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "telephony_settings_parse_failed name=%s value=%r reason=float_parse "
            "— using default %.2f",
            name, raw, default,
        )
        return default
    if min_ is not None and value < min_:
        logger.warning(
            "telephony_settings_clamp name=%s value=%.2f min=%.2f",
            name, value, min_,
        )
        return default
    if max_ is not None and value > max_:
        logger.warning(
            "telephony_settings_clamp name=%s value=%.2f max=%.2f",
            name, value, max_,
        )
        return default
    return value


def parse_optional_float_env(
    name: str, *, default: Optional[float], min_: float, max_: float,
) -> Optional[float]:
    """Parse a float env var that also accepts 'off' / 'none' / 'disabled'
    / '' as ``None``. Used by the eager-EOT threshold which is genuinely
    optional — disabling it is a meaningful production choice."""
    raw = _norm(os.getenv(name))
    if not raw:
        return default
    if raw in {"off", "none", "disabled"}:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "telephony_settings_parse_failed name=%s value=%r reason=float_parse "
            "— using default %r",
            name, raw, default,
        )
        return default
    if not (min_ <= value <= max_):
        logger.warning(
            "telephony_settings_out_of_range name=%s value=%.2f range=[%.2f,%.2f] "
            "— using default %r",
            name, value, min_, max_, default,
        )
        return default
    return value


# --- typed settings groups -------------------------------------------


@dataclass(frozen=True)
class FluxSettings:
    """Deepgram Flux end-of-turn knobs."""

    eot_timeout_ms: int = 2000
    eager_eot_threshold: Optional[float] = 0.5


@dataclass(frozen=True)
class UserFirstSettings:
    """Caller-speaks-first call-flow knobs.

    Default-on safety net (T1.3) is the production-recommended
    behaviour — flip ``fallback_enabled=False`` to opt back into the
    legacy silent-listener mode."""

    fallback_enabled: bool = True
    greet_on_pickup: bool = False
    # 8s default — under 5s the fallback fired before Flux had committed
    # StartOfTurn for soft / far-from-mic "Hello?" openers, drowning out
    # the caller's first utterance. Real callers reliably commit by ~6s
    # even with carrier latency; 8s is a safe headroom.
    open_s: float = 8.0
    reprompt_s: float = 8.0
    farewell_s: float = 6.0
    max_reprompts: int = 2


@dataclass(frozen=True)
class VoiceTuningSettings:
    """Raw JSON env strings consumed by VoiceTuningResolver. The
    resolver does its own parsing + validation; this layer just
    surfaces the strings as typed fields so the resolver doesn't
    need to read os.getenv directly."""

    default_json: str = ""
    overrides_json: str = ""


@dataclass(frozen=True)
class TelephonySettings:
    """Typed source of truth for Tier-4 telephony env knobs."""

    flux: FluxSettings = field(default_factory=FluxSettings)
    user_first: UserFirstSettings = field(default_factory=UserFirstSettings)
    voice_tuning: VoiceTuningSettings = field(default_factory=VoiceTuningSettings)
    mute_during_tts: bool = False
    first_speaker_default: str = "agent"

    @classmethod
    def from_env(cls) -> "TelephonySettings":
        """Build a settings snapshot from current env. Defaults match
        the values each subsystem was using before C5 — zero behaviour
        change for unset env."""
        first_speaker = _norm(os.getenv("TELEPHONY_FIRST_SPEAKER")) or "agent"
        if first_speaker not in {"agent", "user"}:
            logger.warning(
                "telephony_settings_invalid_first_speaker value=%r — using 'agent'",
                first_speaker,
            )
            first_speaker = "agent"

        # User-first OPEN_S has a 2.0s minimum clamp historically — sub-second
        # fallback openers race normal pickup speech and reintroduce the
        # exact first-turn delay this mode is meant to avoid. Preserved here.
        open_s = parse_float_env(
            "TELEPHONY_USER_FIRST_OPEN_S", default=8.0, min_=2.0,
        )
        # parse_float_env returns the default when value < min_, but the
        # original user_first._user_first_open_seconds() clamped to 2.0
        # rather than defaulting to 8.0. Replicate that exactly.
        raw_open = _norm(os.getenv("TELEPHONY_USER_FIRST_OPEN_S"))
        if raw_open and raw_open != "":
            try:
                v = float(raw_open)
                if v < 2.0:
                    open_s = 2.0
            except ValueError:
                pass

        return cls(
            flux=FluxSettings(
                eot_timeout_ms=parse_int_env(
                    "TELEPHONY_FLUX_EOT_TIMEOUT_MS",
                    default=2000, min_=500, max_=10000,
                ),
                eager_eot_threshold=parse_optional_float_env(
                    "TELEPHONY_FLUX_EAGER_EOT_THRESHOLD",
                    default=0.5, min_=0.3, max_=0.9,
                ),
            ),
            user_first=UserFirstSettings(
                fallback_enabled=parse_bool_env(
                    "TELEPHONY_USER_FIRST_FALLBACK_ENABLED", default=True,
                ),
                greet_on_pickup=parse_bool_env(
                    "TELEPHONY_USER_FIRST_GREET_ON_PICKUP", default=False,
                ),
                open_s=open_s,
                reprompt_s=parse_float_env(
                    "TELEPHONY_USER_FIRST_REPROMPT_S", default=8.0,
                ),
                farewell_s=parse_float_env(
                    "TELEPHONY_USER_FIRST_FAREWELL_S", default=6.0,
                ),
                max_reprompts=parse_int_env(
                    "TELEPHONY_USER_FIRST_MAX_REPROMPTS",
                    default=2, min_=0, max_=10,
                ),
            ),
            voice_tuning=VoiceTuningSettings(
                default_json=os.getenv("TELEPHONY_TUNING_DEFAULT_JSON") or "",
                overrides_json=os.getenv("TELEPHONY_TUNING_OVERRIDES_JSON") or "",
            ),
            mute_during_tts=parse_bool_env(
                "TELEPHONY_MUTE_DURING_TTS", default=False,
            ),
            first_speaker_default=first_speaker,
        )


# --- process-level singleton -----------------------------------------

_settings: Optional[TelephonySettings] = None
_settings_lock = threading.Lock()


def get_telephony_settings() -> TelephonySettings:
    """Return the process-wide TelephonySettings snapshot.

    Lazy-loaded on first call; cached for the lifetime of the process.
    Env changes after first read are NOT picked up — operators must
    restart the service. This matches every other production deploy
    pattern in the codebase and avoids surprise mid-call config drift.
    """
    global _settings
    if _settings is None:
        with _settings_lock:
            if _settings is None:
                _settings = TelephonySettings.from_env()
    return _settings


def reset_telephony_settings() -> None:
    """Drop the cached snapshot. Tests use this between cases that
    mutate env; production code should not call it during normal
    operation."""
    global _settings
    with _settings_lock:
        _settings = None

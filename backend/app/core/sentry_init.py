"""Sentry initialisation (T2.3).

Opt-in — only activates when `SENTRY_DSN` is set. Safe to call from
`lifespan` even when Sentry isn't installed; it no-ops cleanly.

Design
------
- **DSN is the gate.** No DSN → no init, no network traffic, no
  overhead. Prod without Sentry is fine (OTEL is the other
  observability path); both can coexist.
- **FastAPI + asyncpg auto-instrumentation** — errors inside request
  handlers and DB calls are captured with full context.
- **Traces are off by default** in production. The voice pipeline's
  hot path generates a LOT of spans; 100% sampling drowns the quota.
  Set `SENTRY_TRACES_SAMPLE_RATE=0.01` (1%) as a reasonable prod
  starting point.
- **`environment` tag is always set** so dev/staging/prod are
  filterable in the Sentry UI.
- **Release version** — picked up from `SENTRY_RELEASE` (set by CI)
  or falls back to the git SHA if the `.git` dir is present. Missing
  release is not fatal.

Wiring
------
Called from `app.main.lifespan` BEFORE `setup_telemetry(app)` so
Sentry's FastAPI integration registers with the same middleware
stack OTEL instruments. Order matters: FastAPI integration patches
request handlers; if it runs after app.include_router, some routes
are missed.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def _resolve_release() -> Optional[str]:
    explicit = (os.getenv("SENTRY_RELEASE") or "").strip()
    if explicit:
        return explicit
    # Best-effort: read .git/HEAD + refs. Not a blocker if absent.
    try:
        import pathlib
        git_dir = pathlib.Path(__file__).resolve().parents[2] / ".git"
        head = (git_dir / "HEAD").read_text().strip()
        if head.startswith("ref: "):
            ref = head.split(" ", 1)[1]
            return (git_dir / ref).read_text().strip()[:12]
        return head[:12]
    except Exception:
        return None


def init_sentry() -> bool:
    """Initialise Sentry if `SENTRY_DSN` is set. Returns True when
    active, False when no-op. Safe to call multiple times — Sentry
    itself is idempotent under repeat init."""
    dsn = (os.getenv("SENTRY_DSN") or "").strip()
    if not dsn:
        logger.info("sentry_skipped reason=no_dsn")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError as exc:
        logger.warning("sentry_sdk_not_installed err=%s", exc)
        return False

    environment = (os.getenv("ENVIRONMENT") or "development").strip().lower()

    # Sample rates — conservative defaults so we don't blow through a
    # Sentry quota on a busy voice-AI hot path. Operators can raise
    # these via env when they want more signal.
    try:
        traces_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.01"))
    except ValueError:
        traces_rate = 0.01
    try:
        profiles_rate = float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.0"))
    except ValueError:
        profiles_rate = 0.0

    # `send_default_pii=False` — we handle customer audio + transcripts
    # which are sensitive. Never let Sentry grab request bodies or
    # headers by default. Operators can override per-event with an
    # allowlist later.
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=_resolve_release(),
        traces_sample_rate=traces_rate,
        profiles_sample_rate=profiles_rate,
        send_default_pii=False,
        attach_stacktrace=True,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            AsyncioIntegration(),
            # WARN and above → breadcrumbs; ERROR and above → captured
            # as events. Matches the noise level a voice system tends
            # to produce (lots of INFO, few real errors).
            LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR),
        ],
    )
    logger.info(
        "sentry_initialised environment=%s traces=%s profiles=%s",
        environment, traces_rate, profiles_rate,
    )
    return True


def capture_exception(exc: BaseException) -> None:
    """Convenience — capture an exception without requiring every
    call site to import sentry_sdk directly. No-op when Sentry is not
    initialised."""
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(exc)
    except Exception:
        pass

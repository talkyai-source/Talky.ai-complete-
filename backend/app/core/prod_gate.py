"""Production startup gate (T0.2 + T0.3).

Refuse to boot when obvious fatal misconfiguration is present in a
production environment. Fail LOUD and EARLY — the alternative is a
silently insecure deploy.

Scope: only rules that would open a legal, regulatory, or security hole
if shipped. Each rule has a single purpose and an explicit override
path for explicitly-acknowledged test deploys (where applicable).

Called from `app.main.lifespan` before the service container starts.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Default credentials commonly left in place by PBX vendors. Booting
# against these in prod is effectively unauthenticated — ClueCon in
# particular gives remote code execution on FreeSWITCH ESL.
_ASTERISK_DEFAULT_PASSWORDS = {"", "asterisk", "ari_password", "secret"}
_FREESWITCH_DEFAULT_PASSWORDS = {"", "ClueCon", "cluecon"}

# JWT secrets that are clearly placeholders left in an .env file. The
# list is short on purpose — this is a smoke test, not an entropy check.
_JWT_BAD_DEFAULTS = {
    "change-me", "changeme", "secret", "dev", "development", "placeholder",
    "your-jwt-secret-here", "your_jwt_secret_here", "test", "default",
}


class ProductionGateError(RuntimeError):
    """Raised when production boot is rejected. Halts startup."""


@dataclass
class GateViolation:
    """One thing that's wrong. Accumulated and reported together so the
    operator can fix every problem in one deploy instead of whack-a-mole.
    """
    rule: str
    detail: str


def enforce_production_gate() -> None:
    """Raise ProductionGateError if this process should not run in its
    current environment.

    No-op for dev/staging — only the "production" environment triggers
    strict checks. Logs a structured summary either way.
    """
    environment = os.getenv("ENVIRONMENT", "development").strip().lower()
    if environment != "production":
        logger.info(
            "prod_gate_skipped environment=%s — strict checks are PROD-only",
            environment,
        )
        return

    violations: list[GateViolation] = []
    violations.extend(_check_guard_bypass_flags())
    violations.extend(_check_pbx_default_credentials())
    violations.extend(_check_required_secrets())
    violations.extend(_check_caller_id_enforcement())

    if not violations:
        logger.info("prod_gate_passed — all production-mandatory checks ok")
        return

    for v in violations:
        logger.error("prod_gate_violation rule=%s detail=%s", v.rule, v.detail)

    msg_lines = ["Production startup refused. Fix these before retrying:"]
    for v in violations:
        msg_lines.append(f"  - [{v.rule}] {v.detail}")
    raise ProductionGateError("\n".join(msg_lines))


def _check_guard_bypass_flags() -> list[GateViolation]:
    """T0.2 — any *dev-bypass* flag set in production is a refusal
    condition. Catches misconfigured deploys where ENVIRONMENT got set
    to 'production' but leftover dev flags are still in the .env.
    """
    violations: list[GateViolation] = []
    suspect = {
        "TELEPHONY_DEV_BYPASS_GUARD_ERRORS": os.getenv,
        "TELEPHONY_LOCAL_DEV": os.getenv,
    }
    for name, getter in suspect.items():
        raw = (getter(name) or "").strip().lower()
        if raw and raw not in {"0", "false", "no"}:
            violations.append(
                GateViolation(
                    rule="dev_bypass_in_prod",
                    detail=f"{name}={raw!r} is not allowed when ENVIRONMENT=production",
                )
            )
    return violations


def _check_caller_id_enforcement() -> list[GateViolation]:
    """T0.1 — refuse prod boot if caller-ID enforcement is weakened. The
    dev/staging "log" mode is explicitly disallowed in prod because it
    lets unauthorised caller_ids dial real carriers."""
    mode = (os.getenv("CALLER_ID_ENFORCEMENT_MODE", "") or "").strip().lower()
    if mode and mode != "enforce":
        return [
            GateViolation(
                rule="caller_id_enforcement_weakened",
                detail=(
                    f"CALLER_ID_ENFORCEMENT_MODE={mode!r} is not allowed in "
                    f"production. Must be 'enforce' or unset."
                ),
            )
        ]
    return []


def _check_pbx_default_credentials() -> list[GateViolation]:
    """T0.3 — refuse to connect against default PBX creds. Only enforced
    when the matching adapter is actually selected."""
    violations: list[GateViolation] = []
    selected = (os.getenv("TELEPHONY_ADAPTER", "") or "").strip().lower()

    # Asterisk ARI
    if selected in ("", "asterisk", "auto"):
        password = (os.getenv("ASTERISK_ARI_PASSWORD", "") or "").strip()
        if password in _ASTERISK_DEFAULT_PASSWORDS:
            violations.append(
                GateViolation(
                    rule="asterisk_default_password",
                    detail=(
                        "ASTERISK_ARI_PASSWORD is blank or a known default — "
                        "set a strong password before running in production"
                    ),
                )
            )

    # FreeSWITCH ESL
    if selected in ("", "freeswitch", "auto"):
        password = (os.getenv("FREESWITCH_ESL_PASSWORD", "") or "").strip()
        if password in _FREESWITCH_DEFAULT_PASSWORDS:
            violations.append(
                GateViolation(
                    rule="freeswitch_default_password",
                    detail=(
                        "FREESWITCH_ESL_PASSWORD is blank or the default "
                        "'ClueCon' — this gives remote code execution on the "
                        "PBX. Set a strong password before running in production"
                    ),
                )
            )

    return violations


def _check_required_secrets() -> list[GateViolation]:
    """T0.3 — secrets that MUST be set in production. JWT controls auth;
    TELEPHONY_METRICS_TOKEN gates the /metrics endpoint; STRIPE_SECRET_KEY
    stops billing from silently falling back to mock mode.
    """
    violations: list[GateViolation] = []

    jwt_secret = (os.getenv("JWT_SECRET", "") or "").strip()
    if not jwt_secret:
        violations.append(
            GateViolation(
                rule="missing_secret",
                detail="JWT_SECRET is not set — auth cannot work",
            )
        )
    elif jwt_secret.lower() in _JWT_BAD_DEFAULTS:
        violations.append(
            GateViolation(
                rule="weak_secret",
                detail=f"JWT_SECRET is set to a placeholder value ({jwt_secret[:16]}…)",
            )
        )

    metrics_token = (os.getenv("TELEPHONY_METRICS_TOKEN", "") or "").strip()
    if not metrics_token:
        violations.append(
            GateViolation(
                rule="missing_secret",
                detail=(
                    "TELEPHONY_METRICS_TOKEN is not set — /metrics endpoint would "
                    "be unauthenticated or exposed to scrapers"
                ),
            )
        )

    # Stripe: refuse mock-mode billing in prod. If the product is intentionally
    # non-billed (self-hosted open-source), set STRIPE_BILLING_DISABLED=1 to
    # acknowledge that and skip the check.
    billing_disabled = (os.getenv("STRIPE_BILLING_DISABLED", "") or "").strip().lower() in {
        "1", "true", "yes"
    }
    stripe_key = (os.getenv("STRIPE_SECRET_KEY", "") or "").strip()
    if not stripe_key and not billing_disabled:
        violations.append(
            GateViolation(
                rule="missing_secret",
                detail=(
                    "STRIPE_SECRET_KEY is not set — billing would silently fall "
                    "back to mock mode. Set the key, or set "
                    "STRIPE_BILLING_DISABLED=1 to acknowledge running without billing."
                ),
            )
        )

    return violations

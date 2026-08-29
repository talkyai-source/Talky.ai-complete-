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

import importlib.util
import ipaddress
import logging
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

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
    violations.extend(_check_staging_only_flags())
    violations.extend(_check_pbx_default_credentials())
    violations.extend(_check_required_secrets())
    violations.extend(_check_caller_id_enforcement())
    violations.extend(_check_inbound_strict_routing())

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


def _check_inbound_strict_routing() -> list[GateViolation]:
    """Reject legacy configuration that claims strict DID routing is off.

    The router itself is now unconditionally strict.  Refusing contradictory
    production configuration prevents an operator from believing a fallback
    route is enabled and makes rollback/config drift visible at startup.
    """

    false_values = {"0", "false", "no", "off", "disabled"}
    violations: list[GateViolation] = []
    for name in (
        "TELEPHONY_INBOUND_REQUIRE_TENANT",
        "INBOUND_STRICT_ROUTING",
        "TELEPHONY_STRICT_INBOUND_ROUTING",
    ):
        raw = (os.getenv(name, "") or "").strip().lower()
        if raw in false_values:
            violations.append(
                GateViolation(
                    rule="inbound_strict_routing_disabled",
                    detail=(
                        f"{name}={raw!r} contradicts mandatory production "
                        "DID-to-tenant routing. Remove it or set it to true."
                    ),
                )
            )
    return violations


def _check_staging_only_flags() -> list[GateViolation]:
    """Refuse proof-only controls when a process identifies as production."""

    violations: list[GateViolation] = []
    raw = (os.getenv("INBOUND_TRANSFER_STAGING_PROOF_ENABLED", "") or "").strip().lower()
    if raw and raw not in {"0", "false", "no", "off", "disabled"}:
        violations.append(
            GateViolation(
                rule="staging_proof_flag_in_prod",
                detail=(
                    "INBOUND_TRANSFER_STAGING_PROOF_ENABLED is staging-only and "
                    "must be unset or false when ENVIRONMENT=production"
                ),
            )
        )
    for name in (
        "INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID",
        "INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID",
    ):
        if (os.getenv(name, "") or "").strip():
            violations.append(
                GateViolation(
                    rule="staging_proof_scope_in_prod",
                    detail=f"{name} is staging-only and must be unset in production",
                )
            )
    return violations


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
    TELEPHONY_METRICS_TOKEN gates the /metrics endpoint;
    INTERNAL_SERVICE_TOKEN authenticates private gateway/worker callbacks;
    STRIPE_SECRET_KEY stops billing from silently falling back to mock mode;
    SECRETS_MASTER_KEY is the KEK every stored secret is encrypted under.
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

    internal_token = (os.getenv("INTERNAL_SERVICE_TOKEN", "") or "").strip()
    if not internal_token:
        violations.append(
            GateViolation(
                rule="missing_secret",
                detail=(
                    "INTERNAL_SERVICE_TOKEN is not set — private worker and "
                    "voice-gateway callbacks would all be rejected, and no "
                    "authenticated gateway deployment can be proven"
                ),
            )
        )
    elif len(internal_token) < 32:
        violations.append(
            GateViolation(
                rule="weak_secret",
                detail="INTERNAL_SERVICE_TOKEN must contain at least 32 characters",
            )
        )

    gateway_auth_token = (os.getenv("VOICE_GATEWAY_AUTH_TOKEN", "") or "").strip()
    if not gateway_auth_token:
        violations.append(
            GateViolation(
                rule="missing_secret",
                detail=(
                    "VOICE_GATEWAY_AUTH_TOKEN is not set — the local media "
                    "gateway control plane would accept unauthenticated session controls"
                ),
            )
        )
    elif len(gateway_auth_token) < 32:
        violations.append(
            GateViolation(
                rule="weak_secret",
                detail="VOICE_GATEWAY_AUTH_TOKEN must contain at least 32 characters",
            )
        )
    elif internal_token and gateway_auth_token == internal_token:
        violations.append(
            GateViolation(
                rule="secret_reuse",
                detail=(
                    "VOICE_GATEWAY_AUTH_TOKEN must be distinct from "
                    "INTERNAL_SERVICE_TOKEN so a control-plane disclosure cannot "
                    "authenticate caller-audio callbacks"
                ),
            )
        )

    callback_host = (os.getenv("VOICE_GATEWAY_CALLBACK_HOST", "") or "").strip()
    callback_host_valid = False
    if callback_host:
        try:
            callback_ip = ipaddress.ip_address(callback_host)
            callback_host_valid = callback_ip.version == 4 and callback_ip.is_loopback
        except ValueError:
            callback_host_valid = False
    if not callback_host_valid:
        violations.append(
            GateViolation(
                rule="gateway_callback_host_unpinned",
                detail=(
                    "VOICE_GATEWAY_CALLBACK_HOST must be one explicit numeric loopback IPv4 "
                    "address; an empty or broad callback target could exfiltrate "
                    "INTERNAL_SERVICE_TOKEN and caller audio"
                ),
            )
        )

    backend_internal_url = (
        os.getenv("BACKEND_INTERNAL_URL", "http://127.0.0.1:8000") or ""
    ).strip()
    try:
        parsed_backend_url = urlsplit(backend_internal_url)
        backend_port = parsed_backend_url.port
        backend_url_valid = (
            parsed_backend_url.scheme == "http"
            and parsed_backend_url.hostname == callback_host
            and backend_port is not None
            and 1 <= backend_port <= 65535
            and parsed_backend_url.username is None
            and parsed_backend_url.password is None
            and parsed_backend_url.path in {"", "/"}
            and not parsed_backend_url.query
            and not parsed_backend_url.fragment
        )
    except ValueError:
        backend_url_valid = False
    if not backend_url_valid:
        violations.append(
            GateViolation(
                rule="gateway_callback_url_mismatch",
                detail=(
                    "BACKEND_INTERNAL_URL must be a plain http://<pinned IPv4>:<port> "
                    "origin whose host exactly equals VOICE_GATEWAY_CALLBACK_HOST"
                ),
            )
        )

    # SECRETS_MASTER_KEY is the key-encryption key (KEK) that wraps every DEK
    # in the secrets table — tenant credentials, provider keys, connector
    # tokens. When it is unset the local KMS backend mints a RANDOM EPHEMERAL
    # key at startup (app/core/kms.py) and secrets_manager silently falls back
    # to JWT_SECRET/SECRET_KEY. Either way the next restart — or a JWT_SECRET
    # rotation — re-keys the service while the ciphertext in the database stays
    # under the old key: unrecoverable, and it looks like a clean deploy.
    # Required regardless of KMS_PROVIDER, because SecretsManager derives its
    # master KEK from this variable even when KMS_PROVIDER=aws.
    if not (os.getenv("SECRETS_MASTER_KEY", "") or "").strip():
        violations.append(
            GateViolation(
                rule="missing_secret",
                detail=(
                    "SECRETS_MASTER_KEY is not set — the service would encrypt "
                    "secrets under an ephemeral/derived key, and every secret "
                    "already stored in the database becomes permanently "
                    "unreadable on the next restart or JWT_SECRET rotation. "
                    "Generate with: python -c \"import secrets; "
                    "print(secrets.token_hex(32))\" — but see the deploy notes "
                    "first if this service has ever booted without it."
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
    elif stripe_key and not stripe_key.startswith("sk_live_"):
        # A set-but-non-live key (test/restricted-test 'sk_test_...' or a
        # malformed value) means the operator believes billing is live when
        # it is actually charging against Stripe's test ledger — or not at
        # all. This does not change billing behavior, only refuses prod boot.
        violations.append(
            GateViolation(
                rule="STRIPE_LIVE_KEY",
                detail=(
                    f"STRIPE_SECRET_KEY is set but is not a live key "
                    f"({stripe_key[:8]}…) — production billing would run "
                    "against Stripe's test mode. Use an 'sk_live_' key."
                ),
            )
        )

    if stripe_key and importlib.util.find_spec("stripe") is None:
        # A key is configured but the SDK that would actually call Stripe
        # isn't installed — billing_service falls back to mock mode
        # silently in that case, so the operator believes billing is live
        # (key is set) while every charge is actually simulated.
        violations.append(
            GateViolation(
                rule="STRIPE_SDK_MISSING",
                detail=(
                    "STRIPE_SECRET_KEY is set but the 'stripe' package is not "
                    "installed — billing would silently run in mock mode "
                    "despite the operator believing it is live. Install the "
                    "stripe SDK or unset STRIPE_SECRET_KEY."
                ),
            )
        )

    return violations

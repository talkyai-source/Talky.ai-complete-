"""Availability of saved cloud selections, separate from credential checks.

The legacy cloud bridges are explicitly opt-in outside production. They do not
establish a production campaign route or a tenant-bound cloud origination path.
"""
from dataclasses import dataclass
import os


CLOUD_PROVIDERS = ("twilio", "vonage")


@dataclass(frozen=True)
class CloudAvailability:
    activation_allowed: bool
    qualification_only: bool
    reason_code: str | None
    reason: str


def cloud_availability(provider: str, *, environment: str | None = None) -> CloudAvailability:
    if provider not in CLOUD_PROVIDERS:
        raise ValueError("Unsupported cloud provider")
    environment = (environment if environment is not None else os.getenv("ENVIRONMENT", "development")).strip().lower()
    if environment == "production":
        return CloudAvailability(False, False, "cloud_telephony_unavailable",
            "Cloud calling is unavailable in production. Credentials remain saved; select a ready SIP route to place calls.")
    enabled = os.getenv(f"{provider.upper()}_BRIDGE_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    return CloudAvailability(enabled, True, None if enabled else "cloud_bridge_disabled",
        "Available only for explicit nonproduction qualification; this does not establish outbound campaign routing."
        if enabled else "The nonproduction cloud bridge is disabled. An operator must explicitly enable it for qualification.")


class TelephonySelectionError(Exception):
    def __init__(self, code: str, message: str, status_code: int):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


async def require_production_outbound_selection(db_pool, *, tenant_id: str, environment: str | None = None) -> None:
    """Refuse unsupported production selections before any new origination.

    This is a current admission read, not an atomic transaction with a later remote
    call. Existing receipt replays should be resolved before invoking this guard.
    Nonproduction qualification behavior remains with its existing bridge controls.
    """
    environment = (environment if environment is not None else os.getenv("ENVIRONMENT", "development")).strip().lower()
    if environment != "production":
        return
    from app.core.db_utils import acquire_with_tenant

    unavailable = TelephonySelectionError("telephony_selection_unavailable",
        "The saved telephony selection could not be verified. Retry when configuration is available.", 503)
    try:
        if db_pool is None:
            raise unavailable
        async with acquire_with_tenant(db_pool, tenant_id) as conn:
            row = await conn.fetchrow("SELECT active_telephony_provider FROM tenants WHERE id=$1::uuid", tenant_id)
        if row is None:
            raise unavailable
        provider = str(row["active_telephony_provider"] or "none").strip().lower()
        if provider not in (*CLOUD_PROVIDERS, "sip", "none"):
            raise unavailable
    except Exception as exc:
        # No raw database exception, provider details or credentials in the
        # client-facing error. Cancellation is not converted to an admission.
        raise unavailable from exc
    if provider in CLOUD_PROVIDERS:
        availability = cloud_availability(provider, environment=environment)
        raise TelephonySelectionError(availability.reason_code, availability.reason, 422)

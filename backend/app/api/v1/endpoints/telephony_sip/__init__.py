"""Tenant SIP onboarding endpoints (Phase 2 / WS-F).

Scope:
- Tenant-scoped CRUD-lite for SIP trunks, codec policies, route policies
- Idempotency-key enforcement on every mutating endpoint
- RFC 9457 problem responses (application/problem+json)
- Rate-limited via the telephony quota policy

Public surface is intentionally identical to the previous single-file
`telephony_sip.py` — schema classes, endpoint coroutines, and the
aggregate `router` are all re-exported below so existing imports keep
working unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter

# Resource modules
from . import codec_policies as _codec_policies_mod
from . import quotas as _quotas_mod
from . import route_policies as _route_policies_mod
from . import trunks as _trunks_mod

# Endpoint coroutines (re-export — used by tests and verifiers)
from .codec_policies import (
    activate_codec_policy,
    create_codec_policy,
    deactivate_codec_policy,
    list_codec_policies,
    update_codec_policy,
)
from .quotas import get_telephony_quota_status
from .route_policies import (
    activate_route_policy,
    create_route_policy,
    deactivate_route_policy,
    list_route_policies,
    update_route_policy,
)
from .schemas import (
    CodecPolicyCreateRequest,
    CodecPolicyResponse,
    CodecPolicyUpdateRequest,
    RoutePolicyCreateRequest,
    RoutePolicyResponse,
    RoutePolicyUpdateRequest,
    SIPDirection,
    SIPRouteType,
    SIPTransport,
    SIPTrunkCreateRequest,
    SIPTrunkResponse,
    SIPTrunkUpdateRequest,
    TelephonyQuotaStatusItem,
    TelephonyQuotaStatusResponse,
)
from .trunks import (
    activate_sip_trunk,
    create_sip_trunk,
    deactivate_sip_trunk,
    list_sip_trunks,
    update_sip_trunk,
)

router = APIRouter(prefix="/telephony/sip", tags=["Telephony SIP"])
router.include_router(_trunks_mod.router)
router.include_router(_codec_policies_mod.router)
router.include_router(_route_policies_mod.router)
router.include_router(_quotas_mod.router)


__all__ = [
    # Aggregate router
    "router",
    # Schemas
    "CodecPolicyCreateRequest",
    "CodecPolicyResponse",
    "CodecPolicyUpdateRequest",
    "RoutePolicyCreateRequest",
    "RoutePolicyResponse",
    "RoutePolicyUpdateRequest",
    "SIPDirection",
    "SIPRouteType",
    "SIPTransport",
    "SIPTrunkCreateRequest",
    "SIPTrunkResponse",
    "SIPTrunkUpdateRequest",
    "TelephonyQuotaStatusItem",
    "TelephonyQuotaStatusResponse",
    # Endpoints
    "activate_codec_policy",
    "activate_route_policy",
    "activate_sip_trunk",
    "create_codec_policy",
    "create_route_policy",
    "create_sip_trunk",
    "deactivate_codec_policy",
    "deactivate_route_policy",
    "deactivate_sip_trunk",
    "get_telephony_quota_status",
    "list_codec_policies",
    "list_route_policies",
    "list_sip_trunks",
    "update_codec_policy",
    "update_route_policy",
    "update_sip_trunk",
]

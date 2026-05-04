"""WS-G runtime policy endpoints.

Implements:
- deterministic compile preview
- activate flow: precheck -> apply -> verify -> commit
- rollback to prior version
- version history listing
- activation/rollback metrics

Public surface mirrors the previous `telephony_runtime.py` — schema
classes, endpoint coroutines, and the aggregate `router` are re-exported
so existing imports (routes.py, tests) keep working unchanged.
"""
from __future__ import annotations

from fastapi import APIRouter

from . import (
    activate as _activate_mod,
    metrics as _metrics_mod,
    preview as _preview_mod,
    rollback as _rollback_mod,
    versions as _versions_mod,
)
from .activate import activate_runtime_policy
from .metrics import get_runtime_activation_metrics
from .preview import preview_runtime_policy
from .rollback import rollback_runtime_policy
from .schemas import (
    RuntimeActivateRequest,
    RuntimeActivationMetricsResponse,
    RuntimeActivationResponse,
    RuntimeCompilePreviewResponse,
    RuntimeRollbackRequest,
    RuntimeRollbackResponse,
    RuntimeVersionResponse,
)
from .versions import list_runtime_policy_versions

router = APIRouter(prefix="/telephony/sip/runtime", tags=["Telephony SIP Runtime"])
router.include_router(_preview_mod.router)
router.include_router(_activate_mod.router)
router.include_router(_rollback_mod.router)
router.include_router(_versions_mod.router)
router.include_router(_metrics_mod.router)


__all__ = [
    "router",
    # Schemas
    "RuntimeActivateRequest",
    "RuntimeActivationMetricsResponse",
    "RuntimeActivationResponse",
    "RuntimeCompilePreviewResponse",
    "RuntimeRollbackRequest",
    "RuntimeRollbackResponse",
    "RuntimeVersionResponse",
    # Endpoints
    "activate_runtime_policy",
    "get_runtime_activation_metrics",
    "list_runtime_policy_versions",
    "preview_runtime_policy",
    "rollback_runtime_policy",
]

"""Ask the running call process to drain before it is stopped.

uvicorn closes its listener before the app's shutdown hook runs, so draining
inside shutdown leaves live calls deaf. The systemd ``ExecStop`` step calls
this route first, while the listener is still open.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.v1.endpoints.telephony_bridge import (
    _require_call_process,
    _require_telephony_control,
)
from app.core import readiness

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sip/telephony", tags=["Telephony Bridge (generic)"])


class DrainSnapshot(BaseModel):
    """Shape of ``readiness.snapshot()``."""

    model_config = ConfigDict(extra="allow")

    ready: bool
    draining: bool
    drain_elapsed_s: float
    drain_timeout_s: int
    active_sessions: Optional[int] = None
    committed_calls: int
    max_sessions: Optional[int] = None
    at_capacity: bool


@router.post(
    "/drain",
    response_model=DrainSnapshot,
    dependencies=[Depends(_require_telephony_control)],
)
async def drain_calls():
    """Stop taking new calls; live calls continue. Idempotent."""
    _require_call_process()
    readiness.begin_drain()
    logger.warning(
        "telephony_drain_requested committed_calls=%s", readiness.committed_calls()
    )
    return readiness.snapshot()

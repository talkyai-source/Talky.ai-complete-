"""
Admin API Module
Aggregates all admin-related endpoints into a single router.
"""
from fastapi import APIRouter

from .base import router as base_router
from .tenants import router as tenants_router
from .calls import router as calls_router
from .actions import router as actions_router
from .connectors import router as connectors_router
from .usage import router as usage_router
from .health import router as health_router

# Create main admin router
router = APIRouter(prefix="/admin", tags=["admin"])

# Include all sub-routers (they don't have prefix since parent has /admin)
router.include_router(base_router)
router.include_router(tenants_router)
router.include_router(calls_router)
router.include_router(actions_router)
router.include_router(connectors_router)
router.include_router(usage_router)
router.include_router(health_router)

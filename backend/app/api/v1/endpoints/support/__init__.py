"""Support API: customer routes under /support, staff console under /admin/support."""
from fastapi import APIRouter

from app.api.v1.endpoints.support.admin import admin_router
from app.api.v1.endpoints.support.attachments import router as attachments_router
from app.api.v1.endpoints.support.conversations import router as conversations_router
from app.api.v1.endpoints.support.tickets import router as tickets_router

router = APIRouter(tags=["support"])
router.include_router(conversations_router)
router.include_router(tickets_router)
router.include_router(attachments_router)

__all__ = ["router", "admin_router"]

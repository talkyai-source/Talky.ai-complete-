"""
API v1 Route Aggregator

Collects all endpoint routers and exposes a single `api_router`.
"""
from fastapi import APIRouter

api_router = APIRouter()

# --- Core endpoints ---
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.campaigns import router as campaigns_router
from app.api.v1.endpoints.contacts import router as contacts_router
from app.api.v1.endpoints.calls import router as calls_router
from app.api.v1.endpoints.recordings import router as recordings_router
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.dashboard import router as dashboard_router
from app.api.v1.endpoints.analytics import router as analytics_router
from app.api.v1.endpoints.billing import router as billing_router
from app.api.v1.endpoints.plans import router as plans_router
from app.api.v1.endpoints.clients import router as clients_router
from app.api.v1.endpoints.connectors import router as connectors_router
from app.api.v1.endpoints.meetings import router as meetings_router
from app.api.v1.endpoints.webhooks import router as webhooks_router

# --- WebSocket / AI endpoints ---
from app.api.v1.endpoints.assistant_ws import router as assistant_ws_router
from app.api.v1.endpoints.ask_ai_ws import router as ask_ai_ws_router
from app.api.v1.endpoints.ai_options import router as ai_options_router
from app.api.v1.endpoints.ai_options_ws import router as ai_options_ws_router
from app.api.v1.endpoints.freeswitch_bridge import router as freeswitch_router

# --- Admin endpoints ---
from app.api.v1.endpoints.admin import router as admin_router

# Include all routers
api_router.include_router(auth_router)
api_router.include_router(campaigns_router)
api_router.include_router(contacts_router)
api_router.include_router(calls_router)
api_router.include_router(recordings_router)
api_router.include_router(health_router)
api_router.include_router(dashboard_router)
api_router.include_router(analytics_router)
api_router.include_router(billing_router)
api_router.include_router(plans_router)
api_router.include_router(clients_router)
api_router.include_router(connectors_router)
api_router.include_router(meetings_router)
api_router.include_router(webhooks_router)
api_router.include_router(assistant_ws_router)
api_router.include_router(ask_ai_ws_router)
api_router.include_router(ai_options_router)
api_router.include_router(ai_options_ws_router)
api_router.include_router(freeswitch_router)
api_router.include_router(admin_router)


"""
API Router
Combines all endpoint routers
"""
from fastapi import APIRouter
from app.api.v1.endpoints import (
    campaigns,
    webhooks,
    websockets,
    auth,
    plans,
    dashboard,
    analytics,
    calls,
    recordings,
    contacts,
    clients,
    admin,
    ai_options,
    ai_options_ws,
    sip_bridge,
)

api_router = APIRouter()

# Include all routers
# Existing routers
api_router.include_router(campaigns.router)
api_router.include_router(webhooks.router)
api_router.include_router(websockets.router)

# New routers (frontend alignment)
api_router.include_router(auth.router)
api_router.include_router(plans.router)
api_router.include_router(dashboard.router)
api_router.include_router(analytics.router)
api_router.include_router(calls.router)
api_router.include_router(recordings.router)
api_router.include_router(contacts.router)
api_router.include_router(clients.router)
api_router.include_router(admin.router)

# AI Options (provider selection & testing)
api_router.include_router(ai_options.router)
api_router.include_router(ai_options_ws.router)

# SIP Bridge (MicroSIP integration - Day 18)
api_router.include_router(sip_bridge.router)

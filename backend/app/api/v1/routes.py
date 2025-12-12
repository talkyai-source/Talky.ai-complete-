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

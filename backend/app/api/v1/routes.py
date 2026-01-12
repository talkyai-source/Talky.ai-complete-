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
    ask_ai_ws,
    sip_bridge,
    billing,  # NEW: Stripe billing endpoints
    assistant_ws,  # NEW: Assistant Agent chat endpoint
    connectors,  # NEW: Connector OAuth and management (Day 24)
    meetings,  # NEW: Meeting booking (Day 25)
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

# Ask AI (one-click voice assistant)
api_router.include_router(ask_ai_ws.router)

# SIP Bridge (MicroSIP integration - Day 18)
api_router.include_router(sip_bridge.router)

# Billing (Stripe subscription management - Day 22)
api_router.include_router(billing.router)

# Assistant Agent (Conversational AI with tools)
api_router.include_router(assistant_ws.router)

# Connectors (OAuth integrations - Day 24)
api_router.include_router(connectors.router)

# Meetings (Calendar booking - Day 25)
api_router.include_router(meetings.router)

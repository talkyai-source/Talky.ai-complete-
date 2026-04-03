"""
API v1 Route Aggregator

Collects all endpoint routers and exposes a single `api_router`.
"""

from fastapi import APIRouter

api_router = APIRouter()

# --- Core endpoints ---
# --- Admin endpoints ---
from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.ai_options import router as ai_options_router
from app.api.v1.endpoints.ai_options_ws import router as ai_options_ws_router
from app.api.v1.endpoints.analytics import router as analytics_router
from app.api.v1.endpoints.ask_ai_ws import router as ask_ai_ws_router

# --- WebSocket / AI endpoints ---
from app.api.v1.endpoints.assistant_ws import router as assistant_ws_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.billing import router as billing_router
from app.api.v1.endpoints.calls import router as calls_router
from app.api.v1.endpoints.campaigns import router as campaigns_router
from app.api.v1.endpoints.clients import router as clients_router
from app.api.v1.endpoints.connectors import router as connectors_router
from app.api.v1.endpoints.contacts import router as contacts_router
from app.api.v1.endpoints.dashboard import router as dashboard_router
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.meetings import router as meetings_router

# --- MFA endpoints ---
from app.api.v1.endpoints.mfa import router as mfa_router
from app.api.v1.endpoints.passkeys import router as passkeys_router
from app.api.v1.endpoints.rbac import router as rbac_router
from app.api.v1.endpoints.sessions import router as sessions_router
from app.api.v1.endpoints.plans import router as plans_router
from app.api.v1.endpoints.recordings import router as recordings_router
from app.api.v1.endpoints.telephony_bridge import router as telephony_bridge_router
from app.api.v1.endpoints.telephony_concurrency import (
    router as telephony_concurrency_router,
)
from app.api.v1.endpoints.telephony_runtime import router as telephony_runtime_router
from app.api.v1.endpoints.telephony_sip import router as telephony_sip_router
from app.api.v1.endpoints.vonage_bridge import router as vonage_bridge_router
from app.api.v1.endpoints.webhooks import router as webhooks_router
from app.api.v1.endpoints.webhooks_secure import router as webhooks_secure_router

# --- Day 8: Audit Logs + Suspension + Secrets endpoints ---
from app.api.v1.endpoints.audit_logs import router as audit_logs_router
from app.api.v1.endpoints.security_events import router as security_events_router
from app.api.v1.endpoints.suspensions import router as suspensions_router
from app.api.v1.endpoints.secrets import router as secrets_router
from app.api.v1.endpoints.emergency_access import router as emergency_access_router

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
api_router.include_router(webhooks_secure_router)
api_router.include_router(assistant_ws_router)
api_router.include_router(ask_ai_ws_router)
api_router.include_router(ai_options_router)
api_router.include_router(ai_options_ws_router)
api_router.include_router(telephony_bridge_router)
api_router.include_router(vonage_bridge_router)
api_router.include_router(telephony_sip_router)
api_router.include_router(telephony_runtime_router)
api_router.include_router(telephony_concurrency_router)
api_router.include_router(mfa_router)
api_router.include_router(passkeys_router)
api_router.include_router(rbac_router)
api_router.include_router(sessions_router)
api_router.include_router(admin_router)

# Day 8: Audit, Suspension, Secrets, Emergency Access
api_router.include_router(audit_logs_router)
api_router.include_router(security_events_router)
api_router.include_router(suspensions_router)
api_router.include_router(secrets_router)
api_router.include_router(emergency_access_router)

# Day 7: Call Guard + Abuse Monitoring
from app.api.v1.endpoints.call_limits import router as call_limits_router
from app.api.v1.endpoints.abuse_monitoring import router as abuse_monitoring_router
api_router.include_router(call_limits_router)
api_router.include_router(abuse_monitoring_router)

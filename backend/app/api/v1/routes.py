"""
API Router
Combines all endpoint routers
"""
from fastapi import APIRouter
from app.api.v1.endpoints import campaigns, webhooks, websockets

api_router = APIRouter()

# Include all routers
api_router.include_router(campaigns.router)
api_router.include_router(webhooks.router)
api_router.include_router(websockets.router)

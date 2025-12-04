"""
Health Check Endpoint
Provides health status for Docker health checks and monitoring
"""
from fastapi import APIRouter, status
from datetime import datetime
from typing import Dict

router = APIRouter(tags=["health"])


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> Dict[str, str]:
    """
    Health check endpoint for Docker and monitoring systems.
    
    Returns:
        Dict with status and timestamp
    """
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "service": "talky-backend"
    }


@router.get("/", status_code=status.HTTP_200_OK)
async def root() -> Dict[str, str]:
    """
    Root endpoint.
    
    Returns:
        Welcome message
    """
    return {
        "message": "Talky.ai Voice AI Backend",
        "version": "1.0.0",
        "docs": "/docs"
    }

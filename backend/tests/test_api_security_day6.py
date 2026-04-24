"""
Integration Tests for API Security Hardening (Day 6)

Verifies:
- Multi-tier rate limiting (IP, User, Tenant)
- Payload sanitization (XSS removal)
- Idempotency support (Idempotency-Key)
- Security headers
"""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import FastAPI, Request, status
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

# Import middleware and utilities
from app.core.api_security_middleware import APISecurityMiddleware
from app.core.security.api_security import APIRateLimiter, RateLimitAction, RateLimitTier
from app.core.security.idempotency import IdempotencyManager

# ── Setup Test App ──
app = FastAPI()

# Add middleware
app.add_middleware(APISecurityMiddleware)

@app.get("/test-endpoint")
async def test_endpoint():
    return {"message": "success"}

@app.post("/test-post")
async def test_post(request: Request):
    data = await request.json()
    return {"data": data}

@app.post("/idempotent-endpoint")
async def idempotent_endpoint(request: Request):
    from app.core.security.idempotency import idempotency_dependency, store_idempotent_response
    # Manually trigger dependency for testing
    from fastapi import Depends
    # In real FastAPI this is a dependency, here we simulate it
    data = await request.json()
    result = {"processed": True, "data": data}
    
    # Simulate storing response
    if hasattr(request.state, "idempotency_key"):
        await store_idempotent_response(request, 200, json.dumps(result))
    
    return result

client = TestClient(app)

# ── Tests ──

def test_security_headers():
    """Verify that security headers are added to every response."""
    response = client.get("/test-endpoint")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-XSS-Protection"] == "1; mode=block"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

def test_payload_sanitization():
    """Verify that malicious script tags are removed from JSON bodies."""
    malicious_data = {
        "name": "Normal Name",
        "bio": "Check this out <script>alert('XSS')</script>",
        "callback": "javascript:alert(1)",
        "nested": {
            "field": "onerror=alert(1)"
        }
    }
    
    response = client.post("/test-post", json=malicious_data)
    assert response.status_code == 200
    data = response.json()["data"]
    
    assert data["name"] == "Normal Name"
    assert "<script>" not in data["bio"]
    assert "[removed]" in data["bio"]
    assert "javascript:" not in data["callback"]
    assert "onerror=" not in data["nested"]["field"]

def test_request_size_limit():
    """Verify that excessively large requests are blocked."""
    large_body = "A" * (11 * 1024 * 1024)  # 11MB (limit is 10MB)
    response = client.post("/test-post", content=large_body, headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert "Request body too large" in response.json()["detail"]

@patch("app.core.security.api_security.APIRateLimiter.check_all_tiers")
def test_rate_limiting_block(mock_check):
    """Verify that rate limit exceeded returns 429."""
    # Mock rate limit block
    mock_check.return_value = (False, {"Retry-After": "60"}, "Rate limit exceeded (ip tier)")
    
    # We need to mock the container as well since middleware calls get_container
    with patch("app.core.container.get_container") as mock_container:
        mock_container.return_value.is_initialized = True
        mock_container.return_value.redis_enabled = True
        
        response = client.get("/test-endpoint")
        assert response.status_code == 429
        assert "Rate limit exceeded" in response.json()["detail"]
        assert response.headers["Retry-After"] == "60"

@pytest.mark.asyncio
async def test_idempotency_logic():
    """Test the core IdempotencyManager logic (mocking Redis)."""
    mock_redis = AsyncMock()
    manager = IdempotencyManager(redis_client=mock_redis)
    
    # Mock a new request
    mock_redis.get.return_value = None
    mock_redis.exists.return_value = False
    
    request = MagicMock(spec=Request)
    request.method = "POST"
    request.url.path = "/test"
    
    is_new, cached, error = await manager.check_idempotency("test-key", request)
    assert is_new is True
    assert cached is None
    assert error is None
    
    # Verify lock was set
    mock_redis.setex.assert_called()

if __name__ == "__main__":
    print("Run with: pytest backend/tests/test_api_security_day6.py")

"""
Shared fixtures for the security test suite.

Provides:
- TOTP encryption key setup
- Mock asyncpg connections
- Mock Redis clients
- Mock FastAPI request objects
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from cryptography.fernet import Fernet


# ---------------------------------------------------------------------------
# Environment: generate a real Fernet key for TOTP tests
# ---------------------------------------------------------------------------
_TEST_FERNET_KEY = Fernet.generate_key().decode("utf-8")


@pytest.fixture(autouse=True)
def _security_env(monkeypatch):
    """Set environment variables required by the security layer."""
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", _TEST_FERNET_KEY)
    monkeypatch.setenv("TOTP_ISSUER_NAME", "TestIssuer")
    yield


# ---------------------------------------------------------------------------
# Mock asyncpg connection
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_conn():
    """Return an AsyncMock that behaves like an asyncpg.Connection."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=0)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value="UPDATE 0")
    conn.executemany = AsyncMock(return_value=None)
    return conn


# ---------------------------------------------------------------------------
# Mock Redis client
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_redis():
    """Return an AsyncMock that behaves like a redis.asyncio.Redis client."""
    r = AsyncMock()
    r.exists = AsyncMock(return_value=0)
    r.get = AsyncMock(return_value=None)
    r.setex = AsyncMock(return_value=True)
    r.delete = AsyncMock(return_value=1)
    r.zadd = AsyncMock(return_value=1)
    r.zcard = AsyncMock(return_value=0)
    r.zremrangebyscore = AsyncMock(return_value=0)
    r.expire = AsyncMock(return_value=True)
    r.ttl = AsyncMock(return_value=60)
    return r


# ---------------------------------------------------------------------------
# Mock FastAPI Request
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_request():
    """Return a MagicMock that behaves like a fastapi.Request."""
    req = MagicMock()
    req.headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": '"Chrome";v="120"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Ch-Ua-Platform-Version": '"15.0.0"',
        "DNT": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
    }
    req.client = MagicMock()
    req.client.host = "192.168.1.100"

    # For idempotency / rate limiting tests
    req.method = "POST"
    req.url = MagicMock()
    req.url.path = "/api/v1/test"
    req.state = MagicMock()
    req.body = AsyncMock(return_value=b'{"test": true}')
    req.query_params = {}
    req.path_params = {}
    return req


import pytest
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from fastapi import Request
from app.core.security.sessions import (
    create_session,
    validate_session,
    revoke_all_user_sessions,
    get_active_sessions_detailed,
    SESSION_IDLE_TIMEOUT_MINUTES
)
from app.core.security.device_fingerprint import generate_device_fingerprint

@pytest.mark.asyncio
async def test_session_lifecycle_and_binding(db_pool):
    """
    Validates Day 5 Session Security features:
    1. Creation with binding
    2. Expiration and idle timeout
    3. IP/Fingerprint mismatch detection
    4. Logout all sessions
    """
    user_id = str(uuid4())
    ip_orig = "1.1.1.1"
    ua_orig = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
    
    async with db_pool.acquire() as conn:
        # 1. Create Session
        token = await create_session(
            conn,
            user_id=user_id,
            ip_address=ip_orig,
            user_agent=ua_orig,
            bind_to_ip=True,
            bind_to_fingerprint=True,
            device_fingerprint="fp_orig"
        )
        assert token is not None
        
        # 2. Validate with same IP/Fingerprint (Should succeed)
        session = await validate_session(
            conn,
            token,
            current_ip=ip_orig,
            current_fingerprint="fp_orig"
        )
        assert session is not None
        assert session["is_suspicious"] is False
        
        # 3. Validate with DIFFERENT IP (Should mark suspicious in non-strict mode)
        # Note: Default strict_binding=False in validate_session parameters
        session_ip_change = await validate_session(
            conn,
            token,
            current_ip="2.2.2.2", 
            current_fingerprint="fp_orig"
        )
        assert session_ip_change is not None
        assert session_ip_change["is_suspicious"] is True
        assert "ip_mismatch" in session_ip_change["suspicious_reason"]
        
        # 4. Validate with DIFFERENT Fingerprint
        token2 = await create_session(
            conn,
            user_id=user_id,
            ip_address=ip_orig,
            user_agent=ua_orig,
            device_fingerprint="fp_orig"
        )
        session_fp_change = await validate_session(
            conn,
            token2,
            current_ip=ip_orig,
            current_fingerprint="fp_hacker"
        )
        assert session_fp_change is not None
        assert session_fp_change["is_suspicious"] is True
        assert "fingerprint_mismatch" in session_fp_change["suspicious_reason"]

        # 5. Test Logout All
        revoked_count = await revoke_all_user_sessions(conn, user_id)
        assert revoked_count >= 2
        
        # Verify tokens are now invalid
        invalid_session = await validate_session(conn, token)
        assert invalid_session is None

@pytest.mark.asyncio
async def test_session_idle_timeout(db_pool):
    user_id = str(uuid4())
    async with db_pool.acquire() as conn:
        token = await create_session(
            conn,
            user_id=user_id,
            ip_address="127.0.0.1"
        )
        
        # Manually backdate last_active_at to trigger idle timeout
        # We need to reach past SESSION_IDLE_TIMEOUT_MINUTES
        past_time = datetime.now(timezone.utc) - timedelta(minutes=SESSION_IDLE_TIMEOUT_MINUTES + 1)
        await conn.execute(
            "UPDATE security_sessions SET last_active_at = $1 WHERE user_id = $2",
            past_time, user_id
        )
        
        # Validation should now fail and revoke the session
        session = await validate_session(conn, token)
        assert session is None
        
        # Verify it was revoked in DB
        revoked_check = await conn.fetchval(
            "SELECT revoked FROM security_sessions WHERE user_id = $1",
            user_id
        )
        assert revoked_check is True

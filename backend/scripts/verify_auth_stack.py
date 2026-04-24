"""
Verification Script for Passkey and MFA Authentication Stack.
This script performs high-level validation of the core authentication logic
to ensure the implementation is functional.
"""

import asyncio
import uuid
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

# Add the app directory to sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.core.security.passkeys import (
    generate_registration_options,
    generate_authentication_options,
)
from app.api.v1.endpoints.mfa import create_mfa_challenge, resolve_mfa_challenge

async def test_passkey_options_generation():
    print("Testing Passkey Options Generation...")
    mock_conn = AsyncMock()
    
    # Mock user details
    user_id = str(uuid.uuid4())
    user_email = "test@talky.ai"
    user_name = "Test User"
    
    # 1. Test Registration Options
    reg_options = await generate_registration_options(
        conn=mock_conn,
        user_id=user_id,
        user_email=user_email,
        user_name=user_name,
        authenticator_type="any",
        ip_address="127.0.0.1"
    )
    
    assert reg_options.ceremony_id is not None
    assert "challenge" in reg_options.options_json
    print(f"✅ Passkey Registration Options generated. Ceremony ID: {reg_options.ceremony_id}")
    
    # 2. Test Authentication Options
    auth_options = await generate_authentication_options(
        conn=mock_conn,
        user_id=user_id,
        credential_ids=["cred1", "cred2"],
        ip_address="127.0.0.1"
    )
    
    assert auth_options.ceremony_id is not None
    assert "challenge" in auth_options.options_json
    print(f"✅ Passkey Authentication Options generated. Ceremony ID: {auth_options.ceremony_id}")

async def test_mfa_challenge_flow():
    print("\nTesting MFA Challenge Flow...")
    mock_conn = AsyncMock()
    user_id = str(uuid.uuid4())
    
    # 1. Create Challenge
    challenge_token = await create_mfa_challenge(
        conn=mock_conn,
        user_id=user_id,
        ip_address="127.0.0.1"
    )
    
    assert len(challenge_token) > 20
    print(f"✅ MFA Challenge created. Token: {challenge_token[:10]}...")
    
    # 2. Resolve Challenge (Mocking the fetchrow)
    mock_conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "ip_address": "127.0.0.1",
        "expires_at": datetime.now(timezone.utc),
        "used": False
    }
    
    resolved = await resolve_mfa_challenge(mock_conn, challenge_token)
    assert resolved is not None
    assert str(resolved["user_id"]) == user_id
    print("✅ MFA Challenge resolved correctly.")

async def main():
    print("="*50)
    print("  AUTH STACK VERIFICATION")
    print("="*50)
    
    try:
        # Generate a real Fernet key for testing
        from cryptography.fernet import Fernet
        test_key = Fernet.generate_key().decode()
        
        # Set dummy env for TOTP
        os.environ["TOTP_ENCRYPTION_KEY"] = test_key
        os.environ["TOTP_ISSUER_NAME"] = "Talky.ai"
        
        await test_passkey_options_generation()
        await test_mfa_challenge_flow()
        
        print("\n" + "="*50)
        print("  VERIFICATION SUCCESSFUL")
        print("="*50)
    except Exception as e:
        print(f"\n❌ VERIFICATION FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())

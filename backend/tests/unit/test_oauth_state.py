"""
Tests for OAuth State Manager
Day 24: Unified Connector System
"""
import pytest
import os
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import json

# Set test environment
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")


class TestOAuthStateManager:
    """Tests for OAuth state management with PKCE"""
    
    @pytest.fixture
    def mock_redis(self):
        """Mock Redis client"""
        mock = AsyncMock()
        mock.setex = AsyncMock()
        mock.get = AsyncMock()
        mock.delete = AsyncMock()
        return mock
    
    @pytest.mark.asyncio
    async def test_state_creation(self, mock_redis):
        """State is created with correct fields"""
        from app.infrastructure.connectors.oauth import OAuthStateManager
        
        manager = OAuthStateManager()
        manager._redis = mock_redis
        
        result = await manager.create_state(
            tenant_id="tenant-123",
            user_id="user-456",
            provider="google_calendar",
            redirect_uri="https://example.com/callback"
        )
        
        # Check returned data
        assert "state" in result
        assert "code_verifier" in result
        assert "code_challenge" in result
        assert result["code_challenge_method"] == "S256"
        
        # Verify Redis was called
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        
        # Check TTL
        assert call_args[0][1] == 300  # 5 minutes
        
        # Check stored data
        stored_data = json.loads(call_args[0][2])
        assert stored_data["tenant_id"] == "tenant-123"
        assert stored_data["user_id"] == "user-456"
        assert stored_data["provider"] == "google_calendar"
        assert "code_verifier" in stored_data
    
    @pytest.mark.asyncio
    async def test_state_validation_success(self, mock_redis):
        """Valid state returns stored data"""
        from app.infrastructure.connectors.oauth import OAuthStateManager
        
        manager = OAuthStateManager()
        manager._redis = mock_redis
        
        # Setup mock to return valid data
        stored_data = {
            "tenant_id": "tenant-123",
            "user_id": "user-456",
            "provider": "google_calendar",
            "redirect_uri": "https://example.com/callback",
            "code_verifier": "test_verifier_value",
            "created_at": "2024-01-01T00:00:00"
        }
        mock_redis.get.return_value = json.dumps(stored_data)
        mock_redis.delete.return_value = 1
        
        result = await manager.validate_state(
            state="test-state-uuid",
            expected_tenant_id="tenant-123"
        )
        
        assert result is not None
        assert result["tenant_id"] == "tenant-123"
        assert result["code_verifier"] == "test_verifier_value"
        
        # State should be deleted after use
        mock_redis.delete.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_state_not_found_returns_error(self, mock_redis):
        """Expired/missing state raises error"""
        from app.infrastructure.connectors.oauth import OAuthStateManager, OAuthStateError
        
        manager = OAuthStateManager()
        manager._redis = mock_redis
        mock_redis.get.return_value = None
        
        with pytest.raises(OAuthStateError) as exc_info:
            await manager.validate_state(
                state="nonexistent-state",
                expected_tenant_id="tenant-123"
            )
        
        assert "not found or expired" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_tenant_binding_enforced(self, mock_redis):
        """Wrong tenant_id fails validation"""
        from app.infrastructure.connectors.oauth import OAuthStateManager, OAuthStateError
        
        manager = OAuthStateManager()
        manager._redis = mock_redis
        
        # State belongs to different tenant
        stored_data = {
            "tenant_id": "tenant-ORIGINAL",
            "user_id": "user-456",
            "provider": "google_calendar",
            "code_verifier": "test_verifier"
        }
        mock_redis.get.return_value = json.dumps(stored_data)
        
        with pytest.raises(OAuthStateError) as exc_info:
            await manager.validate_state(
                state="test-state",
                expected_tenant_id="tenant-DIFFERENT"
            )
        
        assert "tenant mismatch" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_pkce_challenge_generation(self):
        """PKCE code_challenge is valid S256"""
        from app.infrastructure.connectors.oauth import OAuthStateManager
        import hashlib
        import base64
        
        manager = OAuthStateManager()
        
        # Generate verifier and challenge
        verifier = manager._generate_code_verifier()
        challenge = manager._generate_code_challenge(verifier)
        
        # Verify challenge format
        assert len(challenge) > 40  # S256 produces ~43 chars
        assert "=" not in challenge  # No padding
        
        # Verify challenge can be reproduced
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        assert challenge == expected
    
    @pytest.mark.asyncio
    async def test_code_verifier_format(self):
        """Code verifier meets RFC 7636 requirements"""
        from app.infrastructure.connectors.oauth import OAuthStateManager
        
        manager = OAuthStateManager()
        verifier = manager._generate_code_verifier()
        
        # RFC 7636: 43-128 characters
        assert 43 <= len(verifier) <= 128
        
        # Only unreserved characters
        import re
        assert re.match(r'^[A-Za-z0-9_-]+$', verifier)
    
    @pytest.mark.asyncio
    async def test_delete_state(self, mock_redis):
        """Explicit state deletion works"""
        from app.infrastructure.connectors.oauth import OAuthStateManager
        
        manager = OAuthStateManager()
        manager._redis = mock_redis
        mock_redis.delete.return_value = 1
        
        result = await manager.delete_state("state-to-delete")
        
        assert result is True
        mock_redis.delete.assert_called_once_with("oauth_state:state-to-delete")
    
    @pytest.mark.asyncio
    async def test_extra_data_stored(self, mock_redis):
        """Extra data is included in state"""
        from app.infrastructure.connectors.oauth import OAuthStateManager
        
        manager = OAuthStateManager()
        manager._redis = mock_redis
        
        await manager.create_state(
            tenant_id="tenant-123",
            user_id="user-456",
            provider="gmail",
            redirect_uri="https://example.com/callback",
            extra_data={"connector_id": "conn-789"}
        )
        
        # Check stored data includes extra_data
        call_args = mock_redis.setex.call_args
        stored_data = json.loads(call_args[0][2])
        assert stored_data["connector_id"] == "conn-789"

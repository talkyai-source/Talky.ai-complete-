"""
Unit tests for ReplayProtectionService
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta


class TestReplayProtectionValidateRequest:
    """Tests for ReplayProtectionService.validate_request method."""
    
    @pytest.fixture
    def mock_supabase(self):
        """Create mock Supabase client."""
        mock = MagicMock()
        return mock
    
    @pytest.mark.asyncio
    async def test_valid_request_passes(self, mock_supabase):
        """Test that a valid request passes validation."""
        from app.services.replay_protection_service import ReplayProtectionService
        
        # Mock no duplicate found
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        
        with patch('app.services.replay_protection_service.get_audit_service'):
            service = ReplayProtectionService(mock_supabase)
            
            is_valid, error = await service.validate_request(
                tenant_id="tenant-123",
                idempotency_key="unique-key-123",
                request_timestamp=datetime.utcnow()
            )
            
            assert is_valid is True
            assert error is None
    
    @pytest.mark.asyncio
    async def test_old_request_rejected(self, mock_supabase):
        """Test that old requests are rejected."""
        from app.services.replay_protection_service import ReplayProtectionService
        
        with patch('app.services.replay_protection_service.get_audit_service') as mock_audit:
            mock_audit_instance = MagicMock()
            mock_audit_instance.log_replay_attempt = AsyncMock()
            mock_audit.return_value = mock_audit_instance
            
            service = ReplayProtectionService(mock_supabase)
            
            # Request from 10 minutes ago (over 5 min limit)
            old_timestamp = datetime.utcnow() - timedelta(minutes=10)
            
            is_valid, error = await service.validate_request(
                tenant_id="tenant-123",
                idempotency_key="old-key",
                request_timestamp=old_timestamp
            )
            
            assert is_valid is False
            assert "too old" in error.lower()
    
    @pytest.mark.asyncio
    async def test_future_timestamp_rejected(self, mock_supabase):
        """Test that requests with future timestamps are rejected."""
        from app.services.replay_protection_service import ReplayProtectionService
        
        with patch('app.services.replay_protection_service.get_audit_service') as mock_audit:
            mock_audit_instance = MagicMock()
            mock_audit_instance.log_replay_attempt = AsyncMock()
            mock_audit.return_value = mock_audit_instance
            
            service = ReplayProtectionService(mock_supabase)
            
            # Request from the future
            future_timestamp = datetime.utcnow() + timedelta(minutes=5)
            
            is_valid, error = await service.validate_request(
                tenant_id="tenant-123",
                request_timestamp=future_timestamp
            )
            
            assert is_valid is False
            assert "timestamp" in error.lower()
    
    @pytest.mark.asyncio
    async def test_duplicate_key_rejected(self, mock_supabase):
        """Test that duplicate idempotency keys are rejected."""
        from app.services.replay_protection_service import ReplayProtectionService
        
        # Mock duplicate found
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"id": "existing-action-123"}
        ]
        
        with patch('app.services.replay_protection_service.get_audit_service') as mock_audit:
            mock_audit_instance = MagicMock()
            mock_audit_instance.log_replay_attempt = AsyncMock()
            mock_audit.return_value = mock_audit_instance
            
            service = ReplayProtectionService(mock_supabase)
            
            is_valid, error = await service.validate_request(
                tenant_id="tenant-123",
                idempotency_key="duplicate-key",
                request_timestamp=datetime.utcnow()
            )
            
            assert is_valid is False
            assert "duplicate" in error.lower()
    
    @pytest.mark.asyncio
    async def test_no_idempotency_key_allowed(self, mock_supabase):
        """Test that requests without idempotency key pass (backward compatible)."""
        from app.services.replay_protection_service import ReplayProtectionService
        
        with patch('app.services.replay_protection_service.get_audit_service'):
            service = ReplayProtectionService(mock_supabase)
            
            is_valid, error = await service.validate_request(
                tenant_id="tenant-123",
                idempotency_key=None,
                request_timestamp=datetime.utcnow()
            )
            
            assert is_valid is True
            assert error is None


class TestReplayProtectionIsDuplicate:
    """Tests for ReplayProtectionService.is_duplicate method."""
    
    @pytest.fixture
    def mock_supabase(self):
        """Create mock Supabase client."""
        mock = MagicMock()
        return mock
    
    @pytest.mark.asyncio
    async def test_is_duplicate_returns_true_for_existing(self, mock_supabase):
        """Test that is_duplicate returns True for existing keys."""
        from app.services.replay_protection_service import ReplayProtectionService
        
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"id": "existing-123"}
        ]
        
        with patch('app.services.replay_protection_service.get_audit_service'):
            service = ReplayProtectionService(mock_supabase)
            
            is_dup, action_id = await service.is_duplicate(
                idempotency_key="existing-key",
                tenant_id="tenant-123"
            )
            
            assert is_dup is True
            assert action_id == "existing-123"
    
    @pytest.mark.asyncio
    async def test_is_duplicate_returns_false_for_new(self, mock_supabase):
        """Test that is_duplicate returns False for new keys."""
        from app.services.replay_protection_service import ReplayProtectionService
        
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        
        with patch('app.services.replay_protection_service.get_audit_service'):
            service = ReplayProtectionService(mock_supabase)
            
            is_dup, action_id = await service.is_duplicate(
                idempotency_key="new-key",
                tenant_id="tenant-123"
            )
            
            assert is_dup is False
            assert action_id is None


class TestGenerateIdempotencyKey:
    """Tests for idempotency key generation."""
    
    def test_generate_deterministic_key(self):
        """Test that same inputs produce same key."""
        from app.services.replay_protection_service import ReplayProtectionService
        
        mock_supabase = MagicMock()
        
        with patch('app.services.replay_protection_service.get_audit_service'):
            service = ReplayProtectionService(mock_supabase)
            
            key1 = service.generate_idempotency_key("tenant-1", "send_email", "user@example.com")
            key2 = service.generate_idempotency_key("tenant-1", "send_email", "user@example.com")
            
            assert key1 == key2
    
    def test_generate_different_keys_for_different_inputs(self):
        """Test that different inputs produce different keys."""
        from app.services.replay_protection_service import ReplayProtectionService
        
        mock_supabase = MagicMock()
        
        with patch('app.services.replay_protection_service.get_audit_service'):
            service = ReplayProtectionService(mock_supabase)
            
            key1 = service.generate_idempotency_key("tenant-1", "send_email", "user@example.com")
            key2 = service.generate_idempotency_key("tenant-1", "send_email", "other@example.com")
            
            assert key1 != key2
    
    def test_generate_key_length(self):
        """Test that generated key has expected length."""
        from app.services.replay_protection_service import ReplayProtectionService
        
        mock_supabase = MagicMock()
        
        with patch('app.services.replay_protection_service.get_audit_service'):
            service = ReplayProtectionService(mock_supabase)
            
            key = service.generate_idempotency_key("some", "components", "here")
            
            assert len(key) == 32  # SHA256 truncated to 32 chars


class TestSingletonPattern:
    """Tests for singleton pattern."""
    
    def test_get_replay_protection_service_singleton(self):
        """Test that get_replay_protection_service returns same instance."""
        import app.services.replay_protection_service as replay_module
        
        # Reset singleton
        replay_module._replay_protection_service = None
        
        mock_supabase = MagicMock()
        
        with patch.object(replay_module, 'get_audit_service'):
            service1 = replay_module.get_replay_protection_service(mock_supabase)
            service2 = replay_module.get_replay_protection_service(mock_supabase)
            
            assert service1 is service2
        
        # Reset for other tests
        replay_module._replay_protection_service = None

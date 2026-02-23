"""
Unit tests for QuotaService
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import date


class TestQuotaStatus:
    """Tests for QuotaStatus dataclass."""
    
    def test_remaining_calculations(self):
        """Test remaining quota calculations."""
        from app.services.quota_service import QuotaStatus
        
        status = QuotaStatus(
            emails_limit=50,
            emails_used=30,
            sms_limit=25,
            sms_used=25,  # At limit
            calls_limit=50,
            calls_used=60,  # Over limit
            meetings_limit=10,
            meetings_used=0
        )
        
        assert status.emails_remaining == 20
        assert status.sms_remaining == 0  # At limit
        assert status.calls_remaining == 0  # Over limit (clamped to 0)
        assert status.meetings_remaining == 10
    
    def test_to_dict(self):
        """Test dictionary conversion."""
        from app.services.quota_service import QuotaStatus
        
        status = QuotaStatus(
            emails_limit=50,
            emails_used=10,
            sms_limit=25,
            sms_used=5,
            calls_limit=50,
            calls_used=0,
            meetings_limit=10,
            meetings_used=3
        )
        
        result = status.to_dict()
        
        assert result["emails"]["limit"] == 50
        assert result["emails"]["used"] == 10
        assert result["emails"]["remaining"] == 40
        assert result["sms"]["remaining"] == 20


class TestQuotaExceededError:
    """Tests for QuotaExceededError exception."""
    
    def test_error_message(self):
        """Test error message format."""
        from app.services.quota_service import QuotaExceededError
        
        error = QuotaExceededError("send_email", 50, 50)
        
        assert "send_email" in str(error)
        assert "50/50" in str(error)
        assert error.action_type == "send_email"
        assert error.limit == 50
        assert error.used == 50


class TestQuotaServiceCheckQuota:
    """Tests for QuotaService.check_quota method."""
    
    @pytest.fixture
    def mock_supabase(self):
        """Create mock Supabase client."""
        mock = MagicMock()
        return mock
    
    @pytest.mark.asyncio
    async def test_check_quota_within_limit(self, mock_supabase):
        """Test quota check when under limit."""
        from app.services.quota_service import QuotaService
        
        # Mock quota lookup
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"emails_per_day": 50, "sms_per_day": 25}
        ]
        
        # Mock usage lookup (empty = 0 usage)
        mock_usage = MagicMock()
        mock_usage.data = [{"emails_sent": 10, "sms_sent": 5}]
        
        with patch.object(QuotaService, '_get_tenant_quota', new_callable=AsyncMock) as mock_get_quota:
            with patch.object(QuotaService, '_get_today_usage', new_callable=AsyncMock) as mock_get_usage:
                mock_get_quota.return_value = {"emails_per_day": 50}
                mock_get_usage.return_value = {"emails_sent": 10}
                
                service = QuotaService(mock_supabase)
                result = await service.check_quota("tenant-123", "send_email")
                
                assert result is True
    
    @pytest.mark.asyncio
    async def test_check_quota_at_limit(self, mock_supabase):
        """Test quota check when at limit."""
        from app.services.quota_service import QuotaService
        
        with patch.object(QuotaService, '_get_tenant_quota', new_callable=AsyncMock) as mock_get_quota:
            with patch.object(QuotaService, '_get_today_usage', new_callable=AsyncMock) as mock_get_usage:
                mock_get_quota.return_value = {"emails_per_day": 50}
                mock_get_usage.return_value = {"emails_sent": 50}
                
                service = QuotaService(mock_supabase)
                result = await service.check_quota("tenant-123", "send_email")
                
                assert result is False
    
    @pytest.mark.asyncio
    async def test_check_quota_unknown_action(self, mock_supabase):
        """Test quota check for unknown action type."""
        from app.services.quota_service import QuotaService
        
        service = QuotaService(mock_supabase)
        result = await service.check_quota("tenant-123", "unknown_action")
        
        # Unknown actions should be allowed
        assert result is True


class TestQuotaServiceIncrementUsage:
    """Tests for QuotaService.increment_usage method."""
    
    @pytest.fixture
    def mock_supabase(self):
        """Create mock Supabase client."""
        mock = MagicMock()
        return mock
    
    @pytest.mark.asyncio
    async def test_increment_usage_new_record(self, mock_supabase):
        """Test incrementing usage when no record exists."""
        from app.services.quota_service import QuotaService
        
        # RPC not available, falls back to manual
        mock_supabase.rpc.side_effect = Exception("RPC not found")
        
        # No existing record
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        
        # Insert succeeds
        mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [{"id": "new-id"}]
        
        service = QuotaService(mock_supabase)
        result = await service.increment_usage("tenant-123", "send_email")
        
        assert result == 1


class TestQuotaServiceGetStatus:
    """Tests for QuotaService.get_quota_status method."""
    
    @pytest.fixture
    def mock_supabase(self):
        """Create mock Supabase client."""
        mock = MagicMock()
        return mock
    
    @pytest.mark.asyncio
    async def test_get_quota_status(self, mock_supabase):
        """Test getting full quota status."""
        from app.services.quota_service import QuotaService
        
        with patch.object(QuotaService, '_get_tenant_quota', new_callable=AsyncMock) as mock_get_quota:
            with patch.object(QuotaService, '_get_today_usage', new_callable=AsyncMock) as mock_get_usage:
                mock_get_quota.return_value = {
                    "emails_per_day": 50,
                    "sms_per_day": 25,
                    "calls_per_day": 50,
                    "meetings_per_day": 10
                }
                mock_get_usage.return_value = {
                    "emails_sent": 15,
                    "sms_sent": 5,
                    "calls_initiated": 3,
                    "meetings_booked": 1
                }
                
                service = QuotaService(mock_supabase)
                status = await service.get_quota_status("tenant-123")
                
                assert status.emails_limit == 50
                assert status.emails_used == 15
                assert status.emails_remaining == 35
                assert status.meetings_remaining == 9


class TestSingletonPattern:
    """Tests for singleton pattern."""
    
    def test_get_quota_service_singleton(self):
        """Test that get_quota_service returns same instance."""
        import app.services.quota_service as quota_module
        
        # Reset singleton
        quota_module._quota_service = None
        
        mock_supabase = MagicMock()
        
        service1 = quota_module.get_quota_service(mock_supabase)
        service2 = quota_module.get_quota_service(mock_supabase)
        
        assert service1 is service2
        
        # Reset for other tests
        quota_module._quota_service = None

"""
Unit tests for AuditService
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime


class TestOutcomeStatus:
    """Tests for OutcomeStatus enum."""
    
    def test_outcome_status_values(self):
        """Test that all expected outcome statuses exist."""
        from app.services.audit_service import OutcomeStatus
        
        assert OutcomeStatus.SUCCESS.value == "success"
        assert OutcomeStatus.FAILED.value == "failed"
        assert OutcomeStatus.QUOTA_EXCEEDED.value == "quota_exceeded"
        assert OutcomeStatus.PERMISSION_DENIED.value == "permission_denied"
        assert OutcomeStatus.REPLAY_REJECTED.value == "replay_rejected"


class TestSecurityEventType:
    """Tests for SecurityEventType enum."""
    
    def test_security_event_values(self):
        """Test that all security event types exist."""
        from app.services.audit_service import SecurityEventType
        
        assert SecurityEventType.TOKEN_REFRESH.value == "token_refresh"
        assert SecurityEventType.CONNECTOR_REVOKED.value == "connector_revoked"
        assert SecurityEventType.REPLAY_ATTEMPT.value == "replay_attempt"


class TestAuditServiceLogAction:
    """Tests for AuditService.log_action method."""
    
    @pytest.fixture
    def mock_supabase(self):
        """Create mock Supabase client."""
        mock = MagicMock()
        mock.table.return_value.insert.return_value.execute.return_value.data = [
            {"id": "action-123"}
        ]
        return mock
    
    @pytest.mark.asyncio
    async def test_log_action_success(self, mock_supabase):
        """Test logging a successful action."""
        from app.services.audit_service import AuditService
        
        service = AuditService(mock_supabase)
        action_id = await service.log_action(
            tenant_id="tenant-123",
            action_type="send_email",
            triggered_by="assistant",
            outcome_status="success",
            input_data={"to": ["user@example.com"]},
            output_data={"message_id": "msg-123"}
        )
        
        assert action_id == "action-123"
        mock_supabase.table.assert_called_with("assistant_actions")
    
    @pytest.mark.asyncio
    async def test_log_action_with_all_fields(self, mock_supabase):
        """Test logging with all optional fields."""
        from app.services.audit_service import AuditService
        
        service = AuditService(mock_supabase)
        action_id = await service.log_action(
            tenant_id="tenant-123",
            action_type="book_meeting",
            triggered_by="chat",
            outcome_status="success",
            input_data={"title": "Demo"},
            output_data={"meeting_id": "meet-123"},
            user_id="user-456",
            conversation_id="conv-789",
            lead_id="lead-111",
            ip_address="192.168.1.1",
            user_agent="Mozilla/5.0",
            request_id="req-222",
            idempotency_key="idem-333",
            duration_ms=150
        )
        
        assert action_id is not None
        
        # Verify insert was called with correct data
        insert_call = mock_supabase.table.return_value.insert.call_args
        data = insert_call[0][0]
        
        assert data["tenant_id"] == "tenant-123"
        assert data["type"] == "book_meeting"
        assert data["outcome_status"] == "success"
        assert data["idempotency_key"] == "idem-333"


class TestAuditServiceSanitizeData:
    """Tests for AuditService._sanitize_data method."""
    
    def test_sanitize_removes_sensitive_fields(self):
        """Test that sensitive fields are redacted."""
        from app.services.audit_service import AuditService
        
        mock_supabase = MagicMock()
        service = AuditService(mock_supabase)
        
        data = {
            "to": ["user@example.com"],
            "access_token": "secret123",
            "password": "secret456",
            "api_key": "key789",
            "normal_field": "visible"
        }
        
        sanitized = service._sanitize_data(data)
        
        assert sanitized["to"] == ["user@example.com"]
        assert sanitized["normal_field"] == "visible"
        assert sanitized["access_token"] == "[REDACTED]"
        assert sanitized["password"] == "[REDACTED]"
        assert sanitized["api_key"] == "[REDACTED]"
    
    def test_sanitize_nested_objects(self):
        """Test that nested objects are sanitized."""
        from app.services.audit_service import AuditService
        
        mock_supabase = MagicMock()
        service = AuditService(mock_supabase)
        
        data = {
            "config": {
                "url": "https://api.example.com",
                "secret_key": "mysecret"
            }
        }
        
        sanitized = service._sanitize_data(data)
        
        assert sanitized["config"]["url"] == "https://api.example.com"
        assert sanitized["config"]["secret_key"] == "[REDACTED]"


class TestAuditServiceSecurityEvents:
    """Tests for security event logging."""
    
    @pytest.fixture
    def mock_supabase(self):
        """Create mock Supabase client."""
        mock = MagicMock()
        mock.table.return_value.insert.return_value.execute.return_value.data = [
            {"id": "security-123"}
        ]
        return mock
    
    @pytest.mark.asyncio
    async def test_log_token_rotation(self, mock_supabase):
        """Test logging token rotation event."""
        from app.services.audit_service import AuditService
        
        service = AuditService(mock_supabase)
        action_id = await service.log_token_rotation(
            tenant_id="tenant-123",
            connector_id="conn-456",
            success=True
        )
        
        assert action_id is not None
    
    @pytest.mark.asyncio
    async def test_log_connector_revoked(self, mock_supabase):
        """Test logging connector revocation."""
        from app.services.audit_service import AuditService
        
        service = AuditService(mock_supabase)
        action_id = await service.log_connector_event(
            tenant_id="tenant-123",
            connector_id="conn-456",
            event="revoked",
            reason="user_requested",
            user_id="user-789"
        )
        
        assert action_id is not None
    
    @pytest.mark.asyncio
    async def test_log_replay_attempt(self, mock_supabase):
        """Test logging replay attempt."""
        from app.services.audit_service import AuditService
        
        service = AuditService(mock_supabase)
        action_id = await service.log_replay_attempt(
            tenant_id="tenant-123",
            idempotency_key="dup-key-123",
            action_type="send_email",
            ip_address="192.168.1.1"
        )
        
        assert action_id is not None


class TestAuditServiceGetAuditLog:
    """Tests for AuditService.get_audit_log method."""
    
    @pytest.fixture
    def mock_supabase(self):
        """Create mock Supabase client."""
        mock = MagicMock()
        mock.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value.data = [
            {"id": "action-1", "type": "send_email", "outcome_status": "success"},
            {"id": "action-2", "type": "book_meeting", "outcome_status": "failed"}
        ]
        return mock
    
    @pytest.mark.asyncio
    async def test_get_audit_log_basic(self, mock_supabase):
        """Test basic audit log query."""
        from app.services.audit_service import AuditService
        
        service = AuditService(mock_supabase)
        
        # Need to set up the mock chain properly
        query_mock = MagicMock()
        mock_supabase.table.return_value.select.return_value = query_mock
        query_mock.eq.return_value = query_mock
        query_mock.order.return_value = query_mock
        query_mock.limit.return_value = query_mock
        query_mock.execute.return_value.data = [
            {"id": "action-1", "type": "send_email", "outcome_status": "success"}
        ]
        
        logs = await service.get_audit_log(tenant_id="tenant-123")
        
        assert len(logs) == 1
        assert logs[0]["type"] == "send_email"


class TestSingletonPattern:
    """Tests for singleton pattern."""
    
    def test_get_audit_service_singleton(self):
        """Test that get_audit_service returns same instance."""
        import app.services.audit_service as audit_module
        
        # Reset singleton
        audit_module._audit_service = None
        
        mock_supabase = MagicMock()
        
        service1 = audit_module.get_audit_service(mock_supabase)
        service2 = audit_module.get_audit_service(mock_supabase)
        
        assert service1 is service2
        
        # Reset for other tests
        audit_module._audit_service = None

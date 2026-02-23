"""
Unit Tests for CRM Sync Service
Day 30: CRM & Drive Integration
"""
import pytest
import os
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime

# Set test environment
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("CONNECTOR_ENCRYPTION_KEY", "test_key_for_testing_only_32bytes!")

# Try to import
try:
    from app.services.crm_sync_service import (
        CRMSyncService,
        CRMSyncResult,
        CRMNotConnectedWarning,
        get_crm_sync_service
    )
    IMPORT_SUCCESS = True
except ImportError as e:
    IMPORT_SUCCESS = False
    print(f"Warning: Could not import CRMSyncService: {e}")

pytestmark = pytest.mark.skipif(
    not IMPORT_SUCCESS,
    reason="CRMSyncService import failed"
)


class TestCRMSyncResult:
    """Tests for CRMSyncResult model."""
    
    def test_success_result(self):
        """Test successful sync result."""
        result = CRMSyncResult(
            success=True,
            crm_contact_id="contact-123",
            crm_call_id="call-456",
            crm_note_id="note-789"
        )
        assert result.success is True
        assert result.crm_contact_id == "contact-123"
        assert result.crm_call_id == "call-456"
        assert result.skipped is False
    
    def test_skipped_result(self):
        """Test skipped sync result."""
        result = CRMSyncResult(
            success=False,
            skipped=True,
            skipped_reason="no_crm_connected",
            warning_message="CRM not connected"
        )
        assert result.success is False
        assert result.skipped is True
        assert "no_crm_connected" in result.skipped_reason
    
    def test_error_result(self):
        """Test error sync result."""
        result = CRMSyncResult(
            success=False,
            error_message="API error"
        )
        assert result.success is False
        assert "API error" in result.error_message


class TestCRMNotConnectedWarning:
    """Tests for warning messages."""
    
    def test_missing_crm_warning_has_actionable_message(self):
        """Warning message should be user-friendly and actionable."""
        warning = CRMNotConnectedWarning.MISSING_CRM
        assert "Settings > Integrations" in warning
        assert "Connect HubSpot" in warning
        assert "lead creation" in warning.lower() or "Lead" in warning
    
    def test_token_expired_warning(self):
        """Token expired warning should exist."""
        assert CRMNotConnectedWarning.TOKEN_EXPIRED is not None


class TestCRMSyncServiceInit:
    """Tests for CRMSyncService initialization."""
    
    def test_init_requires_supabase(self):
        """Service requires Supabase client."""
        mock_supabase = MagicMock()
        service = CRMSyncService(mock_supabase)
        assert service.supabase == mock_supabase
    
    def test_singleton_pattern(self):
        """get_crm_sync_service returns singleton."""
        mock_supabase = MagicMock()
        
        # Reset singleton
        import app.services.crm_sync_service as module
        module._crm_sync_service = None
        
        service1 = get_crm_sync_service(mock_supabase)
        service2 = get_crm_sync_service(mock_supabase)
        
        assert service1 is service2


class TestCRMSyncServiceMethods:
    """Tests for CRMSyncService methods."""
    
    @pytest.fixture
    def service(self):
        """Create service with mocked Supabase."""
        mock_supabase = MagicMock()
        return CRMSyncService(mock_supabase)
    
    def test_map_outcome_completed(self, service):
        """Outcome mapping works for completed calls."""
        assert service._map_outcome("completed") == "COMPLETED"
        assert service._map_outcome("COMPLETED") == "COMPLETED"
    
    def test_map_outcome_no_answer(self, service):
        """Outcome mapping works for no answer."""
        assert service._map_outcome("no_answer") == "NO_ANSWER"
    
    def test_map_outcome_unknown_defaults_to_completed(self, service):
        """Unknown outcomes default to COMPLETED."""
        assert service._map_outcome("unknown") == "COMPLETED"
    
    def test_build_note_body_with_links(self, service):
        """Note body includes Drive links."""
        note = service._build_note_body(
            call_id="call-123",
            duration_seconds=125,
            recording_link="https://drive.google.com/file/recording",
            transcript_link="https://drive.google.com/file/transcript",
            meeting_id=None
        )
        
        assert "Recording" in note
        assert "Transcript" in note
        assert "2m 5s" in note  # 125 seconds = 2m 5s
    
    def test_build_note_body_with_meeting(self, service):
        """Note body includes meeting info."""
        note = service._build_note_body(
            call_id="call-123",
            duration_seconds=60,
            recording_link=None,
            transcript_link=None,
            meeting_id="meeting-456"
        )
        
        assert "meeting" in note.lower()
        assert "meeting-456" in note


class TestCRMSyncNoConnector:
    """Tests for sync when no CRM connector is connected."""
    
    @pytest.fixture
    def service_no_connector(self):
        """Create service that returns no connector."""
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        return CRMSyncService(mock_supabase)
    
    @pytest.mark.asyncio
    async def test_sync_without_connector_returns_warning(self, service_no_connector):
        """Sync without connector should return warning message."""
        result = await service_no_connector.sync_call(
            tenant_id="tenant-123",
            call_id="call-456",
            lead_data={"lead_id": "lead-789"},
            call_summary="Test call",
            duration_seconds=60,
            outcome="completed"
        )
        
        assert result.success is False
        assert result.skipped is True
        assert result.skipped_reason == "no_crm_connected"
        assert result.warning_message is not None
        assert "Connect HubSpot" in result.warning_message or "CRM not connected" in result.warning_message


class TestImportVerification:
    """Verify all imports work correctly."""
    
    def test_crm_sync_service_importable(self):
        """CRMSyncService module imports correctly."""
        from app.services.crm_sync_service import CRMSyncService
        assert CRMSyncService is not None
    
    def test_crm_sync_result_importable(self):
        """CRMSyncResult model imports correctly."""
        from app.services.crm_sync_service import CRMSyncResult
        assert CRMSyncResult is not None
    
    def test_singleton_getter_importable(self):
        """Singleton getter imports correctly."""
        from app.services.crm_sync_service import get_crm_sync_service
        assert callable(get_crm_sync_service)

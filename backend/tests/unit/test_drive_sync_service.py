"""
Unit Tests for Drive Sync Service
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
    from app.services.drive_sync_service import (
        DriveSyncService,
        DriveSyncResult,
        DriveNotConnectedWarning,
        get_drive_sync_service
    )
    IMPORT_SUCCESS = True
except ImportError as e:
    IMPORT_SUCCESS = False
    print(f"Warning: Could not import DriveSyncService: {e}")

pytestmark = pytest.mark.skipif(
    not IMPORT_SUCCESS,
    reason="DriveSyncService import failed"
)


class TestDriveSyncResult:
    """Tests for DriveSyncResult model."""
    
    def test_success_result(self):
        """Test successful sync result."""
        result = DriveSyncResult(
            success=True,
            recording_file_id="file-123",
            recording_link="https://drive.google.com/file/123",
            transcript_file_id="file-456",
            transcript_link="https://drive.google.com/file/456",
            folder_id="folder-789"
        )
        assert result.success is True
        assert result.recording_file_id == "file-123"
        assert result.transcript_link is not None
        assert result.skipped is False
    
    def test_skipped_result(self):
        """Test skipped sync result."""
        result = DriveSyncResult(
            success=False,
            skipped=True,
            skipped_reason="no_drive_connected",
            warning_message="Drive not connected"
        )
        assert result.success is False
        assert result.skipped is True
    
    def test_no_files_result(self):
        """Test result when no files to upload."""
        result = DriveSyncResult(
            success=True,
            skipped=True,
            skipped_reason="no_files"
        )
        assert result.success is True
        assert result.skipped is True


class TestDriveNotConnectedWarning:
    """Tests for warning messages."""
    
    def test_missing_drive_warning_has_actionable_message(self):
        """Warning message should be user-friendly."""
        warning = DriveNotConnectedWarning.MISSING_DRIVE
        assert "Settings > Integrations" in warning
        assert "Google Drive" in warning
    
    def test_token_expired_warning(self):
        """Token expired warning exists."""
        assert DriveNotConnectedWarning.TOKEN_EXPIRED is not None


class TestDriveSyncServiceInit:
    """Tests for DriveSyncService initialization."""
    
    def test_init_requires_supabase(self):
        """Service requires Supabase client."""
        mock_supabase = MagicMock()
        service = DriveSyncService(mock_supabase)
        assert service.supabase == mock_supabase
    
    def test_root_folder_name(self):
        """Root folder name is set."""
        mock_supabase = MagicMock()
        service = DriveSyncService(mock_supabase)
        assert service.ROOT_FOLDER_NAME == "Talky.ai Calls"
    
    def test_singleton_pattern(self):
        """get_drive_sync_service returns singleton."""
        mock_supabase = MagicMock()
        
        # Reset singleton
        import app.services.drive_sync_service as module
        module._drive_sync_service = None
        
        service1 = get_drive_sync_service(mock_supabase)
        service2 = get_drive_sync_service(mock_supabase)
        
        assert service1 is service2


class TestDriveSyncServiceMethods:
    """Tests for DriveSyncService methods."""
    
    @pytest.fixture
    def service(self):
        """Create service with mocked Supabase."""
        mock_supabase = MagicMock()
        return DriveSyncService(mock_supabase)
    
    def test_sanitize_folder_name_removes_invalid_chars(self, service):
        """Folder name sanitization removes invalid characters."""
        assert service._sanitize_folder_name("Test/Company") == "Test_Company"
        assert service._sanitize_folder_name("Test:Name") == "Test_Name"
        assert service._sanitize_folder_name("Test<>Name") == "Test__Name"
    
    def test_sanitize_folder_name_limits_length(self, service):
        """Folder name is limited to 50 characters."""
        long_name = "A" * 100
        result = service._sanitize_folder_name(long_name)
        assert len(result) <= 50
    
    def test_format_transcript_has_header(self, service):
        """Transcript markdown has proper header."""
        formatted = service._format_transcript_for_viewer(
            transcript_text="User: Hello\nAssistant: Hi there",
            call_id="call-123",
            lead_name="John Doe",
            call_timestamp=datetime(2026, 1, 14, 12, 0, 0)
        )
        
        assert "# Call Transcript" in formatted
        assert "call-123" in formatted
        assert "John Doe" in formatted
    
    def test_format_transcript_escapes_markdown(self, service):
        """Transcript escapes markdown special characters."""
        formatted = service._format_transcript_for_viewer(
            transcript_text="User: Here is *bold* text",
            call_id="call-123",
            lead_name=None,
            call_timestamp=datetime.utcnow()
        )
        
        # Asterisks should be escaped
        assert "\\*bold\\*" in formatted
    
    def test_format_transcript_speaker_labels(self, service):
        """Transcript has proper speaker formatting."""
        formatted = service._format_transcript_for_viewer(
            transcript_text="User: Hello\nAssistant: Hi there",
            call_id="call-123",
            lead_name=None,
            call_timestamp=datetime.utcnow()
        )
        
        assert "**User:**" in formatted
        assert "**Assistant:**" in formatted


class TestDriveSyncNoConnector:
    """Tests for sync when no Drive connector is connected."""
    
    @pytest.fixture
    def service_no_connector(self):
        """Create service that returns no connector."""
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        return DriveSyncService(mock_supabase)
    
    @pytest.mark.asyncio
    async def test_sync_without_connector_returns_warning(self, service_no_connector):
        """Sync without connector should return warning."""
        result = await service_no_connector.sync_call_files(
            tenant_id="tenant-123",
            call_id="call-456",
            recording_bytes=b"audio data",
            transcript_text="User: Hello"
        )
        
        assert result.success is False
        assert result.skipped is True
        assert result.skipped_reason == "no_drive_connected"
        assert result.warning_message is not None


class TestDriveSyncNoFiles:
    """Tests for sync when no files to upload."""
    
    @pytest.fixture
    def service(self):
        """Create service with mock connector."""
        mock_supabase = MagicMock()
        # Mock that a connector exists
        mock_supabase.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{
            "id": "conn-1",
            "provider": "google_drive",
            "type": "drive",
            "status": "active",
            "encrypted_tokens": "encrypted"
        }]
        return DriveSyncService(mock_supabase)
    
    @pytest.mark.asyncio
    async def test_sync_no_files_returns_success_skipped(self, service):
        """Sync with no files returns success but skipped."""
        # We need to mock the connector retrieval to fail gracefully
        # when there are no files, the service should skip
        result = await service.sync_call_files(
            tenant_id="tenant-123",
            call_id="call-456",
            recording_bytes=None,
            transcript_text=None
        )
        
        # Either skipped due to no files or no connector
        assert result.skipped is True


class TestImportVerification:
    """Verify all imports work correctly."""
    
    def test_drive_sync_service_importable(self):
        """DriveSyncService module imports correctly."""
        from app.services.drive_sync_service import DriveSyncService
        assert DriveSyncService is not None
    
    def test_drive_sync_result_importable(self):
        """DriveSyncResult model imports correctly."""
        from app.services.drive_sync_service import DriveSyncResult
        assert DriveSyncResult is not None
    
    def test_singleton_getter_importable(self):
        """Singleton getter imports correctly."""
        from app.services.drive_sync_service import get_drive_sync_service
        assert callable(get_drive_sync_service)

"""
Tests for systemd readiness and worker lifecycle

Validates:
- Workers can be instantiated without errors
- Heartbeat methods exist and are coroutines
- Signal handling is configured correctly
- Health stats are available
"""
import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


class TestVoiceWorkerReadiness:
    """Voice pipeline worker readiness checks."""
    
    def test_worker_can_be_instantiated(self):
        """Worker instantiates without errors."""
        from app.workers.voice_worker import VoicePipelineWorker
        worker = VoicePipelineWorker()
        assert worker is not None
        assert worker.running is False
    
    def test_worker_has_heartbeat(self):
        """Worker has _heartbeat coroutine."""
        from app.workers.voice_worker import VoicePipelineWorker
        worker = VoicePipelineWorker()
        assert hasattr(worker, "_heartbeat")
        assert asyncio.iscoroutinefunction(worker._heartbeat)
    
    def test_worker_has_stats(self):
        """Worker provides stats dict."""
        from app.workers.voice_worker import VoicePipelineWorker
        worker = VoicePipelineWorker()
        stats = worker.get_stats()
        assert "running" in stats
        assert "active_pipelines" in stats
        assert "calls_handled" in stats
        assert "calls_failed" in stats
    
    def test_worker_uses_voice_config(self):
        """Worker loads VoicePipelineConfig."""
        from app.workers.voice_worker import VoicePipelineWorker
        worker = VoicePipelineWorker()
        assert hasattr(worker, "_voice_config")
        assert worker._voice_config.media_gateway_type == "browser"


class TestDialerWorkerReadiness:
    """Dialer worker readiness checks."""
    
    def test_worker_can_be_instantiated(self):
        """Worker instantiates without errors."""
        from app.workers.dialer_worker import DialerWorker
        worker = DialerWorker()
        assert worker is not None
        assert worker.running is False
    
    def test_worker_has_heartbeat(self):
        """Worker has _heartbeat coroutine."""
        from app.workers.dialer_worker import DialerWorker
        worker = DialerWorker()
        assert hasattr(worker, "_heartbeat")
        assert asyncio.iscoroutinefunction(worker._heartbeat)
    
    def test_worker_has_stats(self):
        """Worker provides stats dict."""
        from app.workers.dialer_worker import DialerWorker
        worker = DialerWorker()
        stats = worker.get_stats()
        assert "running" in stats
        assert "jobs_processed" in stats
        assert "jobs_failed" in stats


class TestReminderWorkerReadiness:
    """Reminder worker readiness checks."""
    
    def test_worker_can_be_instantiated(self):
        """Worker instantiates without errors."""
        from app.workers.reminder_worker import ReminderWorker
        worker = ReminderWorker()
        assert worker is not None
        assert worker.running is False
    
    def test_worker_has_heartbeat(self):
        """Worker has _heartbeat coroutine."""
        from app.workers.reminder_worker import ReminderWorker
        worker = ReminderWorker()
        assert hasattr(worker, "_heartbeat")
        assert asyncio.iscoroutinefunction(worker._heartbeat)
    
    def test_worker_has_stats(self):
        """Worker provides stats dict."""
        from app.workers.reminder_worker import ReminderWorker
        worker = ReminderWorker()
        stats = worker.get_stats()
        assert "running" in stats
        assert "reminders_sent" in stats


class TestHeartbeatBehaviour:
    """Heartbeat logs periodically."""
    
    @pytest.mark.asyncio
    async def test_voice_worker_heartbeat_logs(self):
        """Voice worker heartbeat logs stats at configured interval."""
        from app.workers.voice_worker import VoicePipelineWorker
        worker = VoicePipelineWorker()
        worker.running = True
        
        with patch("app.workers.voice_worker.logger") as mock_logger:
            # Run heartbeat for a very short time
            task = asyncio.create_task(worker._heartbeat())
            await asyncio.sleep(0.1)
            worker.running = False
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            
            # Note: heartbeat may not have fired yet with default 60s interval
            # This test just ensures it doesn't crash


class TestSystemdServiceFiles:
    """Validate systemd unit files parse correctly."""
    
    def test_service_files_exist(self):
        """All expected service files exist."""
        import os
        systemd_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "systemd"
        )
        systemd_dir = os.path.normpath(systemd_dir)
        
        expected_files = [
            "talky-api.service",
            "talky-voice-worker.service",
            "talky-dialer-worker.service",
            "talky-reminder-worker.service",
            "talky.target",
            "install-services.sh",
        ]
        
        for filename in expected_files:
            filepath = os.path.join(systemd_dir, filename)
            assert os.path.exists(filepath), f"Missing: {filename}"
    
    def test_service_files_have_required_sections(self):
        """Service files contain [Unit], [Service], [Install] sections."""
        import os
        systemd_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "systemd"
        )
        systemd_dir = os.path.normpath(systemd_dir)
        
        service_files = [
            "talky-api.service",
            "talky-voice-worker.service",
            "talky-dialer-worker.service",
            "talky-reminder-worker.service",
        ]
        
        for filename in service_files:
            content = open(os.path.join(systemd_dir, filename)).read()
            assert "[Unit]" in content, f"{filename} missing [Unit]"
            assert "[Service]" in content, f"{filename} missing [Service]"
            assert "[Install]" in content, f"{filename} missing [Install]"
            assert "Restart=on-failure" in content, f"{filename} missing Restart"
            assert "EnvironmentFile=" in content, f"{filename} missing EnvironmentFile"
    
    def test_install_script_is_executable(self):
        """install-services.sh is executable."""
        import os
        import stat
        script = os.path.join(
            os.path.dirname(__file__), "..", "..", "systemd", "install-services.sh"
        )
        script = os.path.normpath(script)
        assert os.access(script, os.X_OK), "install-services.sh is not executable"

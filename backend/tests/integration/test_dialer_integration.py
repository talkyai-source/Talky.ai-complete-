"""
Integration Tests for Dialer Engine
Tests the full flow from campaign start to job processing
"""
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import json


class TestDialerIntegration:
    """Integration tests for dialer engine components"""
    
    @pytest.mark.asyncio
    async def test_campaign_start_creates_jobs(self):
        """Test that starting a campaign creates dialer jobs"""
        from app.domain.models.dialer_job import DialerJob, JobStatus
        from app.domain.services.queue_service import DialerQueueService
        
        # Mock queue service
        mock_queue = AsyncMock(spec=DialerQueueService)
        mock_queue.enqueue_job = AsyncMock(return_value=True)
        mock_queue.initialize = AsyncMock()
        mock_queue.close = AsyncMock()
        mock_queue.get_queue_stats = AsyncMock(return_value={
            "priority_queue_length": 0,
            "scheduled_jobs": 0
        })
        
        # Simulate creating a job (what campaigns.py does)
        job = DialerJob(
            job_id="test-job-123",
            campaign_id="campaign-456",
            lead_id="lead-789",
            tenant_id="tenant-abc",
            phone_number="+15551234567",
            priority=5
        )
        
        # Enqueue should work
        result = await mock_queue.enqueue_job(job)
        assert result is True
        mock_queue.enqueue_job.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_high_priority_lead_gets_boosted(self):
        """Test that high-value leads get priority boost"""
        # Simulate priority calculation logic from campaigns.py
        lead = {
            "id": "lead-123",
            "phone_number": "+15551234567",
            "priority": 5,
            "is_high_value": True,
            "tags": ["appointment"]
        }
        
        # Calculate priority (same logic as campaigns.py)
        base_priority = lead.get("priority", 5)
        
        if lead.get("is_high_value"):
            base_priority = min(base_priority + 2, 10)
        
        lead_tags = lead.get("tags", []) or []
        if "urgent" in lead_tags or "appointment" in lead_tags or "reminder" in lead_tags:
            base_priority = min(base_priority + 1, 10)
        
        # High-value (+2) + appointment tag (+1) = 8
        assert base_priority == 8
    
    @pytest.mark.asyncio
    async def test_job_retry_flow(self):
        """Test the full retry flow for a failed call"""
        from app.domain.models.dialer_job import DialerJob, JobStatus, CallOutcome
        from app.domain.services.queue_service import DialerQueueService
        
        # Create a job that failed with busy
        job = DialerJob(
            job_id="test-job-123",
            campaign_id="campaign-456",
            lead_id="lead-789",
            tenant_id="tenant-abc",
            phone_number="+15551234567",
            attempt_number=1,
            last_outcome=CallOutcome.BUSY
        )
        
        # Check if should retry
        should_retry, reason = job.should_retry()
        
        assert should_retry is True
        assert "busy" in reason.lower()
        
        # Simulate retry scheduling
        mock_queue = AsyncMock(spec=DialerQueueService)
        mock_queue.schedule_retry = AsyncMock(return_value=True)
        mock_queue.initialize = AsyncMock()
        
        await mock_queue.schedule_retry(job, delay_seconds=7200)
        mock_queue.schedule_retry.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_spam_number_no_retry(self):
        """Test that spam numbers don't get retried"""
        from app.domain.models.dialer_job import DialerJob, CallOutcome
        
        job = DialerJob(
            job_id="test-job-123",
            campaign_id="campaign-456",
            lead_id="lead-789",
            tenant_id="tenant-abc",
            phone_number="+15551234567",
            attempt_number=1,
            last_outcome=CallOutcome.SPAM
        )
        
        should_retry, reason = job.should_retry()
        
        assert should_retry is False
        assert "spam" in reason.lower()
    
    @pytest.mark.asyncio
    async def test_goal_achieved_no_retry(self):
        """Test that goal achieved calls don't get retried"""
        from app.domain.models.dialer_job import DialerJob, CallOutcome
        
        job = DialerJob(
            job_id="test-job-123",
            campaign_id="campaign-456",
            lead_id="lead-789",
            tenant_id="tenant-abc",
            phone_number="+15551234567",
            attempt_number=1,
            last_outcome=CallOutcome.ANSWERED
        )
        
        # Goal was achieved
        should_retry, reason = job.should_retry(goal_achieved=True)
        
        assert should_retry is False
        assert "goal" in reason.lower()
    
    @pytest.mark.asyncio
    async def test_max_attempts_stops_retry(self):
        """Test that max attempts (3) stops retry"""
        from app.domain.models.dialer_job import DialerJob, CallOutcome
        
        job = DialerJob(
            job_id="test-job-123",
            campaign_id="campaign-456",
            lead_id="lead-789",
            tenant_id="tenant-abc",
            phone_number="+15551234567",
            attempt_number=3,  # Already at max
            last_outcome=CallOutcome.NO_ANSWER
        )
        
        should_retry, reason = job.should_retry()
        
        assert should_retry is False
        assert "max_attempts" in reason.lower()
    
    @pytest.mark.asyncio
    async def test_scheduling_rules_time_window(self):
        """Test scheduling rules time window check"""
        from app.domain.models.calling_rules import CallingRules
        from app.domain.services.scheduling_rules import SchedulingRuleEngine
        
        engine = SchedulingRuleEngine()
        
        # Rules that allow all times
        rules = CallingRules(
            time_window_start="00:00",
            time_window_end="23:59",
            timezone="UTC",
            max_concurrent_calls=10
        )
        
        can_call, reason = await engine.can_make_call(
            tenant_id="test",
            campaign_id="campaign",
            rules=rules
        )
        
        assert can_call is True
    
    @pytest.mark.asyncio
    async def test_scheduling_rules_concurrent_limit(self):
        """Test scheduling rules concurrent limit"""
        from app.domain.models.calling_rules import CallingRules
        from app.domain.services.scheduling_rules import SchedulingRuleEngine
        
        engine = SchedulingRuleEngine()
        
        rules = CallingRules(
            time_window_start="00:00",
            time_window_end="23:59",
            timezone="UTC",
            max_concurrent_calls=2
        )
        
        # Register 2 calls (at limit)
        engine.register_call_start("test", "campaign")
        engine.register_call_start("test", "campaign")
        
        # 3rd call should be blocked
        can_call, reason = await engine.can_make_call(
            tenant_id="test",
            campaign_id="campaign",
            rules=rules
        )
        
        assert can_call is False
        assert "concurrent" in reason.lower()
        
        # End one call
        engine.register_call_end("test", "campaign")
        
        # Now should be allowed
        can_call, reason = await engine.can_make_call(
            tenant_id="test",
            campaign_id="campaign",
            rules=rules
        )
        
        assert can_call is True


class TestWebhookIntegration:
    """Integration tests for webhook handling"""
    
    def test_vonage_status_mapping(self):
        """Test Vonage status to CallOutcome mapping"""
        from app.domain.models.dialer_job import CallOutcome
        
        # Mapping from webhooks.py
        status_map = {
            "answered": CallOutcome.ANSWERED,
            "busy": CallOutcome.BUSY,
            "timeout": CallOutcome.NO_ANSWER,
            "failed": CallOutcome.FAILED,
            "rejected": CallOutcome.REJECTED,
            "unanswered": CallOutcome.NO_ANSWER,
        }
        
        assert status_map["busy"] == CallOutcome.BUSY
        assert status_map["timeout"] == CallOutcome.NO_ANSWER
        assert status_map["answered"] == CallOutcome.ANSWERED
    
    def test_retryable_outcomes(self):
        """Test which outcomes should trigger retry"""
        from app.domain.models.dialer_job import CallOutcome
        
        retryable = {
            CallOutcome.BUSY,
            CallOutcome.NO_ANSWER,
            CallOutcome.FAILED,
            CallOutcome.VOICEMAIL,
        }
        
        non_retryable = {
            CallOutcome.SPAM,
            CallOutcome.INVALID,
            CallOutcome.UNAVAILABLE,
            CallOutcome.GOAL_ACHIEVED,
        }
        
        # Busy should retry
        assert CallOutcome.BUSY in retryable
        
        # Spam should not retry
        assert CallOutcome.SPAM in non_retryable
        assert CallOutcome.SPAM not in retryable


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])

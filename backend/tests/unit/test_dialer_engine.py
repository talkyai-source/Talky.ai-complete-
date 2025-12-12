"""
Unit Tests for Dialer Engine Components
Tests for dialer_job, queue_service, scheduling_rules, and retry logic
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import json

# Import models
from app.domain.models.dialer_job import DialerJob, JobStatus, CallOutcome
from app.domain.models.calling_rules import CallingRules


class TestDialerJob:
    """Tests for DialerJob model"""
    
    def test_job_creation_with_defaults(self):
        """Test creating a job with default values"""
        job = DialerJob(
            job_id="test-job-123",
            campaign_id="campaign-456",
            lead_id="lead-789",
            tenant_id="tenant-abc",
            phone_number="+15551234567"
        )
        
        assert job.job_id == "test-job-123"
        assert job.priority == 5
        assert job.status == JobStatus.PENDING
        assert job.attempt_number == 1
        assert job.last_outcome is None
    
    def test_job_creation_with_high_priority(self):
        """Test creating a high-priority job"""
        job = DialerJob(
            job_id="test-job-123",
            campaign_id="campaign-456",
            lead_id="lead-789",
            tenant_id="tenant-abc",
            phone_number="+15551234567",
            priority=9
        )
        
        assert job.priority == 9
    
    def test_job_serialization_to_redis(self):
        """Test serializing a job for Redis storage"""
        job = DialerJob(
            job_id="test-job-123",
            campaign_id="campaign-456",
            lead_id="lead-789",
            tenant_id="tenant-abc",
            phone_number="+15551234567",
            priority=7
        )
        
        redis_dict = job.to_redis_dict()
        
        assert redis_dict["job_id"] == "test-job-123"
        assert redis_dict["priority"] == 7
        assert redis_dict["status"] == "pending"
        assert "created_at" in redis_dict
    
    def test_job_deserialization_from_redis(self):
        """Test deserializing a job from Redis"""
        redis_data = {
            "job_id": "test-job-123",
            "campaign_id": "campaign-456",
            "lead_id": "lead-789",
            "tenant_id": "tenant-abc",
            "phone_number": "+15551234567",
            "priority": 8,
            "status": "processing",
            "attempt_number": 2,
            "scheduled_at": datetime.utcnow().isoformat(),
            "created_at": datetime.utcnow().isoformat()
        }
        
        job = DialerJob.from_redis_dict(redis_data)
        
        assert job.job_id == "test-job-123"
        assert job.priority == 8
        assert job.status == "processing"
        assert job.attempt_number == 2


class TestRetryLogic:
    """Tests for smart retry logic"""
    
    def test_no_retry_on_goal_achieved(self):
        """Goal achieved should never retry"""
        job = DialerJob(
            job_id="test-job",
            campaign_id="campaign",
            lead_id="lead",
            tenant_id="tenant",
            phone_number="+15551234567",
            last_outcome=CallOutcome.GOAL_ACHIEVED
        )
        
        should_retry, reason = job.should_retry(goal_achieved=True)
        
        assert should_retry is False
        assert "goal" in reason.lower()
    
    def test_no_retry_on_spam(self):
        """Spam numbers should not retry"""
        job = DialerJob(
            job_id="test-job",
            campaign_id="campaign",
            lead_id="lead",
            tenant_id="tenant",
            phone_number="+15551234567",
            last_outcome=CallOutcome.SPAM
        )
        
        should_retry, reason = job.should_retry()
        
        assert should_retry is False
        assert "spam" in reason.lower()
    
    def test_no_retry_on_invalid(self):
        """Invalid numbers should not retry"""
        job = DialerJob(
            job_id="test-job",
            campaign_id="campaign",
            lead_id="lead",
            tenant_id="tenant",
            phone_number="+15551234567",
            last_outcome=CallOutcome.INVALID
        )
        
        should_retry, reason = job.should_retry()
        
        assert should_retry is False
        assert "invalid" in reason.lower()
    
    def test_no_retry_on_unavailable(self):
        """Unavailable numbers should not retry"""
        job = DialerJob(
            job_id="test-job",
            campaign_id="campaign",
            lead_id="lead",
            tenant_id="tenant",
            phone_number="+15551234567",
            last_outcome=CallOutcome.UNAVAILABLE
        )
        
        should_retry, reason = job.should_retry()
        
        assert should_retry is False
        assert "unavailable" in reason.lower()
    
    def test_retry_on_busy(self):
        """Busy should retry"""
        job = DialerJob(
            job_id="test-job",
            campaign_id="campaign",
            lead_id="lead",
            tenant_id="tenant",
            phone_number="+15551234567",
            attempt_number=1,
            last_outcome=CallOutcome.BUSY
        )
        
        should_retry, reason = job.should_retry()
        
        assert should_retry is True
        assert "busy" in reason.lower()
    
    def test_retry_on_no_answer(self):
        """No answer should retry"""
        job = DialerJob(
            job_id="test-job",
            campaign_id="campaign",
            lead_id="lead",
            tenant_id="tenant",
            phone_number="+15551234567",
            attempt_number=1,
            last_outcome=CallOutcome.NO_ANSWER
        )
        
        should_retry, reason = job.should_retry()
        
        assert should_retry is True
        assert "no_answer" in reason.lower()
    
    def test_no_retry_on_max_attempts(self):
        """Should not retry after max attempts"""
        job = DialerJob(
            job_id="test-job",
            campaign_id="campaign",
            lead_id="lead",
            tenant_id="tenant",
            phone_number="+15551234567",
            attempt_number=3,  # Max is 3
            last_outcome=CallOutcome.BUSY
        )
        
        should_retry, reason = job.should_retry()
        
        assert should_retry is False
        assert "max_attempts" in reason.lower()
    
    def test_retry_delay_is_2_hours(self):
        """Retry delay should be 2 hours (7200 seconds)"""
        job = DialerJob(
            job_id="test-job",
            campaign_id="campaign",
            lead_id="lead",
            tenant_id="tenant",
            phone_number="+15551234567"
        )
        
        delay = job.get_retry_delay()
        
        assert delay == 7200  # 2 hours


class TestCallingRules:
    """Tests for tenant calling rules"""
    
    def test_default_rules(self):
        """Test default calling rules"""
        rules = CallingRules.default()
        
        assert rules.time_window_start == "09:00"
        assert rules.time_window_end == "19:00"
        assert rules.max_concurrent_calls == 10
        assert rules.retry_delay_seconds == 7200
        assert rules.max_retry_attempts == 3
    
    def test_custom_time_window(self):
        """Test custom time window configuration"""
        rules = CallingRules(
            time_window_start="08:00",
            time_window_end="20:00",
            timezone="America/Los_Angeles"
        )
        
        assert rules.time_window_start == "08:00"
        assert rules.time_window_end == "20:00"
        assert rules.timezone == "America/Los_Angeles"
    
    def test_custom_retry_settings(self):
        """Test custom retry settings"""
        rules = CallingRules(
            retry_delay_seconds=3600,  # 1 hour
            max_retry_attempts=5
        )
        
        assert rules.retry_delay_seconds == 3600
        assert rules.max_retry_attempts == 5
    
    def test_priority_settings(self):
        """Test priority override settings"""
        rules = CallingRules(
            enable_priority_override=True,
            high_priority_threshold=7
        )
        
        assert rules.enable_priority_override is True
        assert rules.high_priority_threshold == 7
    
    def test_time_window_check_weekday(self):
        """Test time window check for weekday during hours"""
        rules = CallingRules(
            time_window_start="09:00",
            time_window_end="17:00",
            allowed_days=[0, 1, 2, 3, 4],  # Mon-Fri
            timezone="UTC"
        )
        
        # Test during allowed hours on a weekday
        import pytz
        test_time = datetime(2024, 12, 9, 12, 0, 0)  # Monday at noon
        test_time = pytz.UTC.localize(test_time)
        
        is_allowed, reason = rules.is_within_time_window(test_time)
        
        assert is_allowed is True
    
    def test_time_window_check_weekend(self):
        """Test time window check for weekend"""
        rules = CallingRules(
            time_window_start="09:00",
            time_window_end="17:00",
            allowed_days=[0, 1, 2, 3, 4],  # Mon-Fri only
            timezone="UTC"
        )
        
        # Test on Saturday
        import pytz
        test_time = datetime(2024, 12, 14, 12, 0, 0)  # Saturday
        test_time = pytz.UTC.localize(test_time)
        
        is_allowed, reason = rules.is_within_time_window(test_time)
        
        # Saturday is day 5, not in allowed_days
        assert is_allowed is False
    
    def test_rules_to_dict(self):
        """Test converting rules to dict for database storage"""
        rules = CallingRules(
            time_window_start="10:00",
            time_window_end="18:00"
        )
        
        rules_dict = rules.to_dict()
        
        assert isinstance(rules_dict, dict)
        assert rules_dict["time_window_start"] == "10:00"
        assert rules_dict["time_window_end"] == "18:00"
    
    def test_rules_from_dict(self):
        """Test creating rules from dict (database load)"""
        rules_dict = {
            "time_window_start": "08:00",
            "time_window_end": "21:00",
            "max_concurrent_calls": 20
        }
        
        rules = CallingRules.from_dict(rules_dict)
        
        assert rules.time_window_start == "08:00"
        assert rules.time_window_end == "21:00"
        assert rules.max_concurrent_calls == 20


class TestSchedulingRules:
    """Tests for scheduling rule engine"""
    
    @pytest.mark.asyncio
    async def test_can_make_call_within_window(self):
        """Test that calls are allowed within time window"""
        from app.domain.services.scheduling_rules import SchedulingRuleEngine
        
        engine = SchedulingRuleEngine()
        rules = CallingRules(
            time_window_start="00:00",
            time_window_end="23:59",
            max_concurrent_calls=10,
            timezone="UTC"
        )
        
        can_call, reason = await engine.can_make_call(
            tenant_id="test-tenant",
            campaign_id="test-campaign",
            rules=rules
        )
        
        assert can_call is True
        assert "passed" in reason.lower()
    
    @pytest.mark.asyncio
    async def test_concurrent_limit_check(self):
        """Test concurrent call limit enforcement"""
        from app.domain.services.scheduling_rules import SchedulingRuleEngine
        
        engine = SchedulingRuleEngine()
        rules = CallingRules(
            time_window_start="00:00",
            time_window_end="23:59",
            max_concurrent_calls=1,  # Only 1 concurrent call
            timezone="UTC"
        )
        
        # Register first call
        engine.register_call_start("test-tenant", "test-campaign")
        
        # Second call should be blocked
        can_call, reason = await engine.can_make_call(
            tenant_id="test-tenant",
            campaign_id="test-campaign",
            rules=rules
        )
        
        assert can_call is False
        assert "concurrent" in reason.lower()
    
    @pytest.mark.asyncio
    async def test_lead_cooldown(self):
        """Test lead cooldown enforcement"""
        from app.domain.services.scheduling_rules import SchedulingRuleEngine
        
        engine = SchedulingRuleEngine()
        rules = CallingRules(
            time_window_start="00:00",
            time_window_end="23:59",
            min_hours_between_calls=2,
            timezone="UTC"
        )
        
        # Lead was called 1 hour ago
        last_called = datetime.utcnow() - timedelta(hours=1)
        
        can_call, reason = await engine.can_make_call(
            tenant_id="test-tenant",
            campaign_id="test-campaign",
            rules=rules,
            lead_last_called=last_called
        )
        
        assert can_call is False
        assert "cooldown" in reason.lower()


class TestQueueService:
    """Tests for queue service"""
    
    @pytest.mark.asyncio
    async def test_enqueue_high_priority_job(self):
        """Test that high priority jobs go to priority queue"""
        from app.domain.services.queue_service import DialerQueueService
        
        # Create mock Redis
        mock_redis = AsyncMock()
        mock_redis.lpush = AsyncMock()
        mock_redis.rpush = AsyncMock()
        mock_redis.hincrby = AsyncMock()
        
        service = DialerQueueService(redis_client=mock_redis)
        service._initialized = True
        
        job = DialerJob(
            job_id="test-job",
            campaign_id="campaign",
            lead_id="lead",
            tenant_id="tenant",
            phone_number="+15551234567",
            priority=9  # High priority
        )
        
        await service.enqueue_job(job)
        
        # Should use lpush to priority queue
        mock_redis.lpush.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_enqueue_normal_priority_job(self):
        """Test that normal priority jobs go to tenant queue"""
        from app.domain.services.queue_service import DialerQueueService
        
        # Create mock Redis
        mock_redis = AsyncMock()
        mock_redis.lpush = AsyncMock()
        mock_redis.rpush = AsyncMock()
        mock_redis.hincrby = AsyncMock()
        
        service = DialerQueueService(redis_client=mock_redis)
        service._initialized = True
        
        job = DialerJob(
            job_id="test-job",
            campaign_id="campaign",
            lead_id="lead",
            tenant_id="tenant",
            phone_number="+15551234567",
            priority=5  # Normal priority
        )
        
        await service.enqueue_job(job)
        
        # Should use rpush to tenant queue
        mock_redis.rpush.assert_called_once()


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])

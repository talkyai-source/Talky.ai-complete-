"""
Day 18 Unit Tests
Tests for retention config and signed URL endpoint with plan-based access control.
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# Import the retention config functions
from app.domain.models.retention_config import (
    RetentionConfig,
    RetentionPeriod,
    get_retention_config_for_plan,
    is_recording_accessible,
    PLAN_RETENTION_DEFAULTS
)


class TestRetentionConfig:
    """Test RetentionConfig model and plan-based settings."""
    
    def test_default_config_values(self):
        """Test default retention config values."""
        config = RetentionConfig()
        assert config.recording_retention_days == 90
        assert config.recording_enabled is True
        assert config.transcript_retention_days == 365
        assert config.transcript_enabled is True
    
    def test_basic_plan_config(self):
        """Test Basic plan has 30-day recording retention."""
        config = get_retention_config_for_plan("basic")
        assert config.recording_retention_days == 30
        assert config.plan_id == "basic"
        assert config.plan_name == "Basic"
    
    def test_professional_plan_config(self):
        """Test Professional plan has 90-day recording retention."""
        config = get_retention_config_for_plan("professional")
        assert config.recording_retention_days == 90
        assert config.plan_id == "professional"
        assert config.plan_name == "Professional"
    
    def test_enterprise_plan_config(self):
        """Test Enterprise plan has 365-day recording retention."""
        config = get_retention_config_for_plan("enterprise")
        assert config.recording_retention_days == 365
        assert config.plan_id == "enterprise"
        assert config.plan_name == "Enterprise"
    
    def test_unknown_plan_defaults_to_basic(self):
        """Test that unknown plan IDs default to Basic (most restrictive)."""
        config = get_retention_config_for_plan("unknown_plan")
        assert config.recording_retention_days == 30
        assert config.plan_id == "basic"
    
    def test_none_plan_defaults_to_basic(self):
        """Test that None plan defaults to Basic."""
        config = get_retention_config_for_plan(None)
        assert config.recording_retention_days == 30
    
    def test_case_insensitive_plan_lookup(self):
        """Test plan lookup is case-insensitive."""
        config = get_retention_config_for_plan("PROFESSIONAL")
        assert config.recording_retention_days == 90


class TestRecordingAccessibility:
    """Test is_recording_accessible function."""
    
    def test_basic_plan_recording_within_retention(self):
        """Test Basic plan can access 25-day old recording."""
        assert is_recording_accessible("basic", 25) is True
    
    def test_basic_plan_recording_at_limit(self):
        """Test Basic plan can access 30-day old recording (at limit)."""
        assert is_recording_accessible("basic", 30) is True
    
    def test_basic_plan_recording_exceeds_retention(self):
        """Test Basic plan cannot access 31-day old recording."""
        assert is_recording_accessible("basic", 31) is False
    
    def test_professional_plan_recording_within_retention(self):
        """Test Professional plan can access 85-day old recording."""
        assert is_recording_accessible("professional", 85) is True
    
    def test_professional_plan_recording_exceeds_retention(self):
        """Test Professional plan cannot access 91-day old recording."""
        assert is_recording_accessible("professional", 91) is False
    
    def test_enterprise_plan_recording_long_retention(self):
        """Test Enterprise plan can access 300-day old recording."""
        assert is_recording_accessible("enterprise", 300) is True
    
    def test_enterprise_plan_recording_exceeds_retention(self):
        """Test Enterprise plan cannot access 400-day old recording."""
        assert is_recording_accessible("enterprise", 400) is False
    
    def test_new_recording_always_accessible(self):
        """Test that brand new recordings (0 days) are always accessible."""
        assert is_recording_accessible("basic", 0) is True
        assert is_recording_accessible("professional", 0) is True
        assert is_recording_accessible("enterprise", 0) is True


class TestRetentionPeriodEnum:
    """Test RetentionPeriod enum values."""
    
    def test_retention_period_values(self):
        """Test all retention period enum values exist."""
        assert RetentionPeriod.DAYS_30.value == "30d"
        assert RetentionPeriod.DAYS_90.value == "90d"
        assert RetentionPeriod.DAYS_180.value == "180d"
        assert RetentionPeriod.DAYS_365.value == "365d"
        assert RetentionPeriod.FOREVER.value == "forever"


class TestPlanRetentionDefaults:
    """Test PLAN_RETENTION_DEFAULTS dictionary."""
    
    def test_all_plans_have_defaults(self):
        """Test that all standard plans have default configs."""
        assert "basic" in PLAN_RETENTION_DEFAULTS
        assert "professional" in PLAN_RETENTION_DEFAULTS
        assert "enterprise" in PLAN_RETENTION_DEFAULTS
    
    def test_plan_configs_are_valid(self):
        """Test all plan configs are valid RetentionConfig instances."""
        for plan_id, config in PLAN_RETENTION_DEFAULTS.items():
            assert isinstance(config, RetentionConfig)
            assert config.recording_retention_days > 0
            assert config.recording_enabled is True
    
    def test_retention_increases_with_plan_tier(self):
        """Test that higher tier plans have longer retention."""
        basic = PLAN_RETENTION_DEFAULTS["basic"]
        professional = PLAN_RETENTION_DEFAULTS["professional"]
        enterprise = PLAN_RETENTION_DEFAULTS["enterprise"]
        
        assert basic.recording_retention_days < professional.recording_retention_days
        assert professional.recording_retention_days < enterprise.recording_retention_days

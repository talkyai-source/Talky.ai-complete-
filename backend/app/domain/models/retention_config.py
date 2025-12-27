"""
Retention Configuration Model
Plan-based recording and transcript retention settings

CRITICAL: Recording availability is determined by the user's purchased plan.
- Basic: 30-day recording retention
- Professional: 90-day recording retention  
- Enterprise: 365-day recording retention (or unlimited)
"""
from pydantic import BaseModel
from enum import Enum
from typing import Optional, Dict


class RetentionPeriod(str, Enum):
    """Recording/transcript retention periods based on plan tier."""
    DAYS_30 = "30d"     # Basic plan
    DAYS_90 = "90d"     # Professional plan
    DAYS_180 = "180d"   # Custom tier
    DAYS_365 = "365d"   # Enterprise plan
    FOREVER = "forever" # Enterprise with unlimited option


# Plan-based retention defaults (CRITICAL - maps to purchased plan)
PLAN_RETENTION_DEFAULTS: Dict[str, "RetentionConfig"] = {}


class RetentionConfig(BaseModel):
    """
    Per-tenant retention settings based on purchased plan.
    
    IMPORTANT: These settings are determined by the tenant's plan_id.
    Do not allow users to exceed their plan's retention limits.
    """
    # Recording settings
    recording_retention_days: int = 90
    recording_enabled: bool = True
    
    # Transcript settings
    transcript_retention_days: int = 365
    transcript_enabled: bool = True
    
    # Storage settings
    max_recording_size_mb: int = 50  # Max single recording size
    compression_enabled: bool = True
    
    # Plan metadata (for display/enforcement)
    plan_id: Optional[str] = None
    plan_name: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "recording_retention_days": 90,
                "recording_enabled": True,
                "transcript_retention_days": 365,
                "transcript_enabled": True,
                "max_recording_size_mb": 50,
                "compression_enabled": True,
                "plan_id": "professional",
                "plan_name": "Professional"
            }
        }


# Define plan-specific defaults
PLAN_RETENTION_DEFAULTS = {
    "basic": RetentionConfig(
        recording_retention_days=30,
        recording_enabled=True,
        transcript_retention_days=90,
        transcript_enabled=True,
        max_recording_size_mb=25,
        compression_enabled=True,
        plan_id="basic",
        plan_name="Basic"
    ),
    "professional": RetentionConfig(
        recording_retention_days=90,
        recording_enabled=True,
        transcript_retention_days=365,
        transcript_enabled=True,
        max_recording_size_mb=50,
        compression_enabled=True,
        plan_id="professional",
        plan_name="Professional"
    ),
    "enterprise": RetentionConfig(
        recording_retention_days=365,
        recording_enabled=True,
        transcript_retention_days=365,  # Same as recording for enterprise
        transcript_enabled=True,
        max_recording_size_mb=100,
        compression_enabled=True,
        plan_id="enterprise",
        plan_name="Enterprise"
    )
}


def get_retention_config_for_plan(plan_id: str) -> RetentionConfig:
    """
    Get retention configuration for a specific plan.
    
    CRITICAL: Always use this function to get retention settings.
    Never hardcode retention values - they depend on purchased plan.
    
    Args:
        plan_id: The plan ID from tenant's plan_id field
        
    Returns:
        RetentionConfig for the plan, or basic tier defaults if unknown
    """
    return PLAN_RETENTION_DEFAULTS.get(
        plan_id.lower() if plan_id else "basic",
        PLAN_RETENTION_DEFAULTS["basic"]
    )


def is_recording_accessible(
    plan_id: str,
    recording_age_days: float
) -> bool:
    """
    Check if a recording is still accessible based on plan retention.
    
    CRITICAL: Recordings beyond retention period should NOT be accessible.
    This is enforced before serving signed URLs.
    
    Args:
        plan_id: Tenant's plan ID
        recording_age_days: Age of recording in days
        
    Returns:
        True if recording is within retention period, False otherwise
    """
    config = get_retention_config_for_plan(plan_id)
    
    if not config.recording_enabled:
        return False
    
    # Forever retention (enterprise option)
    if config.recording_retention_days <= 0:
        return True
        
    return recording_age_days <= config.recording_retention_days

"""
Calling Rules Model
Tenant-configurable rules for outbound calling
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, time
import pytz


class CallingRules(BaseModel):
    """
    Tenant-configurable calling rules.
    
    Stored in the tenants table as JSONB in the calling_rules column.
    Users can configure their own time windows, limits, and retry settings.
    """
    
    # Time Window Configuration
    time_window_start: str = Field(
        default="09:00",
        description="Start time for calling (HH:MM format)"
    )
    time_window_end: str = Field(
        default="19:00",
        description="End time for calling (HH:MM format)"
    )
    timezone: str = Field(
        default="America/New_York",
        description="Timezone for time window (e.g., 'America/New_York', 'UTC')"
    )
    allowed_days: List[int] = Field(
        default=[0, 1, 2, 3, 4],
        description="Days when calling is allowed (0=Monday, 6=Sunday)"
    )
    
    # Concurrency Limits
    max_concurrent_calls: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum concurrent calls for this tenant"
    )
    
    # Retry Settings
    retry_delay_seconds: int = Field(
        default=7200,  # 2 hours
        ge=1800,       # Minimum 30 minutes
        le=86400,      # Maximum 24 hours
        description="Delay between retry attempts (seconds)"
    )
    max_retry_attempts: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Maximum retry attempts per lead"
    )
    
    # Priority Settings
    enable_priority_override: bool = Field(
        default=True,
        description="Allow high-priority jobs to skip FIFO queue"
    )
    high_priority_threshold: int = Field(
        default=8,
        ge=1,
        le=10,
        description="Priority level (1-10) that triggers priority queue"
    )
    
    # Lead Filters
    skip_dnc: bool = Field(
        default=True,
        description="Skip leads on Do Not Call list"
    )
    min_hours_between_calls: int = Field(
        default=2,
        ge=1,
        le=24,
        description="Minimum hours between calls to same lead"
    )
    
    # Caller ID
    caller_id: Optional[str] = Field(
        default=None,
        description="Default caller ID for this tenant"
    )
    
    def is_within_time_window(self, check_time: Optional[datetime] = None) -> tuple[bool, str]:
        """
        Check if current time is within the calling window.
        
        Args:
            check_time: Time to check (default: now)
            
        Returns:
            (is_allowed, reason)
        """
        try:
            tz = pytz.timezone(self.timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            tz = pytz.UTC
        
        if check_time is None:
            check_time = datetime.now(tz)
        elif check_time.tzinfo is None:
            check_time = tz.localize(check_time)
        else:
            check_time = check_time.astimezone(tz)
        
        # Check day of week (0=Monday)
        current_day = check_time.weekday()
        if current_day not in self.allowed_days:
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            return False, f"calling_not_allowed_on_{day_names[current_day]}"
        
        # Parse time window
        try:
            start_hour, start_min = map(int, self.time_window_start.split(":"))
            end_hour, end_min = map(int, self.time_window_end.split(":"))
        except ValueError:
            return True, "invalid_time_format_default_allow"
        
        start_time = time(start_hour, start_min)
        end_time = time(end_hour, end_min)
        current_time = check_time.time()
        
        # Check if within window
        if start_time <= current_time <= end_time:
            return True, "within_time_window"
        else:
            return False, f"outside_time_window_{self.time_window_start}_{self.time_window_end}"
    
    def get_next_window_start(self, from_time: Optional[datetime] = None) -> datetime:
        """
        Get the next time the calling window opens.
        
        Args:
            from_time: Start checking from this time (default: now)
            
        Returns:
            datetime when next window opens
        """
        try:
            tz = pytz.timezone(self.timezone)
        except pytz.exceptions.UnknownTimeZoneError:
            tz = pytz.UTC
        
        if from_time is None:
            from_time = datetime.now(tz)
        elif from_time.tzinfo is None:
            from_time = tz.localize(from_time)
        else:
            from_time = from_time.astimezone(tz)
        
        start_hour, start_min = map(int, self.time_window_start.split(":"))
        
        # Check if we can call today
        today_start = from_time.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
        
        if from_time.weekday() in self.allowed_days and from_time < today_start:
            return today_start
        
        # Find next allowed day
        check_date = from_time.date()
        for _ in range(7):
            from datetime import timedelta
            check_date = check_date + timedelta(days=1)
            if check_date.weekday() in self.allowed_days:
                next_window = datetime.combine(check_date, time(start_hour, start_min))
                return tz.localize(next_window)
        
        # Fallback: tomorrow at start time
        from datetime import timedelta
        return today_start + timedelta(days=1)
    
    @classmethod
    def default(cls) -> "CallingRules":
        """Create default calling rules."""
        return cls()
    
    def to_dict(self) -> dict:
        """Convert to dictionary for database storage."""
        return self.model_dump()
    
    @classmethod
    def from_dict(cls, data: dict) -> "CallingRules":
        """Create from dictionary (database load)."""
        if data is None:
            return cls.default()
        return cls(**data)

"""
Calling Rules Model
Tenant-configurable rules for outbound calling
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, time, timedelta
import pytz


def _parse_clock(value, *, allow_end_of_day: bool = False) -> Optional[time]:
    """Parse a strict 24h ``HH:MM`` into a ``time``; ``None`` when unreadable.

    ``"24:00"`` is accepted only as the END of a window (``allow_end_of_day``)
    and means the end of the day, ``time.max`` -- ``datetime.time`` itself
    cannot represent it.
    """
    try:
        hour_text, minute_text = str(value).strip().split(":")
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, TypeError):
        return None
    if allow_end_of_day and (hour, minute) == (24, 0):
        return time.max
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return time(hour, minute)
    return None


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
        default="UTC",
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
        default=0,
        ge=0,
        le=24,
        description="Minimum hours between calls to same lead (0 = no cooldown)"
    )
    max_calls_per_lead_per_day: int = Field(
        default=0,
        ge=0,
        le=20,
        description=(
            "Hard ceiling on dial attempts to the same lead within one "
            "calendar day (tenant timezone). Defense-in-depth above the "
            "per-disposition caps: even if busy/no-answer retries stack up, "
            "a lead is never dialled more than this many times per day. "
            "0 = disabled (rely on per-disposition caps only)."
        )
    )

    # Caller ID
    caller_id: Optional[str] = Field(
        default=None,
        description="Default caller ID for this tenant"
    )
    
    def _resolve_tz(self, tz_override: Optional[str] = None):
        """Pick the timezone to evaluate the window in: the per-lead
        override (Phase 3c — derived from the prospect's phone number)
        when supplied and valid, otherwise the tenant's configured tz,
        falling back to UTC. Keeps TCPA windows anchored to the person
        being called, not the account's home timezone."""
        for candidate in (tz_override, self.timezone):
            if not candidate:
                continue
            try:
                return pytz.timezone(candidate)
            except pytz.exceptions.UnknownTimeZoneError:
                continue
        return pytz.UTC

    def _has_resolvable_tz(self, tz_override: Optional[str] = None) -> bool:
        """False only when a zone was configured but none of them is known."""
        configured = [c for c in (tz_override, self.timezone) if c]
        if not configured:
            return True
        for candidate in configured:
            try:
                pytz.timezone(candidate)
                return True
            except pytz.exceptions.UnknownTimeZoneError:
                continue
        return False

    def is_within_time_window(
        self,
        check_time: Optional[datetime] = None,
        tz_override: Optional[str] = None,
    ) -> tuple[bool, str]:
        """
        Check if current time is within the calling window.

        Args:
            check_time: Time to check (default: now)
            tz_override: per-lead IANA timezone; when set, the window is
                evaluated in the prospect's local time instead of the
                tenant's configured timezone.

        Returns:
            (is_allowed, reason)

        An unreadable window fails CLOSED (``invalid_time_window``). So does a
        window whose start is after its end (22:00-06:00): consumer calling
        hours never span midnight, and treating it as "overnight" would turn a
        12-hour-clock slip such as 09:00-05:00 into 20 hours of dialing a day.
        ``"24:00"`` is the end of the day.
        """
        tz = self._resolve_tz(tz_override)
        if not self._has_resolvable_tz(tz_override):
            # A typo'd zone must not silently become UTC and open the line at
            # the wrong local hour. Fail closed, like invalid_time_window.
            return False, "invalid_timezone"

        if check_time is None:
            check_time = datetime.now(tz)
        elif check_time.tzinfo is None:
            check_time = tz.localize(check_time)
        else:
            check_time = check_time.astimezone(tz)

        # Parse time window. Fail closed: a typo must not open the line.
        start_time = _parse_clock(self.time_window_start)
        end_time = _parse_clock(self.time_window_end, allow_end_of_day=True)
        if start_time is None or end_time is None or start_time > end_time:
            return False, "invalid_time_window"

        # Check day of week (0=Monday)
        current_day = check_time.weekday()
        if current_day not in self.allowed_days:
            day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            return False, f"calling_not_allowed_on_{day_names[current_day]}"

        # Check if within window
        current_time = check_time.time()
        if start_time <= current_time <= end_time:
            return True, "within_time_window"
        else:
            return False, f"outside_time_window_{self.time_window_start}_{self.time_window_end}"
    
    def get_next_window_start(
        self,
        from_time: Optional[datetime] = None,
        tz_override: Optional[str] = None,
    ) -> datetime:
        """
        Get the next time the calling window opens.

        Args:
            from_time: Start checking from this time (default: now)
            tz_override: per-lead IANA timezone (see is_within_time_window).

        Returns:
            datetime when next window opens
        """
        tz = self._resolve_tz(tz_override)

        if from_time is None:
            from_time = datetime.now(tz)
        elif from_time.tzinfo is None:
            from_time = tz.localize(from_time)
        else:
            from_time = from_time.astimezone(tz)
        
        start = _parse_clock(self.time_window_start)
        if start is None:
            # No readable opening time (is_within_time_window reports it as
            # invalid_time_window and keeps the line closed): there is no
            # window to wait for, so re-check in an hour rather than raise.
            return from_time + timedelta(hours=1)
        start_hour, start_min = start.hour, start.minute

        # Check if we can call today
        today_start = from_time.replace(hour=start_hour, minute=start_min, second=0, microsecond=0)
        
        if from_time.weekday() in self.allowed_days and from_time < today_start:
            return today_start
        
        # Find next allowed day
        check_date = from_time.date()
        for _ in range(7):
            check_date = check_date + timedelta(days=1)
            if check_date.weekday() in self.allowed_days:
                next_window = datetime.combine(check_date, time(start_hour, start_min))
                return tz.localize(next_window)

        # Fallback: tomorrow at start time
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

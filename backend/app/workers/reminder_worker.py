"""
Reminder Worker
Background worker for processing scheduled reminders (SMS/Email).

Run as separate process:
    python -m app.workers.reminder_worker

Day 27: Timed Communication System
"""
import asyncio
import logging
import os
import signal
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

try:
    from supabase import create_client, Client
except ImportError as e:
    raise ImportError(f"Required dependency not installed: {e}")

logger = logging.getLogger(__name__)

# Configure logging for worker
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


class ReminderWorker:
    """
    Background worker for processing scheduled reminders.
    
    Responsibilities:
    - Scan for pending reminders due to be sent
    - Send SMS if lead has phone number, email otherwise
    - Handle retries with exponential backoff
    - Enforce idempotency (no duplicate sends)
    
    Follows the pattern from DialerWorker.
    """
    
    # Worker configuration
    POLL_INTERVAL = 30.0  # Seconds between queue scans
    MAX_CONSECUTIVE_ERRORS = 10
    BATCH_SIZE = 50  # Max reminders to process per scan
    
    # Retry configuration
    MAX_RETRIES = 3
    RETRY_BACKOFF_MULTIPLIER = 2  # Exponential backoff
    INITIAL_RETRY_DELAY = 60  # 1 minute
    
    def __init__(self):
        self.running = False
        self._supabase: Optional[Client] = None
        self._sms_service = None
        self._email_service = None
        
        # Stats
        self._reminders_sent = 0
        self._reminders_failed = 0
        self._emails_sent = 0
    
    async def initialize(self) -> None:
        """Initialize connections and services."""
        logger.info("Initializing Reminder Worker...")
        
        # Initialize Supabase client
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        
        if not supabase_url or not supabase_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        
        self._supabase = create_client(supabase_url, supabase_key)
        
        # Initialize services (lazy import to avoid circular deps)
        from app.services.sms_service import get_sms_service
        from app.services.email_service import get_email_service
        
        self._sms_service = get_sms_service(self._supabase)
        self._email_service = get_email_service(self._supabase)
        
        logger.info("Reminder Worker initialized successfully")
    
    async def run(self) -> None:
        """
        Main worker loop.
        
        Continuously:
        1. Fetch pending reminders due to be sent
        2. Process each reminder (SMS or Email)
        3. Handle errors and schedule retries
        """
        await self.initialize()
        
        self.running = True
        consecutive_errors = 0
        
        logger.info("Reminder Worker started - scanning for due reminders")
        
        while self.running:
            try:
                # Process due reminders
                processed = await self._process_due_reminders()
                
                if processed > 0:
                    logger.info(f"Processed {processed} reminders")
                    consecutive_errors = 0
                
                # Wait before next scan
                await asyncio.sleep(self.POLL_INTERVAL)
                
            except asyncio.CancelledError:
                logger.info("Worker received cancellation signal")
                break
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Worker error ({consecutive_errors}): {e}", exc_info=True)
                
                if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                    logger.critical("Too many consecutive errors, stopping worker")
                    break
                
                await asyncio.sleep(min(5 * consecutive_errors, 60))
        
        await self.shutdown()
    
    async def _process_due_reminders(self) -> int:
        """
        Fetch and process all due reminders.
        
        Returns:
            Number of reminders processed
        """
        # Fetch pending reminders that are due
        now = datetime.utcnow()
        
        response = self._supabase.table("reminders").select(
            "*, meetings(id, title, start_time, end_time, join_link, tenant_id), "
            "leads(id, first_name, last_name, phone_number, email)"
        ).eq("status", "pending").lte("scheduled_at", now.isoformat()).limit(
            self.BATCH_SIZE
        ).execute()
        
        reminders = response.data or []
        
        if not reminders:
            return 0
        
        logger.info(f"Found {len(reminders)} due reminders")
        
        processed = 0
        for reminder in reminders:
            try:
                await self._process_reminder(reminder)
                processed += 1
            except Exception as e:
                logger.error(f"Failed to process reminder {reminder['id']}: {e}")
        
        return processed
    
    async def _process_reminder(self, reminder: Dict[str, Any]) -> None:
        """
        Process a single reminder.
        
        Decision logic:
        1. If lead has phone_number → send SMS
        2. If no phone but has email → send Email
        3. If neither → mark as failed
        """
        reminder_id = reminder["id"]
        tenant_id = reminder.get("tenant_id")
        meeting = reminder.get("meetings") or {}
        lead = reminder.get("leads") or {}
        
        # Extract contact info
        phone_number = lead.get("phone_number")
        email = lead.get("email")
        lead_name = f"{lead.get('first_name', '')} {lead.get('last_name', '')}".strip() or "there"
        lead_id = lead.get("id")
        meeting_id = meeting.get("id")
        
        # Get meeting details
        meeting_title = meeting.get("title", "Your meeting")
        start_time = meeting.get("start_time")
        join_link = meeting.get("join_link")
        
        # Determine reminder type from content or timing
        reminder_type = self._determine_reminder_type(reminder)
        
        # Format time for display
        time_str = self._format_time(start_time) if start_time else "soon"
        
        # Generate idempotency key
        idempotency_key = reminder.get("idempotency_key") or f"reminder-{reminder_id}"
        
        logger.info(f"Processing reminder {reminder_id}: {reminder_type} for {meeting_title}")
        
        # Mark as processing
        self._supabase.table("reminders").update({
            "status": "processing",
            "idempotency_key": idempotency_key
        }).eq("id", reminder_id).execute()
        
        success = False
        channel = None
        external_message_id = None
        error = None
        
        try:
            # Try SMS first if phone number exists
            if phone_number:
                channel = "sms"
                result = await self._sms_service.send_meeting_reminder(
                    tenant_id=tenant_id,
                    to_number=phone_number,
                    reminder_type=reminder_type,
                    name=lead_name,
                    title=meeting_title,
                    time=time_str,
                    join_link=join_link,
                    lead_id=lead_id,
                    meeting_id=meeting_id,
                    reminder_id=reminder_id,
                    idempotency_key=idempotency_key
                )
                
                success = result.get("success", False)
                external_message_id = result.get("message_id")
                if not success:
                    error = result.get("error")
            
            # Fall back to email if no phone or SMS failed
            elif email:
                channel = "email"
                result = await self._send_email_reminder(
                    tenant_id=tenant_id,
                    to_email=email,
                    reminder_type=reminder_type,
                    name=lead_name,
                    title=meeting_title,
                    time=time_str,
                    join_link=join_link,
                    lead_id=lead_id,
                    meeting_id=meeting_id
                )
                
                success = result.get("success", False)
                external_message_id = result.get("message_id")
                if not success:
                    error = result.get("error")
            
            else:
                error = "No phone number or email available for lead"
                logger.warning(f"Reminder {reminder_id}: {error}")
        
        except Exception as e:
            error = str(e)
            logger.error(f"Exception processing reminder {reminder_id}: {e}")
        
        # Update reminder status
        if success:
            self._supabase.table("reminders").update({
                "status": "sent",
                "sent_at": datetime.utcnow().isoformat(),
                "channel": channel,
                "external_message_id": external_message_id
            }).eq("id", reminder_id).execute()
            
            self._reminders_sent += 1
            if channel == "email":
                self._emails_sent += 1
            
            logger.info(f"Reminder {reminder_id} sent successfully via {channel}")
        
        else:
            # Handle retry
            retry_count = reminder.get("retry_count", 0) + 1
            max_retries = reminder.get("max_retries", self.MAX_RETRIES)
            
            if retry_count < max_retries:
                # Schedule retry with exponential backoff
                delay = self.INITIAL_RETRY_DELAY * (self.RETRY_BACKOFF_MULTIPLIER ** (retry_count - 1))
                next_retry = datetime.utcnow() + timedelta(seconds=delay)
                
                self._supabase.table("reminders").update({
                    "status": "pending",  # Back to pending for retry
                    "retry_count": retry_count,
                    "next_retry_at": next_retry.isoformat(),
                    "last_error": error,
                    "scheduled_at": next_retry.isoformat()  # Reschedule
                }).eq("id", reminder_id).execute()
                
                logger.info(f"Reminder {reminder_id} scheduled for retry {retry_count}/{max_retries} at {next_retry}")
            
            else:
                # Max retries exceeded, mark as failed
                self._supabase.table("reminders").update({
                    "status": "failed",
                    "retry_count": retry_count,
                    "last_error": error
                }).eq("id", reminder_id).execute()
                
                self._reminders_failed += 1
                logger.error(f"Reminder {reminder_id} failed after {retry_count} attempts: {error}")
    
    def _determine_reminder_type(self, reminder: Dict[str, Any]) -> str:
        """Determine reminder type (24h, 1h, 10m) from content or context."""
        content = reminder.get("content") or {}
        
        # Check if type is stored in content
        if "reminder_type" in content:
            return content["reminder_type"]
        
        # Infer from template name
        template = content.get("template", "")
        if "24h" in template:
            return "24h"
        elif "1h" in template:
            return "1h"
        elif "10m" in template:
            return "10m"
        
        # Default to 1h
        return "1h"
    
    def _format_time(self, iso_time: str) -> str:
        """Format ISO time string for display."""
        try:
            dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
            return dt.strftime("%I:%M %p")
        except Exception:
            return iso_time
    
    async def _send_email_reminder(
        self,
        tenant_id: str,
        to_email: str,
        reminder_type: str,
        name: str,
        title: str,
        time: str,
        join_link: Optional[str],
        lead_id: Optional[str],
        meeting_id: Optional[str]
    ) -> Dict[str, Any]:
        """Send email reminder using EmailService."""
        # Map reminder type to email template
        template_map = {
            "24h": "reminder",
            "1h": "reminder",
            "10m": "reminder"
        }
        
        template_name = template_map.get(reminder_type, "reminder")
        
        # Build template context
        context = {
            "recipient_name": name,
            "title": title,
            "time": time,
            "is_tomorrow": reminder_type == "24h"
        }
        
        if join_link:
            context["join_link"] = join_link
        
        try:
            return await self._email_service.send_templated_email(
                tenant_id=tenant_id,
                template_name=template_name,
                recipients=[to_email],
                context=context,
                lead_ids=[lead_id] if lead_id else None,
                triggered_by="reminder"
            )
        except Exception as e:
            logger.error(f"Email send failed: {e}")
            return {"success": False, "error": str(e)}
    
    async def shutdown(self) -> None:
        """Graceful shutdown."""
        logger.info("Shutting down Reminder Worker...")
        self.running = False
        
        # Log final stats
        logger.info(
            f"Reminder Worker shutdown complete. "
            f"SMS Sent: {self._reminders_sent}, "
            f"Emails Sent: {self._emails_sent}, "
            f"Failed: {self._reminders_failed}"
        )
    
    def get_stats(self) -> dict:
        """Get worker statistics."""
        return {
            "running": self.running,
            "reminders_sent": self._reminders_sent,
            "emails_sent": self._emails_sent,
            "reminders_failed": self._reminders_failed
        }


async def main():
    """Entry point for running reminder worker as separate process."""
    worker = ReminderWorker()
    
    # Handle shutdown signals
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("Received shutdown signal")
        worker.running = False
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    
    try:
        await worker.run()
    except KeyboardInterrupt:
        logger.info("Worker interrupted by user")
    finally:
        await worker.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

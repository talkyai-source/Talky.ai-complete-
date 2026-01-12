"""
SMS Template Manager
Templates for SMS messages (meeting reminders, notifications).

Day 27: Timed Communication System
"""
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class SMSTemplateType(str, Enum):
    """Types of SMS templates available."""
    MEETING_REMINDER_24H = "meeting_reminder_24h"
    MEETING_REMINDER_1H = "meeting_reminder_1h"
    MEETING_REMINDER_10M = "meeting_reminder_10m"
    APPOINTMENT_CONFIRMATION = "appointment_confirmation"
    APPOINTMENT_CANCELLED = "appointment_cancelled"
    CUSTOM = "custom"


@dataclass
class SMSTemplate:
    """SMS template with content and metadata."""
    name: str
    template_type: SMSTemplateType
    content: str
    description: str
    required_vars: List[str]
    max_length: int = 160  # Single SMS limit
    
    def render(self, **kwargs) -> str:
        """
        Render the template with provided variables.
        
        Args:
            **kwargs: Template variables
            
        Returns:
            Rendered SMS content
            
        Raises:
            ValueError: If required variables are missing
        """
        # Check for required variables
        missing = [var for var in self.required_vars if var not in kwargs]
        if missing:
            raise ValueError(f"Missing required template variables: {missing}")
        
        try:
            rendered = self.content.format(**kwargs)
            
            # Warn if exceeds single SMS length
            if len(rendered) > self.max_length:
                logger.warning(
                    f"SMS template '{self.name}' rendered to {len(rendered)} chars "
                    f"(exceeds {self.max_length})"
                )
            
            return rendered
        except KeyError as e:
            raise ValueError(f"Template variable not provided: {e}")


# Pre-defined SMS templates
SMS_TEMPLATES: Dict[str, SMSTemplate] = {
    # Meeting reminder 24 hours before
    SMSTemplateType.MEETING_REMINDER_24H.value: SMSTemplate(
        name="Meeting Reminder (24h)",
        template_type=SMSTemplateType.MEETING_REMINDER_24H,
        content="Hi {name}, reminder: You have \"{title}\" scheduled for tomorrow at {time}. Reply CONFIRM to confirm.",
        description="Sent 24 hours before a meeting",
        required_vars=["name", "title", "time"],
        max_length=160
    ),
    
    # Meeting reminder 1 hour before
    SMSTemplateType.MEETING_REMINDER_1H.value: SMSTemplate(
        name="Meeting Reminder (1h)",
        template_type=SMSTemplateType.MEETING_REMINDER_1H,
        content="Hi {name}, your meeting \"{title}\" starts in 1 hour at {time}.",
        description="Sent 1 hour before a meeting",
        required_vars=["name", "title", "time"],
        max_length=160
    ),
    
    # Meeting reminder 10 minutes before
    SMSTemplateType.MEETING_REMINDER_10M.value: SMSTemplate(
        name="Meeting Reminder (10m)",
        template_type=SMSTemplateType.MEETING_REMINDER_10M,
        content="Hi {name}, \"{title}\" starts in 10 min! Join: {join_link}",
        description="Sent 10 minutes before a meeting with join link",
        required_vars=["name", "title", "join_link"],
        max_length=160
    ),
    
    # Appointment confirmation
    SMSTemplateType.APPOINTMENT_CONFIRMATION.value: SMSTemplate(
        name="Appointment Confirmation",
        template_type=SMSTemplateType.APPOINTMENT_CONFIRMATION,
        content="Hi {name}, your appointment \"{title}\" is confirmed for {date} at {time}. See you then!",
        description="Sent when an appointment is confirmed",
        required_vars=["name", "title", "date", "time"],
        max_length=160
    ),
    
    # Appointment cancelled
    SMSTemplateType.APPOINTMENT_CANCELLED.value: SMSTemplate(
        name="Appointment Cancelled",
        template_type=SMSTemplateType.APPOINTMENT_CANCELLED,
        content="Hi {name}, your appointment \"{title}\" on {date} has been cancelled. Contact us to reschedule.",
        description="Sent when an appointment is cancelled",
        required_vars=["name", "title", "date"],
        max_length=160
    ),
}


class SMSTemplateManager:
    """
    Manages SMS templates for the application.
    
    Provides:
    - Template lookup and rendering
    - Template validation
    - Custom template support
    """
    
    def __init__(self, custom_templates: Optional[Dict[str, SMSTemplate]] = None):
        """
        Initialize template manager.
        
        Args:
            custom_templates: Additional custom templates to register
        """
        self._templates = {**SMS_TEMPLATES}
        
        if custom_templates:
            self._templates.update(custom_templates)
    
    def get_template(self, template_name: str) -> SMSTemplate:
        """
        Get a template by name.
        
        Args:
            template_name: Template name/type
            
        Returns:
            SMSTemplate
            
        Raises:
            ValueError: If template not found
        """
        if template_name not in self._templates:
            available = ", ".join(self._templates.keys())
            raise ValueError(f"Unknown SMS template: {template_name}. Available: {available}")
        
        return self._templates[template_name]
    
    def render_template(self, template_name: str, **kwargs) -> str:
        """
        Render a template with provided variables.
        
        Args:
            template_name: Template to render
            **kwargs: Template variables
            
        Returns:
            Rendered SMS content
        """
        template = self.get_template(template_name)
        return template.render(**kwargs)
    
    def render_meeting_reminder(
        self,
        reminder_type: str,  # "24h", "1h", "10m"
        name: str,
        title: str,
        time: str,
        join_link: Optional[str] = None,
        date: Optional[str] = None
    ) -> str:
        """
        Convenience method to render meeting reminder templates.
        
        Args:
            reminder_type: Type of reminder ("24h", "1h", "10m")
            name: Recipient name
            title: Meeting title
            time: Meeting time (formatted string)
            join_link: Video conference join link (required for 10m reminder)
            date: Meeting date (for confirmation messages)
            
        Returns:
            Rendered SMS content
        """
        template_map = {
            "24h": SMSTemplateType.MEETING_REMINDER_24H.value,
            "1h": SMSTemplateType.MEETING_REMINDER_1H.value,
            "10m": SMSTemplateType.MEETING_REMINDER_10M.value,
        }
        
        template_name = template_map.get(reminder_type)
        if not template_name:
            raise ValueError(f"Unknown reminder type: {reminder_type}. Use: 24h, 1h, 10m")
        
        # Build context based on reminder type
        context = {
            "name": name or "there",
            "title": title,
            "time": time,
        }
        
        if reminder_type == "10m" and join_link:
            context["join_link"] = join_link
        elif reminder_type == "10m" and not join_link:
            # Fallback if no join link
            context["join_link"] = "See calendar"
        
        if date:
            context["date"] = date
        
        return self.render_template(template_name, **context)
    
    def list_templates(self) -> List[str]:
        """List all available template names."""
        return list(self._templates.keys())
    
    def get_template_info(self, template_name: str) -> Dict[str, Any]:
        """
        Get template metadata.
        
        Returns:
            Dict with template info
        """
        template = self.get_template(template_name)
        return {
            "name": template.name,
            "type": template.template_type.value,
            "description": template.description,
            "required_vars": template.required_vars,
            "max_length": template.max_length,
            "preview": template.content
        }


# Singleton instance
_sms_template_manager: Optional[SMSTemplateManager] = None


def get_sms_template_manager() -> SMSTemplateManager:
    """Get or create SMSTemplateManager singleton."""
    global _sms_template_manager
    if _sms_template_manager is None:
        _sms_template_manager = SMSTemplateManager()
    return _sms_template_manager

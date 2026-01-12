"""
Email Template Manager
Manages email templates with Jinja2 rendering for AI-generated emails.

Day 26: AI Email System
"""
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field
from jinja2 import Environment, BaseLoader
import logging
import re

logger = logging.getLogger(__name__)


class EmailTemplate(BaseModel):
    """Single email template definition."""
    name: str = Field(..., description="Template identifier")
    subject_template: str = Field(..., description="Jinja2 subject template")
    body_template: str = Field(..., description="Jinja2 plain text body template")
    body_html_template: Optional[str] = Field(None, description="Jinja2 HTML body template")
    variables: List[str] = Field(default_factory=list, description="Required variables")
    description: str = Field("", description="Template purpose description")
    
    class Config:
        extra = "allow"


class RenderedEmail(BaseModel):
    """Rendered email ready for sending."""
    subject: str
    body: str
    body_html: Optional[str] = None
    template_name: str
    variables_used: Dict[str, Any] = {}


class EmailContentValidationError(Exception):
    """Raised when email content fails validation."""
    def __init__(self, message: str, issues: List[str] = None):
        self.message = message
        self.issues = issues or []
        super().__init__(self.message)


class EmailTemplateManager:
    """
    Manages email templates and rendering.
    
    Follows the pattern from PromptManager for voice prompts,
    adapted for email content.
    """
    
    # Content validation settings
    MAX_SUBJECT_LENGTH = 200
    MAX_BODY_LENGTH = 10000
    
    # Patterns to detect potentially problematic content
    BLOCKED_PATTERNS = [
        r'\b(password|ssn|social\s*security|credit\s*card)\b',  # PII mentions
    ]
    
    def __init__(self):
        """Initialize template manager with default templates."""
        self.templates: Dict[str, EmailTemplate] = {}
        self.env = Environment(loader=BaseLoader())
        self._load_default_templates()
    
    def _load_default_templates(self):
        """Load default email templates."""
        
        # Meeting Confirmation Template
        self.templates["meeting_confirmation"] = EmailTemplate(
            name="meeting_confirmation",
            description="Sent when a meeting is booked via AI",
            subject_template="Meeting Confirmed: {{ title }} on {{ date }}",
            body_template="""Hi {{ attendee_name | default('there') }},

Your meeting "{{ title }}" has been confirmed.

📅 Date: {{ date }}
🕐 Time: {{ time }}
{% if location %}📍 Location: {{ location }}
{% endif %}{% if join_link %}🔗 Join Link: {{ join_link }}
{% endif %}
{% if description %}
Details: {{ description }}
{% endif %}
Looking forward to connecting with you!

Best regards,
{{ sender_name | default('Your Team') }}""",
            body_html_template="""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
<p>Hi {{ attendee_name | default('there') }},</p>

<p>Your meeting <strong>"{{ title }}"</strong> has been confirmed.</p>

<table style="border-collapse: collapse; margin: 20px 0;">
<tr><td style="padding: 8px 0;">📅 <strong>Date:</strong></td><td style="padding: 8px 16px;">{{ date }}</td></tr>
<tr><td style="padding: 8px 0;">🕐 <strong>Time:</strong></td><td style="padding: 8px 16px;">{{ time }}</td></tr>
{% if location %}<tr><td style="padding: 8px 0;">📍 <strong>Location:</strong></td><td style="padding: 8px 16px;">{{ location }}</td></tr>{% endif %}
{% if join_link %}<tr><td style="padding: 8px 0;">🔗 <strong>Join:</strong></td><td style="padding: 8px 16px;"><a href="{{ join_link }}" style="color: #0066cc;">{{ join_link }}</a></td></tr>{% endif %}
</table>

{% if description %}<p><strong>Details:</strong> {{ description }}</p>{% endif %}

<p>Looking forward to connecting with you!</p>

<p>Best regards,<br>{{ sender_name | default('Your Team') }}</p>
</body>
</html>""",
            variables=["title", "date", "time", "attendee_name", "sender_name", "join_link", "location", "description"]
        )
        
        # Follow-up Template
        self.templates["follow_up"] = EmailTemplate(
            name="follow_up",
            description="Sent after a call or interaction",
            subject_template="Following up{{ ' - ' + context if context else '' }}",
            body_template="""Hi {{ recipient_name | default('there') }},

Thank you for {{ interaction_type | default('speaking with us') }}{% if interaction_date %} on {{ interaction_date }}{% endif %}.

{{ custom_message }}

{% if next_steps %}
Next Steps:
{% for step in next_steps %}- {{ step }}
{% endfor %}{% endif %}
If you have any questions, feel free to reach out.

Best regards,
{{ sender_name | default('Your Team') }}""",
            body_html_template="""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
<p>Hi {{ recipient_name | default('there') }},</p>

<p>Thank you for {{ interaction_type | default('speaking with us') }}{% if interaction_date %} on {{ interaction_date }}{% endif %}.</p>

<p>{{ custom_message }}</p>

{% if next_steps %}
<p><strong>Next Steps:</strong></p>
<ul>
{% for step in next_steps %}<li>{{ step }}</li>{% endfor %}
</ul>
{% endif %}

<p>If you have any questions, feel free to reach out.</p>

<p>Best regards,<br>{{ sender_name | default('Your Team') }}</p>
</body>
</html>""",
            variables=["recipient_name", "interaction_type", "interaction_date", "custom_message", "next_steps", "sender_name", "context"]
        )
        
        # Reminder Template
        self.templates["reminder"] = EmailTemplate(
            name="reminder",
            description="Sent before a scheduled meeting",
            subject_template="Reminder: {{ title }} {% if is_tomorrow %}tomorrow{% else %}on {{ date }}{% endif %} at {{ time }}",
            body_template="""Hi {{ attendee_name | default('there') }},

This is a friendly reminder about your upcoming meeting:

📅 {{ title }}
🕐 {% if is_tomorrow %}Tomorrow{% else %}{{ date }}{% endif %} at {{ time }}
{% if join_link %}🔗 Join: {{ join_link }}
{% endif %}
{% if preparation_notes %}
Please note: {{ preparation_notes }}
{% endif %}
See you soon!

{{ sender_name | default('Your Team') }}""",
            body_html_template="""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
<p>Hi {{ attendee_name | default('there') }},</p>

<p>This is a friendly reminder about your upcoming meeting:</p>

<div style="background: #f5f5f5; padding: 16px; border-radius: 8px; margin: 16px 0;">
<p style="margin: 4px 0;"><strong>📅 {{ title }}</strong></p>
<p style="margin: 4px 0;">🕐 {% if is_tomorrow %}Tomorrow{% else %}{{ date }}{% endif %} at {{ time }}</p>
{% if join_link %}<p style="margin: 4px 0;">🔗 <a href="{{ join_link }}" style="color: #0066cc;">Join Meeting</a></p>{% endif %}
</div>

{% if preparation_notes %}<p><em>Please note: {{ preparation_notes }}</em></p>{% endif %}

<p>See you soon!</p>

<p>{{ sender_name | default('Your Team') }}</p>
</body>
</html>""",
            variables=["title", "date", "time", "attendee_name", "sender_name", "join_link", "is_tomorrow", "preparation_notes"]
        )
        
        logger.info(f"Loaded {len(self.templates)} default email templates")
    
    def render_email(
        self,
        template_name: str,
        **context
    ) -> RenderedEmail:
        """
        Render an email template with provided context.
        
        Args:
            template_name: Name of template to render
            **context: Variables to inject into template
            
        Returns:
            RenderedEmail with subject, body, and optional HTML body
            
        Raises:
            KeyError: If template not found
        """
        if template_name not in self.templates:
            available = ", ".join(self.templates.keys())
            raise KeyError(f"Template '{template_name}' not found. Available: {available}")
        
        template = self.templates[template_name]
        
        # Render subject
        subject_tmpl = self.env.from_string(template.subject_template)
        subject = subject_tmpl.render(**context)
        
        # Render body
        body_tmpl = self.env.from_string(template.body_template)
        body = body_tmpl.render(**context)
        
        # Render HTML body if available
        body_html = None
        if template.body_html_template:
            html_tmpl = self.env.from_string(template.body_html_template)
            body_html = html_tmpl.render(**context)
        
        logger.debug(f"Rendered email template '{template_name}' with {len(context)} variables")
        
        return RenderedEmail(
            subject=subject,
            body=body,
            body_html=body_html,
            template_name=template_name,
            variables_used=context
        )
    
    def validate_content(
        self,
        subject: str,
        body: str,
        raise_on_error: bool = True
    ) -> tuple[bool, List[str]]:
        """
        Validate email content for safety and appropriateness.
        
        Args:
            subject: Email subject line
            body: Email body content
            raise_on_error: If True, raises EmailContentValidationError on failure
            
        Returns:
            Tuple of (is_valid, list of issues)
            
        Raises:
            EmailContentValidationError: If validation fails and raise_on_error is True
        """
        issues = []
        
        # Check subject length
        if len(subject) > self.MAX_SUBJECT_LENGTH:
            issues.append(f"Subject too long ({len(subject)} > {self.MAX_SUBJECT_LENGTH} chars)")
        
        # Check body length
        if len(body) > self.MAX_BODY_LENGTH:
            issues.append(f"Body too long ({len(body)} > {self.MAX_BODY_LENGTH} chars)")
        
        # Check for empty content
        if not subject.strip():
            issues.append("Subject cannot be empty")
        
        if not body.strip():
            issues.append("Body cannot be empty")
        
        # Check for blocked patterns (case-insensitive)
        full_content = f"{subject} {body}".lower()
        for pattern in self.BLOCKED_PATTERNS:
            if re.search(pattern, full_content, re.IGNORECASE):
                issues.append(f"Content contains potentially sensitive information")
                break
        
        is_valid = len(issues) == 0
        
        if not is_valid and raise_on_error:
            raise EmailContentValidationError(
                f"Email validation failed: {'; '.join(issues)}",
                issues=issues
            )
        
        return is_valid, issues
    
    def add_template(self, template: EmailTemplate) -> None:
        """Add or update a template."""
        self.templates[template.name] = template
        logger.info(f"Added email template: {template.name}")
    
    def get_template(self, name: str) -> Optional[EmailTemplate]:
        """Get template by name."""
        return self.templates.get(name)
    
    def list_templates(self) -> List[str]:
        """List all template names."""
        return list(self.templates.keys())
    
    def get_template_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Get template metadata."""
        template = self.templates.get(name)
        if not template:
            return None
        
        return {
            "name": template.name,
            "description": template.description,
            "variables": template.variables,
            "has_html": template.body_html_template is not None
        }


# Singleton instance
_template_manager: Optional[EmailTemplateManager] = None


def get_email_template_manager() -> EmailTemplateManager:
    """Get or create EmailTemplateManager singleton."""
    global _template_manager
    if _template_manager is None:
        _template_manager = EmailTemplateManager()
    return _template_manager

"""
Tests for Email Template Manager
Day 26: AI Email System
"""
import pytest
from app.domain.services.email_template_manager import (
    EmailTemplateManager,
    EmailTemplate,
    RenderedEmail,
    EmailContentValidationError,
    get_email_template_manager
)


class TestEmailTemplateManagerImports:
    """Test that all email template components import correctly"""
    
    def test_email_template_manager_import(self):
        """EmailTemplateManager can be imported"""
        from app.domain.services.email_template_manager import EmailTemplateManager
        assert EmailTemplateManager is not None
    
    def test_email_template_import(self):
        """EmailTemplate model can be imported"""
        from app.domain.services.email_template_manager import EmailTemplate
        assert EmailTemplate is not None
    
    def test_get_template_manager_import(self):
        """get_email_template_manager helper can be imported"""
        from app.domain.services.email_template_manager import get_email_template_manager
        assert get_email_template_manager is not None


class TestEmailTemplateManagerInit:
    """Tests for EmailTemplateManager initialization"""
    
    def test_init_loads_default_templates(self):
        """Manager initializes with default templates"""
        manager = EmailTemplateManager()
        assert len(manager.templates) >= 3
    
    def test_default_templates_include_meeting_confirmation(self):
        """meeting_confirmation template is loaded"""
        manager = EmailTemplateManager()
        assert "meeting_confirmation" in manager.templates
    
    def test_default_templates_include_follow_up(self):
        """follow_up template is loaded"""
        manager = EmailTemplateManager()
        assert "follow_up" in manager.templates
    
    def test_default_templates_include_reminder(self):
        """reminder template is loaded"""
        manager = EmailTemplateManager()
        assert "reminder" in manager.templates


class TestRenderEmail:
    """Tests for render_email method"""
    
    def test_render_meeting_confirmation(self):
        """Meeting confirmation template renders correctly"""
        manager = EmailTemplateManager()
        rendered = manager.render_email(
            "meeting_confirmation",
            title="Product Demo",
            date="January 10, 2026",
            time="2:00 PM",
            attendee_name="John Smith",
            sender_name="Sarah"
        )
        
        assert isinstance(rendered, RenderedEmail)
        assert "Product Demo" in rendered.subject
        assert "January 10, 2026" in rendered.body
        assert "John Smith" in rendered.body
    
    def test_render_follow_up(self):
        """Follow-up template renders correctly"""
        manager = EmailTemplateManager()
        rendered = manager.render_email(
            "follow_up",
            recipient_name="Jane Doe",
            interaction_type="our call today",
            custom_message="I wanted to share the info we discussed.",
            sender_name="Bob"
        )
        
        assert "Following up" in rendered.subject
        assert "our call today" in rendered.body
        assert "Jane Doe" in rendered.body
    
    def test_render_reminder(self):
        """Reminder template renders correctly"""
        manager = EmailTemplateManager()
        rendered = manager.render_email(
            "reminder",
            title="Team Meeting",
            date="January 9, 2026",
            time="3:00 PM",
            is_tomorrow=True,
            attendee_name="Alex"
        )
        
        assert "Reminder" in rendered.subject
        assert "tomorrow" in rendered.subject.lower()
        assert "Alex" in rendered.body
    
    def test_render_with_html(self):
        """Templates render HTML body when available"""
        manager = EmailTemplateManager()
        rendered = manager.render_email(
            "meeting_confirmation",
            title="Test Meeting",
            date="2026-01-08",
            time="10:00 AM"
        )
        
        assert rendered.body_html is not None
        assert "<html>" in rendered.body_html
    
    def test_render_unknown_template_raises(self):
        """Rendering unknown template raises KeyError"""
        manager = EmailTemplateManager()
        
        with pytest.raises(KeyError) as exc_info:
            manager.render_email("nonexistent_template")
        
        assert "nonexistent_template" in str(exc_info.value)
    
    def test_render_preserves_template_name(self):
        """Rendered email includes template name"""
        manager = EmailTemplateManager()
        rendered = manager.render_email(
            "meeting_confirmation",
            title="Test",
            date="Today",
            time="Now"
        )
        
        assert rendered.template_name == "meeting_confirmation"


class TestValidateContent:
    """Tests for validate_content method"""
    
    def test_valid_content_passes(self):
        """Normal content passes validation"""
        manager = EmailTemplateManager()
        is_valid, issues = manager.validate_content(
            subject="Meeting Tomorrow",
            body="Looking forward to our meeting!",
            raise_on_error=False
        )
        
        assert is_valid is True
        assert len(issues) == 0
    
    def test_empty_subject_fails(self):
        """Empty subject fails validation"""
        manager = EmailTemplateManager()
        is_valid, issues = manager.validate_content(
            subject="",
            body="Some body content",
            raise_on_error=False
        )
        
        assert is_valid is False
        assert any("Subject" in issue for issue in issues)
    
    def test_empty_body_fails(self):
        """Empty body fails validation"""
        manager = EmailTemplateManager()
        is_valid, issues = manager.validate_content(
            subject="Valid Subject",
            body="   ",
            raise_on_error=False
        )
        
        assert is_valid is False
        assert any("Body" in issue for issue in issues)
    
    def test_subject_too_long_fails(self):
        """Very long subject fails validation"""
        manager = EmailTemplateManager()
        long_subject = "A" * 250  # Over 200 char limit
        is_valid, issues = manager.validate_content(
            subject=long_subject,
            body="Normal body",
            raise_on_error=False
        )
        
        assert is_valid is False
        assert any("too long" in issue for issue in issues)
    
    def test_raise_on_error_raises_exception(self):
        """ValidationError is raised when raise_on_error=True"""
        manager = EmailTemplateManager()
        
        with pytest.raises(EmailContentValidationError) as exc_info:
            manager.validate_content(
                subject="",
                body="",
                raise_on_error=True
            )
        
        assert "validation failed" in str(exc_info.value).lower()


class TestTemplateManagement:
    """Tests for template management methods"""
    
    def test_list_templates(self):
        """list_templates returns template names"""
        manager = EmailTemplateManager()
        templates = manager.list_templates()
        
        assert isinstance(templates, list)
        assert "meeting_confirmation" in templates
    
    def test_get_template(self):
        """get_template returns template by name"""
        manager = EmailTemplateManager()
        template = manager.get_template("meeting_confirmation")
        
        assert template is not None
        assert isinstance(template, EmailTemplate)
        assert template.name == "meeting_confirmation"
    
    def test_get_template_not_found(self):
        """get_template returns None for unknown template"""
        manager = EmailTemplateManager()
        template = manager.get_template("nonexistent")
        
        assert template is None
    
    def test_add_template(self):
        """add_template adds custom template"""
        manager = EmailTemplateManager()
        custom = EmailTemplate(
            name="custom_template",
            subject_template="Custom: {{ title }}",
            body_template="This is {{ content }}",
            variables=["title", "content"]
        )
        
        manager.add_template(custom)
        
        assert "custom_template" in manager.list_templates()
        
        rendered = manager.render_email(
            "custom_template",
            title="Test Title",
            content="custom body"
        )
        
        assert "Test Title" in rendered.subject
        assert "custom body" in rendered.body
    
    def test_get_template_info(self):
        """get_template_info returns template metadata"""
        manager = EmailTemplateManager()
        info = manager.get_template_info("meeting_confirmation")
        
        assert info is not None
        assert info["name"] == "meeting_confirmation"
        assert "variables" in info
        assert info["has_html"] is True


class TestSingletonInstance:
    """Tests for singleton helper"""
    
    def test_get_email_template_manager_returns_manager(self):
        """get_email_template_manager returns EmailTemplateManager instance"""
        manager = get_email_template_manager()
        
        assert isinstance(manager, EmailTemplateManager)
    
    def test_get_email_template_manager_returns_same_instance(self):
        """get_email_template_manager returns singleton"""
        manager1 = get_email_template_manager()
        manager2 = get_email_template_manager()
        
        assert manager1 is manager2

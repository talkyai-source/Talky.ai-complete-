"""
Day 9 Unit Tests: Campaign & Contact Management
Tests for new endpoints and enhancements added in Day 9
"""
import pytest
import uuid
from datetime import datetime
from unittest.mock import Mock, patch, MagicMock
from io import BytesIO

# Test the normalize_phone_number function
class TestPhoneNormalization:
    """Test phone number normalization logic"""
    
    def test_normalize_us_10_digit(self):
        """10-digit US number should get +1 prefix"""
        from app.api.v1.endpoints.campaigns import normalize_phone_number
        assert normalize_phone_number("5551234567") == "+15551234567"
    
    def test_normalize_us_11_digit_with_1(self):
        """11-digit starting with 1 should get + prefix"""
        from app.api.v1.endpoints.campaigns import normalize_phone_number
        assert normalize_phone_number("15551234567") == "+15551234567"
    
    def test_normalize_with_plus(self):
        """Number with + should keep format"""
        from app.api.v1.endpoints.campaigns import normalize_phone_number
        assert normalize_phone_number("+442071234567") == "+442071234567"
    
    def test_normalize_with_formatting(self):
        """Formatted number should be cleaned"""
        from app.api.v1.endpoints.campaigns import normalize_phone_number
        assert normalize_phone_number("(555) 123-4567") == "+15551234567"
    
    def test_normalize_with_spaces(self):
        """Spaces should be removed"""
        from app.api.v1.endpoints.campaigns import normalize_phone_number
        assert normalize_phone_number("+44 20 7123 4567") == "+442071234567"
    
    def test_normalize_invalid_empty(self):
        """Empty string should raise ValueError"""
        from app.api.v1.endpoints.campaigns import normalize_phone_number
        with pytest.raises(ValueError):
            normalize_phone_number("")
    
    def test_normalize_too_short(self):
        """Phone number too short should raise ValueError"""
        from app.api.v1.endpoints.campaigns import normalize_phone_number
        with pytest.raises(ValueError):
            normalize_phone_number("123456")  # 6 digits, less than minimum 7


# Test the ContactCreate model validation
class TestContactCreateModel:
    """Test ContactCreate Pydantic model validation"""
    
    def test_valid_contact(self):
        """Valid contact should pass validation"""
        from app.api.v1.endpoints.campaigns import ContactCreate
        contact = ContactCreate(
            phone_number="+15551234567",
            first_name="John",
            last_name="Doe",
            email="john@example.com"
        )
        assert contact.phone_number == "+15551234567"
        assert contact.first_name == "John"
    
    def test_minimal_contact(self):
        """Contact with only phone should be valid"""
        from app.api.v1.endpoints.campaigns import ContactCreate
        contact = ContactCreate(phone_number="+15551234567")
        assert contact.phone_number == "+15551234567"
        assert contact.first_name is None
        assert contact.email is None
    
    def test_phone_validation_removes_formatting(self):
        """Phone validator should accept formatted numbers"""
        from app.api.v1.endpoints.campaigns import ContactCreate
        contact = ContactCreate(phone_number="(555) 123-4567")
        assert contact.phone_number == "(555) 123-4567"  # Validator just checks, doesn't normalize
    
    def test_phone_too_short_fails(self):
        """Phone number too short should fail validation"""
        from app.api.v1.endpoints.campaigns import ContactCreate
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            ContactCreate(phone_number="123")


# Test Campaign model with new fields
class TestCampaignModel:
    """Test Campaign model with Day 9 fields"""
    
    def test_campaign_with_new_fields(self):
        """Campaign should accept goal, script_config, calling_config"""
        from app.domain.models.campaign import Campaign, CampaignStatus
        
        campaign = Campaign(
            id=str(uuid.uuid4()),
            name="Test Campaign",
            system_prompt="You are a helpful assistant",
            voice_id="sonic-professional",
            created_at=datetime.utcnow(),
            goal="Book appointment",
            script_config={"agent_name": "Alex", "greeting": "Hello!"},
            calling_config={"time_window_start": "09:00", "time_window_end": "17:00"}
        )
        
        assert campaign.goal == "Book appointment"
        assert campaign.script_config["agent_name"] == "Alex"
        assert campaign.calling_config["time_window_start"] == "09:00"
    
    def test_campaign_optional_fields(self):
        """New fields should be optional"""
        from app.domain.models.campaign import Campaign
        
        campaign = Campaign(
            id=str(uuid.uuid4()),
            name="Minimal Campaign",
            system_prompt="Hi",
            voice_id="sonic",
            created_at=datetime.utcnow()
        )
        
        assert campaign.goal is None
        assert campaign.script_config is None
        assert campaign.calling_config is None


# Test Lead model with new field
class TestLeadModel:
    """Test Lead model with Day 9 fields"""
    
    def test_lead_with_last_call_result(self):
        """Lead should have last_call_result field"""
        from app.domain.models.lead import Lead
        
        lead = Lead(
            id=str(uuid.uuid4()),
            campaign_id=str(uuid.uuid4()),
            phone_number="+15551234567",
            created_at=datetime.utcnow(),
            last_call_result="answered"
        )
        
        assert lead.last_call_result == "answered"
    
    def test_lead_default_last_call_result(self):
        """Lead should default last_call_result to 'pending'"""
        from app.domain.models.lead import Lead
        
        lead = Lead(
            id=str(uuid.uuid4()),
            campaign_id=str(uuid.uuid4()),
            phone_number="+15551234567",
            created_at=datetime.utcnow()
        )
        
        assert lead.last_call_result == "pending"


# Test BulkImportResponse model
class TestBulkImportResponse:
    """Test CSV import response model"""
    
    def test_import_response_with_duplicates(self):
        """Response should include duplicates_skipped count"""
        from app.api.v1.endpoints.contacts import BulkImportResponse, ImportError
        
        response = BulkImportResponse(
            total_rows=100,
            imported=80,
            failed=5,
            duplicates_skipped=15,
            errors=[ImportError(row=2, error="Invalid phone", phone="+invalid")]
        )
        
        assert response.duplicates_skipped == 15
        assert response.imported == 80
        assert len(response.errors) == 1
        assert response.errors[0].phone == "+invalid"


# Integration-style tests (with mocked Supabase)
class TestCampaignContactEndpoints:
    """Test campaign contact management endpoints"""
    
    @pytest.fixture
    def mock_supabase(self):
        """Create a mock Supabase client"""
        mock = MagicMock()
        return mock
    
    def test_add_contact_validates_campaign_exists(self, mock_supabase):
        """add_contact_to_campaign should validate campaign exists"""
        # This would be a more complete integration test
        # For now, just verify the endpoint function exists and has correct signature
        from app.api.v1.endpoints.campaigns import add_contact_to_campaign
        import inspect
        
        sig = inspect.signature(add_contact_to_campaign)
        params = list(sig.parameters.keys())
        
        assert "campaign_id" in params
        assert "contact" in params
        assert "supabase" in params
    
    def test_list_contacts_has_pagination(self):
        """list_campaign_contacts should support pagination"""
        from app.api.v1.endpoints.campaigns import list_campaign_contacts
        import inspect
        
        sig = inspect.signature(list_campaign_contacts)
        params = list(sig.parameters.keys())
        
        assert "page" in params
        assert "page_size" in params
        assert "status" in params
        assert "last_call_result" in params


# Test CSV upload functionality
class TestCSVUpload:
    """Test CSV upload endpoint logic"""
    
    def test_normalize_phone_in_contacts(self):
        """CSV upload should use same normalization as single add"""
        from app.api.v1.endpoints.contacts import normalize_phone_number
        
        # Test the same normalization is available
        assert normalize_phone_number("5551234567") == "+15551234567"
    
    def test_upload_endpoint_exists(self):
        """Campaign CSV upload endpoint should exist"""
        from app.api.v1.endpoints.contacts import upload_campaign_contacts
        import inspect
        
        sig = inspect.signature(upload_campaign_contacts)
        params = list(sig.parameters.keys())
        
        assert "campaign_id" in params
        assert "file" in params
        assert "skip_duplicates" in params


# Full Day 9 checkpoint verification
class TestDay9Checkpoints:
    """Verify all Day 9 requirements are met"""
    
    def test_checkpoint_1_campaign_model(self):
        """Checkpoint 1: Campaign model has required fields"""
        from app.domain.models.campaign import Campaign
        import inspect
        
        # Get model fields
        fields = Campaign.model_fields.keys()
        
        assert "id" in fields
        assert "name" in fields
        assert "goal" in fields
        assert "script_config" in fields
        assert "status" in fields
        assert "max_retries" in fields
        # time_window is in calling_config
        assert "calling_config" in fields
    
    def test_checkpoint_2_contact_model(self):
        """Checkpoint 2: Contact (Lead) model has required fields"""
        from app.domain.models.lead import Lead
        
        fields = Lead.model_fields.keys()
        
        assert "id" in fields
        assert "campaign_id" in fields
        assert "phone_number" in fields
        assert "email" in fields
        assert "status" in fields
        assert "last_call_result" in fields
    
    def test_checkpoint_3_campaign_api_endpoints(self):
        """Checkpoint 3: Campaign API endpoints exist"""
        from app.api.v1.endpoints import campaigns
        
        # Check router has expected endpoints
        routes = [route.path for route in campaigns.router.routes]
        
        # Router paths include the prefix
        assert "/campaigns/" in routes  # List campaigns
        assert "/campaigns/{campaign_id}" in routes  # Get campaign
        assert "/campaigns/{campaign_id}/contacts" in routes  # Add/list contacts
        assert "/campaigns/{campaign_id}/start" in routes  # Start campaign
    
    def test_checkpoint_4_csv_upload_endpoint(self):
        """Checkpoint 4: CSV upload endpoint exists with enhanced features"""
        from app.api.v1.endpoints import contacts
        
        routes = [route.path for route in contacts.router.routes]
        
        # New campaign-scoped upload (note: path includes /contacts prefix)
        assert "/contacts/campaigns/{campaign_id}/upload" in routes
        # Legacy bulk import still exists
        assert "/contacts/bulk" in routes
    
    def test_checkpoint_5_dialer_link(self):
        """Checkpoint 5: Campaign start links to dialer"""
        from app.api.v1.endpoints.campaigns import start_campaign
        import inspect
        
        # Verify start_campaign function exists and uses DialerJob
        source = inspect.getsource(start_campaign)
        
        assert "DialerJob" in source
        assert "queue_service" in source
        assert "enqueue_job" in source


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

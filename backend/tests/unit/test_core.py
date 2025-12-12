"""
Basic Tests for Core Functionality
Tests health endpoint, session manager, and provider validation
"""
import pytest
from httpx import AsyncClient, ASGITransport


class TestHealthEndpoint:
    """Tests for the /health endpoint."""
    
    @pytest.mark.asyncio
    async def test_health_endpoint_returns_healthy(self):
        """Test that /health returns healthy status."""
        from app.main import app
        
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
    
    @pytest.mark.asyncio
    async def test_root_endpoint_returns_running(self):
        """Test that / returns running status."""
        from app.main import app
        
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert "AI Voice Dialer" in data["message"]


class TestSessionManager:
    """Basic tests for SessionManager."""
    
    @pytest.mark.asyncio
    async def test_session_manager_singleton(self):
        """Test that SessionManager is a singleton."""
        from app.domain.services.session_manager import SessionManager
        
        manager1 = await SessionManager.get_instance()
        manager2 = await SessionManager.get_instance()
        
        assert manager1 is manager2
    
    @pytest.mark.asyncio
    async def test_session_manager_stats(self):
        """Test that session stats returns valid data."""
        from app.domain.services.session_manager import SessionManager
        
        manager = await SessionManager.get_instance()
        stats = manager.get_session_stats()
        
        assert "active_sessions" in stats
        assert "redis_enabled" in stats
        assert "states" in stats
        assert isinstance(stats["active_sessions"], int)


class TestProviderValidation:
    """Tests for provider configuration validation."""
    
    def test_validator_checks_required_vars(self):
        """Test that validator identifies missing required vars."""
        import os
        from app.core.validation import ProviderValidator
        
        # Save original values
        original_values = {}
        test_vars = ["DEEPGRAM_API_KEY", "GROQ_API_KEY", "CARTESIA_API_KEY"]
        for var in test_vars:
            original_values[var] = os.environ.get(var)
        
        try:
            # Clear test vars
            for var in test_vars:
                if var in os.environ:
                    del os.environ[var]
            
            validator = ProviderValidator(strict=False)
            all_valid, results = validator.validate_all()
            
            # Should have errors for missing required vars
            errors = [r for r in results if not r.is_valid]
            assert len(errors) > 0
            
        finally:
            # Restore original values
            for var, value in original_values.items():
                if value is not None:
                    os.environ[var] = value
    
    def test_validator_accepts_configured_vars(self):
        """Test that validator passes when core vars are configured."""
        import os
        from app.core.validation import ProviderValidator
        
        # Save original values
        original_values = {}
        test_vars = {
            "DEEPGRAM_API_KEY": "test_key",
            "GROQ_API_KEY": "test_key",
            "CARTESIA_API_KEY": "test_key",
            "SUPABASE_URL": "https://test.supabase.co",
            "SUPABASE_SERVICE_KEY": "test_key",
            "VONAGE_API_KEY": "test_key",
            "VONAGE_API_SECRET": "test_key",
        }
        
        for var in test_vars:
            original_values[var] = os.environ.get(var)
        
        try:
            # Set test values
            for var, value in test_vars.items():
                os.environ[var] = value
            
            validator = ProviderValidator(strict=False)
            all_valid, results = validator.validate_all()
            
            # All core required vars are set, so we should pass
            # Check that the configured vars show as successful
            successes = [r for r in results if r.is_valid and "WARNING" not in r.message]
            assert len(successes) >= 5  # At least 5 successful validations
            
        finally:
            # Restore original values
            for var, value in original_values.items():
                if value is not None:
                    os.environ[var] = value
                elif var in os.environ:
                    del os.environ[var]


class TestCampaignsAPI:
    """Basic tests for campaigns API with mocked Supabase."""
    
    @pytest.mark.asyncio
    async def test_campaigns_supabase_validation(self):
        """Test that campaigns endpoint validates Supabase config."""
        import os
        from app.api.v1.endpoints.campaigns import get_supabase
        
        # Save original values
        original_url = os.environ.get("SUPABASE_URL")
        original_key = os.environ.get("SUPABASE_SERVICE_KEY")
        
        try:
            # Clear Supabase config
            if "SUPABASE_URL" in os.environ:
                del os.environ["SUPABASE_URL"]
            if "SUPABASE_SERVICE_KEY" in os.environ:
                del os.environ["SUPABASE_SERVICE_KEY"]
            
            # Should raise RuntimeError
            with pytest.raises(RuntimeError) as exc_info:
                get_supabase()
            
            assert "SUPABASE_URL" in str(exc_info.value)
            
        finally:
            # Restore original values
            if original_url:
                os.environ["SUPABASE_URL"] = original_url
            if original_key:
                os.environ["SUPABASE_SERVICE_KEY"] = original_key

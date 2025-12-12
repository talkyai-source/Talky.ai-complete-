"""
Tests for New API Endpoints
Tests the frontend-aligned endpoints: auth, plans, dashboard, analytics, calls, recordings, contacts, clients, admin

Note: These tests require proper Supabase environment to be configured.
If Supabase is not available, tests will be skipped.
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import os
import sys


# Set test environment variables before importing app
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")


# Try to import app - skip tests if import fails
try:
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    IMPORT_SUCCESS = True
except Exception as e:
    IMPORT_SUCCESS = False
    client = None
    print(f"Warning: Could not import app for API tests: {e}")


# Skip all tests if app couldn't be imported
pytestmark = pytest.mark.skipif(
    not IMPORT_SUCCESS,
    reason="App import failed - Supabase or other dependencies not available"
)


class TestPlansEndpoint:
    """Tests for /api/v1/plans endpoint"""
    
    @pytest.mark.skipif(not IMPORT_SUCCESS, reason="App not available")
    @patch("app.api.v1.endpoints.plans.get_supabase")
    def test_list_plans_returns_list(self, mock_supabase):
        """Test that plans endpoint returns a list"""
        # Mock Supabase response
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.order.return_value.execute.return_value.data = [
            {
                "id": "basic",
                "name": "Basic",
                "price": 29,
                "description": "Test plan",
                "minutes": 300,
                "agents": 1,
                "concurrent_calls": 1,
                "features": ["Feature 1"],
                "not_included": ["Feature 2"],
                "popular": False
            }
        ]
        mock_supabase.return_value = mock_client
        
        response = client.get("/api/v1/plans/")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == "basic"
        assert data[0]["name"] == "Basic"
        assert data[0]["price"] == 29


class TestAuthEndpoints:
    """Tests for /api/v1/auth endpoints"""
    
    @pytest.mark.skipif(not IMPORT_SUCCESS, reason="App not available")
    def test_me_requires_authorization(self):
        """Test that /me endpoint requires auth header"""
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401
        assert "Authorization" in response.json()["detail"] or "authorization" in response.json()["detail"].lower()
    
    @pytest.mark.skipif(not IMPORT_SUCCESS, reason="App not available")
    def test_me_rejects_invalid_token_format(self):
        """Test that invalid token format is rejected"""
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "InvalidFormat"}
        )
        assert response.status_code == 401


class TestDashboardEndpoint:
    """Tests for /api/v1/dashboard endpoint"""
    
    @pytest.mark.skipif(not IMPORT_SUCCESS, reason="App not available")
    def test_dashboard_requires_auth(self):
        """Test that dashboard endpoint requires authentication"""
        response = client.get("/api/v1/dashboard/summary")
        assert response.status_code == 401


class TestAnalyticsEndpoint:
    """Tests for /api/v1/analytics endpoint"""
    
    @pytest.mark.skipif(not IMPORT_SUCCESS, reason="App not available")
    def test_analytics_requires_auth(self):
        """Test that analytics endpoint requires authentication"""
        response = client.get("/api/v1/analytics/calls")
        assert response.status_code == 401


class TestCallsEndpoints:
    """Tests for /api/v1/calls endpoints"""
    
    @pytest.mark.skipif(not IMPORT_SUCCESS, reason="App not available")
    def test_calls_requires_auth(self):
        """Test that calls list requires authentication"""
        response = client.get("/api/v1/calls/")
        assert response.status_code == 401
    
    @pytest.mark.skipif(not IMPORT_SUCCESS, reason="App not available")
    def test_call_detail_requires_auth(self):
        """Test that call detail requires authentication"""
        response = client.get("/api/v1/calls/test-call-id")
        assert response.status_code == 401


class TestRecordingsEndpoints:
    """Tests for /api/v1/recordings endpoints"""
    
    @pytest.mark.skipif(not IMPORT_SUCCESS, reason="App not available")
    def test_recordings_requires_auth(self):
        """Test that recordings list requires authentication"""
        response = client.get("/api/v1/recordings/")
        assert response.status_code == 401


class TestClientsEndpoints:
    """Tests for /api/v1/clients endpoints"""
    
    @pytest.mark.skipif(not IMPORT_SUCCESS, reason="App not available")
    def test_clients_requires_auth(self):
        """Test that clients list requires authentication"""
        response = client.get("/api/v1/clients/")
        assert response.status_code == 401


class TestAdminEndpoints:
    """Tests for /api/v1/admin endpoints"""
    
    @pytest.mark.skipif(not IMPORT_SUCCESS, reason="App not available")
    def test_admin_tenants_requires_auth(self):
        """Test that admin tenants endpoint requires authentication"""
        response = client.get("/api/v1/admin/tenants")
        assert response.status_code == 401
    
    @pytest.mark.skipif(not IMPORT_SUCCESS, reason="App not available")
    def test_admin_users_requires_auth(self):
        """Test that admin users endpoint requires authentication"""
        response = client.get("/api/v1/admin/users")
        assert response.status_code == 401


class TestEndpointImports:
    """Test that all endpoints are properly imported and registered"""
    
    @pytest.mark.skipif(not IMPORT_SUCCESS, reason="App not available")
    def test_all_routes_registered(self):
        """Verify all expected routes are registered"""
        routes = [route.path for route in app.routes]
        
        # Check essential routes exist (with or without trailing slash)
        assert any("/api/v1/auth/me" in r for r in routes)
        assert any("/api/v1/plans" in r for r in routes)
        assert any("/api/v1/dashboard/summary" in r for r in routes)
        assert any("/api/v1/analytics/calls" in r for r in routes)
        assert any("/api/v1/calls" in r for r in routes)
        assert any("/api/v1/recordings" in r for r in routes)
        assert any("/api/v1/clients" in r for r in routes)
        assert any("/api/v1/admin/tenants" in r for r in routes)
        assert any("/api/v1/admin/users" in r for r in routes)

"""DSAR must be reachable through the application's actual router assembly."""
import httpx
import pytest
from fastapi import FastAPI

from app.api.v1.dependencies import CurrentUser, get_current_user
from app.api.v1.endpoints.admin.dsar import get_dsar_service
from app.api.v1.routes import api_router
from tests.unit.test_round3_dsar_api_cli import ACTOR, PHONE, TENANT, Service


@pytest.fixture(scope="module")
def mounted_app():
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    return app


@pytest.mark.parametrize("role,status", [("platform_admin", 200), ("user", 403)])
async def test_dsar_preview_is_mounted_with_platform_admin_gate(mounted_app, role, status):
    app = mounted_app
    service = Service()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=ACTOR, email="test@example.invalid", tenant_id=TENANT, role=role,
    )
    app.dependency_overrides[get_dsar_service] = lambda: service
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/admin/dsar/preview", json={"tenant_id": TENANT, "phone": PHONE})
    assert response.status_code == status, response.text
    assert len(service.calls) == (1 if role == "platform_admin" else 0)

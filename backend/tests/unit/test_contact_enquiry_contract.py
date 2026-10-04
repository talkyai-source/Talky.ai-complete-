"""The public intake acknowledges durable receipt, never email delivery."""
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_pool
from app.api.v1.endpoints import contact_enquiries as api
from app.core.error_handlers import register_error_handlers
from app.core.security.csrf import CSRFMiddleware
from app.domain.services import contact_enquiry_service as service


def payload(**changes):
    return {"request_id": str(uuid4()), "name": "A Customer", "email": "buyer@example.com",
            "company": "Example", "message": "Please explain your service.", **changes}


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.include_router(api.admin_router, prefix="/api/v1/admin")
    register_error_handlers(app)
    app.dependency_overrides[get_db_pool] = lambda: object()
    app.state.limiter = api.limiter
    monkeypatch.setattr(api.limiter, "enabled", False)

    async def no_user():
        raise HTTPException(status_code=401, detail="Authentication required")

    app.dependency_overrides[get_current_user] = no_user
    with TestClient(app) as test_client:
        yield test_client


def test_submit_returns_only_committed_receipt_and_replay_keeps_it(client, monkeypatch):
    receipt = {"status": "accepted", "receipt_id": uuid4(),
               "accepted_at": datetime(2026, 10, 4, tzinfo=UTC)}
    submit = AsyncMock(side_effect=[(receipt, True), (receipt, False)])
    monkeypatch.setattr(service, "submit_enquiry", submit)
    body = payload()
    first = client.post("/api/v1/public/contact-enquiries", json=body)
    second = client.post("/api/v1/public/contact-enquiries", json=body)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json() == second.json()
    assert set(first.json()) == {"status", "receipt_id", "accepted_at"}
    assert first.json()["status"] == "accepted"


@pytest.mark.parametrize("changes", [
    {"name": " "}, {"name": "x" * 121}, {"name": "Buyer\x00"},
    {"email": "not-an-address"}, {"company": "x" * 121},
    {"message": " "}, {"message": "x" * 501}, {"message": "bad\x00text"},
    {"request_id": "not-a-uuid"}, {"tenant_id": str(uuid4())},
])
def test_invalid_public_payload_never_reaches_storage(client, monkeypatch, changes):
    submit = AsyncMock()
    monkeypatch.setattr(service, "submit_enquiry", submit)
    response = client.post("/api/v1/public/contact-enquiries", json=payload(**changes))
    assert response.status_code == 422
    submit.assert_not_called()


def test_public_payload_is_canonicalized_before_hashing(client, monkeypatch):
    submit = AsyncMock(return_value=({"status": "accepted", "receipt_id": uuid4(),
                                    "accepted_at": datetime.now(UTC)}, True))
    monkeypatch.setattr(service, "submit_enquiry", submit)
    response = client.post("/api/v1/public/contact-enquiries", json=payload(
        name="  A Customer  ", email="  BUYER@EXAMPLE.COM ", company=" Example ",
        message="  First line\r\nSecond line  ",
    ))
    assert response.status_code == 201
    submitted = submit.await_args.args[1]
    assert (submitted.name, submitted.email, submitted.company, submitted.message) == (
        "A Customer", "buyer@example.com", "Example", "First line\nSecond line")


def test_changed_payload_for_request_identity_is_conflict(client, monkeypatch):
    monkeypatch.setattr(service, "submit_enquiry", AsyncMock(side_effect=service.EnquiryConflict))
    response = client.post("/api/v1/public/contact-enquiries", json=payload())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "enquiry_request_conflict"


def test_storage_failure_never_reports_acceptance_or_exposes_details(client, monkeypatch):
    monkeypatch.setattr(service, "submit_enquiry", AsyncMock(side_effect=TimeoutError("private database address")))
    response = client.post("/api/v1/public/contact-enquiries", json=payload())
    assert response.status_code == 503
    assert "private database" not in response.text
    assert "receipt_id" not in response.text


@pytest.mark.parametrize("role", [None, "tenant_admin", "partner_admin", "user", "readonly"])
def test_only_platform_admin_can_read_or_update(client, monkeypatch, role):
    if role:
        client.app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id=str(uuid4()), email="staff@example.com", tenant_id=str(uuid4()), role=role)
    reads, writes = AsyncMock(), AsyncMock()
    monkeypatch.setattr(service, "list_enquiries", reads)
    monkeypatch.setattr(service, "update_enquiry_status", writes)
    expected = 401 if role is None else 403
    assert client.get("/api/v1/admin/contact-enquiries").status_code == expected
    assert client.patch(f"/api/v1/admin/contact-enquiries/{uuid4()}", json={"status": "handled"}).status_code == expected
    reads.assert_not_called()
    writes.assert_not_called()


def test_admin_list_bounds_and_missing_update(client, monkeypatch):
    admin = CurrentUser(id=str(uuid4()), email="operator@example.com", role="platform_admin")
    client.app.dependency_overrides[get_current_user] = lambda: admin
    listing = AsyncMock(return_value={"items": [], "total": 0, "limit": 20, "offset": 3})
    monkeypatch.setattr(service, "list_enquiries", listing)
    response = client.get("/api/v1/admin/contact-enquiries?status=new&limit=20&offset=3")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 3}
    assert listing.await_args.kwargs == {"status": "new", "limit": 20, "offset": 3}
    for query in ("status=deleted", "limit=0", "limit=101", "offset=-1"):
        assert client.get("/api/v1/admin/contact-enquiries?" + query).status_code == 422
    monkeypatch.setattr(service, "update_enquiry_status", AsyncMock(return_value=None))
    assert client.patch(f"/api/v1/admin/contact-enquiries/{uuid4()}", json={"status": "handled"}).status_code == 404
    assert client.patch(f"/api/v1/admin/contact-enquiries/{uuid4()}", json={"status": "deleted"}).status_code == 422


async def test_commit_failure_cannot_return_receipt(monkeypatch):
    row = {"id": uuid4(), "created_at": datetime.now(UTC), "payload_hash": "irrelevant"}

    @asynccontextmanager
    async def failed_commit(*args, **kwargs):
        yield type("Connection", (), {"fetchrow": AsyncMock(return_value=row)})()
        raise TimeoutError("Synthetic failure during commit")

    monkeypatch.setattr(service, "acquire_with_tenant", failed_commit)
    with pytest.raises(TimeoutError, match="during commit"):
        await service.submit_enquiry(object(), service.ContactEnquiryInput(**payload()))


def make_request(peer, forwarded="", real=""):
    return Request({"type": "http", "method": "POST", "path": "/api/v1/public/contact-enquiries",
        "client": (peer, 1234), "headers": [(b"x-forwarded-for", forwarded.encode()),
                                            (b"x-real-ip", real.encode())]})


def test_rate_key_ignores_caller_headers_without_explicit_proxy_trust(monkeypatch):
    monkeypatch.delenv("CONTACT_ENQUIRY_TRUSTED_PROXY_CIDRS", raising=False)
    request = make_request("198.51.100.4", "203.0.113.7", "192.0.2.8")
    assert api.intake_client_ip(request) == "198.51.100.4"


def test_rate_key_walks_only_the_trusted_proxy_suffix(monkeypatch):
    monkeypatch.setenv("CONTACT_ENQUIRY_TRUSTED_PROXY_CIDRS", "127.0.0.0/8,10.0.0.0/8")
    request = make_request("127.0.0.1", "192.0.2.123,198.51.100.4,10.0.0.3")
    assert api.intake_client_ip(request) == "198.51.100.4"
    assert api.intake_client_ip(make_request("198.51.100.5", "192.0.2.1")) == "198.51.100.5"
    assert api.intake_client_ip(make_request("127.0.0.1", "bad address")) == "127.0.0.1"


def test_invalid_limit_cannot_disable_protection(monkeypatch):
    for value in ("0", "-1", "unlimited", "999999"):
        monkeypatch.setenv("CONTACT_ENQUIRY_RATE_LIMIT_PER_MINUTE", value)
        assert api.intake_rate_limit() == "10/minute"


def test_rate_limit_rejects_before_storage(client, monkeypatch):
    monkeypatch.setattr(api.limiter, "enabled", True)
    monkeypatch.setenv("CONTACT_ENQUIRY_RATE_LIMIT_PER_MINUTE", "2")
    api.limiter.reset()
    submit = AsyncMock(return_value=({"status": "accepted", "receipt_id": uuid4(),
                                    "accepted_at": datetime.now(UTC)}, True))
    monkeypatch.setattr(service, "submit_enquiry", submit)
    try:
        for _ in range(2):
            assert client.post("/api/v1/public/contact-enquiries", json=payload()).status_code == 201
        denied = client.post("/api/v1/public/contact-enquiries", json=payload(),
                             headers={"X-Forwarded-For": "203.0.113.200"})
        assert denied.status_code == 429
        assert denied.headers.get("retry-after")
        assert submit.await_count == 2
    finally:
        api.limiter.reset()


@pytest.mark.parametrize("origin,expected", [
    ("https://frontend.example", 201), ("https://untrusted.example", 403), (None, 403),
])
def test_public_route_preserves_existing_origin_protection(monkeypatch, origin, expected):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.add_middleware(CSRFMiddleware, allowed_origins=["https://frontend.example"])
    app.state.limiter = api.limiter
    monkeypatch.setattr(api.limiter, "enabled", False)
    app.dependency_overrides[get_db_pool] = lambda: object()
    submit = AsyncMock(return_value=({"status": "accepted", "receipt_id": uuid4(),
                                    "accepted_at": datetime.now(UTC)}, True))
    monkeypatch.setattr(service, "submit_enquiry", submit)
    with TestClient(app) as public_client:
        response = public_client.post("/api/v1/public/contact-enquiries", json=payload(),
                                      headers={"Origin": origin} if origin else {})
    assert response.status_code == expected
    assert submit.await_count == (1 if expected == 201 else 0)

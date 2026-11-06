"""F12-06 platform-admin boundary and safe CLI contract, with no real I/O."""
from __future__ import annotations

import importlib
import importlib.util
import asyncio
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from zipfile import ZipFile

from fastapi import FastAPI
import httpx
import pytest

from app.api.v1.dependencies import CurrentUser, get_current_user


TENANT = "a0000000-0000-4000-8000-000000000001"
ACTOR = "c0000000-0000-4000-8000-000000000003"
PHONE = "+14155552671"


def api_module():
    path = Path(__file__).resolve().parents[2] / "app/api/v1/endpoints/admin/dsar.py"
    assert path.is_file(), "F12-06 is missing its platform-admin DSAR API"
    return importlib.import_module("app.api.v1.endpoints.admin.dsar")


def cli_module():
    path = Path(__file__).resolve().parents[2] / "scripts/dsar.py"
    assert path.is_file(), "F12-06 is missing its dry-run DSAR CLI"
    name = "_round3_dsar_cli"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Service:
    def __init__(self):
        self.calls = []
        self.output = None

    async def preview(self, scope, *, actor_id):
        assert str(scope.tenant_id) == TENANT and str(actor_id) == ACTOR
        self.calls.append(("preview", scope))
        return {"mode": "dry_run", "destructive_apply_supported": False,
                "counts": {"calls": 1}, "issues": []}

    async def export(self, scope, *, actor_id, output):
        assert str(scope.tenant_id) == TENANT and str(actor_id) == ACTOR
        self.calls.append(("export", scope))
        self.output = Path(output)
        with ZipFile(self.output, "x") as archive:
            archive.writestr("manifest.json", '{"complete":false}')
        return SimpleNamespace(path=self.output, manifest={"complete": False})


def app_and_service(role):
    api = api_module()
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1/admin")
    service = Service()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=ACTOR, email="admin@example.invalid", tenant_id=TENANT, role=role,
    )
    app.dependency_overrides[api.get_dsar_service] = lambda: service
    return app, service


@pytest.mark.parametrize("role", ["user", "admin", "partner_admin"])
@pytest.mark.parametrize("action", ["preview", "export"])
async def test_nonplatform_admin_cannot_use_dsar_api(role, action):
    app, service = app_and_service(role)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/admin/dsar/{action}", json={"tenant_id": TENANT, "phone": PHONE})
    assert response.status_code == 403 and service.calls == []


async def test_admin_preview_exposes_counts_and_has_no_apply_route():
    app, service = app_and_service("platform_admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/admin/dsar/preview", json={"tenant_id": TENANT, "phone": PHONE})
        apply = await client.post("/api/v1/admin/dsar/erase", json={"tenant_id": TENANT, "phone": PHONE, "apply": True})
    assert response.status_code == 200 and response.json()["counts"] == {"calls": 1}
    assert response.json()["destructive_apply_supported"] is False
    assert apply.status_code == 404 and len(service.calls) == 1


async def test_export_is_private_attachment_and_temporary_file_is_cleaned():
    app, service = app_and_service("platform_admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/admin/dsar/export", json={"tenant_id": TENANT, "phone": PHONE})
    assert response.status_code == 200 and response.content.startswith(b"PK")
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert PHONE not in response.headers["content-disposition"]
    assert service.output is not None and not service.output.exists()


async def _download_response():
    api = api_module()
    service = Service()
    response = await api.export_dsar(
        api.DsarScope(tenant_id=TENANT, phone=PHONE),
        current_user=CurrentUser(id=ACTOR, email="admin@example.invalid", role="platform_admin"),
        service=service,
    )
    assert service.output is not None and service.output.exists()
    return response, service.output


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def test_malformed_range_still_removes_private_dsar_archive_and_directory():
    response, path = await _download_response()
    messages = []

    async def send(message):
        messages.append(message)

    try:
        await response(
            {"type": "http", "method": "GET", "headers": [(b"range", b"bytes=not-a-range")]},
            _receive, send,
        )
        assert messages[0]["status"] == 400
        assert not path.exists(), "Malformed Range left a private DSAR archive on disk"
        assert not path.parent.exists(), "Malformed Range left the private export directory"
    finally:
        # Keep the deliberately failing regression itself free of fixture leaks.
        shutil.rmtree(path.parent, ignore_errors=True)


@pytest.mark.parametrize("failure_type", [asyncio.CancelledError, ConnectionResetError])
async def test_interrupted_download_removes_archive_and_preserves_send_failure(failure_type):
    response, path = await _download_response()
    failure = failure_type("synthetic interrupted download")

    async def send(message):
        if message["type"] == "http.response.body":
            raise failure

    try:
        with pytest.raises(failure_type) as caught:
            await response({"type": "http", "method": "GET", "headers": []}, _receive, send)
        assert caught.value is failure
        assert not path.exists(), "Interrupted send left a private DSAR archive on disk"
        assert not path.parent.exists(), "Interrupted send left the private export directory"
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


@pytest.mark.parametrize("body", [
    {"tenant_id": TENANT},
    {"tenant_id": TENANT, "phone": PHONE, "email": "person@example.invalid"},
    {"tenant_id": TENANT, "whole_tenant": True, "phone": PHONE},
    {"tenant_id": TENANT, "phone": PHONE, "apply": True},
    {"tenant_id": "not-a-uuid", "phone": PHONE},
])
async def test_api_rejects_ambiguous_or_destructive_request_fields(body):
    app, service = app_and_service("platform_admin")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/admin/dsar/preview", json=body)
    assert response.status_code == 422 and service.calls == []


def test_cli_requires_explicit_actor_scope_and_supports_only_preview_or_export(tmp_path):
    parser = cli_module().build_parser()
    args = parser.parse_args(["preview", "--tenant", TENANT, "--actor", ACTOR, "--phone", PHONE])
    assert args.action == "preview" and str(args.tenant) == TENANT and str(args.actor) == ACTOR
    exported = parser.parse_args(["export", "--tenant", TENANT, "--actor", ACTOR,
                                  "--whole-tenant", "--output", str(tmp_path / "tenant.zip")])
    assert exported.action == "export" and exported.whole_tenant is True


@pytest.mark.parametrize("argv", [
    ["erase", "--tenant", TENANT, "--actor", ACTOR, "--phone", PHONE],
    ["preview", "--tenant", TENANT, "--actor", ACTOR, "--phone", PHONE, "--apply"],
    ["preview", "--tenant", TENANT, "--phone", PHONE],
    ["preview", "--tenant", TENANT, "--actor", ACTOR],
    ["export", "--tenant", TENANT, "--actor", ACTOR, "--phone", PHONE],
])
def test_cli_refuses_erase_missing_scope_actor_or_export_destination(argv):
    with pytest.raises(SystemExit) as caught:
        cli_module().build_parser().parse_args(argv)
    assert caught.value.code == 2


async def test_cli_dispatches_preview_through_same_service_without_opening_database(monkeypatch, capsys):
    cli = cli_module()
    service = Service()
    args = cli.build_parser().parse_args(["preview", "--tenant", TENANT, "--actor", ACTOR, "--phone", PHONE])
    result = await cli.execute(args, service=service)
    output = capsys.readouterr().out
    assert result == 0 and [action for action, _ in service.calls] == ["preview"]
    assert '"dry_run"' in output and PHONE not in output

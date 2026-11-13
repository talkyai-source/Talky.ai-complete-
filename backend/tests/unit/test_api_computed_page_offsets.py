"""A valid page and size must still reject an excessive combined SQL offset."""
import importlib
import inspect
import socket

import pytest
from fastapi import HTTPException
from fastapi.params import Depends, Param
from starlette.requests import Request

from app.api.v1.dependencies import CurrentUser
from tests.unit.test_api_route_input_contract import mounted_routes, routes_for


PAGE_ROUTES = [
    ("admin.actions", "get_admin_actions"), ("admin.calls", "get_call_history"),
    ("admin.connectors", "list_admin_connectors"), ("admin.health.incidents", "list_incidents"),
    ("admin.media", "list_admin_recordings"), ("admin.media", "list_admin_feedback"),
    ("admin.partners", "list_partners"), ("assistant_ws", "list_conversations"),
    ("assistant_ws", "list_actions"), ("calls", "list_rejected_inbound_calls"),
    ("calls", "list_calls"), ("campaigns", "list_campaign_calls_with_transcripts"),
    ("campaigns", "get_campaign_jobs"), ("campaigns", "list_campaign_contacts"),
    ("conversation_reviews", "list_reviews"), ("recordings", "list_recordings"),
]
ID = "aabbccdd-1234-4abc-8def-0123456789ab"


@pytest.mark.parametrize("module,function", PAGE_ROUTES,
                         ids=[f"{module}.{function}" for module, function in PAGE_ROUTES])
async def test_combined_offset_is_rejected_before_business_work(monkeypatch, mounted_routes, module, function):
    reached = []

    def blocked(*_args, **_kwargs):
        reached.append("business_or_network")
        raise AssertionError("Invalid pagination reached business work")

    class Boundary:
        def __getattr__(self, _name):
            return blocked()

    owner = importlib.import_module("app.api.v1.endpoints." + module)
    monkeypatch.setattr("app.core.container.get_container", blocked)
    if hasattr(owner, "get_container"):
        monkeypatch.setattr(owner, "get_container", blocked)
    user = CurrentUser(id=ID, tenant_id=ID, email="synthetic@example.invalid", role="platform_admin")
    endpoint = routes_for(mounted_routes, module, function)[0].endpoint
    values = {"page": 102, "page_size": 100}
    for name, parameter in inspect.signature(endpoint).parameters.items():
        if name in values:
            continue
        if name in {"current_user", "admin_user", "user"}:
            values[name] = user
        elif name in {"db_client", "pool", "db_pool"}:
            values[name] = Boundary()
        elif name == "request":
            values[name] = Request({"type": "http", "method": "GET", "path": "/synthetic",
                                    "headers": [], "query_string": b"", "state": {}})
        elif name == "campaign_id":
            values[name] = ID
        elif isinstance(parameter.default, Param):
            values[name] = parameter.default.default
        elif isinstance(parameter.default, Depends):
            values[name] = Boundary()
        elif parameter.default is not inspect.Parameter.empty:
            values[name] = parameter.default
        else:
            raise AssertionError(f"Unreviewed required parameter {module}.{function}.{name}")
    status = None
    # Guard only the invocation: Windows' event-loop teardown needs its own
    # local socket pair after this test has returned.
    with monkeypatch.context() as network:
        network.setattr(socket, "socket", blocked)
        network.setattr(socket, "create_connection", blocked)
        network.setattr(socket, "getaddrinfo", blocked)
        try:
            await endpoint(**values)
        except HTTPException as exc:
            status = exc.status_code
        except Exception:
            status = 500
    assert status == 422, f"{module}.{function} accepted or executed page102*size100 (status={status})"
    assert reached == [], "The combined boundary must run before any business lookup"

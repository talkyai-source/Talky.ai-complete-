"""Mounted HTTP metadata guards; explicit local UUIDs, no provider-ID guessing."""
import pytest
from fastapi import FastAPI
from fastapi.routing import iter_route_contexts


PREFIX = "app.api.v1.endpoints."
PAGINATION = {
    "abuse_monitoring": ["get_abuse_alerts", "list_abuse_events"],
    "admin.actions": ["get_admin_actions"],
    "admin.calls": ["get_call_history"],
    "admin.campaigns": ["list_admin_campaigns"],
    "admin.connectors": ["list_admin_connectors"],
    "admin.health.incidents": ["list_incidents"],
    "admin.inbound": ["list_inbound_reassignments"],
    "admin.media": ["list_admin_feedback", "list_admin_media_deletions", "list_admin_recordings"],
    "admin.partners": ["list_partners"],
    "admin.supplier_evidence": ["list_statements"],
    "admin.tenants": ["list_tenants"],
    "admin.user_operations": ["list_user_operations"],
    "admin.users": ["list_users"],
    "alerts": ["list_alerts"],
    "assistant_ws": ["list_actions", "list_conversations"],
    "audit_logs": ["query_audit_logs"],
    "billing_topups": ["list_ledger", "list_orders"],
    "billing": ["list_invoices"],
    "call_limits": ["list_dnc_entries"],
    "calls": ["get_call_events", "list_calls", "list_rejected_inbound_calls"],
    "campaigns": ["get_campaign_jobs", "list_campaign_calls_with_transcripts", "list_campaign_contacts"],
    "contact_enquiries": ["list_contact_enquiries"],
    "conversation_reviews": ["list_reviews"],
    "dnc": ["list_dnc_entries"],
    "meetings": ["list_meetings"],
    "recordings": ["list_recordings"],
    "security_events": ["list_security_events"],
    "stream_events": ["list_stream_events"],
}

# The platform reconciliation CSV is an explicit bounded export, not a page of
# ordinary results. Its existing 50000-row cap/truncated marker needs separate
# export tests and must not be silently treated as an unbounded list exception.
PAGINATION_EXPORT_EXCEPTION = (PREFIX + "billing_topups", "reconciliation")

UUID_ROUTES = {
    "campaigns": {
        **dict.fromkeys(["get_campaign", "update_campaign", "list_campaign_calls_with_transcripts",
            "get_campaign_readiness", "start_campaign", "pause_campaign", "stop_campaign",
            "delete_campaign", "get_campaign_jobs", "get_campaign_stats", "add_contact_to_campaign",
            "list_campaign_contacts"], ("campaign_id",)),
        "update_contact_in_campaign": ("campaign_id", "contact_id"),
        "remove_contact_from_campaign": ("campaign_id", "contact_id"),
    },
    "calls": dict.fromkeys(["hangup_live_call", "get_call_redial", "redial_call", "get_call",
        "get_call_transcript", "get_call_summary", "get_call_events", "get_call_legs"], ("call_id",)),
    "admin.calls": dict.fromkeys(["get_admin_call_detail", "inspect_admin_crm_delivery", "terminate_call"], ("call_id",)),
    "clients": dict.fromkeys(["get_client", "delete_client"], ("client_id",)),
    "meetings": dict.fromkeys(["get_meeting", "update_meeting", "cancel_meeting"], ("meeting_id",)),
    "contacts": dict.fromkeys(["upload_campaign_contacts", "paste_campaign_contacts"], ("campaign_id",)),
    "connectors": dict.fromkeys(["get_connector", "delete_connector", "refresh_connector_tokens"], ("connector_id",)),
    "lead_details": {"get_lead_fields": ("campaign_id",), "set_lead_fields": ("campaign_id",),
        "get_lead_details": ("call_id",), "get_contact_lead_details": ("lead_id",),
        "correct_lead_detail": ("call_id",)},
}
VALID_UUID = "aabbccdd-1234-4abc-8def-0123456789ab"


@pytest.fixture(scope="module")
def mounted_routes():
    # Mount the real API router without lifespan, database, Redis or providers.
    from app.api.v1.routes import api_router
    app = FastAPI()
    app.include_router(api_router, prefix="/api/v1")
    routes = [route for route in iter_route_contexts(app.routes)
              if getattr(route, "dependant", None) is not None and route.methods]
    assert routes, "The mounted-route inventory must not silently inspect an empty router"
    return routes


def routes_for(routes, module, function):
    found = [route for route in routes if route.endpoint.__module__ == PREFIX + module
             and route.endpoint.__name__ == function]
    assert found, f"Reviewed route is not mounted: {module}.{function}"
    return found


def query_fields(dependant):
    fields = list(dependant.query_params)
    for dependency in dependant.dependencies:
        fields.extend(query_fields(dependency))
    return fields


PAGINATION_CASES = [(module, function) for module, functions in PAGINATION.items() for function in functions]


@pytest.mark.parametrize("module,function", PAGINATION_CASES,
                         ids=[f"{module}.{function}" for module, function in PAGINATION_CASES])
def test_mounted_listing_pagination_rejects_out_of_range_values(mounted_routes, module, function):
    for route in routes_for(mounted_routes, module, function):
        fields = [field for field in query_fields(route.dependant)
                  if field.name in {"page", "page_size", "limit", "offset"}]
        assert fields, f"Missing pagination metadata for {route.path}"
        for field in fields:
            valid = "0" if field.name == "offset" else "1"
            _, errors = field.validate(valid, {}, loc=("query", field.name))
            assert not errors, f"Valid lower boundary rejected: {route.path} {field.name}"
            invalids = ("-1", "10001") if field.name == "offset" else (
                "0", "201" if field.name in {"limit", "page_size"} else "10002")
            for invalid in invalids:
                _, errors = field.validate(invalid, {}, loc=("query", field.name))
                assert errors, f"Unbounded pagination: {route.path} {field.name}={invalid}"


def test_every_mounted_pagination_family_is_reviewed(mounted_routes):
    reviewed = {(PREFIX + module, function) for module, function in PAGINATION_CASES}
    reviewed.add(PAGINATION_EXPORT_EXCEPTION)
    missing = set()
    for route in mounted_routes:
        if any(field.name in {"page", "page_size", "limit", "offset"}
               for field in query_fields(route.dependant)):
            identity = (route.endpoint.__module__, route.endpoint.__name__)
            if identity not in reviewed:
                missing.add(identity)
    assert not missing, f"New pagination requires an explicit reviewed contract: {sorted(missing)}"


UUID_CASES = [(module, function, parameter) for module, functions in UUID_ROUTES.items()
              for function, parameters in functions.items() for parameter in parameters]


@pytest.mark.parametrize("module,function,parameter", UUID_CASES,
                         ids=[f"{module}.{function}.{parameter}" for module, function, parameter in UUID_CASES])
def test_reviewed_local_resource_paths_validate_uuid_before_handlers(mounted_routes, module, function, parameter):
    for route in routes_for(mounted_routes, module, function):
        fields = [field for field in route.dependant.path_params if field.name == parameter]
        assert len(fields) == 1, f"Missing local resource path: {route.path} {parameter}"
        field = fields[0]
        value, errors = field.validate(VALID_UUID, {}, loc=("path", parameter))
        assert not errors and str(value) == VALID_UUID
        _, errors = field.validate("not-a-uuid", {}, loc=("path", parameter))
        assert errors, f"Local business ID reaches handler unvalidated: {route.path} {parameter}"


def test_provider_names_keep_their_opaque_non_uuid_contract(mounted_routes):
    controls = [("/connectors/{type}/authorize", "type", "hubspot"),
                ("/crm-deliveries/{provider}/inspection", "provider", "salesforce"),
                ("/lead-details/{field_key}", "field_key", "customer_email")]
    for suffix, parameter, value in controls:
        routes = [route for route in mounted_routes if route.path.endswith(suffix)]
        assert routes, f"Provider control route missing: {suffix}"
        for route in routes:
            field = next(field for field in route.dependant.path_params if field.name == parameter)
            parsed, errors = field.validate(value, {}, loc=("path", parameter))
            assert not errors and parsed == value

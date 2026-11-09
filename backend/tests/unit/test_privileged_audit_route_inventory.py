"""Mounted privileged-write audit inventory; missing entries fail visibly.

This wiring guard complements behavioral rollback tests. Source inspection is
not evidence that a transaction really committed. Never exempt a route merely
because its implementation belongs to another worker.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import importlib
import inspect
from pathlib import Path
import textwrap

from fastapi import APIRouter, Depends
from fastapi.routing import iter_route_contexts

from app.api.v1.dependencies import require_admin_tenant
from app.core.security.rbac import Permission, UserRole, require_permission, require_role


WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PRIVILEGED_DEPENDENCIES = {
    "require_admin", "require_admin_tenant", "require_platform_admin",
    "require_internal_service", "verify_admin_token",
}
# These are ordinary tenant operations, including calendar/reminder management
# and finance-user checkout. A dependency named permission_checker alone does
# not turn every CRUD route into an administrator operation. New permissions
# default to privileged until their actual authority is reviewed.
ORDINARY_PERMISSIONS = {
    Permission.CAMPAIGNS_CREATE, Permission.CAMPAIGNS_READ,
    Permission.CAMPAIGNS_UPDATE, Permission.CAMPAIGNS_DELETE,
    Permission.CAMPAIGNS_ADMIN, Permission.INBOUND_READ, Permission.INBOUND_ASSIGN,
    Permission.USERS_READ, Permission.TENANTS_READ,
    Permission.BILLING_READ, Permission.BILLING_UPDATE,
    Permission.CALLS_CREATE, Permission.CALLS_READ,
    Permission.RECORDINGS_READ, Permission.RECORDINGS_DOWNLOAD,
    Permission.CONNECTORS_CREATE, Permission.CONNECTORS_READ,
    Permission.CONNECTORS_UPDATE, Permission.CONNECTORS_DELETE,
    Permission.EMAIL_SEND, Permission.SMS_SEND, Permission.CALENDAR_READ,
    Permission.CALENDAR_MANAGE, Permission.REMINDERS_MANAGE,
    Permission.SUPPORT_REPORT, Permission.SUPPORT_READ_TEAM,
    Permission.ANALYTICS_READ, Permission.ANALYTICS_EXPORT,
}
PRIVILEGED_PREFIXES = (
    "/admin/", "/rbac/", "/telephony/sip/", "/telephony/providers",
    "/tenant-ai-credentials", "/webhooks/secure/admin/",
)


@dataclass(frozen=True)
class Audited:
    endpoint: str
    boundaries: tuple[str, ...]
    behavior_test: str


def record(module, function, proof="unit/test_privileged_mutation_audit.py", *helpers):
    target = f"app.api.v1.endpoints.{module}.{function}"
    return Audited(target, (target, *helpers), proof)


# Explicit full paths and function identities: a new method, alias or changed
# handler is not silently inherited. AUDITED contains wiring review plus a
# behavioral test reference, not a claim that every referenced suite ran today.
AUDITED = {
    ("POST", "/rbac/roles/{role_id}/permissions"): record("rbac.roles", "add_permission_to_role"),
    ("DELETE", "/rbac/roles/{role_id}/permissions"): record("rbac.roles", "remove_permission_from_role"),
    ("POST", "/rbac/tenant-users"): record("rbac.tenant_users", "add_user_to_tenant"),
    ("PATCH", "/rbac/tenant-users/{tenant_user_id}"): record("rbac.tenant_users", "update_tenant_user"),
    ("DELETE", "/rbac/tenant-users/{tenant_user_id}"): record("rbac.tenant_users", "remove_user_from_tenant"),
    ("POST", "/admin/abuse/rules"): record("abuse_monitoring", "create_abuse_rule"),
    ("PUT", "/admin/abuse/rules/{rule_id}"): record("abuse_monitoring", "update_abuse_rule"),
    ("DELETE", "/admin/abuse/rules/{rule_id}"): record("abuse_monitoring", "delete_abuse_rule"),
    ("POST", "/admin/abuse/events/{event_id}/resolve"): record("abuse_monitoring", "resolve_abuse_event"),
    ("POST", "/tenant-ai-credentials/"): record("tenant_ai_credentials", "create_credential"),
    ("POST", "/tenant-ai-credentials/{credential_id}/check"): record("tenant_ai_credentials", "check_credential"),
    ("DELETE", "/tenant-ai-credentials/{credential_id}"): record("tenant_ai_credentials", "disable_credential"),
}

for method, path, function in (
    ("PUT", "/telephony/providers/{provider}", "save_provider"),
    ("DELETE", "/telephony/providers/{provider}", "delete_provider"),
    ("POST", "/telephony/providers/{provider}/test", "test_provider"),
    ("POST", "/telephony/providers/activate", "activate_provider"),
):
    AUDITED[method, path] = record("telephony_providers", function,
                                   "unit/test_telephony_provider_audit.py")

for resource, module, singular, active_helper in (
    ("trunks", "trunks", "sip_trunk", "_set_trunk_active_state"),
    ("codec-policies", "codec_policies", "codec_policy", "_set_codec_policy_active_state"),
    ("route-policies", "route_policies", "route_policy", "_set_route_policy_active_state"),
):
    parameter = "trunk_id" if resource == "trunks" else "policy_id"
    base = f"/telephony/sip/{resource}"
    target = base + "/{" + parameter + "}"
    for method, path, function, helpers in (
        ("POST", base, f"create_{singular}", ()),
        ("PATCH", target, f"update_{singular}", ()),
        ("POST", target + "/activate", f"activate_{singular}", (active_helper,)),
        ("POST", target + "/deactivate", f"deactivate_{singular}", (active_helper,)),
    ):
        AUDITED[method, path] = record(
            f"telephony_sip.{module}", function, "unit/test_sip_privileged_audit.py",
            *(f"app.api.v1.endpoints.telephony_sip.{module}.{name}" for name in helpers))

for method, path, function in (
    ("POST", "/telephony/sip/trunks/{trunk_id}/test", "test_sip_trunk"),
    ("PUT", "/telephony/sip/trunks/pool-assignment", "set_pool_assignment"),
    ("PUT", "/telephony/sip/trunks/campaign-assignment", "set_campaign_trunk_assignment"),
    ("DELETE", "/telephony/sip/trunks/{trunk_id}", "delete_sip_trunk"),
):
    AUDITED[method, path] = record("telephony_sip.trunks", function,
                                   "unit/test_sip_additional_write_audit.py")

AUDITED["POST", "/webhooks/secure/admin/configure"] = record(
    "webhooks_secure", "configure_webhook", "unit/test_webhook_transactional_audit.py")

# Existing central AuditLogger transaction boundaries, retained as wiring
# coverage. Service identities use module:Class.method to avoid importing a
# class as a module. Every declared helper must be reachable from its handler.
for method, path, function in (
    ("POST", "/admin/users", "create_user"),
    ("PATCH", "/admin/users/{user_id}", "update_user"),
    ("DELETE", "/admin/users/{user_id}", "delete_user"),
    ("POST", "/admin/users/create-requests/{request_id}/cancel", "cancel_create_request"),
    ("POST", "/admin/users/{user_id}/primary-membership", "configure_primary_membership"),
):
    AUDITED[method, path] = record("admin.users", function, "unit/test_admin_user_operations.py")
for path, function in (("/admin/users/operations", "prepare_user_operation"),
                       ("/admin/users/operations/{operation_id}/cancel", "cancel_user_operation")):
    AUDITED["POST", path] = record("admin.user_operations", function, "unit/test_admin_user_operations.py")

for method, path, function in (
    ("POST", "/admin/tenants", "create_tenant"),
    ("PATCH", "/admin/tenants/{tenant_id}/quota", "update_tenant_quota"),
    ("POST", "/admin/tenants/{tenant_id}/suspend", "suspend_tenant"),
    ("POST", "/admin/tenants/{tenant_id}/resume", "resume_tenant"),
    ("POST", "/admin/tenants/{tenant_id}/archive", "archive_tenant"),
):
    helpers = ["app.api.v1.endpoints.admin.tenants._lock_operator_write"]
    if function != "create_tenant":
        helpers.insert(0, "app.api.v1.endpoints.admin.tenants._mutate_tenant")
    AUDITED[method, path] = record("admin.tenants", function, "unit/test_admin_tenant_lifecycle.py", *helpers)

for path, function, service_method in (
    ("/admin/connectors/{connector_id}/reconnect", "force_reconnect_connector", "refresh"),
    ("/admin/connectors/{connector_id}/revoke", "revoke_connector", "disable"),
):
    prefix = "app.services.connector_lifecycle:ConnectorLifecycle."
    AUDITED["POST", path] = record("admin.connectors", function,
        "integration/test_connector_audit_atomicity.py", prefix + service_method,
        prefix + "_begin", prefix + "_record")

for path, function in (("/admin/usage/statements", "import_statement"),
                       ("/admin/usage/statements/{statement_id}/reviews", "review_statement")):
    AUDITED["POST", path] = record("admin.supplier_evidence", function,
        "integration/test_supplier_evidence.py", "app.api.v1.endpoints.admin.supplier_evidence._record")

for path, function, service_method in (
    ("/admin/dsar/preview", "preview_dsar", "preview"),
    ("/admin/dsar/export", "export_dsar", "export"),
):
    prefix = "app.domain.services.dsar_service:DsarService."
    AUDITED["POST", path] = record("admin.dsar", function,
        "unit/test_round3_dsar_export.py", prefix + service_method,
        prefix + "_snapshot", prefix + "_audit")

for method, path, function, service_method in (
    ("POST", "/dnc/", "add_dnc_entry", "add"),
    ("POST", "/dnc/caller-opt-out", "caller_opt_out", "add_caller_opt_out"),
    ("DELETE", "/dnc/{entry_id}", "remove_dnc_entry", "remove"),
):
    prefix = "app.domain.services.dnc_service:DNCService."
    helpers = [prefix + service_method]
    if service_method == "add_caller_opt_out":
        helpers.append(prefix + "add")
    helpers.append("app.domain.services.dnc_service._audit_dnc_change")
    AUDITED[method, path] = record("dnc", function, "unit/test_dnc_audit_trail.py", *helpers)

# QA F12-10 (rest): the platform-admin aliases use the same audited service.
for method, path, function, service_method in (
    ("POST", "/admin/dnc", "add_dnc_entry", "add"),
    ("DELETE", "/admin/dnc/{entry_id}", "remove_dnc_entry", "remove_any"),
):
    AUDITED[method, path] = record(
        "call_limits", function, "unit/test_admin_dnc_audit.py",
        "app.domain.services.dnc_service:DNCService." + service_method,
        "app.domain.services.dnc_service._audit_dnc_change",
    )

for method, path, function, module, service_method, begin in (
    ("PATCH", "/admin/support/tickets/{ticket_id}", "patch_ticket", "staff_service", "patch_ticket", "_begin_staff_write"),
    ("POST", "/admin/support/tickets/{ticket_id}/messages", "staff_message", "staff_service", "add_staff_message", "_begin_staff_write"),
    ("POST", "/admin/support/articles", "create_article", "article_admin", "create_article", "_begin"),
    ("PATCH", "/admin/support/articles/{article_id}", "update_article", "article_admin", "update_article", "_begin"),
):
    prefix = f"app.domain.services.support.{module}."
    AUDITED[method, path] = record("support.admin", function,
        "security/test_support_staff_and_notes.py", prefix + service_method, prefix + begin)

for path, function, helpers in (
    ("/admin/suspensions/users/{user_id}/suspend", "suspend_user", ("suspend_user",)),
    ("/admin/suspensions/users/{user_id}/restore", "restore_user", ("restore_user",)),
    ("/admin/suspensions/tenants/{tenant_id}/suspend", "suspend_tenant", ("suspend_tenant", "_suspend_account")),
    ("/admin/suspensions/tenants/{tenant_id}/restore", "restore_tenant", ("restore_tenant", "_restore_account")),
    ("/admin/suspensions/partners/{partner_id}/suspend", "suspend_partner", ("suspend_partner", "_suspend_account")),
    ("/admin/suspensions/partners/{partner_id}/restore", "restore_partner", ("restore_partner", "_restore_account")),
    ("/admin/suspensions/{suspension_id}/appeal", "submit_appeal", ("submit_appeal",)),
    ("/admin/suspensions/{suspension_id}/appeal/review", "review_appeal", ("review_appeal",)),
):
    prefix = "app.domain.services.suspension_service:SuspensionService."
    AUDITED["POST", path] = record("suspensions", function,
        "integration/test_admin_authority_invariant.py", *(prefix + helper for helper in helpers))

# Genuine non-mutating/rejected operations only. A missing audit is never an
# EXCEPTION. Legacy debt below is a separate, explicit partial-delivery ledger.
EXCEPTIONS = {
    ("POST", "/admin/actions/{action_id}/retry"): (
        "app.api.v1.endpoints.admin.actions.retry_action",
        "Always refuses unsupported replay with501 after read; never queues or writes an action."),
    ("PUT", "/admin/alerts/settings"): (
        "app.api.v1.endpoints.admin.health.alerts.update_alert_settings",
        "Always409: Alertmanager owns these settings; this endpoint changes no settings."),
    ("POST", "/admin/suspensions/bulk-suspend"): (
        "app.api.v1.endpoints.suspensions.bulk_suspend",
        "Always501: unsupported bulk workflow; no account mutation or dispatch."),
    ("POST", "/telephony/sip/runtime/compile/preview"): (
        "app.api.v1.endpoints.telephony_runtime.preview.preview_runtime_policy",
        "Loads tenant snapshot and compiles a returned preview; no stored policy or runtime apply."),
    ("POST", "/admin/secrets/validate-api-key"): (
        "app.api.v1.endpoints.secrets.validate_api_key",
        "Public authentication verifier despite admin URL; not a privileged configuration mutation. Access accounting belongs to the verifier."),
}


@dataclass(frozen=True)
class LegacyGap:
    endpoint: str
    reason: str
    report: str


LEGACY_GAPS = {}


def gap(method, path, module, function, reason):
    key = (method, path)
    assert key not in LEGACY_GAPS
    LEGACY_GAPS[key] = LegacyGap(f"app.api.v1.endpoints.{module}.{function}", reason,
                                "tmp/qa-round3-20261106/item04-audit.md#legacy-gaps")


# Timeboxed item04 debt, not closure. Exact route+handler pins mean a new alias
# or replacement handler still fails; no prefix or 'outside scope' waiver.
for method, path, module, function, reason in (
    ("PUT", "/admin/calls/pause", "admin.base", "set_pause_all_calls", "Redis pause change precedes swallowed buffered audit; needs durable intent/outcome design."),
    ("POST", "/admin/calls/pause", "admin.base", "pause_all_calls", "Legacy pause alias shares Redis mutation and swallowed buffered audit; needs the same durable intent/outcome contract."),
    ("POST", "/admin/calls/{call_id}/terminate", "admin.calls", "terminate_call", "External hangup/settlement precedes buffered audit; needs audited intent and separate confirmed outcome."),
    ("POST", "/admin/incidents/{incident_id}/acknowledge", "admin.health.incidents", "acknowledge_incident", "Incident row records actor/time but _transition has no chain-locked before/after audit receipt."),
    ("POST", "/admin/incidents/{incident_id}/resolve", "admin.health.incidents", "resolve_incident", "Resolution commits incident state without central before/after receipt; same _transition boundary needs regression proof."),
    ("PATCH", "/admin/contact-enquiries/{enquiry_id}", "contact_enquiries", "change_contact_enquiry_status", "CAS/authority transaction records handled_by but no immutable before/after audit receipt."),
    ("POST", "/admin/actions/{action_id}/cancel", "admin.actions", "cancel_action", "Action row stores cancellation actor; no chain-locked before/after audit receipt accompanies its status mutation."),
    ("POST", "/admin/actions/{action_id}/recover-acknowledgement", "admin.actions", "recover_saved_acknowledgement", "Atomic assistant_action_resolutions ledger exists, but it is not the central chained audit; preserve recovery/session/CAS semantics in followup."),
    ("DELETE", "/admin/recordings/{recording_id}", "admin.media", "delete_admin_recording", "Durable media-deletion saga exists; final central audit is buffered and swallowed after storage/SQL work."),
    ("DELETE", "/admin/feedback/{feedback_id}", "admin.media", "delete_admin_feedback", "Feedback-deletion saga retains intent/outcome; central before/after audit still uses post-operation _safe_audit."),
    ("POST", "/admin/feedback/{feedback_id}/transcription/retry", "admin.media", "retry_admin_feedback_transcription", "Retry job can be queued before buffered _safe_audit; needs atomic outbox/audit intent."),
    ("POST", "/admin/audit/logs/export", "audit_logs", "export_audit_logs", "PII export access receipt uses buffered log after query; no fail-closed durable export receipt."),
    ("PUT", "/admin/tenants/{tenant_id}/call-limits", "call_limits", "update_tenant_call_limits", "Limit write commits before buffered audit; requires same-transaction before/after receipt."),
    ("PUT", "/admin/partners/{partner_id}/limits", "call_limits", "update_partner_limits", "Advisory-serialized partner upsert has no central audit call despite accepting AuditLogger."),
    ("POST", "/dnc/bulk-import", "dnc", "bulk_import", "Previously accepted DNC bulk design uses bounded business batches then buffered summary; not one atomic audit transaction (user marked DNC done)."),
    ("PUT", "/recordings/policy", "recordings", "put_recording_policy", "Policy upsert commits before _safe_audit; changing protected consent/retention workflow requires dedicated tests/ownership."),
    ("POST", "/billing/cancel", "billing", "cancel_subscription", "Privileged Stripe cancellation crosses provider and local state; no central same-transaction before/after receipt."),
    ("POST", "/billing/portal", "billing", "create_portal_session", "Privileged external portal-session creation has no durable central intent/access receipt; do not pretend SQL can roll back Stripe."),
    ("POST", "/calls/{call_id}/hangup", "calls", "hangup_live_call", "calls:delete is administrator authority; external hangup workflow lacks central before/after receipt."),
    ("POST", "/admin/billing/packages", "admin.billing_packages", "create_package", "Same-transaction central audit is present; no dedicated rollback behavior proof located in this pass, so verification remains open."),
    ("PATCH", "/admin/billing/packages/{code}", "admin.billing_packages", "update_package", "Same-transaction before/after audit is present; dedicated failed-audit rollback behavior proof still needs owner verification."),
):
    gap(method, path, module, function, reason)

for method, path, function, operation in (
    ("POST", "/admin/secrets/tenants/{tenant_id}/secrets", "create_secret", "creation"),
    ("POST", "/admin/secrets/tenants/{tenant_id}/secrets/{secret_id}/rotate", "rotate_secret", "rotation"),
    ("DELETE", "/admin/secrets/tenants/{tenant_id}/secrets/{secret_id}", "revoke_secret", "revocation"),
    ("POST", "/admin/secrets/tenants/{tenant_id}/secrets/{secret_id}/compromise", "mark_secret_compromised", "compromise marking"),
    ("POST", "/admin/secrets/platform/secrets", "create_platform_secret", "platform creation"),
    ("POST", "/admin/secrets/platform/secrets/{secret_id}/rotate", "rotate_platform_secret", "platform rotation"),
):
    gap(method, path, "secrets", function,
        f"Secret {operation} service transaction completes before endpoint buffered log; preserve access-log/envelope semantics while adding masked central receipt.")

for method, path, function, operation in (
    ("POST", "/admin/emergency/request", "request_emergency_access", "request"),
    ("POST", "/admin/emergency/{request_id}/approve", "approve_emergency_request", "approval"),
    ("POST", "/admin/emergency/{request_id}/deny", "deny_emergency_request", "denial"),
    ("POST", "/admin/emergency/{request_id}/session", "create_emergency_session", "session issuance"),
    ("DELETE", "/admin/emergency/{request_id}/session", "terminate_emergency_session", "session termination"),
    ("POST", "/admin/emergency/{request_id}/review", "complete_emergency_review", "review"),
):
    gap(method, path, "emergency_access", function,
        f"Emergency {operation} uses emergency-access records/session state, without central chain-locked before/after audit; needs break-glass lifecycle review.")

for method, path, function, operation in (
    ("POST", "/admin/security-events/events", "create_security_event", "creation"),
    ("PATCH", "/admin/security-events/events/{event_id}", "update_security_event", "update"),
    ("POST", "/admin/security-events/events/{event_id}/resolve", "resolve_security_event", "resolution"),
    ("POST", "/admin/security-events/events/{event_id}/escalate", "escalate_event", "escalation"),
):
    gap(method, path, "security_events", function,
        f"Security event {operation} writes before buffered audit_logger.log; requires atomic masked state receipt.")

for method, path, module, function, operation in (
    ("POST", "/telephony/sip/runtime/activate", "telephony_runtime.activate", "activate_runtime_policy", "activation"),
    ("POST", "/telephony/sip/runtime/rollback", "telephony_runtime.rollback", "rollback_runtime_policy", "rollback"),
    ("POST", "/telephony/sip/runtime/concurrency/leases/acquire", "telephony_concurrency", "acquire_concurrency_lease", "lease admission"),
    ("POST", "/telephony/sip/runtime/concurrency/leases/expire", "telephony_concurrency", "expire_stale_concurrency_leases", "lease expiration"),
    ("POST", "/telephony/sip/runtime/concurrency/leases/{lease_id}/heartbeat", "telephony_concurrency", "heartbeat_concurrency_lease", "lease heartbeat"),
    ("POST", "/telephony/sip/runtime/concurrency/leases/{lease_id}/release", "telephony_concurrency", "release_concurrency_lease", "lease release"),
):
    gap(method, path, module, function,
        f"Runtime {operation} has operational state/event transactions, not a central AuditLogger chain boundary; external apply/lease semantics require separate design.")

for method, path, function, operation in (
    ("PATCH", "/admin/inbound/controls", "patch_platform_inbound_controls", "platform controls"),
    ("POST", "/admin/inbound/assignments/{assignment_id}/quarantine", "quarantine_inbound_assignment", "quarantine"),
    ("POST", "/admin/inbound/assignments/{assignment_id}/unquarantine", "unquarantine_inbound_assignment", "unquarantine"),
    ("POST", "/admin/inbound/reassignments", "request_inbound_reassignment", "reassignment request"),
    ("POST", "/admin/inbound/reassignments/{request_id}/approve", "approve_inbound_reassignment", "reassignment approval"),
    ("POST", "/admin/inbound/reassignments/{request_id}/cancel", "cancel_inbound_reassignment", "reassignment cancellation"),
    ("POST", "/admin/inbound/tenants/{tenant_id}/calls/{call_id}/billing-hold/resolve", "resolve_inbound_billing_hold", "billing-hold resolution"),
):
    gap(method, path, "admin.inbound", function,
        f"Inbound {operation} owns a specialized transactional evidence ledger; no central audit chain. Protected inbound/billing integration requires owner review, not duplicate audit injection.")

for method, path, function, operation in (
    ("POST", "/tenant-phone-numbers/", "register_phone_number", "registration"),
    ("POST", "/tenant-phone-numbers/{did_id}/verify", "verify_phone_number", "verification"),
    ("DELETE", "/tenant-phone-numbers/{did_id}", "revoke_phone_number", "revocation"),
):
    gap(method, path, "tenant_phone_numbers", function,
        f"DID {operation} writes inbound_audit_events with business state, but no central chained receipt; keep carrier/proof/ingress contracts in owner followup.")

for method, path, module, function, operation in (
    ("POST", "/ai-options/config", "ai_options.config", "save_config", "tenant provider configuration"),
    ("POST", "/ai-options/voices/clone", "ai_options.clone", "clone_voice", "provider voice creation"),
    ("DELETE", "/ai-options/voices/cloned/{clone_id}", "ai_options.clone", "delete_cloned_voice", "provider voice deletion"),
    ("POST", "/ai-options/voices/prefetch", "ai_options.preview", "prefetch_all_voice_samples", "provider sample/cache generation"),
    ("POST", "/ai-options/benchmark", "ai_options.benchmark", "run_benchmark", "billable provider benchmark"),
    ("POST", "/ai-options/test/llm", "ai_options.testing", "test_llm", "billable LLM probe"),
    ("POST", "/ai-options/test/tts", "ai_options.testing", "test_tts", "billable TTS/cache probe"),
):
    gap(method, path, module, function,
        f"tenants:update gates {operation}; protected voice/provider work has no central privileged-operation receipt. Needs owner-designed intent/outcome or explicit probe policy.")


def privileged_dependencies(route):
    stack = list(route.dependant.dependencies)
    seen = set()
    while stack:
        dependency = stack.pop()
        if id(dependency) in seen:
            continue
        seen.add(id(dependency))
        function = dependency.call
        name = getattr(function, "__name__", "")
        if name in PRIVILEGED_DEPENDENCIES:
            return True
        if name in {"role_checker", "permission_checker", "_check_permissions"}:
            captured = inspect.getclosurevars(function).nonlocals
            if name == "role_checker":
                minimum = captured.get("min_role")
                if not isinstance(minimum, UserRole) or minimum.level >= UserRole.TENANT_ADMIN.level:
                    return True
            elif name == "permission_checker":
                if captured.get("permission") not in ORDINARY_PERMISSIONS:
                    return True
            else:
                # Legacy string permissions name admin/security/audit APIs;
                # their full paths are recorded individually below.
                return True
        stack.extend(dependency.dependencies)
    return False


def inventory(router):
    result = {}
    for route in iter_route_contexts(router.routes):
        if not route.endpoint or not route.methods.intersection(WRITE_METHODS):
            continue
        privileged = (route.path.startswith(PRIVILEGED_PREFIXES)
                      or privileged_dependencies(route))
        if not privileged:
            continue
        endpoint = inspect.unwrap(route.endpoint)
        identity = f"{endpoint.__module__}.{endpoint.__qualname__}"
        for method in route.methods.intersection(WRITE_METHODS):
            key = (method, route.path)
            assert key not in result, f"Duplicate privileged mount: {key}"
            result[key] = identity
    return result


def boundary_calls(identity):
    module, name = identity.split(":", 1) if ":" in identity else identity.rsplit(".", 1)
    function = importlib.import_module(module)
    for part in name.split("."):
        function = getattr(function, part)
    tree = ast.parse(textwrap.dedent(inspect.getsource(inspect.unwrap(function))))
    return {node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
            for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, (ast.Attribute, ast.Name))}


def coverage_errors(found, audited=AUDITED, exceptions=EXCEPTIONS, legacy_gaps=None):
    legacy_gaps = legacy_gaps or {}
    errors = []
    for key, identity in sorted(found.items()):
        if key in audited:
            entry = audited[key]
            if entry.endpoint != identity:
                errors.append(f"{key}: handler changed to {identity}")
                continue
            calls = set().union(*(boundary_calls(name) for name in entry.boundaries))
            if not {"lock_transaction", "log_in_transaction"} <= calls:
                errors.append(f"{key}: no declared transactional audit boundary")
            if not entry.behavior_test:
                errors.append(f"{key}: no behavior test recorded")
            elif not (Path(__file__).parents[1] / entry.behavior_test).is_file():
                errors.append(f"{key}: behavior test reference is missing")
            reachable = {entry.endpoint}
            while True:
                reachable_calls = set().union(*(boundary_calls(name) for name in reachable))
                added = {name for name in entry.boundaries
                         if name.rsplit(".", 1)[-1].split(":")[-1] in reachable_calls}
                if added <= reachable:
                    break
                reachable |= added
            if not set(entry.boundaries) <= reachable:
                errors.append(f"{key}: declared audit helper is not reachable from handler")
        elif key in exceptions:
            endpoint, reason = exceptions[key]
            if identity != endpoint or not reason.strip():
                errors.append(f"{key}: stale or unexplained exception")
        elif key in legacy_gaps:
            debt = legacy_gaps[key]
            if identity != debt.endpoint or not debt.reason.strip() or not debt.report.strip():
                errors.append(f"{key}: stale or unexplained legacy audit gap")
        else:
            errors.append(f"{key}: unreviewed privileged writer {identity}")
    for key in sorted((set(audited) | set(exceptions) | set(legacy_gaps)) - set(found)):
        errors.append(f"{key}: inventory entry is not mounted/privileged")
    return errors


def test_every_mounted_privileged_write_has_explicit_audit_disposition():
    from app.api.v1.routes import api_router

    found = inventory(api_router)
    assert len(found) >= len(AUDITED), "Mounted discovery unexpectedly lost routes"
    assert not (set(AUDITED) & set(EXCEPTIONS) or set(AUDITED) & set(LEGACY_GAPS)
                or set(EXCEPTIONS) & set(LEGACY_GAPS)), "Disposition entries must not overlap"
    errors = coverage_errors(found, legacy_gaps=LEGACY_GAPS)
    assert not errors, "Privileged audit coverage incomplete:\n" + "\n".join(errors)


def test_hidden_nested_new_privileged_writer_cannot_escape_inventory():
    leaf = APIRouter()

    @leaf.post("/new", include_in_schema=False)
    async def new_writer():
        return None

    middle = APIRouter()
    middle.include_router(leaf, prefix="/inner", dependencies=[Depends(require_admin_tenant)])
    outer = APIRouter()
    outer.include_router(middle, prefix="/outer")
    found = inventory(outer)
    assert set(found) == {("POST", "/outer/inner/new")}
    errors = coverage_errors(found, audited={}, exceptions={})
    assert len(errors) == 1 and "unreviewed privileged writer" in errors[0]


def test_exception_requires_exact_endpoint_and_individual_reason():
    key = ("POST", "/synthetic")
    found = {key: "synthetic.actual"}
    assert coverage_errors(found, audited={}, exceptions={key: ("synthetic.actual", "")})
    assert coverage_errors(found, audited={}, exceptions={key: ("synthetic.old", "read-only probe")})
    assert not coverage_errors(found, audited={}, exceptions={key: ("synthetic.actual", "read-only probe")})


def test_legacy_debt_is_explicit_and_never_covers_new_or_changed_route():
    key = ("POST", "/synthetic")
    debt = {key: LegacyGap("synthetic.actual", "Buffered receipt follows Redis mutation", "report#gap")}
    assert not coverage_errors({key: "synthetic.actual"}, {}, {}, debt)
    assert coverage_errors({key: "synthetic.replaced"}, {}, {}, debt)
    assert coverage_errors({key: "synthetic.actual", ("POST", "/new"): "synthetic.actual"}, {}, {}, debt)
    assert coverage_errors({}, {}, {}, debt)


def test_ordinary_permission_crud_is_distinct_from_administrative_authority():
    router = APIRouter()

    async def handler():
        return None

    for path, dependency in (
        ("/calendar", require_permission(Permission.CALENDAR_MANAGE)),
        ("/campaign", require_permission(Permission.CAMPAIGNS_UPDATE)),
        ("/user", require_role(UserRole.USER)),
        ("/settings", require_permission(Permission.TENANTS_UPDATE)),
        ("/admin-role", require_role(UserRole.TENANT_ADMIN)),
    ):
        router.add_api_route(path, handler, methods=["POST"], dependencies=[Depends(dependency)])
    assert set(inventory(router)) == {("POST", "/settings"), ("POST", "/admin-role")}

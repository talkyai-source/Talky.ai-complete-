"""Real normal-auth audit/security read contract; no authority/query substitutes.

This first snapshot intentionally exposes the current Next route disagreement,
native SQL hydration, filter validation and unavailable-read behavior. Permission
catalog and role policy remain unchanged. Only the reviewed disposable fixture.
"""
import re
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
import pytest_asyncio

from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import (  # noqa: F401
    call, crud_db, signed_in,
)

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []
AUDIT = "/admin/audit/logs"
SECURITY = "/admin/security-events/events"
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")
STATUSES = ("open", "investigating", "resolved", "false_positive", "escalated")


@pytest_asyncio.fixture
async def event_read_db(crud_db):  # noqa: F811
    from app.api.v1.dependencies import get_audit_logger
    from app.domain.services.audit_logger import AuditEvent

    db = crud_db
    db.event_prefix = "synthetic-read-" + uuid4().hex[:20]
    db.security_ids = [uuid4() for _ in range(21)]
    db.audit_ids = []
    db.audit_service = await get_audit_logger(db.pool)
    async with db.pool.acquire() as conn:
        db.event_runtime_role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", db.event_runtime_role)
    # Real existing logger, JSON codecs, chain/signature and flush. No clock,
    # AuditLogEntry, query result or authentication replacement.
    for index in range(21):
        db.audit_ids.append(await db.audit_service.log(
            event_type=AuditEvent.RECORD_VIEWED,
            action=db.event_prefix,
            actor_id=db.users[0], tenant_id=db.tenants[1],
            resource_type="audit_logs", resource_id=db.security_ids[index],
            description="Owned audit read fixture",
            metadata={"fixture_index": index, "nested": {"synthetic": True}},
            before_state={"state": "before"}, after_state={"state": "after"},
            ip_address="127.0.0.1",
        ))
    await db.audit_service.flush()
    assert await db.owner.fetchval(
        "SELECT count(*) FROM audit_logs WHERE event_id=ANY($1::uuid[])", db.audit_ids,
    ) == 21
    db.audit_times = await db.owner.fetchrow(
        "SELECT min(event_time) AS first,max(event_time) AS last FROM audit_logs "
        "WHERE event_id=ANY($1::uuid[])", db.audit_ids,
    )
    async with db.owner.transaction():
        timestamp = await db.owner.fetchval("SELECT clock_timestamp()")
        db.security_time = timestamp
        for index, event in enumerate(db.security_ids):
            await db.owner.execute(
                """INSERT INTO security_events(event_id,created_at,event_type,severity,status,
                   tenant_id,user_id,detection_source,title,description,evidence)
                   VALUES($1,$2,'suspicious_activity',$3,$4,$5,$6,$7,$8,$9,
                          '{"native_json":{"synthetic":true}}'::jsonb)""",
                event, timestamp, SEVERITIES[index % 5], STATUSES[index % 5],
                db.tenants[1], None if index == 20 else db.users[1],
                db.event_prefix, "Owned security read fixture",
                None if index == 20 else "Synthetic description",
            )
    # Parent owns all actors/tenants and flushes access audits before exact FK
    # cleanup. No extra records or permissions survive that protected snapshot.
    yield db


def rows(response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list), "Canonical legacy contract is a raw list"
    return body


async def test_canonical_audit_native_values_exact_filters_and_offset(event_read_db):
    db = event_read_db
    query = {"tenant_id": str(db.tenants[1]), "resource_type": "audit_logs",
             "start_date": db.audit_times["first"].isoformat(),
             "end_date": db.audit_times["last"].isoformat(), "limit": 20}
    async with signed_in(db) as admin:
        page1 = rows(await call(db, admin, "GET", AUDIT, params=query))
        page2 = rows(await call(db, admin, "GET", AUDIT, params={**query, "offset": 20}))
        assert len(page1) == 20 and len(page2) == 1
        assert {row["event_id"] for row in page1 + page2} == set(map(str, db.audit_ids))
        for row in page1 + page2:
            assert row["actor_id"] == str(db.users[0])
            assert row["resource_id"] in set(map(str, db.security_ids))
            assert row["ip_address"] == "127.0.0.1"
            assert row["metadata"]["nested"] == {"synthetic": True}
            assert row["before_state"] == {"state": "before"} and row["after_state"] == {"state": "after"}
        exact = rows(await call(db, admin, "GET", AUDIT, params={
            **query, "event_type": "record_viewed", "actor_id": str(db.users[0]), "severity": "INFO"}))
        assert exact
        wrong_actor = rows(await call(db, admin, "GET", AUDIT, params={**query, "actor_id": str(db.users[2])}))
        assert wrong_actor == []
        # Existing exact detail/export callers use the unchanged internal
        # query defaults, including the export limit of 10000.
        detail = await call(db, admin, "GET", AUDIT + "/" + str(db.audit_ids[0]))
        assert detail.status_code == 200 and detail.json()["event_id"] == str(db.audit_ids[0])
        assert (await call(db, admin, "GET", AUDIT + "/" + str(uuid4()))).status_code == 404
        exported = await call(db, admin, "POST", AUDIT + "/export", params={
            "tenant_id": str(db.tenants[1]), "start_date": query["start_date"],
            "end_date": query["end_date"], "format": "json",
        })
        assert exported.status_code == 200, exported.text
        assert exported.json()["record_count"] == 21
        assert {r["event_id"] for r in exported.json()["records"]} == set(map(str, db.audit_ids))
        RUN_EVIDENCE.append({"case": "canonical_audit", "pages": [len(page1), len(page2)],
                             "native_inet_json_and_date_hydrated": True})


async def test_existing_direct_read_call_defaults_remain_compatible(event_read_db):
    """Existing Python callers supply old args and omit newly appended filters.

    Normal HTTP login establishes the current principal first. This check then
    exercises the real direct list/SQL seam, not another authorization proof.
    """
    from app.api.v1.endpoints import audit_logs, security_events
    db = event_read_db
    async with signed_in(db) as admin:
        identity = await call(db, admin, "GET", "/auth/me")
        assert identity.status_code == 200
        current = identity.json()
        audit = await audit_logs.query_audit_logs(
            request=None, start_date=db.audit_times["first"], end_date=db.audit_times["last"],
            event_type=None, actor_id=None, tenant_id=db.tenants[1], resource_type="audit_logs",
            severity=None, limit=100, offset=0, current_user=current, audit_logger=db.audit_service,
        )
        assert {str(row.event_id) for row in audit} == set(map(str, db.audit_ids))
        security = await security_events.list_security_events(
            status=None, severity=None, tenant_id=db.tenants[1], assigned_to=None,
            detection_source=db.event_prefix, limit=100, offset=0,
            current_user=current, db_pool=db.pool,
        )
        assert {str(row.event_id) for row in security} == set(map(str, db.security_ids))
        RUN_EVIDENCE.append({"case": "legacy_direct_list_defaults", "audit_rows": len(audit),
                             "security_rows": len(security), "new_arguments_omitted": True})


@pytest.mark.parametrize("nullable", [False, True], ids=["native-json", "nullable-description-subject"])
async def test_canonical_security_native_json_and_nullable_history(event_read_db, nullable):
    db = event_read_db
    async with signed_in(db) as admin:
        query = {"tenant_id": str(db.tenants[1]), "detection_source": db.event_prefix, "limit": 22}
        if not nullable:
            query["severity"] = "HIGH"
        response = await call(db, admin, "GET", SECURITY, params=query)
        RUN_EVIDENCE.append({"case": "canonical_security", "nullable": nullable,
                             "status": response.status_code})
        data = rows(response)
        assert len(data) == (21 if nullable else 4)
        for row in data:
            assert row["evidence"] == {"native_json": {"synthetic": True}}
        if nullable:
            assert {r["severity"] for r in data} == set(SEVERITIES)
            assert {r["status"] for r in data} == set(STATUSES)
            last = next(r for r in data if r["event_id"] == str(db.security_ids[-1]))
            assert last["user_id"] is None and last["description"] is None


@pytest.mark.parametrize("path", ["/admin/audit-logs", "/admin/security-events"])
async def test_old_next_urls_are_unmounted_diagnostic_not_success_aliases(event_read_db, path):
    db = event_read_db
    async with signed_in(db) as admin:
        response = await call(db, admin, "GET", path, params={"tenant_id": str(db.tenants[1]), "limit": 1})
        RUN_EVIDENCE.append({"case": "old_next_route_diagnostic",
                             "path": path, "status": response.status_code})
        assert response.status_code == 404


async def test_current_partner_default_denies_both_read_routes(event_read_db):
    db = event_read_db
    partner_role = await db.owner.fetchval("SELECT id FROM roles WHERE name='partner_admin'")
    assert partner_role is not None
    async with db.owner.transaction():
        await db.owner.execute("UPDATE user_profiles SET role='partner_admin' WHERE id=$1", db.users[1])
        await db.owner.execute("UPDATE tenant_users SET role_id=$2 WHERE user_id=$1", db.users[1], partner_role)
    async with signed_in(db, 1) as partner:
        assert (await call(db, partner, "GET", "/auth/me")).json()["role"] == "partner_admin"
        for path in (AUDIT, SECURITY):
            response = await call(db, partner, "GET", path)
            RUN_EVIDENCE.append({"case": "unchanged_partner_default", "path": path, "status": response.status_code})
            assert response.status_code == 403


async def test_old_admin_token_cannot_retain_read_authority_after_demotion(event_read_db):
    db = event_read_db
    async with signed_in(db) as admin:
        await db.owner.execute("UPDATE user_profiles SET role='tenant_admin' WHERE id=$1", db.users[0])
        for path in (AUDIT, SECURITY):
            assert (await call(db, admin, "GET", path)).status_code == 403


@pytest.mark.parametrize("kind", ["audit", "security"])
async def test_real_select_fault_after_auth_is_unavailable_not_empty(event_read_db, monkeypatch, kind):
    from app.domain.services import audit_logger
    from app.api.v1.endpoints import security_events
    db = event_read_db
    module = audit_logger if kind == "audit" else security_events
    table = "audit_logs" if kind == "audit" else "security_events"
    path = AUDIT if kind == "audit" else SECURITY
    original = module.acquire_with_tenant
    admitted = False

    @asynccontextmanager
    async def deny_select(*args, **kwargs):
        nonlocal admitted
        async with original(*args, **kwargs) as conn:
            await db.owner.execute(f'REVOKE SELECT ON {table} FROM "{db.event_runtime_role}"')
            admitted = True
            yield conn

    async with signed_in(db) as admin:
        await db.audit_service.flush()
        monkeypatch.setattr(module, "acquire_with_tenant", deny_select)
        try:
            response = await call(db, admin, "GET", path, params={"tenant_id": str(db.tenants[1])})
            RUN_EVIDENCE.append({"case": "real_select_fault", "kind": kind, "admitted": admitted,
                                 "status": response.status_code})
            assert admitted
            assert response.status_code == 503, response.text
            assert not isinstance(response.json(), list)
        finally:
            await db.owner.execute(f'GRANT SELECT ON {table} TO "{db.event_runtime_role}"')
            monkeypatch.setattr(module, "acquire_with_tenant", original)
        recovered = rows(await call(db, admin, "GET", path, params={
            "tenant_id": str(db.tenants[1]), "severity": "HIGH" if kind == "security" else "INFO",
        }))
        assert recovered
        RUN_EVIDENCE.append({"case": "select_fault_restored", "kind": kind, "status": 200})


@pytest.mark.parametrize("path,query", [
    (AUDIT, {"start_date": "2026-01-01T00:00:00"}),
    (AUDIT, {"start_date": "2026-02-01T00:00:00Z", "end_date": "2026-01-01T00:00:00Z"}),
    (SECURITY, {"severity": "invented"}),
    (SECURITY, {"status": "invented"}),
], ids=["naive-date", "reversed-date", "unknown-severity", "unknown-status"])
async def test_invalid_read_filters_are_rejected_before_misleading_results(event_read_db, path, query):
    db = event_read_db
    async with signed_in(db) as admin:
        response = await call(db, admin, "GET", path, params={**query, "tenant_id": str(db.tenants[1])})
        RUN_EVIDENCE.append({"case": "invalid_filter", "path": path,
                             "query_keys": sorted(query), "status": response.status_code})
        assert response.status_code == 422


async def test_live_offset_access_audit_shift_is_explicitly_observed(event_read_db):
    """Control: demonstrate live paging, without claiming offset is a snapshot."""
    db = event_read_db
    async with signed_in(db) as admin:
        query = {"tenant_id": str(db.tenants[1]), "resource_type": "audit_logs", "limit": 20}
        first = rows(await call(db, admin, "GET", AUDIT, params=query))
        await db.audit_service.flush()
        inserted = await db.owner.fetchval(
            "SELECT count(*) FROM audit_logs WHERE tenant_id=$1 AND actor_id=$2 AND action='audit_logs_queried'",
            db.tenants[1], db.users[0],
        )
        second = rows(await call(db, admin, "GET", AUDIT, params={**query, "offset": 20}))
        overlap = set(r["event_id"] for r in first) & set(r["event_id"] for r in second)
        RUN_EVIDENCE.append({"case": "live_offset_access_shift", "read_access_events_committed": inserted,
                             "duplicate_event_ids": sorted(overlap), "page_lengths": [len(first), len(second)],
                             "snapshot_traversal_claimed": False})
        assert inserted >= 1 and len(first) == 20
        # The source/API contract is live offset. This control records the
        # actual overlap rather than weakening an invented snapshot promise.


@pytest_asyncio.fixture
async def event_filter_db(event_read_db):
    """Only additional exact-owned rows for promised filters and seek evidence."""
    from app.domain.services.audit_logger import AuditEvent
    db = event_read_db
    db.filter_partners = [uuid4(), uuid4()]
    db.filter_source = db.event_prefix + "-filter"
    db.audit_groups = {key: [] for key in ("login", "role_change", "billing_change", "suspension")}
    db.filter_security = {}
    try:
        await db.owner.execute(
            "UPDATE user_profiles SET name=$2 WHERE id=$1", db.users[1], db.event_prefix + " Literal %_ Alice",
        )
        await db.owner.execute(
            "UPDATE user_profiles SET name=$2 WHERE id=$1", db.users[2], db.event_prefix + " Literal ZZ Alice",
        )
        for index, partner in enumerate(db.filter_partners):
            await db.owner.execute(
                "INSERT INTO white_label_partners(id,tenant_id,company_name,status) VALUES($1,$2,$3,'active')",
                partner, db.tenants[0 if index == 0 else 2], db.event_prefix + " Partner " + str(index),
            )
        for tenant, partner in zip(db.tenants[1:], db.filter_partners):
            await db.owner.execute("UPDATE tenants SET white_label_partner_id=$2 WHERE id=$1", tenant, partner)
        cases = [
            ("login", AuditEvent.LOGIN_SUCCESS, "login_success", "user", {}),
            ("login", AuditEvent.LOGIN_FAILURE, "login_failure", "user", {}),
            ("role_change", AuditEvent.ROLE_ASSIGNED, "role_assigned", "user", {}),
            ("role_change", AuditEvent.ROLE_REMOVED, "role_removed", "user", {}),
            ("role_change", AuditEvent.USER_UPDATED, "role_updated", "user", {"new_role": "readonly"}),
            ("role_change", AuditEvent.USER_UPDATED, "user_updated", "user", {"new_role": "readonly"}),
            ("role_change", AuditEvent.USER_SUSPENDED, "user_updated", "user", {"new_role": "user"}),
            ("role_change", AuditEvent.USER_UPDATED, "user_primary_membership_configured", "user", {}),
            ("billing_change", AuditEvent.BILLING_UPDATED, "billing_updated", "tenant", {}),
            ("suspension", AuditEvent.USER_SUSPENDED, "user_suspended", "user", {}),
            ("suspension", AuditEvent.USER_RESTORED, "user_restored", "user", {}),
            ("suspension", AuditEvent.TENANT_SUSPENDED, "tenant_suspended", "tenant", {}),
            ("suspension", AuditEvent.TENANT_RESTORED, "tenant_restored", "tenant", {}),
            ("suspension", AuditEvent.USER_UPDATED, "appeal_submitted", "suspension", {}),
            ("suspension", AuditEvent.USER_UPDATED, "appeal_reviewed", "suspension", {}),
            (None, AuditEvent.USER_UPDATED, "user_updated", "user", {"new_role": None}),
            (None, AuditEvent.USER_UPDATED, "user_updated", "user", {"new_role": "  "}),
            (None, AuditEvent.USER_UPDATED, "user_updated", "user", {"new_role": False}),
            (None, AuditEvent.USER_UPDATED, "user_updated", "user", {"new_role": 123}),
            (None, AuditEvent.USER_UPDATED, "appeal_submitted", "user", {}),
            (None, AuditEvent.USER_UPDATED, "role_updated_extra", "user", {}),
        ]
        for group, event, action, resource, metadata in cases:
            event_id = await db.audit_service.log(
                event_type=event, action=action, actor_id=db.users[1],
                tenant_id=db.tenants[1], resource_type=resource,
                resource_id=uuid4(), metadata={"fixture": db.event_prefix, **metadata},
            )
            if group:
                db.audit_groups[group].append(str(event_id))
            # Mixed status+role is intentionally in both meaningful groups.
            if event == AuditEvent.USER_SUSPENDED and group == "role_change":
                db.audit_groups["suspension"].append(str(event_id))
        db.audit_search = {}
        for user in db.users[1:]:
            event_id = await db.audit_service.log(
                AuditEvent.RECORD_VIEWED, action="filter_actor", actor_id=user,
                tenant_id=db.tenants[1], resource_type="filter_actor", resource_id=uuid4(),
            )
            db.audit_search[str(user)] = str(event_id)
        db.direct_partner_audits = []
        for partner in db.filter_partners:
            db.direct_partner_audits.append(await db.audit_service.log(
                AuditEvent.TENANT_SUSPENDED, action="partner_suspended",
                actor_id=db.users[0], resource_type="partner", resource_id=partner,
            ))
        await db.audit_service.flush()
        raw_types = ("failed_login", "failed_login_user_not_found", "failed_login_wrong_password",
                     "account_lockout", "rate_limit_violation", "rate_limit_exceeded",
                     "suspicious_activity", "session_hijacking_detected", "suspicious_activity_extra",
                     "failed_login_extra", "prefix_rate_limit_exceeded", "custom_type")
        for index, event_type in enumerate(raw_types):
            event = uuid4()
            subject = db.users[2] if event_type == "custom_type" else db.users[1]
            await db.owner.execute(
                """INSERT INTO security_events(event_id,created_at,event_type,severity,status,
                   tenant_id,user_id,detection_source,title,description,evidence)
                   VALUES($1,$2,$3,'HIGH','open',$4,$5,$6,'Owned filter event','Synthetic',
                          '{"fixture":true}'::jsonb)""",
                event, db.security_time, event_type, db.tenants[1], subject, db.filter_source,
            )
            db.filter_security[event_type] = str(event)
        # A foreign current-partner row is essential: without it, an ignored
        # partner_id can accidentally satisfy every security-list assertion.
        db.other_partner_security = uuid4()
        await db.owner.execute(
            """INSERT INTO security_events(event_id,created_at,event_type,severity,status,
               tenant_id,user_id,detection_source,title,description)
               VALUES($1,$2,'suspicious_activity','HIGH','open',$3,$4,$5,
                      'Other owned partner event','Synthetic')""",
            db.other_partner_security, db.security_time, db.tenants[2], db.users[2], db.filter_source,
        )
        # Keep the existing NULL-description failure separate from seek/filter
        # behavior; this exact owned row remains NULL in its original tests.
        await db.owner.execute(
            "UPDATE security_events SET description='Synthetic seek row' WHERE event_id=$1 AND tenant_id=$2",
            db.security_ids[-1], db.tenants[1],
        )
        yield db
    finally:
        async with db.owner.transaction():
            await db.owner.execute(
                "UPDATE tenants SET white_label_partner_id=NULL "
                "WHERE id=ANY($1::uuid[]) AND white_label_partner_id=ANY($2::uuid[])",
                db.tenants, db.filter_partners,
            )
            await db.owner.execute("DELETE FROM white_label_partners WHERE id=ANY($1::uuid[])", db.filter_partners)


@pytest.mark.parametrize("group", ["login", "role_change", "billing_change", "suspension"])
async def test_promised_audit_groups_match_exact_producer_semantics(event_filter_db, group):
    db = event_filter_db
    async with signed_in(db) as admin:
        result = rows(await call(db, admin, "GET", AUDIT, params={
            "tenant_id": str(db.tenants[1]), "actor_id": str(db.users[1]),
            "event_group": group, "limit": 100,
        }))
        actual = {r["event_id"] for r in result}
        RUN_EVIDENCE.append({"case": "promised_audit_group", "group": group,
                             "expected_count": len(db.audit_groups[group]), "actual_count": len(actual)})
        assert actual == set(db.audit_groups[group])


@pytest.mark.parametrize("group,raw_types", [
    ("failed_login", ("failed_login", "failed_login_user_not_found", "failed_login_wrong_password", "account_lockout")),
    ("rate_limit_violation", ("rate_limit_violation", "rate_limit_exceeded")),
    ("suspicious_activity", ("suspicious_activity", "session_hijacking_detected")),
])
async def test_promised_security_groups_and_exact_type_intersect(event_filter_db, group, raw_types):
    db = event_filter_db
    query = {"tenant_id": str(db.tenants[1]), "detection_source": db.filter_source,
             "event_group": group, "limit": 100}
    async with signed_in(db) as admin:
        result = rows(await call(db, admin, "GET", SECURITY, params=query))
        assert {r["event_id"] for r in result} == {db.filter_security[t] for t in raw_types}
        exact = rows(await call(db, admin, "GET", SECURITY, params={**query, "event_type": raw_types[0]}))
        assert [r["event_id"] for r in exact] == [db.filter_security[raw_types[0]]]
        excluded = rows(await call(db, admin, "GET", SECURITY, params={**query, "event_type": "custom_type"}))
        assert excluded == []


@pytest.mark.parametrize("kind", ["audit", "security"])
async def test_promised_current_name_email_and_saved_uuid_search_are_literal(event_filter_db, kind):
    db = event_filter_db
    path = AUDIT if kind == "audit" else SECURITY
    query = {"tenant_id": str(db.tenants[1]), "limit": 100}
    query.update({"resource_type": "filter_actor"} if kind == "audit" else {"detection_source": db.filter_source})
    expected_one = {db.audit_search[str(db.users[1])]} if kind == "audit" else {
        event for raw, event in db.filter_security.items() if raw != "custom_type"}
    expected_other = {db.audit_search[str(db.users[2])]} if kind == "audit" else {db.filter_security["custom_type"]}
    async with signed_in(db) as admin:
        for search in ("%_", f"{db.users[1]}@example.com", str(db.users[1]), str(db.users[1])[9:22]):
            result = rows(await call(db, admin, "GET", path, params={**query, "user_query": search}))
            assert {r["event_id"] for r in result} == expected_one
        # The saved event is tenant B; this actor/subject's current profile is
        # tenant C. The verified platform read must keep the event predicate
        # while being able to search the other current profile by name/email.
        for search in (str(db.users[2]), f"{db.users[2]}@example.com", "Literal ZZ Alice"):
            other = rows(await call(db, admin, "GET", path, params={**query, "user_query": search}))
            assert {r["event_id"] for r in other} == expected_other
        assert rows(await call(db, admin, "GET", path, params={**query, "user_query": uuid4().hex})) == []


@pytest.mark.parametrize("kind", ["audit", "security"])
async def test_promised_platform_partner_filter_is_current_relation_and_intersection(event_filter_db, kind):
    db = event_filter_db
    path = AUDIT if kind == "audit" else SECURITY
    partner, other_partner = map(str, db.filter_partners)
    query = {"partner_id": partner, "limit": 100}
    async with signed_in(db) as admin:
        result = rows(await call(db, admin, "GET", path, params=query))
        assert result
        if kind == "audit":
            ids = {r["event_id"] for r in result}
            assert str(db.direct_partner_audits[0]) in ids and str(db.direct_partner_audits[1]) not in ids
            assert all(r["tenant_id"] == str(db.tenants[1]) or (
                r["tenant_id"] is None and r["resource_type"] == "partner" and r["resource_id"] == partner
            ) for r in result)
        else:
            assert str(db.other_partner_security) not in {r["event_id"] for r in result}
            assert all(r["tenant_id"] == str(db.tenants[1]) for r in result)
            other = rows(await call(db, admin, "GET", path, params={"partner_id": other_partner, "limit": 100}))
            assert str(db.other_partner_security) in {r["event_id"] for r in other}
        assert rows(await call(db, admin, "GET", path, params={**query, "tenant_id": str(db.tenants[2])})) == []
        # Current assignment semantics, not event-time provenance.
        await db.owner.execute("UPDATE tenants SET white_label_partner_id=$2 WHERE id=$1",
                               db.tenants[1], db.filter_partners[1])
        moved = rows(await call(db, admin, "GET", path, params={"partner_id": other_partner,
                      "tenant_id": str(db.tenants[1]), "limit": 100}))
        assert moved and all(r["tenant_id"] == str(db.tenants[1]) for r in moved)


async def test_seek_audit_does_not_repeat_after_real_access_log_insert(event_filter_db):
    db = event_filter_db
    query = {"tenant_id": str(db.tenants[1]), "resource_type": "audit_logs", "limit": 21}
    async with signed_in(db) as admin:
        first = rows(await call(db, admin, "GET", AUDIT, params=query))
        assert len(first) == 21
        visible, lookahead = first[:20], first[20]
        cursor = visible[-1]
        await db.audit_service.flush()
        second = rows(await call(db, admin, "GET", AUDIT, params={
            **query, "before_time": cursor["event_time"], "before_id": cursor["event_id"],
        }))
        overlap = {r["event_id"] for r in visible} & {r["event_id"] for r in second}
        RUN_EVIDENCE.append({"case": "seek_after_access_audit", "overlap": sorted(overlap),
                             "second_count": len(second)})
        assert overlap == set()
        assert [r["event_id"] for r in second] == [lookahead["event_id"]]


async def test_seek_security_tied_db_timestamps_use_uuid_desc_and_preserve_microseconds(event_filter_db):
    db = event_filter_db
    query = {"tenant_id": str(db.tenants[1]), "detection_source": db.event_prefix, "limit": 21}
    expected = sorted(map(str, db.security_ids), reverse=True)
    async with signed_in(db) as admin:
        first = rows(await call(db, admin, "GET", SECURITY, params=query))
        assert [r["event_id"] for r in first] == expected
        assert len({r["created_at"] for r in first}) == 1
        cursor = first[19]
        second = rows(await call(db, admin, "GET", SECURITY, params={
            **query, "before_time": cursor["created_at"], "before_id": cursor["event_id"],
        }))
        assert [r["event_id"] for r in second] == expected[20:]


@pytest.mark.parametrize("path", [AUDIT, SECURITY])
async def test_seek_pair_and_new_filter_bounds_reject_before_query(event_filter_db, path):
    db = event_filter_db
    async with signed_in(db) as admin:
        for params in (
            {"before_time": db.security_time.isoformat()},
            {"before_id": str(db.security_ids[0])},
            {"before_time": "2026-01-01T00:00:00", "before_id": str(db.security_ids[0])},
            {"before_time": db.security_time.isoformat(), "before_id": "not-a-uuid"},
            {"before_time": db.security_time.isoformat(), "before_id": str(db.security_ids[0]), "offset": 1},
            {"event_group": "invented"},
            {"user_query": "x" * 256},
        ):
            response = await call(db, admin, "GET", path, params={**params, "tenant_id": str(db.tenants[1])})
            assert response.status_code == 422, {"query_keys": sorted(params), "status": response.status_code}


async def test_promised_security_inclusive_end_date_covers_last_postgres_microsecond(event_filter_db):
    from datetime import datetime, timezone
    db = event_filter_db
    target = uuid4()
    timestamp = datetime(2026, 1, 2, 23, 59, 59, 999999, tzinfo=timezone.utc)
    source = db.event_prefix + "-boundary"
    await db.owner.execute(
        """INSERT INTO security_events(event_id,created_at,event_type,severity,status,tenant_id,
           detection_source,title,description) VALUES($1,$2,'suspicious_activity','LOW','open',$3,$4,
           'Owned day boundary','Synthetic')""", target, timestamp, db.tenants[1], source,
    )
    query = {"tenant_id": str(db.tenants[1]), "detection_source": source,
             "start_date": "2026-01-02T00:00:00Z"}
    async with signed_in(db) as admin:
        exact = rows(await call(db, admin, "GET", SECURITY, params={
            **query, "end_date": "2026-01-02T23:59:59.999999Z"}))
        assert [r["event_id"] for r in exact] == [str(target)]
        early = rows(await call(db, admin, "GET", SECURITY, params={
            **query, "end_date": "2026-01-02T23:59:59.999Z"}))
        assert early == []

"""Normal-MFA original Gmail account inspection on exact owned PostgreSQL rows.

Only GmailConnector.get_email is synthetic. Resolver, factory, encryption,
authentication, SQL and response projection remain real. The preseeded account
replacement states do not claim an OAuth replacement journey or provider delivery.
"""
import base64
import hashlib
import json
import os
import re
from datetime import datetime
from uuid import uuid4

import pytest
import pytest_asyncio

from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import call, signed_in

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


@pytest_asyncio.fixture
async def inspection_db(journey_db, monkeypatch):  # noqa: F811
    from app.core import postgres_adapter
    from app.infrastructure.connectors import encryption
    from app.infrastructure.connectors.base import ConnectorProviderError
    from app.infrastructure.connectors.email.base import EmailMessage
    from app.infrastructure.connectors.email.gmail import GmailConnector

    db = journey_db
    db.client_ip = "127.0.1." + str(1 + uuid4().int % 250)
    db.inspection_ids = {"assistant_actions": [], "connector_accounts": [], "connectors": []}
    db.inspection_cleanup = {"complete": False}
    db.report["inspection_cleanup"] = db.inspection_cleanup
    db.inspection_reads, db.inspection_provider_reads = [], []
    db.report["inspection_adapter_reads"] = db.inspection_reads
    db.report["inspection_provider_reads"] = db.inspection_provider_reads
    db.inspection_request = None
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)

    # The fluent adapter opens its own connections. Retain its real execution
    # and bind each connection to the same restricted role as normal auth.
    monkeypatch.setattr(postgres_adapter, "_DATABASE_URL",
                        os.environ["TEST_ADMIN_DATABASE_URL"] + "?role=" + role)
    original_execute = postgres_adapter.QueryBuilder._execute_with_conn

    async def observed_execute(query, conn):
        flags = dict(await conn.fetchrow(
            "SELECT current_user AS role,rolsuper,rolbypassrls "
            "FROM pg_roles WHERE rolname=current_user"
        ))
        assert flags["role"] == role and not flags["rolsuper"] and not flags["rolbypassrls"]
        entry = {"table": query.table_name, "operation": query.query_type, **flags}
        db.inspection_reads.append(entry)
        assert query.query_type == "select"
        if query.table_name in db.inspection_ids:
            selected = db.inspection_request
            assert selected is not None
            filters = {(key, op, str(value)) for key, op, value in query.filters}
            expected = {("id", "=", str(selected["action_id"]))}
            if query.table_name != "assistant_actions":
                expected = {("tenant_id", "=", str(db.tenants[1])), ("status", "=", "active")}
                if query.table_name == "connectors":
                    expected |= {("id", "=", str(db.inspection_connector)),
                                 ("provider", "=", "gmail"), ("type", "=", "email")}
                else:
                    expected |= {("connector_id", "=", str(db.inspection_connector)),
                                 ("id", "=", str(selected["account_id"])),
                                 ("external_account_id", "=", selected["external_id"])}
            assert filters == expected
            entry["exact_owned_filters"] = True
        result = await original_execute(query, conn)
        entry["result"] = "error" if result.error else "success"
        return result

    monkeypatch.setattr(postgres_adapter.QueryBuilder, "_execute_with_conn", observed_execute)
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", base64.urlsafe_b64encode(
        hashlib.sha256(b"synthetic-original-account-inspection-fixture").digest()).decode())
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEYS_OLD", raising=False)
    monkeypatch.setattr(encryption, "_encryption_service", None)
    cipher = encryption.get_encryption_service()

    async def owned_get_email(connector, message_id):
        selected = db.inspection_request
        permitted = selected is db.inspection_actions["active_original"]
        identity_matches = permitted and (
            connector.provider_name == "gmail"
            and str(connector.tenant_id) == str(db.tenants[1])
            and str(connector.connector_id) == str(db.inspection_connector)
            and connector.account_row_id == str(selected["account_id"])
            and connector.external_account_id == selected["external_id"]
            and message_id == selected["message_id"]
        )
        credential_matches = permitted and connector._access_token == "synthetic-original-active-access"
        db.inspection_provider_reads.append({
            "operation": "get_email", "exact_original_identity": bool(identity_matches),
            "original_decrypted_access": bool(credential_matches),
        })
        assert identity_matches and credential_matches, "Unexpected provider read boundary"
        count = len(db.inspection_provider_reads)
        assert count <= 2, "No implicit retry or additional provider read is permitted"
        if count == 1:
            raise ConnectorProviderError(provider="gmail", operation="get_email",
                                         category="not_found", status_code=404,
                                         message="Synthetic owned message is not observed")
        return EmailMessage(id=message_id, subject="Synthetic private subject", body="Synthetic private body")

    monkeypatch.setattr(GmailConnector, "get_email", owned_get_email)
    try:
        await db.owner.execute(f'GRANT SELECT ON assistant_actions,connectors,connector_accounts TO "{role}"')
        async with db.pool.acquire() as conn:
            for table in db.inspection_ids:
                assert not await conn.fetchval(
                    "SELECT has_table_privilege(current_user,$1,'INSERT') "
                    "OR has_table_privilege(current_user,$1,'UPDATE') "
                    "OR has_table_privilege(current_user,$1,'DELETE')", table,
                )
        db.inspection_connector = uuid4()
        db.inspection_ids["connectors"].append(db.inspection_connector)
        await db.owner.execute(
            "INSERT INTO connectors(id,tenant_id,type,provider,name,status,config) "
            "VALUES($1,$2,'email','gmail','Synthetic original account inspection','active','{}'::jsonb)",
            db.inspection_connector, db.tenants[1],
        )
        accounts = {}
        for label, state, age in (("active", "active", 120), ("revoked", "revoked", 60),
                                  ("replacement", "active", 0)):
            account_id = uuid4()
            db.inspection_ids["connector_accounts"].append(account_id)
            external_id = "synthetic-external-" + label
            accounts[label] = (account_id, external_id)
            await db.owner.execute(
                """INSERT INTO connector_accounts(id,connector_id,tenant_id,status,external_account_id,
                       access_token_encrypted,refresh_token_encrypted,token_expires_at,created_at,revoked_at)
                   VALUES($1,$2,$3,$4::varchar,$5,$6,$7,clock_timestamp()+interval '1 hour',
                          clock_timestamp()-make_interval(mins=>$8::int),
                          CASE WHEN $4::varchar='revoked' THEN clock_timestamp() ELSE NULL END)""",
                account_id, db.inspection_connector, db.tenants[1], state, external_id,
                cipher.encrypt("synthetic-original-active-access" if label == "active"
                               else "synthetic-" + label + "-access"),
                cipher.encrypt("synthetic-" + label + "-refresh-never-used"), age,
            )
        db.inspection_actions = {}
        for name, label in (("active_original", "active"), ("revoked_original", "revoked")):
            action_id = uuid4()
            db.inspection_ids["assistant_actions"].append(action_id)
            account_id, external_id = accounts[label]
            message_id = "synthetic-saved-message-" + label
            proof = {"identity_version": "authorization_row_v1", "tenant_id": str(db.tenants[1]),
                     "provider": "gmail", "connector_id": str(db.inspection_connector),
                     "account_row_id": str(account_id), "external_account_id": external_id}
            await db.owner.execute(
                """INSERT INTO assistant_actions(id,tenant_id,user_id,connector_id,type,status,
                       triggered_by,input_data,output_data)
                   VALUES($1,$2,$3,$4,'send_email','unknown','assistant',$5::jsonb,$6::jsonb)""",
                action_id, db.tenants[1], db.users[1], db.inspection_connector,
                json.dumps({"reviewed_connector": proof}), json.dumps({**proof, "message_id": message_id}),
            )
            db.inspection_actions[name] = {"action_id": action_id, "account_id": account_id,
                                           "external_id": external_id, "message_id": message_id}
        yield db
    finally:
        async with db.owner.transaction():
            await db.owner.execute("SET LOCAL app.bypass_rls='on'")
            # Resolutions are immutable. An unexpected one must fail cleanup,
            # never be erased by disabling the evidence-retention trigger.
            assert await db.owner.fetchval(
                "SELECT count(*) FROM assistant_action_resolutions WHERE action_id=ANY($1::uuid[])",
                db.inspection_ids["assistant_actions"],
            ) == 0
            for table, ids in db.inspection_ids.items():
                await db.owner.execute(f"DELETE FROM {table} WHERE id=ANY($1::uuid[]) AND tenant_id=$2",
                                       ids, db.tenants[1])
                assert await db.owner.fetchval(f"SELECT count(*) FROM {table} WHERE id=ANY($1::uuid[])", ids) == 0
        db.inspection_cleanup.update(complete=True, removed_seed_counts={
            table: len(ids) for table, ids in db.inspection_ids.items()
        })


async def owned_snapshot(db):
    result = {}
    for table, ids in db.inspection_ids.items():
        result[table] = [dict(row) for row in await db.owner.fetch(
            f"SELECT * FROM {table} WHERE id=ANY($1::uuid[]) ORDER BY id", ids,
        )]
        assert len(result[table]) == len(ids)
    assert await db.owner.fetchval(
        "SELECT count(*) FROM assistant_action_resolutions WHERE action_id=ANY($1::uuid[])",
        db.inspection_ids["assistant_actions"],
    ) == 0
    all_ids = [identity for ids in db.inspection_ids.values() for identity in ids]
    assert await db.owner.fetchval(
        "SELECT count(*) FROM audit_logs WHERE resource_id=ANY($1::uuid[])", all_ids,
    ) == 0
    return result


async def inspect(db, admin, name, expected_outcome, expected_reason, expected_provider_reads):
    selected = db.inspection_actions[name]
    db.inspection_request = selected
    start = len(db.inspection_reads)
    try:
        response = await call(db, admin, "GET",
                              f"/admin/actions/{selected['action_id']}/email-inspection")
    finally:
        db.inspection_request = None
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data["action_id"] == str(selected["action_id"])
    assert data["outcome"] == expected_outcome and data["reason"] == expected_reason
    assert datetime.fromisoformat(data["observed_at"]).tzinfo is not None
    assert data["observed_message_id"] == (selected["message_id"] if expected_outcome == "observed_message" else None)
    assert "Synthetic private" not in response.text
    reads = db.inspection_reads[start:]
    assert [item["table"] for item in reads] == ["assistant_actions", "connectors", "connector_accounts"]
    assert all(item["exact_owned_filters"] and item["result"] == "success" for item in reads)
    assert len(db.inspection_provider_reads) == expected_provider_reads
    return {key: data[key] for key in ("outcome", "reason", "observed_at")}


async def test_original_account_reads_stay_pinned_and_never_resolve_unknown_actions(inspection_db):
    from app.api.v1 import dependencies

    db = inspection_db
    before = await owned_snapshot(db)
    async with signed_in(db) as admin:
        absent = await inspect(db, admin, "active_original", "not_observed", "absence_is_inconclusive", 1)
        assert await owned_snapshot(db) == before
        observed = await inspect(db, admin, "active_original", "observed_message", "exact_message_observed_only", 2)
        assert await owned_snapshot(db) == before
        revoked = await inspect(db, admin, "revoked_original", "unavailable", "original_authorization_unavailable", 2)
    if dependencies._audit_logger_service:
        await dependencies._audit_logger_service.flush()
    assert await owned_snapshot(db) == before
    admin_steps = [step for step in db.report["steps"] if step["path"].startswith("/admin/")]
    assert len(admin_steps) == 3 and all(step["method"] == "GET" and step["status"] == 200 for step in admin_steps)
    assert len(db.inspection_provider_reads) == 2
    assert all(item["exact_original_identity"] and item["original_decrypted_access"]
               for item in db.inspection_provider_reads)
    RUN_EVIDENCE.append({
        "case": "original_active_then_revoked_authorization_with_newer_replacement",
        "normal_password_mfa": True, "business_gets": 3, "business_http_writes": 0,
        "provider_reads": 2, "revoked_original_provider_reads": 0,
        "provider_boundary": "Only exact owned GmailConnector.get_email returns synthetic404 then exact message",
        "observations": [absent, observed, revoked], "saved_action_statuses": ["unknown", "unknown"],
        "owned_rows_unchanged": True, "owned_rows_before_sha256": digest(before),
        "owned_rows_after_sha256": digest(await owned_snapshot(db)),
        "credential_or_action_write_privilege": False, "actual_adapter_role_verified": True,
        "business_mutation_audits": 0, "resolutions": 0,
        "replacement_state": "Preseeded newer active row; no OAuth replacement transaction claimed",
        "real_provider_or_delivery_claimed": False,
    })

"""AG05 acceptance on actual migrated, disposable PostgreSQL tables.

Each case owns synthetic UUID rows and a NOSUPERUSER NOBYPASSRLS role. Provider,
telephone and email operations are not invoked. Direct API calls below exercise
the real projection handlers, not HTTP authentication or deployed browser login.
"""

import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.api.v1.dependencies import CurrentUser, get_current_user
from app.api.v1.endpoints import lead_details as api
from app.api.v1.endpoints import calls as calls_api
from app.core import container
from app.core.db_utils import acquire_with_tenant
from app.core.security import rbac
from app.domain.services.lead_capture_service import LeadCaptureService
from app.domain.services.transcript_service import TranscriptService
from app.domain.services.voice_pipeline.contact_capture import (
    CaptureStatus,
    ContactCaptureState,
    ContactSource,
)
from app.domain.services.voice_pipeline.lead_slot_capture import capture_session_slots
from app.services.scripts import call_transcript_persister as persister

pytestmark = pytest.mark.integration
_artifact_started = False


def record_example(fixture, name, observed, expected, *, scope="Actual PostgreSQL and direct API projection"):
    """Opt-in synthetic expected/observed evidence, never a production dump."""
    global _artifact_started
    output = os.environ.get("AG05_EVIDENCE_OUTPUT")
    if not output:
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text(encoding="utf-8")) if _artifact_started and path.exists() else {
        "scope": "Synthetic fixtures only; no provider, customer, hearing or deployed login proof",
        "migration_head": fixture.migration_head, "examples": {},
    }
    encoded = json.dumps(observed, default=str)
    for group_index, group in enumerate((fixture.tenants, fixture.campaigns, fixture.leads, fixture.calls), start=1):
        for index, identity in enumerate(group, start=1):
            encoded = encoded.replace(str(identity), f"00000000-0000-0000-0000-{group_index:06d}{index:06d}")
    data["examples"][name] = {"annotated_expected": expected, "scope": scope, "observed": json.loads(encoded)}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _artifact_started = True


@pytest_asyncio.fixture
async def lead_db(monkeypatch):
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit disposable TEST_DATABASE_URL required")
    parsed = urlparse(dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"} or not parsed.path.endswith("_test"):
        pytest.fail("AG05 accepts only a disposable localhost *_test database")
    admin = await asyncpg.connect(dsn)
    pool = None
    role = "ag05_" + uuid4().hex
    tenants, campaigns, leads, calls = ([uuid4(), uuid4()] for _ in range(4))
    try:
        head = await admin.fetchval("SELECT version_num FROM alembic_version")
        assert await admin.fetchval("SELECT to_regclass('public.call_lead_details') IS NOT NULL")
        assert await admin.fetchval("SELECT to_regclass('public.crm_deliveries') IS NOT NULL")
        await admin.execute(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
        await admin.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
        await admin.execute(
            f'GRANT SELECT,INSERT,UPDATE,DELETE ON calls,leads,call_lead_details,transcripts TO "{role}"'
        )
        # The actual outbound-call trigger locks its campaign FOR SHARE, which
        # requires UPDATE privilege even though this fixture does not edit it.
        await admin.execute(f'GRANT SELECT,UPDATE ON campaigns TO "{role}"')
        await admin.execute(f'GRANT SELECT ON campaign_lead_fields,crm_deliveries TO "{role}"')
        await admin.execute(f'GRANT SELECT ON call_legs,recordings_s3,call_feedback TO "{role}"')
        for tenant, campaign, lead, call in zip(tenants, campaigns, leads, calls):
            await admin.execute(
                "INSERT INTO tenants(id,business_name) VALUES($1,'Synthetic AG05')", tenant
            )
            await admin.execute(
                "INSERT INTO campaigns(id,tenant_id,name) VALUES($1,$2,'Synthetic AG05')",
                campaign, tenant,
            )
            await admin.execute(
                "INSERT INTO leads(id,tenant_id,campaign_id,phone_number) VALUES($1,$2,$3,'+15550001001')",
                lead, tenant, campaign,
            )
            await admin.execute(
                """INSERT INTO calls(id,tenant_id,campaign_id,lead_id,phone_number,status)
                VALUES($1,$2,$3,$4,'+15550001001','answered')""",
                call, tenant, campaign, lead,
            )

        async def init(conn):
            await conn.execute(f'SET ROLE "{role}"')

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, init=init)
        service = LeadCaptureService(pool)
        monkeypatch.setattr(api, "_service", lambda: service)
        fixture = SimpleNamespace(
            admin=admin, pool=pool, role=role, tenants=tenants, campaigns=campaigns,
            leads=leads, calls=calls, service=service, migration_head=head,
        )
        # Prove this pool uses the restricted role, rather than only checking
        # that fixture setup requested one.
        async with pool.acquire() as conn:
            flags = await conn.fetchrow(
                "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
            )
            assert tuple(flags.values()) == (False, False)
            assert not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM calls)")
        yield fixture
    finally:
        if pool:
            await pool.close()
        # Only this fixture's synthetic mutable rows; never disable immutable
        # receipt protections or remove another test's identities.
        await admin.execute("DELETE FROM calls WHERE id=ANY($1::uuid[])", calls)
        await admin.execute("DELETE FROM leads WHERE id=ANY($1::uuid[])", leads)
        await admin.execute("DELETE FROM campaigns WHERE id=ANY($1::uuid[])", campaigns)
        await admin.execute("DELETE FROM tenants WHERE id=ANY($1::uuid[])", tenants)
        await admin.execute(f'DROP OWNED BY "{role}"')
        await admin.execute(f'DROP ROLE "{role}"')
        await admin.close()


def contact(value="first@example.org", *, status=CaptureStatus.CONFIRMED):
    suffix = hashlib.sha256(value.encode()).hexdigest()[:12]
    return ContactCaptureState(
        kind="email", status=status, raw_value=value, normalized_value=value,
        confirmed_at=datetime.now(timezone.utc) if status is CaptureStatus.CONFIRMED else None,
        confirmation_evidence="caller_repeatback" if status is CaptureStatus.CONFIRMED else None,
        value_source=ContactSource("synthetic-value-" + suffix, 1, hashlib.sha256(value.encode()).hexdigest()),
        confirmation_source=ContactSource("synthetic-confirmation-" + suffix, 2, hashlib.sha256(("yes " + value).encode()).hexdigest())
        if status is CaptureStatus.CONFIRMED else None,
    )


def session(capture=None, *, earlier=()):
    return SimpleNamespace(captured_slots=SimpleNamespace(
        email_capture=capture or contact(), earlier_email_captures=earlier,
        phone_capture=None, earlier_phone_captures=(),
    ))


async def persist(fixture, state, **kwargs):
    # These service-only fixtures supply actual durable, synthetic caller
    # evidence. Native tests below instead produce it through parser/bridge.
    slots = state.captured_slots
    for capture in (slots.email_capture, *(slots.earlier_email_captures or ())):
        if capture is None or not capture.normalized_value:
            continue
        for source, text in ((capture.value_source, capture.normalized_value),
                             (capture.confirmation_source, "yes " + capture.normalized_value)):
            if source is not None:
                await seed_source_turn(fixture, source.provider_item_id, source.caller_turn_order, text)
    return await capture_session_slots(
        state, pool=fixture.pool, call_id=str(fixture.calls[0]),
        tenant_id=str(fixture.tenants[0]), campaign_id=str(fixture.campaigns[0]),
        lead_id=str(fixture.leads[0]), **kwargs,
    )


async def seed_source_turn(fixture, item, order, text):
    raw = await fixture.admin.fetchval("SELECT transcript_json FROM calls WHERE id=$1", fixture.calls[0])
    turns = json.loads(raw) if isinstance(raw, str) else (raw or [])
    turns = [turn for turn in turns if turn.get("metadata", {}).get("provider_item_id") != item]
    turns.append({"role": "user", "content": text, "is_final": True,
                  "metadata": {"provider_item_id": item, "caller_turn_order": order}})
    await fixture.admin.execute("UPDATE calls SET transcript_json=$2::jsonb WHERE id=$1",
                                fixture.calls[0], json.dumps(turns))


async def details(fixture, index=0):
    return await api.get_lead_details(
        str(fixture.calls[0]), None, SimpleNamespace(tenant_id=str(fixture.tenants[index]))
    )


async def test_current_confirmed_contact_keeps_confirmation_evidence_through_both_apis(lead_db):
    state = session()
    assert await persist(lead_db, state) == 1
    call = (await details(lead_db))["details"][0]
    lead = await api.get_contact_lead_details(
        str(lead_db.leads[0]), SimpleNamespace(tenant_id=str(lead_db.tenants[0]))
    )
    for row in (call, lead["details"][0]):
        assert row["confirmed"] and row["validation_status"] == "confirmed"
        assert row["evidence"]["confirmation_evidence"] == "caller_repeatback"
        assert row["confirmed_at"] is not None
    assert (await details(lead_db, 1))["details"] == []


async def test_additional_contact_withdrawal_removes_only_that_contact(lead_db):
    state = session(contact("second@example.org"), earlier=(contact(),))
    assert await persist(lead_db, state) == 2
    state.captured_slots.email_capture = ContactCaptureState(kind="email", status=CaptureStatus.CANCELLED)
    assert await persist(lead_db, state) == 1
    rows = {row["field_key"]: row for row in (await details(lead_db))["details"]}
    assert rows["email"]["value"] == "first@example.org" and rows["email"]["confirmed"]
    assert rows["email_2"]["value"] is None and not rows["email_2"]["confirmed"]
    assert rows["email_2"]["validation_status"] == "cancelled"
    record_example(lead_db, "cancelled_additional_contact", await details(lead_db),
                   "Only the withdrawn second contact is null/unconfirmed; first remains confirmed.")


async def test_manual_correction_survives_live_retry_and_withdrawal(lead_db):
    state = session()
    assert await persist(lead_db, state) == 1
    await api.correct_lead_detail(
        str(lead_db.calls[0]), "email",
        api.ManualEdit(value="operator@example.org", field_type="email"),
        SimpleNamespace(tenant_id=str(lead_db.tenants[0])),
    )
    state.captured_slots.email_capture = ContactCaptureState(kind="email", status=CaptureStatus.CANCELLED)
    await persist(lead_db, state)
    state.captured_slots.email_capture = contact("retry@example.org")
    await persist(lead_db, state)
    row = (await details(lead_db))["details"][0]
    assert (row["value"], row["source"], row["confirmed"]) == ("operator@example.org", "manual_edit", True)


async def test_contact_capture_rejects_foreign_campaign_or_lead_binding(lead_db):
    try:
        stored = await lead_db.service.capture(
            tenant_id=str(lead_db.tenants[0]), call_id=str(lead_db.calls[0]),
            campaign_id=str(lead_db.campaigns[1]), lead_id=str(lead_db.leads[1]),
            field_key="identified_need", field_type="notes", value="Synthetic note",
            source="caller_stated",
        )
    except asyncpg.ForeignKeyViolationError:
        # Migration0041 already rejects this via composite tenant FKs. A
        # service-level refusal is also valid; neither is a successful write.
        stored = False
    assert not stored
    assert (await details(lead_db))["details"] == []


async def test_contact_capture_rejects_wrong_same_tenant_call_binding(lead_db):
    campaign, lead = uuid4(), uuid4()
    lead_db.campaigns.append(campaign)
    lead_db.leads.append(lead)
    await lead_db.admin.execute(
        "INSERT INTO campaigns(id,tenant_id,name) VALUES($1,$2,'Other synthetic AG05 campaign')",
        campaign, lead_db.tenants[0],
    )
    await lead_db.admin.execute(
        "INSERT INTO leads(id,tenant_id,campaign_id,phone_number) VALUES($1,$2,$3,'+15550001002')",
        lead, lead_db.tenants[0], campaign,
    )
    stored = await lead_db.service.capture(
        tenant_id=str(lead_db.tenants[0]), call_id=str(lead_db.calls[0]),
        campaign_id=str(campaign), lead_id=str(lead), field_key="identified_need",
        field_type="notes", value="Wrong bound synthetic note", source="caller_stated",
    )
    assert not stored
    assert (await details(lead_db))["details"] == []


async def test_capture_and_read_cannot_cross_call_tenant(lead_db):
    assert not await lead_db.service.capture(
        tenant_id=str(lead_db.tenants[1]), call_id=str(lead_db.calls[0]),
        field_key="identified_need", field_type="notes", value="Foreign synthetic note",
        source="caller_stated",
    )
    assert await persist(lead_db, session()) == 1
    assert (await details(lead_db, 1))["details"] == []


async def test_actual_database_write_failure_retries_next_turn_without_duplicate(lead_db):
    state = session()
    await lead_db.admin.execute(f'REVOKE INSERT,UPDATE ON call_lead_details FROM "{lead_db.role}"')
    assert await persist(lead_db, state) == 0
    assert (await details(lead_db))["details"] == []
    await lead_db.admin.execute(f'GRANT INSERT,UPDATE ON call_lead_details TO "{lead_db.role}"')
    assert await persist(lead_db, state) == 1
    assert await persist(lead_db, state) == 0
    assert len((await details(lead_db))["details"]) == 1


async def test_failed_revoke_does_not_memoize_a_rejected_pending_correction(lead_db, monkeypatch):
    state = session()
    assert await persist(lead_db, state) == 1
    state.captured_slots.email_capture = contact(
        "corrected@example.org", status=CaptureStatus.AWAITING_CONFIRMATION,
    )
    original = LeadCaptureService.revoke_caller_contact
    attempts = 0

    async def fail_once(self, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            # Real PostgreSQL transaction failure at this exact operation;
            # later capture uses its normal separate transaction and SQL.
            async with acquire_with_tenant(lead_db.pool, str(lead_db.tenants[0])) as conn:
                await conn.execute("SELECT 1 / 0")
        return await original(self, **kwargs)

    monkeypatch.setattr(LeadCaptureService, "revoke_caller_contact", fail_once)
    assert await persist(lead_db, state) == 0
    await persist(lead_db, state)
    row = (await details(lead_db))["details"][0]
    assert row["value"] == "corrected@example.org"
    assert not row["confirmed"] and row["validation_status"] == "awaiting_confirmation"


async def test_contact_read_preserves_invalid_status_instead_of_inventing_decline(lead_db):
    state = session()
    await persist(lead_db, state)
    state.captured_slots.email_capture = ContactCaptureState(kind="email", status=CaptureStatus.INVALID)
    await persist(lead_db, state)
    row = (await details(lead_db))["details"][0]
    assert row["value"] is None and row["validation_status"] == "invalid"
    assert not row["confirmed"]
    record_example(lead_db, "invalid_contact", await details(lead_db),
                   "Null invalid contact is not a caller refusal or confirmation.")


async def test_manual_withdrawal_is_saved_as_a_protected_explicit_status(lead_db):
    state = session()
    assert await persist(lead_db, state) == 1
    await api.correct_lead_detail(
        str(lead_db.calls[0]), "email", api.ManualEdit(value=None, field_type="email"),
        SimpleNamespace(tenant_id=str(lead_db.tenants[0])),
    )
    state.captured_slots.email_capture = contact("late@example.org")
    await persist(lead_db, state)
    row = (await details(lead_db))["details"][0]
    assert row["value"] is None and row["source"] == "manual_edit"
    assert not row["confirmed"] and row["validation_status"] == "cancelled"
    record_example(lead_db, "manual_withdrawal", await details(lead_db),
                   "Manual withdrawal remains null/cancelled despite a later caller-source retry.")


async def test_required_fields_use_the_call_campaign_not_optional_query_input(lead_db):
    for index, field in enumerate(("email", "phone")):
        await lead_db.admin.execute(
            """INSERT INTO campaign_lead_fields
            (tenant_id,campaign_id,field_key,label,field_type,is_required)
            VALUES($1,$2,$3,$3,$3,TRUE)""",
            lead_db.tenants[index], lead_db.campaigns[index], field,
        )
    assert (await details(lead_db))["missing_required"] == ["email"]
    foreign_query = await api.get_lead_details(
        str(lead_db.calls[0]), str(lead_db.campaigns[1]),
        SimpleNamespace(tenant_id=str(lead_db.tenants[0])),
    )
    assert foreign_query["missing_required"] == ["email"]


async def test_hangup_write_failure_retains_final_evidence_for_existing_retry(lead_db, monkeypatch):
    svc = TranscriptService()
    buffer_id = str(uuid4())
    text = "Synthetic final correction before hangup"
    svc.accumulate_turn(buffer_id, "user", text, turn_index=1)
    voice = SimpleNamespace(
        call_id=buffer_id, _dialer_call_id=str(lead_db.calls[0]),
        _dialer_tenant_id=str(lead_db.tenants[0]), call_session=None,
    )
    monkeypatch.setattr(persister, "_schedule_call_summary", lambda *a: None)
    try:
        await lead_db.admin.execute(f'REVOKE UPDATE ON calls FROM "{lead_db.role}"')
        await persister.save_call_transcript_on_hangup(
            voice_session=voice, transcript_service=svc, db_pool=lead_db.pool,
        )
        assert text in svc.get_transcript_text(buffer_id)
        await lead_db.admin.execute(f'GRANT UPDATE ON calls TO "{lead_db.role}"')
        await persister.save_call_transcript_on_hangup(
            voice_session=voice, transcript_service=svc, db_pool=lead_db.pool,
        )
        row = await lead_db.admin.fetchrow("SELECT transcript FROM calls WHERE id=$1", lead_db.calls[0])
        assert text in row["transcript"]
        assert not svc.get_turns(buffer_id)
    finally:
        svc.clear_buffer(buffer_id)
        svc._sealed.pop(buffer_id, None)


async def test_revision_evidence_is_durable_but_original_is_preserved(lead_db):
    svc = TranscriptService()
    buffer_id = str(uuid4())
    try:
        svc.accumulate_turn(
            buffer_id, "user", "Original synthetic contact", turn_index=1,
            metadata={"provider_item_id": "synthetic-item", "caller_turn_order": 1},
        )
        assert svc.annotate_turn_revision(
            buffer_id, turn_index=1, provider_item_id="synthetic-item",
            caller_turn_order=1, content="Corrected synthetic contact",
        )
        await svc.flush_to_database(
            buffer_id, db_pool=lead_db.pool, tenant_id=str(lead_db.tenants[0]),
            target_call_id=str(lead_db.calls[0]),
        )
        row = await lead_db.admin.fetchrow("SELECT transcript_json FROM calls WHERE id=$1", lead_db.calls[0])
        turns = json.loads(row["transcript_json"])
        assert len(turns) == 1
        assert turns[0]["metadata"]["asr_latest_revision"]["content"] == "Corrected synthetic contact"
        # Raw original retention is separate from the canonical summary/UI view.
        assert svc.get_turns(buffer_id)[0].content == "Original synthetic contact"
    finally:
        svc.clear_buffer(buffer_id)


async def test_incremental_transcript_refuses_a_target_outside_bound_tenant(lead_db):
    svc = TranscriptService()
    buffer_id = str(uuid4())
    try:
        svc.accumulate_turn(buffer_id, "user", "Tenant A synthetic evidence", turn_index=1)
        await svc.flush_to_database(
            buffer_id, db_pool=lead_db.pool, tenant_id=str(lead_db.tenants[0]),
            target_call_id=str(lead_db.calls[1]),
        )
        row = await lead_db.admin.fetchrow(
            "SELECT transcript,transcript_json FROM calls WHERE id=$1", lead_db.calls[1],
        )
        assert row["transcript"] is None and row["transcript_json"] is None
    finally:
        svc.clear_buffer(buffer_id)


async def test_abrupt_child_exit_preserves_only_committed_partial_evidence(lead_db):
    """Real process exit at the writer boundary, not a live voice-worker test."""
    code = """
import asyncio
import os
import sys
from uuid import uuid4
import asyncpg
from app.domain.services.transcript_service import TranscriptService

async def main():
    dsn, role, tenant, call_id = sys.argv[1:]
    async def init(conn):
        await conn.execute('SET ROLE "' + role + '"')
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1, init=init)
    svc = TranscriptService()
    buffer_id = str(uuid4())
    svc.accumulate_turn(buffer_id, 'user', 'Synthetic committed caller evidence', turn_index=1)
    await svc.flush_to_database(buffer_id, db_pool=pool, tenant_id=tenant, target_call_id=call_id)
    svc.accumulate_turn(buffer_id, 'user', 'Uncommitted synthetic final correction', turn_index=2)
    # Deliberately skip all Python cleanup/hangup. The OS releases this
    # child-owned socket; no external service or real worker is terminated.
    os._exit(23)
asyncio.run(main())
"""
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}
    }
    environment["PYTHONUNBUFFERED"] = "1"
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code, os.environ["TEST_DATABASE_URL"], lead_db.role,
        str(lead_db.tenants[0]), str(lead_db.calls[0]),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=environment, creationflags=0x08000000 if os.name == "nt" else 0,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        assert process.returncode == 23, stderr.decode("utf-8", errors="replace")
    finally:
        if process.returncode is None:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=5)
    row = await lead_db.admin.fetchrow(
        "SELECT transcript,transcript_save_state FROM calls WHERE id=$1", lead_db.calls[0],
    )
    assert row["transcript_save_state"] == "partial"
    assert "Synthetic committed caller evidence" in row["transcript"]
    assert "Uncommitted synthetic final correction" not in row["transcript"]
    result = await details(lead_db)
    assert result["transcript_save_state"] == "partial"
    assert result["details"] == []  # No guessed contact or caller confirmation.


def source_evidence(item, order):
    source = {
        "provider_item_id": item, "caller_turn_order": order,
        "revision_sha256": hashlib.sha256(item.encode("utf-8")).hexdigest(),
    }
    return {"value_source": source, "confirmation_source": source,
            "confirmation_evidence": "caller_repeatback"}


async def write_contact(fixture, value, evidence, expected):
    for source_name in ("value_source", "confirmation_source"):
        source = evidence[source_name]
        await seed_source_turn(fixture, source["provider_item_id"], source["caller_turn_order"],
                               source["provider_item_id"])
    return await fixture.service.capture(
        tenant_id=str(fixture.tenants[0]), call_id=str(fixture.calls[0]),
        field_key="email", field_type="email", value=value, source="caller_stated",
        confirmed=True, evidence=evidence, expected_contact=expected,
    )


@pytest.mark.parametrize("newer_value", ["first@example.org", "independent@example.org"])
async def test_stale_revoke_cannot_remove_newer_independent_caller_evidence(lead_db, newer_value):
    old, current = source_evidence("old-item", 1), source_evidence("new-item", 2)
    assert await write_contact(lead_db, "first@example.org", old, {"absent": True})
    expected_old = {"value": "first@example.org", "evidence": old}
    assert await write_contact(lead_db, newer_value, current, expected_old)
    assert not await lead_db.service.revoke_caller_contact(
        tenant_id=str(lead_db.tenants[0]), call_id=str(lead_db.calls[0]),
        field_key="email", validation_status="cancelled", expected_contact=expected_old,
    )
    row = (await details(lead_db))["details"][0]
    assert row["value"] == newer_value and row["confirmed"]
    assert all(row["evidence"][key] == value for key, value in current.items())
    assert row["evidence"]["provenance_status"] == "matched"
    # Positive control: the exact current owner can still withdraw its value.
    assert await lead_db.service.revoke_caller_contact(
        tenant_id=str(lead_db.tenants[0]), call_id=str(lead_db.calls[0]),
        field_key="email", validation_status="cancelled",
        expected_contact={"value": newer_value, "evidence": current},
    )
    assert (await details(lead_db))["details"][0]["value"] is None


async def test_stale_insert_cannot_replace_independent_saved_contact(lead_db):
    current, old = source_evidence("current-item", 4), source_evidence("old-item", 1)
    assert await write_contact(lead_db, "first@example.org", current, {"absent": True})
    assert not await write_contact(lead_db, "stale@example.org", old, {"absent": True})
    row = (await details(lead_db))["details"][0]
    assert row["value"] == "first@example.org"
    assert all(row["evidence"][key] == value for key, value in current.items())


async def test_http_lead_routes_serialize_saved_evidence_and_enforce_scope(lead_db, monkeypatch):
    """Actual ASGI routes/SQL; authenticated identity and permission lookup are synthetic."""
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    actor = CurrentUser(id=str(uuid4()), email="synthetic@example.org",
                        tenant_id=str(lead_db.tenants[0]), role="tenant_admin")
    permissions = {rbac.Permission.CALLS_READ, rbac.Permission.CALLS_CREATE}

    async def effective_permissions(*args):
        return permissions

    async def seeded(*args):
        return True

    app.dependency_overrides[get_current_user] = lambda: actor
    monkeypatch.setattr(container, "get_db_pool_from_container", lambda: lead_db.pool)
    monkeypatch.setattr(rbac, "get_effective_permissions", effective_permissions)
    monkeypatch.setattr(rbac, "rbac_data_is_seeded", seeded)
    assert await persist(lead_db, session()) == 1
    path = f"/api/v1/calls/{lead_db.calls[0]}/lead-details"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        response = await client.get(path)
        assert response.status_code == 200
        saved = response.json()
        assert saved["transcript_save_state"] == "unknown"
        record_example(lead_db, "legacy_unknown_asgi", saved,
                       "No completed transcript is inferred from a saved contact.",
                       scope="Actual ASGI routes and PostgreSQL; authenticated identity and permission lookup are synthetic")
        row = saved["details"][0]
        assert row["confirmed_at"] and isinstance(row["confirmed_at"], str)
        assert row["evidence"]["confirmation_evidence"] == "caller_repeatback"
        actor.tenant_id = str(lead_db.tenants[1])
        foreign = await client.get(path)
        assert foreign.status_code == 200 and foreign.json()["details"] == []
        assert foreign.json()["processing_status"] == "no_calls"
        denied_write = await client.put(path + "/email", json={"value": None, "field_type": "email"})
        assert denied_write.status_code in {404, 409}
        actor.tenant_id = str(lead_db.tenants[0])
        withdrawn = await client.put(path + "/email", json={"value": None, "field_type": "email"})
        assert withdrawn.status_code == 200
        row = (await client.get(path)).json()["details"][0]
        assert row["value"] is None and not row["confirmed"]
        assert row["source"] == "manual_edit" and row["validation_status"] == "cancelled"
        permissions.clear()
        refused_read = await client.get(path)
        assert refused_read.status_code == 403


@pytest.mark.parametrize("provider", ["openai", "xai"])
async def test_native_confirmation_revision_reaches_actual_lead_and_transcript_reads(lead_db, provider):
    """Real event parsers/bridge/SQL, synthetic socket and playback receipt."""
    from tests.qualification.ag04_native import NativeReplay

    corpus = json.loads(
        (Path(__file__).parents[1] / "fixtures/conversation/ag04_native.json").read_text(encoding="utf-8")
    )
    replay = NativeReplay({"id": "ag05-pg", "expect": {}, "semantic_ids": []}, provider, corpus)
    replay.session._dialer_call_id = str(lead_db.calls[0])
    replay.session._dialer_tenant_id = str(lead_db.tenants[0])
    replay.session._dialer_campaign_id = str(lead_db.campaigns[0])
    replay.session._dialer_lead_id = str(lead_db.leads[0])
    replay.bridge._knowledge_pool = lead_db.pool

    async def drain_writes():
        tasks = [task for task in (
            replay.bridge._contact_persist_tail, replay.bridge._transcript_flush_task,
        ) if task is not None]
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=8)

    try:
        await replay.step({"kind": "caller", "item": "source",
                           "text": "My email is alex at example dot com."})
        await drain_writes()
        pending_response = await details(lead_db)
        pending = pending_response["details"][0]
        assert pending["value"] == "alex@example.com" and not pending["confirmed"]
        await replay.step({"kind": "response", "text": "Your email is alex@example.com, correct?"})
        await replay.step({"kind": "caller", "item": "confirmation", "text": "Yes, that is correct."})
        await drain_writes()
        confirmed_response = await details(lead_db)
        confirmed = confirmed_response["details"][0]
        assert confirmed["confirmed"] and confirmed["validation_status"] == "confirmed"
        assert confirmed["evidence"]["value_source"]["provider_item_id"] == "source"
        assert confirmed["evidence"]["confirmation_source"]["provider_item_id"] == "confirmation"
        assert confirmed["evidence"]["readback"]["evidence"] == "transport_played"
        await replay.step({"kind": "caller", "item": "confirmation", "revision": True,
                           "text": "No, that is wrong."})
        await drain_writes()
        revised = await details(lead_db)
        assert not revised["details"][0]["confirmed"]
        assert revised["details"][0]["validation_status"] != "confirmed"
        stored = await lead_db.admin.fetchrow(
            "SELECT transcript,transcript_save_state FROM calls WHERE id=$1", lead_db.calls[0],
        )
        assert "No, that is wrong." in stored["transcript"]
        assert "Yes, that is correct." not in stored["transcript"]
        assert stored["transcript_save_state"] == "partial"
        assert revised["transcript_save_state"] == "partial"
        if provider == "openai":
            for state_name, response in (("pending", pending_response), ("confirmed", confirmed_response), ("revised", revised)):
                record_example(lead_db, "native_" + state_name, response,
                    {"pending": "Caller-stated value is saved without confirmation.",
                     "confirmed": "Independent value/confirmation turns and synthetic matching receipt are retained.",
                     "revised": "Replacing the yes final removes its confirmation; transcript remains partial."}[state_name],
                    scope="Actual native event parser, bridge, PostgreSQL and direct API; synthetic wire/receipt, no provider/hearing proof")
    finally:
        replay.gateway.release.set()
        await replay.bridge.stop()
        replay.transcripts.clear_buffer(replay.call_id)


async def test_business_quote_remains_needs_review_and_manual_correction_wins(lead_db):
    from app.domain.services.call_summary.business_details import (
        persist_business_details, save_summary_details, transcript_revision,
    )

    quote = "I need information about accepting card payments."
    row = {"transcript": "User: " + quote, "transcript_json": [{"role": "user", "content": quote}],
           "action_results": None, "campaign_id": lead_db.campaigns[0], "lead_id": lead_db.leads[0]}
    summary = {"business_details": [{"field_key": "identified_need", "value": "Synthetic classification",
                                     "source_quote": quote}]}
    await lead_db.admin.execute(
        """UPDATE calls SET transcript=$2,transcript_json=$3::jsonb,summary_transcript_hash=$4,
        transcript_save_state='partial',action_results=NULL WHERE id=$1""",
        lead_db.calls[0], row["transcript"], json.dumps(row["transcript_json"]), transcript_revision(row),
    )
    await save_summary_details(lead_db.pool, str(lead_db.tenants[0]), str(lead_db.calls[0]), summary, row)
    response = await details(lead_db)
    note = response["details"][0]
    assert note["value"] == quote and not note["confirmed"]
    assert note["evidence"]["status"] == "needs_review"
    assert note["evidence"]["validation"] == "caller_quote_only"
    record_example(lead_db, "business_needs_review", response,
                   "Anchored caller quote is reviewable evidence, not verified qualification or an arranged action.")
    await api.correct_lead_detail(
        str(lead_db.calls[0]), "identified_need", api.ManualEdit(value="Operator clarification", field_type="notes"),
        SimpleNamespace(tenant_id=str(lead_db.tenants[0])),
    )
    assert await persist_business_details(
        lead_db.pool, str(lead_db.tenants[0]), str(lead_db.calls[0]), summary, row,
    ) == 0
    assert (await details(lead_db))["details"][0]["value"] == "Operator clarification"


async def test_final_child_write_failure_rolls_back_snapshot_and_reports_failed_then_complete(lead_db):
    svc = TranscriptService()
    buffer_id = str(uuid4())
    kwargs = {"db_pool": lead_db.pool, "tenant_id": str(lead_db.tenants[0]),
              "target_call_id": str(lead_db.calls[0])}
    try:
        svc.accumulate_turn(buffer_id, "user", "Earlier synthetic committed evidence", turn_index=1)
        assert await svc.flush_to_database(buffer_id, **kwargs)
        svc.accumulate_turn(buffer_id, "user", "Final synthetic correction", turn_index=2)
        await lead_db.admin.execute(f'REVOKE INSERT ON transcripts FROM "{lead_db.role}"')
        assert await svc.save_transcript(buffer_id, **kwargs) is None
        failed = await lead_db.admin.fetchrow(
            "SELECT transcript,transcript_save_state FROM calls WHERE id=$1", lead_db.calls[0],
        )
        assert failed["transcript_save_state"] == "failed"
        assert "Earlier synthetic committed evidence" in failed["transcript"]
        assert "Final synthetic correction" not in failed["transcript"]
        assert "Final synthetic correction" in svc.get_transcript_text(buffer_id)
        record_example(lead_db, "transcript_failed", await details(lead_db),
                       "Final child-write failure rolls back both final snapshots; earlier committed text remains.")
        await lead_db.admin.execute(f'GRANT INSERT ON transcripts TO "{lead_db.role}"')
        assert await svc.save_transcript(buffer_id, **kwargs)
        complete = await lead_db.admin.fetchrow(
            "SELECT transcript,transcript_save_state FROM calls WHERE id=$1", lead_db.calls[0],
        )
        assert complete["transcript_save_state"] == "complete"
        assert "Final synthetic correction" in complete["transcript"]
        children = await lead_db.admin.fetch("SELECT full_text FROM transcripts WHERE call_id=$1", lead_db.calls[0])
        assert len(children) == 1 and children[0]["full_text"] == complete["transcript"]
        record_example(lead_db, "transcript_complete", await details(lead_db),
                       "Successful same-process retry atomically stores call and child snapshot; completeness is not accuracy/hearing.")
    finally:
        svc.clear_buffer(buffer_id)
        svc._sealed.pop(buffer_id, None)


async def test_duplicate_final_jobs_and_stale_incremental_cannot_replace_completed_snapshot(lead_db):
    svc = TranscriptService()
    first_buffer, stale_buffer = str(uuid4()), str(uuid4())
    kwargs = {"db_pool": lead_db.pool, "tenant_id": str(lead_db.tenants[0]),
              "target_call_id": str(lead_db.calls[0])}
    try:
        svc.accumulate_turn(first_buffer, "user", "Authoritative synthetic final snapshot", turn_index=1)
        assert await svc.save_transcript(first_buffer, **kwargs)
        svc.accumulate_turn(stale_buffer, "user", "Stale synthetic job snapshot", turn_index=1)
        assert not await svc.flush_to_database(stale_buffer, **kwargs)
        # Different session buffers emulate separate finalizer jobs; the DB
        # calls-row fence, not only a process-local buffer lock, must decide.
        saved = await asyncio.gather(
            svc.save_transcript(first_buffer, **kwargs), svc.save_transcript(stale_buffer, **kwargs),
        )
        assert all(saved)
        call = await lead_db.admin.fetchrow("SELECT transcript FROM calls WHERE id=$1", lead_db.calls[0])
        children = await lead_db.admin.fetch("SELECT full_text FROM transcripts WHERE call_id=$1", lead_db.calls[0])
        assert call["transcript"] == "User: Authoritative synthetic final snapshot"
        assert len(children) == 1 and children[0]["full_text"] == call["transcript"]
    finally:
        for buffer_id in (first_buffer, stale_buffer):
            svc.clear_buffer(buffer_id)
            svc._sealed.pop(buffer_id, None)


async def test_transcript_api_prefers_current_call_snapshot_over_later_stale_child(lead_db):
    current = [{"role": "user", "content": "Current synthetic correction"}]
    stale = [{"role": "user", "content": "Stale child contact"}]
    await lead_db.admin.execute(
        "UPDATE calls SET transcript=$2,transcript_json=$3::jsonb,transcript_save_state='complete' WHERE id=$1",
        lead_db.calls[0], "User: Current synthetic correction", json.dumps(current),
    )
    for offset in (1, 2):
        await lead_db.admin.execute(
            """INSERT INTO transcripts(call_id,tenant_id,turns,full_text,updated_at)
            VALUES($1,$2,$3::jsonb,$4,NOW()+$5::int*INTERVAL '1 second')""",
            lead_db.calls[0], lead_db.tenants[0], json.dumps(stale), "User: Stale child contact", offset,
        )
    client = SimpleNamespace(pool=lead_db.pool)
    for fmt in ("json", "text"):
        response = await calls_api.get_call_transcript(
            str(lead_db.calls[0]), fmt, SimpleNamespace(tenant_id=str(lead_db.tenants[0])), client,
        )
        assert response["transcript_save_state"] == "complete"
        assert "Current synthetic correction" in json.dumps(response, default=str)
        assert "Stale child contact" not in json.dumps(response, default=str)
    with pytest.raises(Exception) as exc:
        await calls_api.get_call_transcript(
            str(lead_db.calls[0]), "text", SimpleNamespace(tenant_id=str(lead_db.tenants[1])), client,
        )
    assert exc.value.status_code == 404


async def test_contact_projection_prefers_latest_manual_edit_even_on_older_call(lead_db):
    newer_call = uuid4()
    lead_db.calls.append(newer_call)
    await lead_db.admin.execute("UPDATE calls SET created_at=NOW()-INTERVAL '1 day' WHERE id=$1", lead_db.calls[0])
    await lead_db.admin.execute(
        """INSERT INTO calls(id,tenant_id,campaign_id,lead_id,phone_number,status)
        VALUES($1,$2,$3,$4,'+15550001001','answered')""",
        newer_call, lead_db.tenants[0], lead_db.campaigns[0], lead_db.leads[0],
    )
    user = SimpleNamespace(tenant_id=str(lead_db.tenants[0]))
    await api.correct_lead_detail(str(newer_call), "identified_need",
        api.ManualEdit(value="Earlier operator review", field_type="notes"), user)
    await api.correct_lead_detail(str(lead_db.calls[0]), "identified_need",
        api.ManualEdit(value="Latest operator correction", field_type="notes"), user)
    timestamps = await lead_db.admin.fetch(
        "SELECT call_id,updated_at FROM call_lead_details WHERE tenant_id=$1 ORDER BY updated_at", lead_db.tenants[0],
    )
    assert [str(row["call_id"]) for row in timestamps] == [str(newer_call), str(lead_db.calls[0])]
    projected = await lead_db.service.details_for_lead(str(lead_db.tenants[0]), str(lead_db.leads[0]))
    assert projected[0]["value"] == "Latest operator correction"
    assert projected[0]["call_id"] == lead_db.calls[0]


@pytest.mark.parametrize("recovers", [True, False])
async def test_finalizer_retries_contact_after_transcript_commit_recovers_database(lead_db, monkeypatch, recovers):
    state = session()
    svc = TranscriptService()
    buffer_id = str(uuid4())
    capture = state.captured_slots.email_capture
    for index, (source, text) in enumerate(((capture.value_source, capture.normalized_value),
                                          (capture.confirmation_source, "yes " + capture.normalized_value)), 1):
        svc.accumulate_turn(buffer_id, "user", text, turn_index=index, is_final=True,
                            metadata={"provider_item_id": source.provider_item_id,
                                      "caller_turn_order": source.caller_turn_order})
    voice = SimpleNamespace(
        call_id=buffer_id, _dialer_call_id=str(lead_db.calls[0]),
        _dialer_tenant_id=str(lead_db.tenants[0]), _dialer_campaign_id=str(lead_db.campaigns[0]),
        _dialer_lead_id=str(lead_db.leads[0]), call_session=state,
    )
    original = LeadCaptureService.capture
    first = True

    async def one_actual_sql_failure(self, **kwargs):
        nonlocal first
        if kwargs.get("field_key") == "email" and (first or not recovers):
            first = False
            await lead_db.admin.execute(f'REVOKE INSERT ON call_lead_details FROM "{lead_db.role}"')
            try:
                return await original(self, **kwargs)
            finally:
                await lead_db.admin.execute(f'GRANT INSERT ON call_lead_details TO "{lead_db.role}"')
        return await original(self, **kwargs)

    monkeypatch.setattr(LeadCaptureService, "capture", one_actual_sql_failure)
    monkeypatch.setattr(persister, "_schedule_call_summary", lambda *args: None)
    try:
        assert await persister.save_call_transcript_on_hangup(
            voice_session=voice, transcript_service=svc, db_pool=lead_db.pool,
        )
        response = await details(lead_db)
        assert response["transcript_save_state"] == "complete"
        fields = {row["field_key"]: row for row in response["details"]}
        if recovers:
            assert fields["email"]["confirmed"]
            assert fields["email"]["value"] == "first@example.org"
            assert fields["contact_followup"]["evidence"]["status"] == "contact_save_recovered"
        else:
            assert "email" not in fields
            assert fields["contact_followup"]["evidence"]["status"] == "contact_save_failed"
            # Finishing the independent analysis path must not erase this
            # capture failure or imply that its absent contact was saved.
            from app.domain.services.call_summary.business_details import save_summary_details, transcript_revision
            row = dict(await lead_db.admin.fetchrow(
                "SELECT transcript,transcript_json,action_results,campaign_id,lead_id FROM calls WHERE id=$1",
                lead_db.calls[0],
            ))
            await lead_db.admin.execute("UPDATE calls SET summary_transcript_hash=$2 WHERE id=$1",
                                        lead_db.calls[0], transcript_revision(row))
            await save_summary_details(lead_db.pool, str(lead_db.tenants[0]), str(lead_db.calls[0]),
                                       {"business_details": []}, row)
            response = await details(lead_db)
            assert response["processing_status"] == "complete"
            assert response["details"][0]["evidence"]["status"] == "contact_save_failed"
        record_example(lead_db, "contact_save_recovered" if recovers else "contact_save_failed",
                       response, "Hangup retries the failed contact after transcript commit." if recovers else
                       "Known contact-save failure remains distinct even when transcript save and analysis complete.")
    finally:
        svc.clear_buffer(buffer_id)
        svc._sealed.pop(buffer_id, None)


@pytest.mark.parametrize("manual_override", [False, True])
async def test_durable_revision_invalidates_confirmation_when_contact_revoke_cannot_commit(
    lead_db, manual_override,
):
    """Projection trusts durable source revisions, not a stale confirmed flag.

    Actual native parser/bridge and SQL writes are used. The synthetic readback
    receipt proves this harness's transport boundary only, never human hearing.
    API reads have no access to the live session, modelling its subsequent loss.
    """
    from tests.qualification.ag04_native import NativeReplay

    corpus = json.loads(
        (Path(__file__).parents[1] / "fixtures/conversation/ag04_native.json").read_text(encoding="utf-8")
    )
    replay = NativeReplay({"id": "ag05-revoke-write-fault", "expect": {}, "semantic_ids": []}, "openai", corpus)
    replay.session._dialer_call_id = str(lead_db.calls[0])
    replay.session._dialer_tenant_id = str(lead_db.tenants[0])
    replay.session._dialer_campaign_id = str(lead_db.campaigns[0])
    replay.session._dialer_lead_id = str(lead_db.leads[0])
    replay.bridge._knowledge_pool = lead_db.pool

    async def drain_writes():
        tasks = [task for task in (
            replay.bridge._contact_persist_tail, replay.bridge._transcript_flush_task,
        ) if task is not None]
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=8)

    try:
        await replay.step({"kind": "caller", "item": "source",
                           "text": "My email is alex at example dot com."})
        await drain_writes()
        await replay.step({"kind": "response", "text": "Your email is alex@example.com, correct?"})
        await replay.step({"kind": "caller", "item": "confirmation", "text": "Yes, that is correct."})
        await drain_writes()
        before = (await details(lead_db))["details"][0]
        assert before["confirmed"]
        assert before["evidence"]["confirmation_source"]["provider_item_id"] == "confirmation"
        if manual_override:
            await api.correct_lead_detail(
                str(lead_db.calls[0]), "email",
                api.ManualEdit(value="operator@example.org", field_type="email"),
                SimpleNamespace(tenant_id=str(lead_db.tenants[0])),
            )
        await lead_db.admin.execute(f'REVOKE UPDATE ON call_lead_details FROM "{lead_db.role}"')
        await replay.step({"kind": "caller", "item": "confirmation", "revision": True,
                           "text": "No, that is wrong."})
        await drain_writes()
        saved = await lead_db.admin.fetchrow(
            "SELECT transcript,transcript_save_state FROM calls WHERE id=$1", lead_db.calls[0],
        )
        assert "No, that is wrong." in saved["transcript"]
        assert "Yes, that is correct." not in saved["transcript"]
        raw = await lead_db.admin.fetchrow(
            "SELECT value,confirmed,evidence FROM call_lead_details WHERE call_id=$1 AND field_key='email'",
            lead_db.calls[0],
        )
        assert raw["confirmed"]  # Failed write preserves the original DB audit row.
        call_response = await details(lead_db)
        contact_response = await api.get_contact_lead_details(
            str(lead_db.leads[0]), SimpleNamespace(tenant_id=str(lead_db.tenants[0])),
        )
        for response in (call_response, contact_response):
            row = next(detail for detail in response["details"] if detail["field_key"] == "email")
            if manual_override:
                assert row["value"] == "operator@example.org" and row["confirmed"]
                assert row["source"] == "manual_edit"
            else:
                assert not row["confirmed"]
                assert row["validation_status"] != "confirmed"
        listed = await calls_api.list_calls(
            page=1, page_size=20, status=None, direction=None, inbound_campaign_id=None,
            from_date=None, to_date=None, current_user=SimpleNamespace(tenant_id=str(lead_db.tenants[0])),
            db_client=SimpleNamespace(pool=lead_db.pool),
        )
        assert len(listed.items) == 1 and listed.items[0].id == str(lead_db.calls[0])
        assert listed.items[0].captured_email == ("operator@example.org" if manual_override else "alex@example.com")
        assert listed.items[0].captured_email_confirmed is manual_override
        record_example(lead_db, "manual_revision_immune" if manual_override else "failed_revoke_projection",
                       {"call_api": call_response, "contact_api": contact_response,
                        "call_list_api": listed.model_dump(mode="json"),
                        "raw_stored_value": raw["value"], "raw_stored_confirmed": raw["confirmed"]},
                       "Manual review remains authoritative." if manual_override else
                       "Durable replacement of the confirmation invalidates API trust despite a failed contact write; audit row is retained.",
                       scope="Actual parser/bridge and PostgreSQL; contact UPDATE denied, later API uses only durable data")
    finally:
        await lead_db.admin.execute(f'GRANT UPDATE ON call_lead_details TO "{lead_db.role}"')
        replay.gateway.release.set()
        await replay.bridge.stop()
        replay.transcripts.clear_buffer(replay.call_id)

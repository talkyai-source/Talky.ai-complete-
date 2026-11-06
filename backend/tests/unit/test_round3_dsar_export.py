"""F12-06: actual tenant-scoped manifest/export service, no external I/O."""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
import importlib
import io
import json
from pathlib import Path
import re
from uuid import UUID
from zipfile import ZipFile

import pytest
from fastapi import HTTPException


TENANT = "a0000000-0000-4000-8000-000000000001"
OTHER = "b0000000-0000-4000-8000-000000000002"
ACTOR = "c0000000-0000-4000-8000-000000000003"
PHONE = "+14155552671"
EMAIL = "person@example.invalid"
EMPTY_ALERT_TABLES = frozenset({"security_events", "campaign_alert_outbox", "campaign_alert_state", "tenant_alert_state"})


def uid(number):
    return f"d0000000-0000-4000-8000-{number:012d}"


def module():
    path = Path(__file__).resolve().parents[2] / "app/domain/services/dsar_service.py"
    assert path.is_file(), "F12-06 is missing the audited DSAR manifest/export service"
    return importlib.import_module("app.domain.services.dsar_service")


def data():
    rows = {name: [] for name in (
        "leads", "calls", "call_lead_details", "transcripts", "conversations",
        "recordings_s3", "recordings", "call_feedback", "conversation_reviews", "call_workflows",
        "crm_deliveries", "dnc_entries", "call_guard_decisions", "call_events",
        "call_legs", "stream_events", "dialer_jobs", "assistant_actions", "meetings",
        "reminders",
    )}
    for index, tenant, phone, email in (
        (1, TENANT, PHONE, EMAIL),
        (2, TENANT, "+442079460958", "unrelated@example.invalid"),
        (3, OTHER, PHONE, EMAIL),
    ):
        lead, call = uid(index), uid(index + 10)
        rows["leads"].append({
            "id": lead, "tenant_id": tenant, "campaign_id": uid(index + 20),
            "phone_number": phone, "email": email, "first_name": f"Subject {index}",
            "calling_notes": f"private lead note {index}", "custom_fields": {"private": index},
        })
        rows["calls"].append({
            "id": call, "tenant_id": tenant, "lead_id": lead, "phone_number": phone,
            "status": "completed", "transcript": f"private transcript {index}",
            "transcript_json": [{"text": f"private turn {index}"}],
            "summary": f"private summary {index}",
        })
        for offset, name in enumerate(rows):
            if name in {"leads", "calls", "dnc_entries", "recordings"}:
                continue
            row = {"id": uid(index * 100 + offset), "tenant_id": tenant,
                   "call_id": call, "lead_id": lead, "private_marker": f"{name}:{index}"}
            if name == "call_lead_details":
                row.update(field_key="email", field_type="email", value=email,
                           raw_value=email, normalized_value=email, confirmed=True,
                           validation_status="confirmed")
            elif name == "recordings_s3":
                row.update(s3_bucket="local", s3_key=f"private/{index}.wav",
                           status="uploaded", mime_type="audio/wav", file_size_bytes=4)
            elif name == "call_feedback":
                row.update(audio_storage_provider="s3", audio_bucket="qa-feedback",
                           audio_key=f"feedback/{index}.wav", audio_size_bytes=4,
                           transcript=f"private reviewer note {index}")
            elif name == "crm_deliveries":
                row.update(provider="hubspot", contact_effect={"arguments": {"email": email}},
                           remote_contact_id=f"remote-{index}")
            elif name == "call_guard_decisions":
                row.update(phone_number=phone)
            elif name == "stream_events":
                row["related_call_id"] = row.pop("call_id")
            elif name in {"call_events", "call_legs"}:
                row.pop("tenant_id")
            rows[name].append(row)
        rows["dnc_entries"].append({"id": uid(index + 30), "tenant_id": tenant,
                                     "phone_number": phone, "normalized_number": phone})
    # A platform-global suppression record is not owned by the requested tenant.
    rows["dnc_entries"].append({"id": uid(34), "tenant_id": None,
                                 "phone_number": PHONE, "normalized_number": PHONE})
    # This older fixture models present-but-empty alert relations. The dedicated
    # campaign-alert DSAR fixture executes their nonempty predicates in SQLite.
    # Actual absence is still represented only by Connection.absent.
    rows.update({table: [] for table in EMPTY_ALERT_TABLES})
    return rows


class Connection:
    """Strict query fixture with no implicit RLS filtering (BYPASSRLS proof)."""

    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.depth = 0
        self.audit_locked = False
        self.admin = True
        self.events = []
        self.hold = False
        self.absent = set()
        self.reads = []
        self.fail_table = None

    def is_in_transaction(self):
        return self.depth > 0

    @asynccontextmanager
    async def transaction(self, **kwargs):
        before = deepcopy(self.events)
        self.depth += 1
        try:
            yield
        except BaseException:
            self.events = before
            raise
        finally:
            self.depth -= 1
            if self.depth == 0:
                self.audit_locked = False

    async def execute(self, sql, *args):
        flat = " ".join(sql.lower().split())
        allowed = (
            "set local statement_timeout", "set local lock_timeout",
            "set local app.current_tenant_id", "set local app.bypass_rls",
            "select set_config('app.current_user_id'", "select set_config('app.current_request_id'",
        )
        assert flat.startswith(allowed), f"Unexpected business mutation: {sql}"
        return "SELECT 1"

    async def fetchval(self, sql, *args):
        flat = " ".join(sql.lower().split())
        if "to_regclass" in flat:
            table = str(args[0]).removeprefix("public.")
            assert table in self.rows or table == "suspension_events"
            return None if table in self.absent else table
        if "suspension_events" in flat:
            assert str(args[0]) == TENANT
            assert "$1" in flat and "target_id" in flat
            return self.hold
        raise AssertionError(f"Unexpected scalar SQL: {sql}")

    def _select(self, sql, args):
        flat = " ".join(sql.lower().split())
        match = re.search(r"\bfrom\s+(?:public\.)?(\w+)\s+(?:as\s+)?r\b", flat)
        assert match, f"Expected fixed-registry SQL using alias r: {sql}"
        table = match.group(1)
        assert table in self.rows, f"Unexpected data table: {table}"
        assert table not in self.absent, "Missing-table preflight must precede the data query"
        assert self.audit_locked and self.is_in_transaction(), "Authorize and audit-lock before reading PII"
        assert "limit" in flat and isinstance(args[-1], int) and 0 < args[-1] <= 10001
        assert str(args[0]) == TENANT, "Every data query binds the selected tenant first"
        if table in {"call_events", "call_legs", "recordings", "transcripts"}:
            assert re.search(r"\bjoin\s+(?:public\.)?calls\s+(?:as\s+)?c\b", flat)
            assert re.search(r"c\.tenant_id\s*=\s*\$1", flat)
            assert "r.call_id" in flat and "c.id" in flat
        else:
            assert re.search(r"r\.tenant_id\s*=\s*\$1", flat), f"Unscoped table: {table}"
        if table == "dnc_entries":
            assert "tenant_id is null" not in flat, "Do not export platform-global DNC data"
        if self.fail_table == table:
            raise RuntimeError("synthetic permission failure; not a missing table")
        self.reads.append(table)
        tenant = str(args[0])
        rows = self.rows[table]
        if table in {"call_events", "call_legs", "recordings", "transcripts"}:
            owned = {r["id"] for r in self.rows["calls"] if r["tenant_id"] == tenant}
            rows = [r for r in rows if r["call_id"] in owned and r.get("tenant_id") in {None, tenant}]
        else:
            rows = [r for r in rows if r["tenant_id"] == tenant]

        # The scope parameters are deliberately explicit across all registry queries:
        # tenant, whole_tenant, normalized_phone, normalized_email, lead_ids, call_ids, limit.
        assert len(args) == 7, "Scope must travel as bound values, not interpolated SQL"
        _, whole, phone, email, leads, calls, limit = args
        leads, calls = set(map(str, leads)), set(map(str, calls))
        assert isinstance(whole, bool)
        assert "$2" in flat
        if table in EMPTY_ALERT_TABLES:
            assert not self.rows[table], "Use the SQL-executing alert fixture for nonempty alert data"
            if table in {"security_events", "campaign_alert_outbox"}:
                assert "alert_call.tenant_id=$1" in flat and "alert_lead.tenant_id=$1" in flat
                assert "alert_call.id=any($6" in flat and "alert_lead.id=any($5" in flat
            if table == "campaign_alert_outbox":
                assert "join public.security_events e" in flat and "e.tenant_id=$1" in flat
            if table in {"campaign_alert_state", "tenant_alert_state"}:
                assert "$2::boolean or (false)" in flat
            return []
        if not whole:
            if table == "leads":
                assert "r.phone_number" in flat and "r.email" in flat and "$5" in flat
                rows = [r for r in rows if r["id"] in leads
                        or (phone is not None and r["phone_number"] == phone)
                        or (email is not None and str(r.get("email") or "").strip().lower() == email)]
            elif table == "calls":
                assert "r.phone_number" in flat and "$5" in flat and "$6" in flat
                rows = [r for r in rows if r["lead_id"] in leads or r["id"] in calls
                        or (phone is not None and r["phone_number"] == phone)]
            elif table == "call_lead_details" and "validation_status" in flat:
                assert "confirmed" in flat and "normalized_value" in flat
                rows = [r for r in rows if r.get("validation_status") == "confirmed" and
                        ((email is not None and r["normalized_value"] == email)
                         or (phone is not None and r["normalized_value"] == phone))]
            elif table in {"dnc_entries", "call_guard_decisions"}:
                number_field = "normalized_number" if table == "dnc_entries" else "phone_number"
                assert number_field in flat and "$3" in flat
                numbers = {r["phone_number"] for r in self.rows["leads"] if r["id"] in leads}
                numbers |= {r["phone_number"] for r in self.rows["calls"] if r["id"] in calls}
                rows = [r for r in rows if r[number_field] == phone or r[number_field] in numbers
                        or (table == "call_guard_decisions" and r.get("call_id") in calls)]
            else:
                assert "$6" in flat and "call_id" in flat
                rows = [r for r in rows if r.get("call_id", r.get("related_call_id")) in calls
                        or r.get("lead_id") in leads]
        return [{"data": json.dumps(r), "total_count": len(rows)} for r in rows[:limit]]

    async def fetch(self, sql, *args):
        return self._select(sql, args)


class Pool:
    def __init__(self, conn):
        self.conn = conn
        self.active = 0

    @asynccontextmanager
    async def acquire(self, *, timeout=None):
        # acquire_connection owns the entry timeout; passing asyncpg's timeout
        # here would also shorten connection release/reset (the C3 regression).
        assert timeout is None
        self.active += 1
        try:
            yield self.conn
        finally:
            self.active -= 1


class Audit:
    fail = False

    async def lock_transaction(self, conn):
        assert conn.is_in_transaction()
        conn.audit_locked = True

    async def log_in_transaction(self, conn, **entry):
        assert conn.audit_locked and conn.is_in_transaction()
        assert str(entry["tenant_id"]) == TENANT and str(entry["actor_id"]) == ACTOR
        rendered = json.dumps(entry, default=str)
        assert PHONE not in rendered and EMAIL not in rendered, "Audit counts/digest, not raw subject identity"
        if self.fail:
            raise RuntimeError("synthetic durable audit failure")
        conn.events.append(entry)
        return UUID(uid(9999))


class Artifacts:
    def __init__(self):
        self.reads = []
        self.missing = set()

    async def copy_into(self, kind, row, target, *, max_bytes):
        assert kind in {"recordings_s3", "recordings", "call_feedback"}
        assert row["tenant_id"] == TENANT or (kind == "recordings" and row["tenant_id"] is None)
        assert 0 < max_bytes <= 256 * 1024 * 1024
        self.reads.append((kind, row["id"]))
        if row["id"] in self.missing:
            raise FileNotFoundError("synthetic missing object")
        payload = b"RIFF" if kind == "recordings_s3" else b"NOTE"
        target.write(payload)
        return len(payload)


@pytest.fixture
def harness(monkeypatch):
    mod = module()
    conn, audit, artifacts = Connection(data()), Audit(), Artifacts()

    async def authority(connection, actor):
        assert connection is conn and conn.is_in_transaction() and conn.audit_locked
        assert str(actor) == ACTOR
        if not conn.admin:
            raise HTTPException(403, "Current platform admin authority is required")

    monkeypatch.setattr(mod, "require_platform_admin_mutation", authority)
    pool = Pool(conn)
    service = mod.DsarService(pool, audit=audit, artifacts=artifacts)
    return mod, service, conn, audit, artifacts, pool


def scope(mod, **kwargs):
    return mod.DsarScope(tenant_id=TENANT, **(kwargs or {"phone": PHONE}))


def read_rows(path):
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        rows = {name.split("/")[1].removesuffix(".jsonl"):
                [json.loads(line) for line in archive.read(name).splitlines()]
                for name in archive.namelist() if name.startswith("rows/")}
        audio = {name: archive.read(name) for name in archive.namelist() if name.startswith("audio/")}
    return manifest, rows, audio


async def test_preview_is_audited_but_never_mutates_subject_data_or_reads_audio(harness):
    mod, service, conn, _, artifacts, pool = harness
    before = deepcopy(conn.rows)
    result = await service.preview(scope(mod), actor_id=ACTOR)
    assert result["mode"] == "dry_run" and result["destructive_apply_supported"] is False
    for table in conn.rows:
        assert result["counts"][table] == (0 if table in EMPTY_ALERT_TABLES | {"recordings"} else 1), table
    assert conn.rows == before and artifacts.reads == [] and pool.active == 0
    assert len(conn.events) == 1 and conn.events[0]["action"] == "dsar.preview"


@pytest.mark.parametrize("selector", [{"phone": "+1 (415) 555-2671"}, {"email": " Person@Example.Invalid "}])
async def test_export_contains_all_subject_rows_and_audio_and_no_other_person_or_tenant(harness, tmp_path, selector):
    mod, service, conn, _, artifacts, pool = harness
    before = deepcopy(conn.rows)
    result = await service.export(scope(mod, **selector), actor_id=ACTOR, output=tmp_path / "subject.zip")
    manifest, rows, audio = read_rows(result.path)
    assert manifest["complete"] is True
    assert result.manifest == manifest
    assert rows["leads"][0]["calling_notes"] == "private lead note 1"
    assert rows["calls"][0]["transcript"] == "private transcript 1"
    assert rows["call_feedback"][0]["transcript"] == "private reviewer note 1"
    assert rows["crm_deliveries"][0]["contact_effect"]["arguments"]["email"] == EMAIL
    for table, values in rows.items():
        assert len(values) == (0 if table in EMPTY_ALERT_TABLES | {"recordings"} else 1), table
        assert all(r.get("tenant_id", TENANT) == TENANT for r in values)
        assert all(r.get("call_id", uid(11)) == uid(11) for r in values)
    assert sorted(audio.values()) == [b"NOTE", b"RIFF"]
    assert len(artifacts.reads) == 2 and conn.rows == before and pool.active == 0
    assert any(e["action"] == "dsar.export" for e in conn.events)
    if __import__("os").name != "nt":
        assert result.path.stat().st_mode & 0o777 == 0o600


async def test_direct_number_call_without_a_lead_is_included(harness, tmp_path):
    mod, service, conn, *_ = harness
    conn.rows["calls"].append({"id": uid(44), "tenant_id": TENANT, "lead_id": None,
                               "phone_number": PHONE, "transcript": "direct inbound subject"})
    result = await service.export(scope(mod), actor_id=ACTOR, output=tmp_path / "direct.zip")
    _, rows, _ = read_rows(result.path)
    assert {r["id"] for r in rows["calls"]} == {uid(11), uid(44)}


async def test_email_export_includes_phone_guard_decisions_without_a_call_id(harness, tmp_path):
    mod, service, conn, *_ = harness
    conn.rows["call_guard_decisions"][0]["call_id"] = None
    result = await service.export(scope(mod, email=EMAIL), actor_id=ACTOR, output=tmp_path / "guard.zip")
    _, rows, _ = read_rows(result.path)
    assert len(rows["call_guard_decisions"]) == 1
    assert rows["call_guard_decisions"][0]["phone_number"] == PHONE


async def test_legacy_nullable_tenant_metadata_is_bound_through_its_call(harness, tmp_path):
    mod, service, conn, *_ = harness
    conn.rows["transcripts"][0]["tenant_id"] = None
    conn.rows["recordings"].extend([
        {"id": uid(80), "tenant_id": None, "call_id": uid(11), "storage_path": "private/legacy.wav"},
        {"id": uid(81), "tenant_id": None, "call_id": uid(13), "storage_path": "private/other.wav"},
        {"id": uid(82), "tenant_id": OTHER, "call_id": uid(11), "storage_path": "private/mismatched.wav"},
    ])
    result = await service.export(scope(mod), actor_id=ACTOR, output=tmp_path / "legacy-local.zip")
    _, rows, _ = read_rows(result.path)
    assert [r["id"] for r in rows["recordings"]] == [uid(80)]
    assert len(rows["transcripts"]) == 1 and rows["transcripts"][0]["tenant_id"] is None


async def test_confirmed_captured_email_resolves_call_but_unconfirmed_capture_does_not(harness, tmp_path):
    mod, service, conn, *_ = harness
    conn.rows["leads"][0]["email"] = None
    # A second person's unconfirmed mention must not widen the export.
    conn.rows["call_lead_details"][1].update(normalized_value=EMAIL, validation_status="awaiting_confirmation")
    result = await service.export(scope(mod, email=EMAIL), actor_id=ACTOR, output=tmp_path / "capture.zip")
    _, rows, _ = read_rows(result.path)
    assert {r["id"] for r in rows["calls"]} == {uid(11)}
    assert {r["id"] for r in rows["leads"]} == {uid(1)}


async def test_missing_optional_table_and_missing_audio_are_visible_incomplete_results(harness, tmp_path):
    mod, service, conn, _, artifacts, _ = harness
    conn.absent.add("stream_events")
    missing_id = conn.rows["recordings_s3"][0]["id"]
    artifacts.missing.add(missing_id)
    result = await service.export(scope(mod), actor_id=ACTOR, output=tmp_path / "missing.zip")
    manifest, rows, audio = read_rows(result.path)
    assert manifest["complete"] is False
    assert "stream_events" in manifest["missing_tables"]
    assert any(f["code"] == "artifact_unavailable" and f["resource_id"] == missing_id
               for f in manifest["issues"])
    assert len(rows["recordings_s3"]) == 1 and list(audio.values()) == [b"NOTE"]


async def test_hold_is_disclosed_without_changing_it_or_claiming_erase_readiness(harness):
    mod, service, conn, *_ = harness
    conn.hold = True
    result = await service.preview(scope(mod), actor_id=ACTOR)
    assert result["legal_hold"] is True
    assert result["destructive_apply_supported"] is False
    assert conn.hold is True


async def test_unknown_or_permission_error_is_not_misreported_as_absent_table(harness):
    mod, service, conn, *_ = harness
    conn.fail_table = "transcripts"
    with pytest.raises(RuntimeError, match="synthetic permission"):
        await service.preview(scope(mod), actor_id=ACTOR)
    assert conn.events == []


async def test_legacy_drive_reference_is_preserved_and_marked_unsupported(harness, tmp_path):
    mod, service, conn, _, artifacts, _ = harness
    conn.rows["recordings"].append({"id": uid(88), "tenant_id": TENANT, "call_id": uid(11),
                                    "drive_file_id": "synthetic-drive-id", "storage_path": None})
    result = await service.export(scope(mod), actor_id=ACTOR, output=tmp_path / "legacy.zip")
    manifest, rows, _ = read_rows(result.path)
    assert manifest["complete"] is False and rows["recordings"][0]["drive_file_id"] == "synthetic-drive-id"
    assert any(i["code"] == "unsupported_storage" and i["resource_id"] == uid(88) for i in manifest["issues"])
    assert all(kind != "recordings" for kind, _ in artifacts.reads)


async def test_whole_tenant_export_includes_both_people_but_declares_unimplemented_full_tenant_scope(harness, tmp_path):
    mod, service, conn, *_ = harness
    result = await service.export(scope(mod, whole_tenant=True), actor_id=ACTOR, output=tmp_path / "tenant.zip")
    manifest, rows, _ = read_rows(result.path)
    assert {r["id"] for r in rows["leads"]} == {uid(1), uid(2)}
    assert {r["id"] for r in rows["calls"]} == {uid(11), uid(12)}
    assert manifest["complete"] is False
    assert any(i["code"] == "tenant_scope_not_complete" for i in manifest["issues"])
    assert all(r.get("tenant_id", TENANT) != OTHER for values in rows.values() for r in values)


async def test_revoked_admin_cannot_read_subject_data_or_files(harness, tmp_path):
    mod, service, conn, _, artifacts, _ = harness
    conn.admin = False
    with pytest.raises(HTTPException) as caught:
        await service.export(scope(mod), actor_id=ACTOR, output=tmp_path / "denied.zip")
    assert caught.value.status_code == 403
    assert conn.reads == [] and artifacts.reads == [] and not (tmp_path / "denied.zip").exists()


async def test_audit_failure_never_leaves_an_unaudited_export(harness, tmp_path):
    mod, service, _, audit, _, pool = harness
    audit.fail = True
    with pytest.raises(RuntimeError, match="durable audit"):
        await service.export(scope(mod), actor_id=ACTOR, output=tmp_path / "unaudited.zip")
    assert not (tmp_path / "unaudited.zip").exists() and pool.active == 0


async def test_export_refuses_to_overwrite_an_existing_operator_file(harness, tmp_path):
    mod, service, *_ = harness
    target = tmp_path / "existing.zip"
    target.write_bytes(b"operator artifact")
    with pytest.raises(FileExistsError):
        await service.export(scope(mod), actor_id=ACTOR, output=target)
    assert target.read_bytes() == b"operator artifact"


@pytest.mark.parametrize("values", [
    {}, {"phone": PHONE, "email": EMAIL}, {"whole_tenant": True, "phone": PHONE},
    {"phone": "unknown"}, {"email": "not-an-email"},
])
def test_subject_scope_requires_exactly_one_valid_selector(values):
    with pytest.raises(ValueError):
        module().DsarScope(tenant_id=TENANT, **values)


async def test_real_local_reader_rejects_outside_root_without_reading_it(tmp_path, monkeypatch):
    mod = module()
    root = tmp_path / "recordings"
    root.mkdir()
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"private other file")
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(root))
    reader = mod.DsarArtifactReader()
    output = io.BytesIO()
    with pytest.raises(Exception):
        await reader.copy_into("recordings_s3", {"s3_bucket": "local", "s3_key": str(outside)},
                               output, max_bytes=1024)
    assert output.getvalue() == b""


async def test_real_local_reader_streams_only_owned_path_and_enforces_cap(tmp_path, monkeypatch):
    mod = module()
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path))
    path = tmp_path / "call.wav"
    path.write_bytes(b"RIFFaudio")
    reader = mod.DsarArtifactReader()
    output = io.BytesIO()
    assert await reader.copy_into("recordings_s3", {"s3_bucket": "local", "s3_key": str(path)},
                                  output, max_bytes=1024) == 9
    assert output.getvalue() == b"RIFFaudio"
    with pytest.raises(ValueError, match="limit|large|size"):
        await reader.copy_into("recordings_s3", {"s3_bucket": "local", "s3_key": str(path)},
                               io.BytesIO(), max_bytes=4)


async def test_row_cap_is_reported_with_exact_count_and_never_claims_complete_export(harness, tmp_path):
    mod, _, conn, audit, artifacts, pool = harness
    service = mod.DsarService(pool, audit=audit, artifacts=artifacts, max_rows=1)
    result = await service.export(scope(mod, whole_tenant=True), actor_id=ACTOR, output=tmp_path / "bounded.zip")
    manifest, rows, _ = read_rows(result.path)
    assert manifest["counts"]["leads"] == 2 and len(rows["leads"]) == 1
    assert manifest["complete"] is False
    assert any(i["code"] == "row_limit" and i["table"] == "leads" for i in manifest["issues"])
    assert conn.events


async def test_s3_reader_uses_bucket_key_and_closes_bounded_stream_without_network():
    mod = module()

    class Body(io.BytesIO):
        requested = []

        def read(self, size=-1):
            assert 0 < size <= 65536, "No unbounded object read"
            self.requested.append(size)
            return super().read(size)

    body = Body(b"RIFFaudio")

    class Client:
        def get_object(self, **kwargs):
            assert kwargs == {"Bucket": "qa-bucket", "Key": "tenant/call.wav"}
            return {"Body": body, "ContentLength": 9}

    class S3:
        _client = Client()

        def is_available(self):
            return True

    reader = mod.DsarArtifactReader(s3_client=S3())
    target = io.BytesIO()
    assert await reader.copy_into("recordings_s3", {"s3_bucket": "qa-bucket", "s3_key": "tenant/call.wav"},
                                  target, max_bytes=32) == 9
    assert target.getvalue() == b"RIFFaudio" and body.closed and body.requested

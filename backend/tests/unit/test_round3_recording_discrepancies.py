"""F14-04 discrepancy reporting is local, bounded and strictly read-only."""
from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import pytest

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND / "scripts" / "report_recording_discrepancies.py"
TENANT = UUID("00000000-0000-0000-0000-000000000091")
OTHER = UUID("00000000-0000-0000-0000-000000000092")


@pytest.fixture
def script():
    assert SCRIPT.exists(), "F14-04 is missing the read-only recording discrepancy report"
    spec = importlib.util.spec_from_file_location("round3_recording_discrepancies", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(path, number=1, *, tenant=TENANT, bucket="local", status="uploaded"):
    return {"id": UUID(int=number), "tenant_id": tenant, "call_id": UUID(int=number + 100),
            "s3_key": str(path), "s3_bucket": bucket, "status": status}


class Conn:
    def __init__(self, rows, legacy_rows=()):
        self.rows = rows
        self.legacy_rows = legacy_rows
        self.queries = []

    async def fetchval(self, query, *args):
        assert " ".join(query.split()) == "SELECT to_regclass($1)"
        assert args == ("public.recordings",)
        return "recordings"

    @asynccontextmanager
    async def transaction(self, **kwargs):
        assert kwargs.get("readonly") is True
        yield

    async def execute(self, query, *args):
        flat = " ".join(query.split())
        assert flat.startswith(("SELECT set_config(", "SET LOCAL ")), "Discrepancy reporting tried to mutate data"
        self.queries.append((flat, args))
        return "SELECT 1"

    async def fetch(self, query, *args):
        flat = " ".join(query.split())
        self.queries.append((flat, args))
        if "FROM tenants" in flat:
            cursor, limit = args
            return [{"id": value} for value in (TENANT, OTHER) if cursor is None or value > cursor][:limit]
        if "FROM recordings r" in flat:
            assert "JOIN calls c" in flat and "c.tenant_id = $1" in flat
            assert "(r.tenant_id = $1::uuid OR r.tenant_id IS NULL) AS ownership_consistent" in flat
            tenant, cursor, limit = args
            return [{**value, "ownership_consistent": value["tenant_id"] in (None, tenant)}
                    for value in self.legacy_rows if value["owner_tenant_id"] == tenant
                    and (cursor is None or value["id"] > cursor)][:limit]
        assert "FROM recordings_s3" in flat and "tenant_id = $1" in flat
        assert "s3_bucket = 'local'" in flat, "Cloud objects must not be probed as local files"
        tenant, cursor, limit = args
        return [dict(value) for value in self.rows if value["tenant_id"] == tenant and value["s3_bucket"] == "local"
                and (cursor is None or value["id"] > cursor)][:limit]


@pytest.mark.asyncio
async def test_report_detects_both_directions_without_reading_audio_or_deleting(script, tmp_path, monkeypatch):
    root = tmp_path / "recordings"
    root.mkdir()
    present = root / "owned.wav"
    present.write_bytes(b"private-audio")
    orphan = root / "orphan.mp3"
    orphan.write_bytes(b"more-private-audio")
    missing = root / "missing.wav"
    conn = Conn([row(present), row(missing, 2)])
    monkeypatch.setattr(Path, "open", lambda *a, **k: pytest.fail("Report opened audio contents"))
    result = await script.report_discrepancies(conn, root, page_size=1)
    assert result["complete"] and result["read_only"]
    assert result["counts"]["files_without_rows"] == 1
    assert result["counts"]["rows_without_files"] == 1
    assert result["files_without_rows"] == ["orphan.mp3"]
    assert result["rows_without_files"][0]["recording_id"] == str(UUID(int=2))
    assert present.exists() and orphan.exists() and not missing.exists()


@pytest.mark.asyncio
async def test_all_tenants_are_read_with_explicit_predicates_before_orphan_classification(script, tmp_path):
    root = tmp_path / "recordings"
    root.mkdir()
    owned = root / "tenant-two.wav"
    owned.write_bytes(b"owned")
    conn = Conn([row(owned, tenant=OTHER)])
    result = await script.report_discrepancies(conn, root)
    assert result["counts"]["files_without_rows"] == 0
    queried = {args[0] for sql, args in conn.queries if "FROM recordings_s3" in sql}
    assert queried == {TENANT, OTHER}


@pytest.mark.asyncio
async def test_cloud_rows_are_excluded_without_any_provider_request(script, tmp_path):
    result = await script.report_discrepancies(Conn([row("tenant/cloud.wav", bucket="private-s3")]), tmp_path)
    assert result["rows_without_files"] == [] and result["complete"]
    assert result["scope"] == "local_recordings_s3_and_legacy"


@pytest.mark.asyncio
async def test_pending_or_failed_upload_is_not_reported_as_missing_playable_file(script, tmp_path):
    result = await script.report_discrepancies(Conn([row(tmp_path / "not-written.wav", status="failed")]), tmp_path)
    assert result["rows_without_files"] == []
    assert result["counts"]["nonuploaded_rows"] == 1


@pytest.mark.asyncio
async def test_existing_file_owned_by_failed_row_is_not_an_orphan(script, tmp_path):
    path = tmp_path / "partial.wav"
    path.write_bytes(b"partial")
    result = await script.report_discrepancies(Conn([row(path, status="failed")]), tmp_path)
    assert result["files_without_rows"] == []
    assert path.read_bytes() == b"partial"


@pytest.mark.asyncio
async def test_missing_root_is_incomplete_not_a_mass_missing_file_claim(script, tmp_path):
    root = tmp_path / "absent"
    result = await script.report_discrepancies(Conn([row(root / "a.wav")]), root)
    assert not result["complete"] and "recording_root_unavailable" in result["issues"]
    assert result["rows_without_files"] == [] and not root.exists()


@pytest.mark.asyncio
async def test_outside_path_is_reported_invalid_without_probing_it(script, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "private.wav"
    outside.write_bytes(b"must-stay-private")
    result = await script.report_discrepancies(Conn([row(outside)]), root)
    assert not result["complete"] and result["counts"]["invalid_paths"] == 1
    assert result["rows_without_files"] == []
    assert "private.wav" not in str(result)
    assert outside.read_bytes() == b"must-stay-private"


@pytest.mark.asyncio
async def test_symlink_directory_is_not_followed_or_treated_as_owned_audio(script, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    audio = outside / "hidden.wav"
    audio.write_bytes(b"keep")
    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This host does not permit creating directory symlinks")
    result = await script.report_discrepancies(Conn([row(link / "hidden.wav")]), root)
    assert not result["complete"] and result["counts"]["invalid_paths"] == 1
    assert "link/hidden.wav" not in result["files_without_rows"]
    assert audio.read_bytes() == b"keep"


@pytest.mark.asyncio
async def test_unreadable_directory_is_reported_incomplete(script, tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise PermissionError("sensitive-local-path")
    monkeypatch.setattr(script.os, "scandir", unavailable)
    result = await script.report_discrepancies(Conn([]), tmp_path)
    assert not result["complete"] and "directory_scan_failed:PermissionError" in result["issues"]
    assert "sensitive-local-path" not in str(result)


@pytest.mark.asyncio
async def test_query_failure_is_not_converted_to_an_orphan_report(script, tmp_path):
    conn = Conn([])
    async def fail(query, *args):
        raise RuntimeError("database unavailable")
    conn.fetch = fail
    with pytest.raises(RuntimeError, match="database unavailable"):
        await script.report_discrepancies(conn, tmp_path)


@pytest.mark.asyncio
async def test_file_limit_marks_report_incomplete_and_never_deletes(script, tmp_path):
    for number in range(3):
        (tmp_path / f"{number}.wav").write_bytes(b"keep")
    result = await script.report_discrepancies(Conn([]), tmp_path, max_files=2)
    assert not result["complete"] and "file_limit_reached" in result["issues"]
    assert len(list(tmp_path.iterdir())) == 3


def test_cli_has_no_apply_or_delete_option(script):
    assert script.build_parser().parse_args([]).root is None
    for option in ("--apply", "--delete", "--repair"):
        with pytest.raises(SystemExit):
            script.build_parser().parse_args([option])


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_tenant", [TENANT, None])
async def test_legacy_recording_row_owns_file_even_without_recordings_s3_row(script, tmp_path, legacy_tenant):
    path = tmp_path / "legacy.wav"
    path.write_bytes(b"owned-legacy-audio")
    legacy = {"id": UUID(int=10), "tenant_id": legacy_tenant, "owner_tenant_id": TENANT,
              "call_id": UUID(int=110), "storage_path": str(path), "drive_file_id": None,
              "status": "uploaded"}
    result = await script.report_discrepancies(Conn([], [legacy]), tmp_path)
    assert result["files_without_rows"] == [], "A legacy recordings row already owns this file"
    assert result["counts"]["legacy_local_rows"] == 1
    assert path.read_bytes() == b"owned-legacy-audio"


@pytest.mark.asyncio
async def test_legacy_local_row_without_file_is_reported_with_source_table(script, tmp_path):
    legacy = {"id": UUID(int=11), "tenant_id": TENANT, "owner_tenant_id": TENANT,
              "call_id": UUID(int=111), "storage_path": str(tmp_path / "missing-legacy.wav"),
              "drive_file_id": None, "status": "uploaded"}
    result = await script.report_discrepancies(Conn([], [legacy]), tmp_path)
    assert result["counts"]["rows_without_files"] == 1
    assert result["rows_without_files"][0]["table"] == "recordings"


@pytest.mark.asyncio
async def test_legacy_query_failure_cannot_publish_a_false_orphan_list(script, tmp_path):
    (tmp_path / "legacy.wav").write_bytes(b"keep")
    conn = Conn([])
    original = conn.fetch
    async def fetch(query, *args):
        if "FROM recordings r" in query:
            raise PermissionError("legacy ownership unavailable")
        return await original(query, *args)
    conn.fetch = fetch
    with pytest.raises(PermissionError, match="legacy ownership unavailable"):
        await script.report_discrepancies(conn, tmp_path)


@pytest.mark.asyncio
async def test_conflicting_legacy_tenant_ownership_withholds_orphan_classification(script, tmp_path):
    path = tmp_path / "conflicting-owner.wav"
    path.write_bytes(b"still-has-a-row")
    legacy = {"id": UUID(int=12), "tenant_id": OTHER, "owner_tenant_id": TENANT,
              "call_id": UUID(int=112), "storage_path": str(path), "drive_file_id": None,
              "status": "uploaded"}
    result = await script.report_discrepancies(Conn([], [legacy]), tmp_path)
    assert not result["complete"], "Conflicting legacy ownership cannot certify a complete inventory"
    assert result["files_without_rows"] == [], "An excluded corrupt row must not turn its file into a purported orphan"
    assert "legacy_ownership_conflict" in result["issues"]


def test_nightly_unit_runs_read_only_report_and_installer_enables_timer():
    service = BACKEND / "systemd" / "talky-recording-discrepancy.service"
    timer = BACKEND / "systemd" / "talky-recording-discrepancy.timer"
    assert service.exists() and timer.exists(), "F14-04 has no installed nightly discrepancy report"
    unit = service.read_text(encoding="utf-8")
    assert "Type=oneshot" in unit and "User=talky" in unit
    assert "ProtectSystem=strict" in unit and "NoNewPrivileges=yes" in unit
    assert "scripts/report_recording_discrepancies.py" in unit
    assert "ReadWritePaths=" not in unit and "--delete" not in unit
    timing = timer.read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 03:30:00" in timing and "Persistent=true" in timing
    installer = (BACKEND / "systemd" / "install-services.sh").read_text(encoding="utf-8")
    assert "systemctl enable talky-recording-discrepancy.timer --now" in installer

"""Actual recording storage entry points with synthetic DB/transport sinks."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.domain.services import recording_policy_service as policy
from app.domain.services import recording_service as recording

CALL = "10000000-0000-4000-8000-000000000007"
TENANT = "20000000-0000-4000-8000-000000000007"
CAMPAIGN = "30000000-0000-4000-8000-000000000007"
RECORDING = "40000000-0000-4000-8000-000000000007"


@pytest.fixture
def fixture(monkeypatch, tmp_path):
    conn = SimpleNamespace(fetchrow=AsyncMock(return_value={"id": UUID(CALL)}))

    @asynccontextmanager
    async def scoped(pool, tenant):
        assert tenant == TENANT
        yield conn

    monkeypatch.setattr(recording, "acquire_with_tenant", scoped)
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setattr(recording, "encode_recording_audio", lambda raw: (raw, ".wav", "audio/wav"))
    decision = policy.RecordingDecision(True, False, None, None, 90, "tenant_policy_one_party")
    decide = AsyncMock(return_value=decision)
    monkeypatch.setattr(policy.RecordingPolicyService, "decide", decide)
    s3 = SimpleNamespace(is_available=lambda: True, bucket="synthetic", upload=MagicMock())
    service = recording.RecordingService(object(), s3_client=s3)
    service._insert_recording_record = AsyncMock(return_value=UUID(RECORDING))
    service._update_call_recording_url = AsyncMock()
    service._mark_upload_failed = AsyncMock()
    legacy_upload = MagicMock()
    buffer = recording.RecordingBuffer(call_id=CALL)
    buffer._wav_bytes_override = b"synthetic-audio-only"
    buffer.total_bytes = 20
    policy.clear_disclosure_state(CALL)
    yield SimpleNamespace(
        service=service,
        conn=conn,
        decide=decide,
        s3=s3,
        legacy_upload=legacy_upload,
        buffer=buffer,
        directory=tmp_path,
    )
    policy.clear_disclosure_state(CALL)


async def invoke(fixture, entry, tenant=TENANT):
    service = fixture.service
    if entry == "local":
        return await service._save_local(CALL, fixture.buffer, tenant, CAMPAIGN)
    if entry == "configured-local":
        fixture.s3.is_available = lambda: False
    if entry == "legacy":
        service.supabase = SimpleNamespace(
            storage=SimpleNamespace(from_=lambda _: SimpleNamespace(upload=fixture.legacy_upload))
        )
        return await service.save_recording(CALL, fixture.buffer, tenant, CAMPAIGN)
    return await service.save_and_link(CALL, fixture.buffer, tenant, CAMPAIGN)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["cloud", "local", "configured-local", "legacy"])
@pytest.mark.parametrize(
    "failure",
    [
        "disabled",
        "disclosure-failed",
        "policy-unavailable",
        "foreign-call",
        "binding-unavailable",
        "unknown-tenant",
    ],
)
async def test_no_storage_entry_retains_unapproved_or_unbound_audio(fixture, entry, failure):
    tenant = TENANT
    if failure == "disabled":
        fixture.decide.return_value = policy.RecordingDecision(
            False, False, None, None, 90, "tenant_policy_disabled"
        )
        policy.record_disclosure_state(policy.DISCLOSURE_NOT_REQUIRED, CALL)
    elif failure == "disclosure-failed":
        fixture.decide.return_value = policy.RecordingDecision(
            True, True, "notice", None, 90, "two_party"
        )
        policy.record_disclosure_state(policy.DISCLOSURE_FAILED, CALL)
    elif failure == "policy-unavailable":
        fixture.decide.side_effect = RuntimeError("synthetic policy read failed")
    elif failure == "foreign-call":
        fixture.conn.fetchrow.return_value = None
    elif failure == "binding-unavailable":
        fixture.conn.fetchrow.side_effect = RuntimeError("synthetic binding read failed")
    else:
        tenant = "unknown"
    result = await invoke(fixture, entry, tenant)
    assert result is None
    assert not list(fixture.directory.rglob("*.wav"))
    fixture.s3.upload.assert_not_called()
    fixture.legacy_upload.assert_not_called()
    fixture.service._insert_recording_record.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["cloud", "local", "configured-local", "legacy"])
async def test_explicit_policy_and_owned_call_allow_existing_storage(fixture, entry):
    result = await invoke(fixture, entry)
    assert result
    assert fixture.conn.fetchrow.await_count >= 1
    assert fixture.decide.await_count >= 1
    if entry in {"local", "configured-local"}:
        assert len(list(fixture.directory.rglob("*.wav"))) == 1
    elif entry == "cloud":
        fixture.s3.upload.assert_called_once()
    else:
        fixture.legacy_upload.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["cloud", "local"])
@pytest.mark.parametrize("state", ["spoken", "transmitted"])
async def test_required_notice_supported_transport_evidence_allows_storage(fixture, entry, state):
    fixture.decide.return_value = policy.RecordingDecision(
        True, True, "notice", None, 90, "configured_notice"
    )
    policy.record_disclosure_state(state, CALL)
    assert await invoke(fixture, entry)
    fixture.service._insert_recording_record.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["cloud", "local"])
async def test_policy_revocation_during_encoding_prevents_storage(fixture, entry, monkeypatch):
    def encode(raw):
        fixture.decide.return_value = policy.RecordingDecision(
            False, False, None, None, 90, "tenant_policy_disabled"
        )
        return raw, ".wav", "audio/wav"

    monkeypatch.setattr(recording, "encode_recording_audio", encode)
    assert await invoke(fixture, entry) is None
    assert not list(fixture.directory.rglob("*.wav"))
    fixture.s3.upload.assert_not_called()
    fixture.service._insert_recording_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_teardown_database_failure_never_creates_disk_only_orphan(fixture, monkeypatch):
    from app.domain.services.telephony.recording import _save_call_recording

    @asynccontextmanager
    async def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic database unavailable")
        yield  # pragma: no cover

    pool = SimpleNamespace(acquire=unavailable)
    container = SimpleNamespace(
        is_initialized=True, db_pool=pool, db_client=SimpleNamespace(pool=pool)
    )
    monkeypatch.setattr("app.core.container.get_container", lambda: container)
    monkeypatch.setattr("app.core.db.get_db", unavailable)
    monkeypatch.setattr(recording, "RecordingService", lambda _: fixture.service)
    gateway = SimpleNamespace(
        get_recording_buffer=lambda _: [b"\x01\x00" * 9600],
        get_tts_recording_buffer=lambda _: [],
        clear_recording_buffer=MagicMock(),
        _sample_rate=16000,
    )
    voice = SimpleNamespace(
        call_id=CALL,
        media_gateway=gateway,
        config=SimpleNamespace(tenant_id=TENANT, campaign_id=CAMPAIGN),
    )
    policy.record_disclosure_state(policy.DISCLOSURE_NOT_REQUIRED, CALL)
    fixture.decide.return_value = policy.RecordingDecision(False, False, None, None, 90, "disabled")
    await _save_call_recording(voice, "synthetic-pbx")
    assert not list(fixture.directory.rglob("*.wav"))
    fixture.s3.upload.assert_not_called()
    fixture.service._insert_recording_record.assert_not_awaited()
    gateway.clear_recording_buffer.assert_called_once_with(CALL)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["conflicting-config", "foreign-campaign", "same-owner"])
async def test_teardown_stub_preserves_authoritative_tenant(fixture, monkeypatch, case):
    from app.domain.services.telephony.recording import _save_call_recording

    other_tenant = "20000000-0000-4000-8000-000000000008"
    conn = SimpleNamespace(fetchrow=AsyncMock(), execute=AsyncMock(return_value="INSERT 0 1"))

    async def fetch(query, *args):
        if "FROM campaigns" in query:
            return None if case == "foreign-campaign" else {"id": UUID(CAMPAIGN)}
        return None  # No previous calls row, exercising actual standalone stub path.

    conn.fetchrow.side_effect = fetch

    @asynccontextmanager
    async def acquire(*args, **kwargs):
        yield conn

    conn.transaction = acquire
    pool = SimpleNamespace(acquire=acquire)
    monkeypatch.setattr("app.core.db.get_db", acquire)
    monkeypatch.setattr(
        "app.core.container.get_container",
        lambda: SimpleNamespace(
            is_initialized=True, db_pool=pool, db_client=SimpleNamespace(pool=pool)
        ),
    )
    monkeypatch.setattr(recording, "RecordingService", lambda _: fixture.service)
    gateway = SimpleNamespace(
        get_recording_buffer=lambda _: [b"\x01\x00" * 9600],
        get_tts_recording_buffer=lambda _: [],
        clear_recording_buffer=MagicMock(),
        _sample_rate=16000,
    )
    voice = SimpleNamespace(
        call_id=CALL,
        media_gateway=gateway,
        _dialer_tenant_id=TENANT,
        config=SimpleNamespace(
            tenant_id=other_tenant if case == "conflicting-config" else TENANT, campaign_id=CAMPAIGN
        ),
    )
    await _save_call_recording(voice, "synthetic-pbx")
    inserts = [call for call in conn.execute.await_args_list if "INSERT INTO calls" in call.args[0]]
    if case == "same-owner":
        assert len(inserts) == 1
        assert inserts[0].args[2] == UUID(TENANT)
        fixture.s3.upload.assert_called_once()
    else:
        assert inserts == []
        fixture.s3.upload.assert_not_called()
        fixture.service._insert_recording_record.assert_not_awaited()
    gateway.clear_recording_buffer.assert_called_once_with(CALL)

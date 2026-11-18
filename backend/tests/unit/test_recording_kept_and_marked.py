"""Owner decision, 18 November 2026: a captured recording is kept and marked.

* A captured outbound recording is never discarded because the notice receipt
  is missing: it is kept and the row says ``unconfirmed``.
* No saved policy records, with the spoken notice.
* Refused only by: a saved ``disabled`` policy, ``strict_notice`` (inbound),
  or the platform switch ``RECORDING_REQUIRE_NOTICE_PROOF``.
* When the decision cannot be taken (policy unreadable, call row missing,
  storage or insert failed) the answer is ``retry`` and nothing is destroyed.

The pool, policy store and storage are synthetic; the real INSERT is run and
its arguments are inspected.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from app.domain.services import recording_policy_service as policy
from app.domain.services import recording_service as recording

CALL = "10000000-0000-4000-8000-00000000a103"
TENANT = "20000000-0000-4000-8000-00000000a103"
CAMPAIGN = "30000000-0000-4000-8000-00000000a103"
RECORDING = UUID("40000000-0000-4000-8000-00000000a103")
EARLIER = UUID("50000000-0000-4000-8000-00000000a103")

NOTICE_OWED = policy.RecordingDecision(True, True, "notice", None, 90, "tenant_policy_two_party_announce")
NO_NOTICE = policy.RecordingDecision(True, False, None, None, 90, "tenant_policy_one_party")
DISABLED = policy.RecordingDecision(False, False, None, None, 90, "tenant_policy_disabled")


class Conn:
    def __init__(self):
        self.call_row = {"id": UUID(CALL)}
        self.saved_id = None
        self.inserts = []
        self.insert_error = None

    @asynccontextmanager
    async def transaction(self):
        yield self

    async def execute(self, sql, *args):
        return "OK"

    async def fetchval(self, sql, *args):
        query = " ".join(sql.split())
        assert query == (
            "SELECT id FROM recordings_s3 WHERE tenant_id = $1 AND call_id = $2 "
            "AND status = 'uploaded' ORDER BY created_at DESC LIMIT 1"
        )
        assert args == (UUID(TENANT), UUID(CALL))
        return self.saved_id

    async def fetchrow(self, sql, *args):
        query = " ".join(sql.split())
        if query.startswith("SELECT id FROM calls WHERE tenant_id = $1 "):
            return self.call_row
        if query.startswith("INSERT INTO recordings_s3 (") and query.endswith("RETURNING id"):
            if self.insert_error is not None:
                raise self.insert_error
            self.inserts.append(args)
            return {"id": RECORDING}
        raise AssertionError(f"Unexpected SQL: {query}")


@pytest.fixture
def fx(monkeypatch, tmp_path):
    conn = Conn()

    @asynccontextmanager
    async def scoped(pool, tenant, **kwargs):
        yield conn

    monkeypatch.setattr(recording, "acquire_with_tenant", scoped)
    monkeypatch.setattr(recording, "encode_recording_audio", lambda raw: (raw, ".wav", "audio/wav"))
    monkeypatch.setenv("LOCAL_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.delenv("RECORDING_REQUIRE_NOTICE_PROOF", raising=False)
    decide = AsyncMock(return_value=NOTICE_OWED)
    monkeypatch.setattr(policy.RecordingPolicyService, "decide", decide)
    s3 = SimpleNamespace(is_available=lambda: True, bucket="synthetic", region="synthetic", upload=MagicMock())
    service = recording.RecordingService(object(), s3_client=s3)
    service._update_call_recording_url = AsyncMock()
    service._mark_upload_failed = AsyncMock()
    service._discard_unregistered_object = AsyncMock()
    buffer = recording.RecordingBuffer(call_id=CALL)
    buffer._wav_bytes_override = b"synthetic-audio-only"
    buffer.total_bytes = 20
    policy.clear_disclosure_state(CALL)
    yield SimpleNamespace(
        service=service, conn=conn, decide=decide, s3=s3, buffer=buffer, directory=tmp_path,
    )
    policy.clear_disclosure_state(CALL)


async def save(fx, *, local=False, **kwargs):
    if local:
        fx.s3.is_available = lambda: False
    return await fx.service.save_and_link_outcome(CALL, fx.buffer, TENANT, CAMPAIGN, **kwargs)


def stored(fx):
    return fx.s3.upload.call_count + len(list(fx.directory.rglob("*.wav")))


# ── decisions ────────────────────────────────────────────────────────────────

class _Rows:
    def __init__(self, row):
        self.row = row

    @asynccontextmanager
    async def acquire(self):
        row = self.row

        class C:
            @asynccontextmanager
            async def transaction(self):
                yield self

            async def execute(self, *a, **k):
                return None

            async def fetchrow(self, *a, **k):
                if row == "boom":
                    raise RuntimeError("policy store down")
                return row

        yield C()


@pytest.mark.asyncio
async def test_absent_policy_decision_records_with_the_notice():
    decision = await policy.RecordingPolicyService(_Rows(None)).decide(tenant_id=TENANT)
    assert decision.should_record is True and decision.announcement_required is True
    assert decision.reason == "tenant_policy_absent_default_announce"
    assert policy.spoken_disclosure_text(decision) == policy.MINIMUM_DISCLOSURE_TEXT


@pytest.mark.asyncio
async def test_lookup_failure_decision_carries_the_retry_reason():
    decision = await policy.RecordingPolicyService(_Rows("boom")).decide(tenant_id=TENANT)
    assert decision.reason == policy.POLICY_LOOKUP_FAILED_REASON == "recording_policy_lookup_failed"
    assert decision.announcement_required is True  # the live call still speaks the notice


def test_notice_state_values():
    assert policy.notice_state(NO_NOTICE, CALL) == policy.NOTICE_NOT_REQUIRED == "not_required"
    assert policy.notice_state(NOTICE_OWED, CALL) == policy.NOTICE_UNCONFIRMED == "unconfirmed"
    policy.record_disclosure_state(policy.DISCLOSURE_SPOKEN, CALL)
    try:
        assert policy.notice_state(NOTICE_OWED, CALL) == policy.NOTICE_DELIVERED == "delivered"
    finally:
        policy.clear_disclosure_state(CALL)


@pytest.mark.parametrize("value,expected", [
    (None, False), ("", False), ("0", False), ("false", False), ("1", True),
    ("true", True), ("YES", True), (" On ", True),
])
def test_notice_proof_switch_is_read_at_call_time(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("RECORDING_REQUIRE_NOTICE_PROOF", raising=False)
    else:
        monkeypatch.setenv("RECORDING_REQUIRE_NOTICE_PROOF", value)
    assert policy.notice_proof_required() is expected


# ── saves ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("local", [False, True], ids=["cloud", "local"])
async def test_outbound_notice_required_and_not_delivered_is_saved_unconfirmed(fx, local):
    outcome = await save(fx, local=local)
    assert (outcome.status, outcome.recording_id) == (recording.SAVE_SAVED, str(RECORDING))
    assert len(fx.conn.inserts) == 1 and stored(fx) == 1
    assert fx.conn.inserts[0][-2:] == ("unconfirmed", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [False, True], ids=["cloud", "local"])
async def test_delivered_notice_is_marked_delivered(fx, local):
    policy.record_disclosure_state(policy.DISCLOSURE_SPOKEN, CALL)
    outcome = await save(fx, local=local)
    assert outcome.status == recording.SAVE_SAVED
    assert fx.conn.inserts[0][-2] == "delivered"


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [False, True], ids=["cloud", "local"])
async def test_no_notice_needed_is_marked_not_required(fx, local):
    fx.decide.return_value = NO_NOTICE
    outcome = await save(fx, local=local)
    assert outcome.status == recording.SAVE_SAVED
    assert fx.conn.inserts[0][-2] == "not_required"


@pytest.mark.asyncio
async def test_transfer_part_reaches_the_insert(fx):
    outcome = await save(fx, transfer_part="missing")
    assert outcome.status == recording.SAVE_SAVED
    assert fx.conn.inserts[0][-1] == "missing"


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [False, True], ids=["cloud", "local"])
async def test_disabled_policy_is_refused_and_nothing_is_stored(fx, local):
    fx.decide.return_value = DISABLED
    outcome = await save(fx, local=local)
    assert (outcome.status, outcome.reason) == (recording.SAVE_REFUSED, "tenant_policy_disabled")
    assert stored(fx) == 0 and fx.conn.inserts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [False, True], ids=["cloud", "local"])
async def test_strict_notice_without_delivery_is_refused(fx, local):
    outcome = await save(fx, local=local, strict_notice=True)
    assert (outcome.status, outcome.reason) == (recording.SAVE_REFUSED, "notice_unconfirmed")
    assert stored(fx) == 0 and fx.conn.inserts == []


@pytest.mark.asyncio
async def test_strict_notice_with_delivery_is_saved(fx):
    policy.record_disclosure_state(policy.DISCLOSURE_TRANSMITTED, CALL)
    outcome = await save(fx, strict_notice=True)
    assert outcome.status == recording.SAVE_SAVED
    assert fx.conn.inserts[0][-2] == "delivered"


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [False, True], ids=["cloud", "local"])
async def test_platform_switch_brings_back_the_proof_rule(fx, monkeypatch, local):
    monkeypatch.setenv("RECORDING_REQUIRE_NOTICE_PROOF", "true")
    outcome = await save(fx, local=local)
    assert (outcome.status, outcome.reason) == (recording.SAVE_REFUSED, "notice_unconfirmed")
    assert stored(fx) == 0 and fx.conn.inserts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [False, True], ids=["cloud", "local"])
async def test_policy_lookup_failure_at_save_is_retry_and_nothing_is_stored(fx, local):
    fx.decide.return_value = policy._POLICY_LOOKUP_FAILURE_DEFAULT
    outcome = await save(fx, local=local)
    assert (outcome.status, outcome.reason) == (recording.SAVE_RETRY, "policy_unavailable")
    assert stored(fx) == 0 and fx.conn.inserts == []


@pytest.mark.asyncio
async def test_policy_exception_at_save_is_retry(fx):
    fx.decide.side_effect = RuntimeError("synthetic")
    outcome = await save(fx)
    assert (outcome.status, outcome.reason) == (recording.SAVE_RETRY, "admission_unavailable")
    assert stored(fx) == 0


@pytest.mark.asyncio
async def test_call_row_not_found_is_retry(fx):
    fx.conn.call_row = None
    outcome = await save(fx)
    assert (outcome.status, outcome.reason) == (recording.SAVE_RETRY, "call_binding_unavailable")
    assert stored(fx) == 0


@pytest.mark.asyncio
async def test_empty_buffer_is_refused_without_a_lookup(fx):
    fx.buffer.total_bytes = 0
    outcome = await save(fx)
    assert (outcome.status, outcome.reason) == (recording.SAVE_REFUSED, "no_audio")
    fx.decide.assert_not_awaited()


@pytest.mark.asyncio
async def test_cloud_storage_failure_is_retry(fx):
    fx.s3.upload.side_effect = OSError("synthetic storage down")
    outcome = await save(fx)
    assert (outcome.status, outcome.reason) == (recording.SAVE_RETRY, "storage_failed")
    fx.service._mark_upload_failed.assert_awaited_once()
    assert fx.conn.inserts == []


@pytest.mark.asyncio
async def test_local_write_failure_is_retry(fx, monkeypatch):
    def full_disk(*args):
        raise OSError("synthetic full disk")

    monkeypatch.setattr(recording, "_write_wav_file", full_disk)
    outcome = await save(fx, local=True)
    assert (outcome.status, outcome.reason) == (recording.SAVE_RETRY, "storage_failed")
    fx.service._mark_upload_failed.assert_awaited_once()
    assert fx.conn.inserts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("local", [False, True], ids=["cloud", "local"])
async def test_insert_failure_is_retry_and_cleans_the_unregistered_object(fx, local):
    fx.conn.insert_error = RuntimeError("synthetic insert failure")
    outcome = await save(fx, local=local)
    assert (outcome.status, outcome.reason) == (recording.SAVE_RETRY, "register_failed")
    fx.service._discard_unregistered_object.assert_awaited_once()


@pytest.mark.asyncio
async def test_second_save_of_an_already_saved_recording_writes_nothing(fx):
    fx.conn.saved_id = EARLIER
    outcome = await save(fx)
    assert (outcome.status, outcome.recording_id, outcome.reason) == (
        recording.SAVE_SAVED, str(EARLIER), "already_saved",
    )
    assert stored(fx) == 0 and fx.conn.inserts == []
    fx.decide.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_and_link_wrapper_returns_the_id_or_none(fx):
    assert await fx.service.save_and_link(CALL, fx.buffer, TENANT, CAMPAIGN) == str(RECORDING)
    fx.decide.return_value = DISABLED
    assert await fx.service.save_and_link(CALL, fx.buffer, TENANT, CAMPAIGN) is None

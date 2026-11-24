"""A database error the old adapter RETURNS is not "no such call".

The adapter hands most failures back as ``data=None, error=<text>`` instead of
raising.  ``mark_goal_achieved`` / ``mark_as_spam`` used to read that as "no row"
(a final 404, and in one path a 200 "Marked as spam" with nothing suppressed).
A failed read or write must raise; a genuine zero-row result stays None (404).
"""
from types import SimpleNamespace

import pytest

from tests.unit.test_webhooks_call_idor import (
    TENANT_A,
    TENANT_B,
    _FakeClient,
    _FakeQuery,
    _run,
)
from app.domain.services.call_service import CallService

CALL_ID = "11111111-1111-1111-1111-111111111111"
LEAD_ID = "22222222-2222-2222-2222-222222222222"
JOB_ID = "33333333-3333-3333-3333-333333333333"
DRIVER_TEXT = "SECRET-DRIVER-TEXT"


def _seed():
    return {
        "calls": [
            {"id": CALL_ID, "tenant_id": TENANT_B, "lead_id": LEAD_ID,
             "dialer_job_id": JOB_ID, "outcome": "in_progress", "goal_achieved": False},
        ],
        "leads": [
            {"id": LEAD_ID, "tenant_id": TENANT_B, "status": "calling",
             "phone_number": "+15550001111"},
        ],
        "dialer_jobs": [
            {"id": JOB_ID, "tenant_id": TENANT_B, "status": "processing"},
        ],
    }


class _FailingQuery(_FakeQuery):
    def __init__(self, store, table, failing):
        super().__init__(store, table)
        self._failing = failing

    def execute(self):
        if (self._table, self._op) in self._failing:
            return SimpleNamespace(data=None, error=DRIVER_TEXT)
        return super().execute()


class _FailingClient(_FakeClient):
    def __init__(self, store, failing):
        super().__init__(store)
        self.failing = set(failing)

    def table(self, name):
        return _FailingQuery(self._store, name, self.failing)


def _service(store, failing):
    client = _FailingClient(store, failing)
    return CallService(db_client=client), client


def _assert_unchanged(store):
    assert store == _seed()


# ---- goal achieved --------------------------------------------------------
def test_goal_calls_update_error_raises():
    store = _seed()
    svc, client = _service(store, {("calls", "update")})
    with pytest.raises(RuntimeError) as ei:
        _run(svc.mark_goal_achieved(tenant_id=TENANT_B, call_id=CALL_ID))
    assert DRIVER_TEXT not in str(ei.value)
    _assert_unchanged(store)
    client.failing.clear()
    assert _run(svc.mark_goal_achieved(tenant_id=TENANT_B, call_id=CALL_ID))["call_id"] == CALL_ID


def test_goal_dialer_job_update_error_raises():
    store = _seed()
    svc, client = _service(store, {("dialer_jobs", "update")})
    with pytest.raises(RuntimeError) as ei:
        _run(svc.mark_goal_achieved(tenant_id=TENANT_B, call_id=CALL_ID))
    assert DRIVER_TEXT not in str(ei.value)
    assert store["dialer_jobs"][0]["status"] == "processing"
    client.failing.clear()
    assert _run(svc.mark_goal_achieved(tenant_id=TENANT_B, call_id=CALL_ID)) is not None
    assert store["dialer_jobs"][0]["status"] == "goal_achieved"


# ---- mark spam ------------------------------------------------------------
def test_spam_calls_select_error_raises():
    store = _seed()
    svc, client = _service(store, {("calls", "select")})
    with pytest.raises(RuntimeError) as ei:
        _run(svc.mark_as_spam(tenant_id=TENANT_B, call_id=CALL_ID))
    assert DRIVER_TEXT not in str(ei.value)
    _assert_unchanged(store)
    client.failing.clear()
    assert _run(svc.mark_as_spam(tenant_id=TENANT_B, call_id=CALL_ID))["message"] == "Marked as spam"


def test_spam_calls_update_error_raises():
    store = _seed()
    svc, client = _service(store, {("calls", "update")})
    with pytest.raises(RuntimeError) as ei:
        _run(svc.mark_as_spam(tenant_id=TENANT_B, call_id=CALL_ID))
    assert DRIVER_TEXT not in str(ei.value)
    _assert_unchanged(store)
    client.failing.clear()
    assert _run(svc.mark_as_spam(tenant_id=TENANT_B, call_id=CALL_ID)) is not None


def test_spam_leads_update_error_with_call_id_raises():
    store = _seed()
    svc, client = _service(store, {("leads", "update")})
    with pytest.raises(RuntimeError) as ei:
        _run(svc.mark_as_spam(tenant_id=TENANT_B, call_id=CALL_ID))
    assert DRIVER_TEXT not in str(ei.value)
    assert store["leads"][0]["status"] == "calling"
    assert "do_not_call" not in store["leads"][0]
    client.failing.clear()
    assert _run(svc.mark_as_spam(tenant_id=TENANT_B, call_id=CALL_ID)) is not None
    assert store["leads"][0]["status"] == "dnc"


def test_spam_lead_only_update_error_raises():
    store = _seed()
    svc, client = _service(store, {("leads", "update")})
    with pytest.raises(RuntimeError) as ei:
        _run(svc.mark_as_spam(tenant_id=TENANT_B, lead_id=LEAD_ID))
    assert DRIVER_TEXT not in str(ei.value)
    assert store["leads"][0]["status"] == "calling"
    client.failing.clear()
    assert _run(svc.mark_as_spam(tenant_id=TENANT_B, lead_id=LEAD_ID)) is not None


# ---- must not change: non-UUID id with an error stays a 404 (None) --------
def test_goal_non_uuid_call_id_with_error_is_still_none():
    svc, _ = _service(_seed(), {("calls", "update")})
    assert _run(svc.mark_goal_achieved(tenant_id=TENANT_A, call_id="ghost")) is None


def test_spam_non_uuid_call_id_with_error_is_still_none():
    svc, _ = _service(_seed(), {("calls", "select")})
    assert _run(svc.mark_as_spam(tenant_id=TENANT_A, call_id="ghost")) is None


def test_spam_non_uuid_lead_only_with_error_is_still_none():
    svc, _ = _service(_seed(), {("leads", "update")})
    assert _run(svc.mark_as_spam(tenant_id=TENANT_A, lead_id="ghost")) is None


# ---- must not change: zero rows is None -----------------------------------
def test_zero_rows_still_none_for_foreign_uuid():
    store = _seed()
    svc, _ = _service(store, set())
    assert _run(svc.mark_goal_achieved(tenant_id=TENANT_A, call_id=CALL_ID)) is None
    assert _run(svc.mark_as_spam(tenant_id=TENANT_A, call_id=CALL_ID)) is None
    assert _run(svc.mark_as_spam(tenant_id=TENANT_A, lead_id=LEAD_ID)) is None
    _assert_unchanged(store)


# --- review of 24 November: only what the DRIVER accepts counts as an id -----------
@pytest.mark.parametrize("shape", ["{%s}", "urn:uuid:%s", "uuid:%s"])
def test_an_id_the_driver_refuses_is_still_not_found(shape):
    from asyncpg.pgproto import pgproto

    call_id = shape % CALL_ID
    # Python's UUID() parses these; the driver's own parser does not, and its refusal comes
    # back as a returned error. Such an id can match no row: it stays None (404), and must
    # not become a 500 that a signed sender retries for ever.
    with pytest.raises(ValueError):
        pgproto.UUID(call_id)
    svc, _ = _service(_seed(), {("calls", "select"), ("calls", "update")})
    assert _run(svc.mark_as_spam(tenant_id=TENANT_B, call_id=call_id)) is None
    assert _run(svc.mark_goal_achieved(tenant_id=TENANT_B, call_id=call_id)) is None


@pytest.mark.parametrize("spelled", [
    CALL_ID, CALL_ID.upper(), CALL_ID.replace("-", ""),
    "1111111-11111-1111-1111-111111111111",    # a hyphen in an odd place: the driver still takes it
])
def test_an_id_the_driver_accepts_with_a_failed_read_is_still_an_error(spelled):
    from asyncpg.pgproto import pgproto

    pgproto.UUID(spelled)  # the driver takes it
    svc, _ = _service(_seed(), {("calls", "select"), ("calls", "update")})
    with pytest.raises(RuntimeError):
        _run(svc.mark_as_spam(tenant_id=TENANT_B, call_id=spelled))

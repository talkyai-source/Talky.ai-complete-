"""One spelling of "this tenant stopped paying", on both sides of the boundary.

WHY THIS EXISTS (2026-08-29)
----------------------------
``tenants.subscription_status`` was WRITTEN by ``billing_service`` with
Stripe's American spelling ``"canceled"`` (one L) and READ by ``call_guard``
with the British ``"cancelled"`` (two Ls):

    billing_service.py:397  {"subscription_status": "canceled"}
    billing_service.py:835  {"subscription_status": "canceled"}
    call_guard.py:468       if status in ("suspended", "cancelled")
    call_guard.py:521       if status in ("suspended", "cancelled")
    call_guard.py:616       if status in ("suspended", "cancelled", "past_due")

Nothing tied the two literals together, so a tenant whose subscription was
cancelled through Stripe passed every guard check and kept dialling
indefinitely — revenue leakage and a service-delivery failure at once.

Worse, two further writes passed Stripe's ``Subscription.status`` straight
through (``billing_service.py`` in ``cancel_subscription`` and
``_sync_subscription``), so the column could also receive ``unpaid`` and
``incomplete_expired`` — neither of which any read has ever blocked on.

The fix is the same shape ``call_outcomes.py`` uses for the outcome
vocabulary: ONE module spells the strings, both sides import it, and a drift
guard fails if a second copy appears.
"""
from __future__ import annotations

import ast
import re

import pytest

from tests.unit._source_scan import BACKEND, app_sources


# ── fakes ───────────────────────────────────────────────────────────────────

class _FakeRow(dict):
    """asyncpg Record-like: supports both ``row["k"]`` and ``row.get("k")``."""

    def get(self, key, default=None):
        return dict.get(self, key, default)


class _FakeConn:
    def __init__(self, row=None):
        self._row = row

    async def fetchrow(self, *a, **k):
        return self._row


class _FakeAcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        return None


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquireCtx(self._conn)


def _guard(status: str):
    from app.domain.services.call_guard import CallGuard

    return CallGuard(
        db_pool=_FakePool(_FakeConn(_FakeRow(id="t1", subscription_status=status))),
        redis_client=None,
    )


# ── the three guard sites block BOTH spellings ──────────────────────────────

# 'canceled' is what production rows already carry (every Stripe-driven
# cancellation to date wrote it); 'cancelled' is the canonical value new
# writes use. Both must block, or the fix leaves live rows unblocked.
_BLOCKING = ["canceled", "cancelled", "suspended"]
_ALLOWED = ["active", "trialing"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _BLOCKING)
async def test_tenant_active_blocks_every_stopped_status(status):
    result = await _guard(status)._check_tenant_active(tenant_id="t1")
    assert result.passed is False, f"{status!r} tenant was allowed to dial"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _ALLOWED)
async def test_tenant_active_allows_a_paying_tenant(status):
    assert (await _guard(status)._check_tenant_active(tenant_id="t1")).passed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _BLOCKING)
async def test_partner_active_blocks_every_stopped_status(status):
    result = await _guard(status)._check_partner_active(
        tenant_id="t1", partner_id="p1"
    )
    assert result.passed is False, f"{status!r} partner was allowed to dial"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _ALLOWED)
async def test_partner_active_allows_a_paying_partner(status):
    result = await _guard(status)._check_partner_active(
        tenant_id="t1", partner_id="p1"
    )
    assert result.passed is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", _BLOCKING + ["past_due", "unpaid", "incomplete_expired"]
)
async def test_subscription_check_blocks_every_non_paying_status(status):
    result = await _guard(status)._check_subscription_uncached(tenant_id="t1")
    assert result.passed is False, f"{status!r} subscription was allowed to dial"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", _ALLOWED)
async def test_subscription_check_allows_a_paying_tenant(status):
    result = await _guard(status)._check_subscription_uncached(tenant_id="t1")
    assert result.passed is True


@pytest.mark.asyncio
async def test_the_deny_reason_uses_the_canonical_spelling():
    """A legacy 'canceled' row must not produce a second reason string.

    Reasons land in guard telemetry and audit rows; two spellings there is the
    same divergence one layer down.
    """
    from app.domain.services.subscription_status import CANCELLED

    r = await _guard("canceled")._check_tenant_active(tenant_id="t1")
    assert r.reason == f"tenant_{CANCELLED}"

    r = await _guard("canceled")._check_subscription_uncached(tenant_id="t1")
    assert r.reason == f"subscription_{CANCELLED}"


@pytest.mark.asyncio
async def test_status_is_matched_case_and_whitespace_insensitively():
    """Stripe has never sent 'Canceled', but an admin UPDATE can."""
    r = await _guard("  Cancelled ")._check_subscription_uncached(tenant_id="t1")
    assert r.passed is False


# ── billing writes the canonical value ──────────────────────────────────────

class _Recorder:
    """Captures ``table(t).update(payload).eq(...).execute()`` chains."""

    def __init__(self):
        self.updates: list[tuple[str, dict]] = []
        self._select_row = {"stripe_subscription_id": "sub_123"}

    # -- entry point ------------------------------------------------------
    def table(self, name):
        return _RecorderTable(self, name)


class _RecorderTable:
    def __init__(self, rec: _Recorder, name: str):
        self._rec = rec
        self._name = name
        self._payload = None
        self._is_select = False

    def update(self, payload):
        self._payload = payload
        self._rec.updates.append((self._name, payload))
        return self

    def upsert(self, payload, **k):
        self._rec.updates.append((self._name, payload))
        return self

    def select(self, *a, **k):
        self._is_select = True
        return self

    def eq(self, *a, **k):
        return self

    def single(self):
        return self

    def limit(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        class _R:
            data = self._rec._select_row if self._is_select else None

        return _R()


def _billing(rec: _Recorder):
    from app.domain.services.billing_service import BillingService

    svc = BillingService(rec)
    svc.mock_mode = True
    return svc


def _tenant_status_writes(rec: _Recorder) -> list[str]:
    return [
        payload["subscription_status"]
        for table, payload in rec.updates
        if table == "tenants" and "subscription_status" in payload
    ]


@pytest.mark.asyncio
async def test_cancelling_a_subscription_writes_the_canonical_value():
    from app.domain.services.subscription_status import CANCELLED

    rec = _Recorder()
    await _billing(rec).cancel_subscription("t1")
    assert _tenant_status_writes(rec) == [CANCELLED]


@pytest.mark.asyncio
async def test_the_stripe_deleted_webhook_writes_the_canonical_value():
    """``customer.subscription.deleted`` is the path a real cancellation takes
    in production — the one that wrote the unblockable spelling."""
    from app.domain.services.subscription_status import CANCELLED

    rec = _Recorder()
    await _billing(rec)._handle_subscription_deleted(
        {"id": "sub_123", "metadata": {"tenant_id": "t1"}}
    )
    assert _tenant_status_writes(rec) == [CANCELLED]


@pytest.mark.asyncio
async def test_a_synced_stripe_status_is_normalised_before_it_is_stored():
    """``_sync_subscription`` passed Stripe's raw status straight through, so
    ``customer.subscription.updated`` could store 'canceled' even after the
    two explicit writes were fixed."""
    from app.domain.services.subscription_status import CANCELLED

    rec = _Recorder()
    await _billing(rec)._sync_subscription(
        {
            "id": "sub_123",
            "customer": "cus_1",
            "status": "canceled",
            "current_period_start": 1700000000,
            "current_period_end": 1700086400,
            "metadata": {"tenant_id": "t1"},
        }
    )
    assert _tenant_status_writes(rec) == [CANCELLED]


# ── the drift guard ─────────────────────────────────────────────────────────

# Modules allowed to spell a cancel literal in subscription-status context:
#   * the vocabulary module itself — the definition;
#   * admin/tenants.py, whose ARCHIVED_STATUS and input-validation regex are
#     pinned to the canonical constant by the test below (it belongs to
#     another owner and keeps its own literal).
_CANCEL_LITERAL_OWNERS = {
    "app/domain/services/subscription_status.py",
    "app/api/v1/endpoints/admin/tenants.py",
}

_CANCEL_STRINGS = {"cancelled", "canceled"}

# Other words that only ever appear alongside a cancel spelling when the
# collection is a *subscription* status set. `{"completed", "cancelled"}`
# (campaign status) and `{"ended", "failed", "cancelled"}` (call status) are
# different vocabularies and must not be flagged.
_SUBSCRIPTION_ONLY_WORDS = {
    "suspended",
    "past_due",
    "trialing",
    "unpaid",
    "incomplete_expired",
}


def _app_modules():
    for path in sorted((BACKEND / "app").rglob("*.py")):
        rel = str(path.relative_to(BACKEND)).replace("\\", "/")
        if rel in _CANCEL_LITERAL_OWNERS:
            continue
        src = path.read_text(encoding="utf-8")
        try:
            yield rel, src, ast.parse(src)
        except SyntaxError:  # pragma: no cover - the import gate catches this
            continue


def _string_constants(node) -> set[str]:
    return {
        n.value.strip().lower()
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }


def test_no_write_of_subscription_status_spells_a_cancel_literal():
    """The write side of the bug.

    ``{"subscription_status": "canceled"}`` and the guard's ``"cancelled"``
    were two literals in two files with nothing tying them together. Any
    statement that both names the column and spells a cancel string is a
    second copy of the vocabulary.
    """
    offenders = []
    for rel, src, tree in _app_modules():
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr, ast.Return)
            ):
                continue
            # a bare string statement is a docstring, not code
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue
            segment = ast.get_source_segment(src, node) or ""
            if "subscription_status" not in segment:
                continue
            if _string_constants(node) & _CANCEL_STRINGS:
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "a cancel spelling is written into subscription_status at "
        + str(offenders)
        + " — import it from app/domain/services/subscription_status.py"
    )


def test_no_module_carries_its_own_subscription_status_set():
    """The read side of the bug.

    ``("suspended", "cancelled")`` and ``("suspended", "cancelled",
    "past_due")`` were the three inline tuples the guard blocked on. A literal
    collection pairing a cancel spelling with another subscription-only status
    is by definition a second definition of the blocking vocabulary.
    """
    offenders = []
    for rel, _src, tree in _app_modules():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                continue
            values = {
                e.value.strip().lower()
                for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)
            }
            if values & _CANCEL_STRINGS and values & _SUBSCRIPTION_ONLY_WORDS:
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        "an inline subscription-status set reappeared at "
        + str(offenders)
        + " — use TENANT_BLOCKED_STATUSES / SUBSCRIPTION_BLOCKED_STATUSES"
    )


def test_the_archive_verb_writes_the_same_value_the_guard_blocks_on():
    """admin/tenants.py keeps its own literal (it is another owner's file);
    this pins it to the canonical constant so the two cannot drift."""
    from app.api.v1.endpoints.admin.tenants import (
        ARCHIVED_STATUS,
        _SUBSCRIPTION_STATUS_PATTERN,
    )
    from app.domain.services.subscription_status import (
        CANCELLED,
        TENANT_BLOCKED_STATUSES,
    )

    assert ARCHIVED_STATUS == CANCELLED
    assert ARCHIVED_STATUS in TENANT_BLOCKED_STATUSES
    assert re.match(_SUBSCRIPTION_STATUS_PATTERN, CANCELLED)


def test_the_guard_imports_the_shared_vocabulary():
    """Structural guard: call_guard must compare against the shared sets."""
    code = dict(app_sources())["app/domain/services/call_guard.py"]
    assert "from app.domain.services.subscription_status import" in code
    for literal in ('"suspended"', "'suspended'", '"past_due"', "'past_due'"):
        assert literal not in code, (
            f"call_guard spells {literal} inline again — that is the second "
            "copy this module exists to prevent"
        )


def test_every_blocked_status_is_part_of_the_known_vocabulary():
    """A blocked status that is not a known value can never match a row."""
    from app.domain.services.subscription_status import (
        KNOWN_STATUSES,
        SUBSCRIPTION_BLOCKED_STATUSES,
        TENANT_BLOCKED_STATUSES,
    )

    assert TENANT_BLOCKED_STATUSES <= SUBSCRIPTION_BLOCKED_STATUSES
    assert SUBSCRIPTION_BLOCKED_STATUSES <= KNOWN_STATUSES
    # A paying tenant must never be in a blocking set.
    assert not ({"active", "trialing"} & SUBSCRIPTION_BLOCKED_STATUSES)


def test_canonical_folds_the_stripe_spelling_and_leaves_others_alone():
    from app.domain.services.subscription_status import CANCELLED, canonical

    assert canonical("canceled") == CANCELLED
    assert canonical("CANCELED") == CANCELLED
    assert canonical(" cancelled ") == CANCELLED
    assert canonical("active") == "active"
    assert canonical("past_due") == "past_due"
    assert canonical(None) == ""

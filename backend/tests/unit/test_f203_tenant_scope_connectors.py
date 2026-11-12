"""QA F2-03, connectors slice: review, reward, connector and CRM-delivery SQL names its tenant.

Production enforces row-level security, but the project rule is an explicit
``tenant_id`` predicate in the SQL itself ("never rely on RLS alone"). Every test
here drives the real method with a fake connection that records ``(sql, args)``
and asserts that the statement *sent* carries ``tenant_id = $n`` and that ``$n``
is bound to the tenant the caller passed. Nothing evaluates SQL: the property
under test is what leaves the service, which is what an RLS-bypassing role would
execute.

Sections 1-3 fail on the code before this change (24 of the 38 parametrized
cases). Section 4 holds characterization tests that pass before and after: they
cover statements that already bind their tenant but that the F2-03 scanner cannot
prove (dynamic WHERE, CASE, ON CONFLICT DO UPDATE, NOW() in VALUES), so their
baseline entries stay as debt; the tests show that debt is not a real gap.
"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.domain.services import conversation_review_service as review_module
from app.domain.services.conversation_review_service import (
    ConversationReviewService,
    ReviewCallNotFoundError,
)
from app.services import connector_lifecycle as lifecycle_module
from app.services import crm_delivery_store as crm_module
from app.services.connector_lifecycle import ConnectorActor, ConnectorLifecycle
from app.services.crm_delivery_store import CRMDeliveryStore

TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "99999999-9999-4999-8999-999999999999"
CALL = "22222222-2222-4222-8222-222222222222"
USER = "33333333-3333-4333-8333-333333333333"
CAMPAIGN = "44444444-4444-4444-8444-444444444444"
REVIEW = "55555555-5555-4555-8555-555555555555"
CONNECTOR = "66666666-6666-4666-8666-666666666666"
NOW = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(params=[TENANT_A, TENANT_B], ids=["tenant-a", "tenant-b"])
def tenant(request):
    """Two tenants, so a constant baked into the SQL cannot satisfy the tests."""
    return request.param


# -- recording connection ---------------------------------------------------


def norm(sql):
    return " ".join(sql.split())


class Conn:
    """Records every statement as (method, sql, args); answers by SQL fragment."""

    def __init__(self, answers=None, *, execute_result="UPDATE 1"):
        self.calls = []
        self.answers = dict(answers or {})
        self.execute_result = execute_result

    def _record(self, method, sql, args):
        text = norm(sql)
        self.calls.append((method, text, args))
        for fragment, value in self.answers.items():
            if fragment in text:
                return value
        return None

    async def fetchrow(self, sql, *args):
        return self._record("fetchrow", sql, args)

    async def fetchval(self, sql, *args):
        return self._record("fetchval", sql, args)

    async def fetch(self, sql, *args):
        return self._record("fetch", sql, args) or []

    async def execute(self, sql, *args):
        self._record("execute", sql, args)
        return self.execute_result

    def statements(self, fragment):
        return [(sql, args) for _method, sql, args in self.calls if fragment in sql]

    def one(self, fragment):
        found = self.statements(fragment)
        assert len(found) == 1, (
            f"expected exactly one statement containing {fragment!r}, got {len(found)}: "
            f"{[sql for sql, _ in found]}"
        )
        return found[0]


def patch_acquire(monkeypatch, module, conn):
    """Make ``acquire_with_tenant`` hand out the recording connection."""
    acquired = []

    @asynccontextmanager
    async def acquire(pool, tenant_id, **_kwargs):
        acquired.append(tenant_id)
        yield conn

    monkeypatch.setattr(module, "acquire_with_tenant", acquire)
    return acquired


# -- what a statement binds ---------------------------------------------------


def tenant_params(sql, qualifier=None):
    """Parameter numbers n in every ``[qualifier.]tenant_id = $n`` of the statement."""
    qual = re.escape(qualifier) + r"\." if qualifier else r"(?:\w+\.)?"
    return [int(n) for n in re.findall(rf"(?<![\w.]){qual}tenant_id\s*=\s*\$(\d+)", norm(sql), re.I)]


def assert_tenant_bound(sql, args, tenant, *, qualifier=None):
    params = tenant_params(sql, qualifier)
    label = f"{qualifier}.tenant_id" if qualifier else "tenant_id"
    assert params, f"no `{label} = $n` predicate in: {norm(sql)}"
    for n in params:
        assert str(args[n - 1]) == tenant, (
            f"`{label} = ${n}` is bound to {args[n - 1]!r}, not the caller's tenant {tenant}"
        )


def inserted_value(sql, args, column):
    """The bound value an INSERT writes into ``column``."""
    match = re.search(r"INSERT INTO \w+\s*\(([^)]*)\)\s*VALUES\s*\((.*?)\)\s*(?:ON CONFLICT|RETURNING|$)",
                      norm(sql), re.I)
    assert match, f"not a single-row INSERT: {norm(sql)}"
    columns = [c.strip().lower() for c in match.group(1).split(",")]
    values = [v.strip() for v in match.group(2).split(",")]
    placeholder = re.match(r"\$(\d+)", values[columns.index(column)])
    assert placeholder, f"{column} is not bound to a parameter in: {norm(sql)}"
    return args[int(placeholder.group(1)) - 1]


# ===========================================================================
# 1. conversation_review_service
# ===========================================================================

CALL_ROW = {"id": UUID(CALL), "campaign_id": UUID(CAMPAIGN), "status": "completed",
            "prompt_template": "tpl", "prompt_version": "v1", "prompt_hash": "h1"}
REVIEW_ROW = {"id": UUID(REVIEW), "tenant_id": None, "call_id": UUID(CALL), "campaign_id": UUID(CAMPAIGN),
              "user_id": UUID(USER), "rating": 4, "review_tags": ["good_conversation"], "comment": None,
              "prompt_template": "tpl", "prompt_version": "v1", "prompt_hash": "h1", "llm_model": None,
              "created_at": NOW, "updated_at": NOW}


@pytest.fixture
def reviews(monkeypatch):
    for name in ("REVIEW_REWARDS_ENABLED", "REVIEW_REWARD_BARE_RATING_ELIGIBLE",
                 "REVIEW_REWARD_POINTS", "REVIEW_REWARD_DAILY_MAX"):
        monkeypatch.delenv(name, raising=False)
    conn = Conn({"FROM calls": CALL_ROW})
    patch_acquire(monkeypatch, review_module, conn)
    return ConversationReviewService(object()), conn


async def test_get_my_review_names_the_tenant(reviews, tenant):
    service, conn = reviews
    conn.answers["FROM conversation_reviews"] = dict(REVIEW_ROW)

    review = await service.get_my_review(tenant_id=tenant, call_id=CALL, user_id=USER)

    assert review["rating"] == 4, "a correct caller still gets their review back"
    sql, args = conn.one("FROM conversation_reviews")
    assert_tenant_bound(sql, args, tenant)
    assert UUID(CALL) in args and UUID(USER) in args, "the call and user filters are unchanged"


async def test_list_for_call_names_the_tenant(reviews, tenant):
    service, conn = reviews
    conn.answers["FROM conversation_reviews"] = [dict(REVIEW_ROW)]

    rows = await service.list_for_call(tenant_id=tenant, call_id=CALL)

    assert [r["rating"] for r in rows] == [4]
    sql, args = conn.one("FROM conversation_reviews")
    assert_tenant_bound(sql, args, tenant)
    assert UUID(CALL) in args
    assert sql.endswith("ORDER BY created_at"), "ordering is unchanged"


async def test_submit_upsert_cannot_update_another_tenants_review(reviews, tenant):
    """ON CONFLICT is keyed (call_id, user_id), so the UPDATE branch must carry its own guard."""
    service, conn = reviews
    conn.answers["INSERT INTO conversation_reviews"] = dict(REVIEW_ROW, tenant_id=UUID(tenant))

    review = await service.submit(tenant_id=tenant, call_id=CALL, user_id=USER, rating=4,
                                  tags=["good_conversation"])

    assert review["awarded_points"] == 0 and review["rating"] == 4, "a correct caller is unaffected"
    sql, args = conn.one("INSERT INTO conversation_reviews")
    assert str(inserted_value(sql, args, "tenant_id")) == tenant
    guard = re.search(r"DO UPDATE .*\bWHERE\s+(?:\w+\.)?tenant_id\s*=\s*\$(\d+)", sql)
    assert guard, f"the DO UPDATE branch has no tenant guard: {sql}"
    assert str(args[int(guard.group(1)) - 1]) == tenant


async def test_submit_refuses_when_the_upsert_touches_no_row(reviews, monkeypatch, tenant):
    """A conflicting row owned by another tenant is skipped by the guard: report the call as not found."""
    monkeypatch.setenv("REVIEW_REWARDS_ENABLED", "true")
    service, conn = reviews
    conn.answers["INSERT INTO conversation_reviews"] = None  # the guard blocked the UPDATE

    with pytest.raises(ReviewCallNotFoundError):
        await service.submit(tenant_id=tenant, call_id=CALL, user_id=USER, rating=5,
                             tags=["good_conversation"])

    assert not conn.statements("review_reward_ledger"), "no reward may be granted for a review that was not written"


async def test_reward_ledger_reads_name_the_tenant(reviews, monkeypatch, tenant):
    monkeypatch.setenv("REVIEW_REWARDS_ENABLED", "true")
    service, conn = reviews
    conn.answers.update({"SELECT 1 FROM review_reward_ledger": None,
                         "SELECT count(*) FROM review_reward_ledger": 0})

    points = await service._maybe_award(tenant_id=tenant, user_id=USER, review_id=REVIEW,
                                        rating=4, tags=["good_conversation"], comment=None)

    assert points == 10, "a correct caller still earns the default award"
    for fragment in ("SELECT 1 FROM review_reward_ledger", "SELECT count(*) FROM review_reward_ledger"):
        sql, args = conn.one(fragment)
        assert_tenant_bound(sql, args, tenant)
    already_sql, already_args = conn.one("SELECT 1 FROM review_reward_ledger")
    assert UUID(REVIEW) in already_args
    cap_sql, cap_args = conn.one("SELECT count(*) FROM review_reward_ledger")
    assert UUID(USER) in cap_args and "awarded_at >= date_trunc('day'" in cap_sql
    ledger_sql, ledger_args = conn.one("INSERT INTO review_reward_ledger")
    assert str(inserted_value(ledger_sql, ledger_args, "tenant_id")) == tenant


async def test_an_edit_is_not_credited_twice_when_the_tenants_ledger_has_the_review(reviews, monkeypatch, tenant):
    monkeypatch.setenv("REVIEW_REWARDS_ENABLED", "true")
    service, conn = reviews
    conn.answers["SELECT 1 FROM review_reward_ledger"] = 1

    points = await service._maybe_award(tenant_id=tenant, user_id=USER, review_id=REVIEW,
                                        rating=4, tags=["good_conversation"], comment=None)

    assert points == 0
    assert not conn.statements("INSERT INTO review_reward_ledger")


async def test_reward_balance_is_summed_for_this_tenant_only(reviews, tenant):
    service, conn = reviews
    conn.answers["FROM review_reward_ledger"] = {"total": 30, "entries": 3, "today": 1}

    balance = await service.reward_balance(tenant_id=tenant, user_id=USER)

    assert (balance["total_points"], balance["entries"], balance["awarded_today"]) == (30, 3, 1)
    sql, args = conn.one("FROM review_reward_ledger")
    assert_tenant_bound(sql, args, tenant)
    assert UUID(USER) in args


# ===========================================================================
# 2. connector_lifecycle
# ===========================================================================


class Audit:
    async def lock_transaction(self, conn):
        return None

    async def log_in_transaction(self, conn, **entry):
        return uuid4()


def parent_row(status):
    return {"id": UUID(CONNECTOR), "tenant_id": None, "type": "calendar", "provider": "google_calendar",
            "status": status, "config": {}, "created_at": NOW}


@pytest.fixture
def connectors(monkeypatch):
    """A system-actor lifecycle (no principal lookups) over the recording connection."""
    conn = Conn({
        "SELECT * FROM connectors WHERE id=$1 AND tenant_id=$2 FOR UPDATE": parent_row("active"),
    })
    patch_acquire(monkeypatch, lifecycle_module, conn)
    life = ConnectorLifecycle(SimpleNamespace(pool=object()), actor=ConnectorActor.system("f203_test"),
                              audit_logger=Audit())
    return life, conn


async def test_update_config_writes_only_the_callers_connector(connectors, tenant):
    life, conn = connectors

    config = await life.update_config(tenant, CONNECTOR, {"log_calls": False}, provider="google_calendar")

    assert config == {"log_calls": False}
    sql, args = conn.one("UPDATE connectors SET config=")
    assert_tenant_bound(sql, args, tenant)
    assert UUID(CONNECTOR) in args and '"log_calls": false' in args[1]


async def test_reject_pending_closes_only_the_callers_connector(connectors, tenant):
    life, conn = connectors
    conn.answers["UPDATE connectors SET status='error'"] = UUID(CONNECTOR)

    await life.reject_pending(tenant, CONNECTOR)

    sql, args = conn.one("UPDATE connectors SET status='error'")
    assert_tenant_bound(sql, args, tenant)
    assert UUID(CONNECTOR) in args and "status='pending'" in sql, "only a pending row is rejected, as before"


async def test_disable_marks_revocation_pending_for_the_callers_connector_only(connectors, tenant):
    life, conn = connectors
    conn.answers["UPDATE connectors SET provider_revocation_status=$3"] = UUID(CONNECTOR)

    async def never_called(*_args, **_kwargs):  # no account holds a token in this scenario
        raise AssertionError("no provider revocation is expected without tokens")

    result = await life.disable(tenant, CONNECTOR, revoke_token=never_called)

    assert result.provider_revocation_status == "unconfirmed"
    sql, args = conn.one("UPDATE connectors SET provider_revocation_status='pending'")
    assert_tenant_bound(sql, args, tenant)
    assert UUID(CONNECTOR) in args


async def test_activate_installs_the_grant_on_the_callers_connector_only(connectors, tenant):
    life, conn = connectors
    target = dict(parent_row("pending"))
    conn.answers.update({
        "SELECT * FROM connectors WHERE tenant_id=$1 AND type=$2": [target],
        "INSERT INTO connector_accounts": {"id": uuid4(), "credential_generation": 1},
    })

    await life.activate(tenant, CONNECTOR, "google_calendar", "calendar",
                        {"access_token_encrypted": "enc"}, {"scopes": ["a"]})

    sql, args = conn.one("UPDATE connectors SET status='active'")
    assert_tenant_bound(sql, args, tenant)
    assert UUID(CONNECTOR) in args and "provider_revocation_status='not_requested'" in sql
    # The account row is written for the same tenant (kept as scanner debt: NOW() inside VALUES).
    insert_sql, insert_args = conn.one("INSERT INTO connector_accounts")
    assert str(inserted_value(insert_sql, insert_args, "tenant_id")) == tenant
    assert str(inserted_value(insert_sql, insert_args, "connector_id")) == CONNECTOR


# ===========================================================================
# 3. crm_delivery_store
# ===========================================================================

LEASE = "77777777-7777-4777-8777-777777777777"
EFFECT = {"connector_id": CONNECTOR, "account_id": "acct-1", "source_call_revision": "777"}


def receipt_for(tenant):
    return {"tenant_id": tenant, "call_id": CALL, "provider": "hubspot", "lease_token": LEASE,
            "desired_key": "k1", "attempts": 1, "phase": "resolving_contact"}


@pytest.fixture
def crm(monkeypatch):
    conn = Conn()
    patch_acquire(monkeypatch, crm_module, conn)
    return CRMDeliveryStore(object()), conn


async def test_bind_contact_effect_reads_only_the_receipts_tenants_call(crm, tenant):
    store, conn = crm

    await store.bind_contact_effect(receipt_for(tenant), dict(EFFECT))

    sql, args = conn.one("UPDATE crm_deliveries SET contact_effect")
    assert_tenant_bound(sql, args, tenant)  # the delivery row
    assert_tenant_bound(sql, args, tenant, qualifier="c")  # the call it is checked against
    assert "FROM calls c" in sql


async def test_begin_contact_create_reads_only_the_receipts_tenants_call(crm, tenant):
    store, conn = crm

    await store.begin_contact_create(receipt_for(tenant), dict(EFFECT), source_revision="777")

    sql, args = conn.one("UPDATE crm_deliveries SET phase='creating_contact'")
    assert_tenant_bound(sql, args, tenant)  # the delivery row
    assert_tenant_bound(sql, args, tenant, qualifier="c")  # the call it is checked against
    assert "FROM calls c" in sql and "777" in args


# ===========================================================================
# 4. statements outside the scanner's proof that were verified to be bound
# ===========================================================================


@pytest.mark.parametrize("filters", [
    {},
    {"campaign_id": CAMPAIGN, "prompt_version": "v2", "rating_min": 1, "rating_max": 2,
     "tag": "response_too_long"},
], ids=["no-filters", "all-filters"])
async def test_filtered_review_listing_always_carries_the_tenant(reviews, tenant, filters):
    service, conn = reviews
    conn.answers["SELECT count(*) FROM conversation_reviews"] = 0

    await service.list_reviews(tenant_id=tenant, **filters)

    statements = conn.statements("FROM conversation_reviews")
    assert len(statements) == 2, "a count and a page"
    for sql, args in statements:
        assert_tenant_bound(sql, args, tenant)


@pytest.mark.parametrize("filters", [{}, {"campaign_id": CAMPAIGN, "prompt_version": "v2"}],
                         ids=["no-filters", "all-filters"])
async def test_review_summary_always_carries_the_tenant(reviews, tenant, filters):
    service, conn = reviews

    await service.summarise(tenant_id=tenant, **filters)

    statements = conn.statements("conversation_reviews")
    assert len(statements) == 3, "by prompt version, by tag, totals"
    for sql, args in statements:
        assert_tenant_bound(sql, args, tenant)


async def test_enqueue_inserts_for_the_callers_tenant_and_conflicts_inside_it(crm, tenant):
    store, conn = crm
    conn.answers["INSERT INTO crm_deliveries"] = {"tenant_id": tenant, "status": "pending"}

    await store.enqueue(tenant, CALL, "hubspot", "key-1")

    sql, args = conn.one("INSERT INTO crm_deliveries")
    assert str(inserted_value(sql, args, "tenant_id")) == tenant
    assert "ON CONFLICT (tenant_id,call_id,provider)" in sql, "the primary key keeps the upsert in one tenant"


async def test_save_updates_only_the_receipts_tenants_delivery(crm, tenant):
    store, conn = crm

    await store.save(receipt_for(tenant), status="succeeded", phase="done")

    sql, args = conn.one("UPDATE crm_deliveries SET phase=COALESCE")
    assert_tenant_bound(sql, args, tenant)
    assert str(LEASE) in [str(a) for a in args], "the lease fence is unchanged"

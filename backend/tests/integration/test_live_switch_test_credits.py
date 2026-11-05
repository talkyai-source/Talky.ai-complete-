"""The live-mode switch names accounts holding minutes bought in test mode (QA F13-10).

The check listed only plans bought with a test card, so minute bundles paid in
test mode stayed on the ledger and would be spent as real minutes after the
switch to live keys.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
import pytest

from app.core.db import _register_jsonb_codecs
from app.domain.services.payg import PAYG
from app.domain.services.topup_service import TopupService
from scripts import stripe_catalog_setup

pytestmark = pytest.mark.integration


def _dsn() -> str:
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Explicit TEST_DATABASE_URL is required")
    target = urlparse(dsn)
    if target.hostname not in {"127.0.0.1", "localhost"} or not target.path.endswith("_test"):
        pytest.fail("Live-switch tests require a disposable local *_test database")
    return dsn


async def test_the_live_switch_lists_minutes_bought_with_a_test_card(monkeypatch, capsys):
    dsn = _dsn()
    owner = await asyncpg.connect(dsn, timeout=5, command_timeout=20)
    tenant = uuid4()
    pool = None
    try:
        async with owner.transaction():
            await owner.execute("SET LOCAL app.bypass_rls = 'on'")
            await owner.execute(
                """INSERT INTO tenants (id, business_name, plan_id, minutes_allocated, minutes_used,
                       subscription_status)
                   VALUES ($1, 'Synthetic test-mode buyer', NULL, 0, 0, 'inactive')""", tenant)
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, init=_register_jsonb_codecs)
        service = TopupService(pool)
        order = await service.create_order(tenant_id=str(tenant), user_id=None,
                                           package_code="payg_125", audience=PAYG)
        session = "cs_test_" + uuid4().hex
        await service.attach_session(str(order["id"]), session)
        assert await service.credit_paid_order(session_id=session, event_id="evt_" + uuid4().hex)

        monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace(database_url=dsn))
        await stripe_catalog_setup.report_test_mode_plans()

        printed = capsys.readouterr().out
        assert f"{str(tenant)[:8]} (125 min)" in printed
    finally:
        # billing_ledger is append-only: its UUID-scoped synthetic rows remain,
        # with the order and account they belong to.
        if pool is not None:
            await pool.close()
        await owner.close()

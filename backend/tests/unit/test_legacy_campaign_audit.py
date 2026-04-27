"""T2.6 — legacy-campaign audit tests.

Exercises the audit logic with a fake asyncpg pool. The audit
returns a count + small sample of running/scheduled/paused/draft
campaigns missing `script_config.persona_type`, and never raises
into the startup path.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.core.legacy_campaign_audit import (
    LegacyCampaignAuditResult,
    audit_legacy_campaigns,
    log_audit_summary,
)


# ──────────────────────────────────────────────────────────────────────────
# Fake DB pool (asyncpg-shaped)
# ──────────────────────────────────────────────────────────────────────────

class _FakeConn:
    def __init__(self, *, total: int, missing_count: int, sample: list[str], raise_on: str | None = None):
        self._total = total
        self._missing = missing_count
        self._sample = sample
        self._raise = raise_on

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def fetchval(self, sql: str, *args):
        if self._raise == "fetchval":
            raise RuntimeError("db down")
        # First fetchval is total active; second is missing count.
        # Cheap discriminator: presence of the "persona_type" filter
        # in the SQL means it's the missing count query.
        if "persona_type" in sql:
            return self._missing
        return self._total

    async def fetch(self, sql: str, *args):
        if self._raise == "fetch":
            raise RuntimeError("db down")
        return [{"id": s} for s in self._sample]


class _FakePool:
    def __init__(self, **kwargs):
        self._kwargs = kwargs

    def acquire(self):
        return _FakeConn(**self._kwargs)


# ──────────────────────────────────────────────────────────────────────────
# Audit
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_pool_returns_unprobed():
    result = await audit_legacy_campaigns(None)
    assert result.probed is False
    assert result.error == "no_db_pool"


@pytest.mark.asyncio
async def test_db_failure_returns_unprobed():
    pool = _FakePool(total=10, missing_count=2, sample=["c1"], raise_on="fetchval")
    result = await audit_legacy_campaigns(pool)
    assert result.probed is False
    assert "db down" in (result.error or "")


@pytest.mark.asyncio
async def test_fully_migrated_reports_zero_and_no_warning():
    pool = _FakePool(total=10, missing_count=0, sample=[])
    result = await audit_legacy_campaigns(pool)
    assert result.probed is True
    assert result.total_active == 10
    assert result.missing_persona == 0
    assert result.sample_ids == []
    assert result.fully_migrated is True


@pytest.mark.asyncio
async def test_partial_migration_returns_count_and_sample():
    pool = _FakePool(
        total=20,
        missing_count=3,
        sample=["camp-1", "camp-2", "camp-3"],
    )
    result = await audit_legacy_campaigns(pool)
    assert result.probed is True
    assert result.total_active == 20
    assert result.missing_persona == 3
    assert result.sample_ids == ["camp-1", "camp-2", "camp-3"]
    assert result.fully_migrated is False


@pytest.mark.asyncio
async def test_zero_total_zero_missing_is_fully_migrated():
    """Empty campaigns table is the cleanest state."""
    pool = _FakePool(total=0, missing_count=0, sample=[])
    result = await audit_legacy_campaigns(pool)
    assert result.fully_migrated is True


def test_to_dict_round_trip():
    r = LegacyCampaignAuditResult(
        probed=True, total_active=5, missing_persona=2, sample_ids=["a", "b"],
    )
    d = r.to_dict()
    assert d == {
        "probed": True,
        "total_active": 5,
        "missing_persona": 2,
        "sample_ids": ["a", "b"],
        "error": None,
    }


# ──────────────────────────────────────────────────────────────────────────
# Log emission — confirm the right log levels fire
# ──────────────────────────────────────────────────────────────────────────

def test_log_summary_skipped_when_unprobed(caplog: pytest.LogCaptureFixture):
    result = LegacyCampaignAuditResult(probed=False, error="no_db_pool")
    with caplog.at_level("INFO", logger="app.core.legacy_campaign_audit"):
        log_audit_summary(result)
    assert any("legacy_campaign_audit_skipped" in r.message for r in caplog.records)


def test_log_summary_ok_when_fully_migrated(caplog: pytest.LogCaptureFixture):
    result = LegacyCampaignAuditResult(probed=True, total_active=5, missing_persona=0)
    with caplog.at_level("INFO", logger="app.core.legacy_campaign_audit"):
        log_audit_summary(result)
    assert any("legacy_campaign_audit ok" in r.message for r in caplog.records)
    # No WARN should fire.
    assert not any(r.levelname == "WARNING" for r in caplog.records)


def test_log_summary_warns_when_unmigrated_present(caplog: pytest.LogCaptureFixture):
    result = LegacyCampaignAuditResult(
        probed=True, total_active=10, missing_persona=3,
        sample_ids=["x", "y", "z"],
    )
    with caplog.at_level("WARNING", logger="app.core.legacy_campaign_audit"):
        log_audit_summary(result)
    warns = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warns, "expected a WARNING when missing_persona > 0"
    assert "legacy_campaign_audit_unmigrated" in warns[0].message
    assert "x" in warns[0].message  # sample IDs in the line

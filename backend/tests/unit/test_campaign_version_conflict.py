"""Two campaign saves from one version cannot overwrite each other (QA F3-03)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import campaigns
from tests.unit.test_campaign_visibility_w5 import (
    CAMPAIGN, TENANT, _FakeDB, _Table, _campaign_row, _patch_update_dependencies, _update_body,
)


class VersionedTable(_Table):
    def execute(self):
        if self.op == "select" and self.columns == ["*"]:
            rows = [dict(row) for row in self.db.rows if all(row.get(k) == v for k, v in self.filters)]
            return SimpleNamespace(data=rows, error=None)
        response = super().execute()
        if self.op == "update" and getattr(self.db, "timestamp_trigger", True):
            # PostgreSQL's existing BEFORE UPDATE timestamp trigger.
            for result in response.data:
                row = next(row for row in self.db.rows if row["id"] == result["id"])
                row["updated_at"] += timedelta(microseconds=1)
                result["updated_at"] = row["updated_at"]
        return response


class VersionedDB(_FakeDB):
    def table(self, name):
        assert name == "campaigns"
        return VersionedTable(self)


@pytest.fixture
def editing(monkeypatch):
    _patch_update_dependencies(monkeypatch)
    row = _campaign_row()
    row.update(name="Original", updated_at=datetime(2026, 11, 6, tzinfo=timezone.utc))
    db = VersionedDB([row])
    user = CurrentUser(id="user", email="user@example.com", tenant_id=TENANT, role="tenant_admin")
    return db, row, user


async def save(editing, **values):
    db, _, user = editing
    return await campaigns.update_campaign(CAMPAIGN, _update_body(**values), SimpleNamespace(), user, db)


async def test_second_save_from_same_version_gets_409_and_preserves_first(editing):
    _, row, _ = editing
    original = row["updated_at"]
    await save(editing, name="First editor", expected_updated_at=original)
    with pytest.raises(HTTPException) as caught:
        await save(editing, name="Stale editor", expected_updated_at=original)
    assert caught.value.status_code == 409
    assert caught.value.detail["current"]["name"] == "First editor"
    assert caught.value.detail["current"]["updated_at"] == row["updated_at"].isoformat()
    assert row["name"] == "First editor"


async def test_missing_version_keeps_legacy_save_and_logs_adoption(editing, caplog):
    with caplog.at_level("INFO", logger=campaigns.logger.name):
        await save(editing, name="Legacy editor")
    assert editing[1]["name"] == "Legacy editor"
    assert "campaign_update_without_version" in caplog.text


async def test_version_conflict_never_discloses_another_tenant(editing):
    editing[1]["tenant_id"] = "99999999-9999-9999-9999-999999999999"
    with pytest.raises(HTTPException) as caught:
        await save(editing, expected_updated_at=datetime(2026, 11, 6, tzinfo=timezone.utc))
    assert caught.value.status_code == 404
    assert editing[1]["name"] == "Original"


async def test_http_conflict_serializes_database_timestamps(editing):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from app.core.error_handlers import register_error_handlers

    version = editing[1]["updated_at"]
    await save(editing, name="First editor", expected_updated_at=version)
    app = FastAPI()
    register_error_handlers(app)

    @app.put("/campaign")
    async def stale_save():
        return await save(editing, name="Stale editor", expected_updated_at=version)

    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                           base_url="http://test") as client:
        response = await client.put("/campaign")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["details"]["current"]["updated_at"] == editing[1]["updated_at"].isoformat()


async def test_version_advances_with_disabled_historical_timestamp_trigger(editing):
    # The retained 2026-06-02 schema disables update_campaigns_updated_at.
    editing[0].timestamp_trigger = False
    original = editing[1]["updated_at"]
    await save(editing, name="First editor", expected_updated_at=original)
    with pytest.raises(HTTPException) as caught:
        await save(editing, name="Stale editor", expected_updated_at=original)
    assert caught.value.status_code == 409
    assert editing[1]["name"] == "First editor"


async def test_legacy_save_advances_version_without_timestamp_trigger(editing):
    editing[0].timestamp_trigger = False
    original = editing[1]["updated_at"]
    await save(editing, name="Legacy editor")
    assert editing[1]["updated_at"] != original

"""D3-18: one defined campaign status list, matching what is written.

The domain enum lacked 'stopped' (written by Stop) and 'deleted' (written by
Delete), while the database check (0088) allows both. The enum is now the
same set as the check, each value names its writer, and the display phases
(preparing / pausing / stopping) stay phases, not statuses.
"""
import importlib.util
from pathlib import Path

from app.domain.models.campaign import CAMPAIGN_STATUS_WRITERS, CampaignStatus
from app.domain.services.campaign_service import PAUSABLE_STATUSES, STOPPABLE_STATUSES

BACKEND = Path(__file__).resolve().parents[2]


def _db_check_states():
    path = BACKEND / "Alembic" / "versions" / "0088_qa_data_constraints.py"
    spec = importlib.util.spec_from_file_location("migration_0088", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.CAMPAIGN_STATES)


def test_the_enum_is_exactly_the_database_check():
    assert {status.value for status in CampaignStatus} == _db_check_states()


def test_every_status_has_a_documented_writer_or_says_it_has_none():
    assert set(CAMPAIGN_STATUS_WRITERS) == {status.value for status in CampaignStatus}
    assert all(CAMPAIGN_STATUS_WRITERS[status].strip() for status in CAMPAIGN_STATUS_WRITERS)
    assert "Stop" in CAMPAIGN_STATUS_WRITERS["stopped"]
    assert "no writer" in CAMPAIGN_STATUS_WRITERS["completed"]


def test_display_phases_are_not_statuses():
    statuses = {status.value for status in CampaignStatus}
    assert not {"preparing", "pausing", "stopping"} & statuses


def test_pause_and_stop_only_name_real_statuses():
    statuses = {status.value for status in CampaignStatus}
    assert set(PAUSABLE_STATUSES) <= statuses and set(STOPPABLE_STATUSES) <= statuses


def test_the_service_writes_only_listed_statuses():
    source = (BACKEND / "app" / "domain" / "services" / "campaign_service.py").read_text(encoding="utf-8")
    for written in ('"status": "paused"', '"status": "stopped"', '"running"'):
        assert written in source
    for value in ("paused", "stopped", "running"):
        assert value in {status.value for status in CampaignStatus}

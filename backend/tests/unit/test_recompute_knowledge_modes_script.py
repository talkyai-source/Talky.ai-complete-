"""The backfill that re-derives campaigns.knowledge_mode.

The mode is STORED at upload time, not derived at call time, so fixing the
budget code moves nothing on its own. This script re-derives it using the same
choose_mode the ingest path uses, so the two can never disagree.

The decision is a pure function precisely so it can be pinned here without a
database.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "recompute_knowledge_modes.py"
)
_spec = importlib.util.spec_from_file_location("recompute_knowledge_modes", _SCRIPT)
recompute = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recompute)


def _row(**kw):
    base = {
        "campaign_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
        "campaign_name": "Reception",
        "current_mode": "none",
        "model": "openai/gpt-oss-20b",
        "ready_tokens": 0,
        "ready_sources": 0,
    }
    base.update(kw)
    return base


def test_a_campaign_with_no_ready_source_is_skipped_not_reset():
    # A campaign mid-upload must never be stamped by a race with its own
    # ingest transaction, so it is skipped rather than forced to 'none'.
    changes, skipped = recompute.plan_changes([_row(current_mode="inline")])
    assert changes == []
    assert skipped == 1


def test_a_stale_mode_is_corrected():
    changes, skipped = recompute.plan_changes(
        [_row(current_mode="retrieve", ready_sources=1, ready_tokens=800)]
    )
    assert skipped == 0
    assert len(changes) == 1
    assert changes[0].current_mode == "retrieve"
    assert changes[0].wanted_mode == "inline"
    assert changes[0].ready_tokens == 800


def test_an_already_correct_mode_is_left_alone():
    changes, _ = recompute.plan_changes(
        [_row(current_mode="inline", ready_sources=1, ready_tokens=800)]
    )
    assert changes == []


def test_a_null_current_mode_is_treated_as_none():
    changes, _ = recompute.plan_changes(
        [_row(current_mode=None, ready_sources=1, ready_tokens=800)]
    )
    assert len(changes) == 1
    assert changes[0].current_mode == "none"


def test_a_very_large_knowledge_base_still_lands_on_retrieve():
    changes, _ = recompute.plan_changes(
        [_row(current_mode="inline", ready_sources=3, ready_tokens=5_000_000)]
    )
    assert changes[0].wanted_mode == "retrieve"


def test_the_tenant_id_is_carried_through_for_the_update_predicate():
    changes, _ = recompute.plan_changes(
        [_row(current_mode="none", ready_sources=1, ready_tokens=900)]
    )
    assert changes[0].tenant_id == "22222222-2222-2222-2222-222222222222"


def test_every_write_is_tenant_scoped():
    # Guard, not proof: the plan SQL and the update must both carry a tenant
    # predicate, because the production app role has bypassed RLS before.
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "AND tenant_id = $2::uuid" in source
    assert "c.tenant_id = $1::uuid" in source


def test_the_script_only_writes_the_mode_column():
    source = _SCRIPT.read_text(encoding="utf-8")
    updates = [ln for ln in source.splitlines() if "UPDATE " in ln and "--" not in ln]
    assert updates, "expected at least one UPDATE"
    for line in updates:
        assert "campaigns SET knowledge_mode" in line, line

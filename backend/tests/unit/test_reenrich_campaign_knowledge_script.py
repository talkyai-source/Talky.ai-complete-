"""Filling in enrichment that a month-dead model never produced.

The enricher called ``llama-3.1-8b-instant`` for a month after Groq removed it.
Enrichment is fail-soft, so uploads in that window published bare nodes. Two
live campaigns still carry them: ``dojo`` (43 nodes, 0 enriched) and
``Estimation new`` (27 nodes, 0 enriched).

The obvious repair -- re-upload the document -- is the wrong one. Re-ingesting
DELETES the nodes and rebuilds them, which on a running campaign leaves a window
with no knowledge at all, and the ingest guard refuses a campaign that has
calls. This script updates the four enrichment columns in place instead, so
nothing is ever deleted and the guard does not apply.

These tests pin the properties that make that safe.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "reenrich_campaign_knowledge.py"
)
_spec = importlib.util.spec_from_file_location("reenrich_campaign_knowledge", _SCRIPT)
reenrich = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reenrich)

_SOURCE = _SCRIPT.read_text(encoding="utf-8")


def test_the_script_never_deletes_a_knowledge_row():
    """The whole reason this exists rather than a re-upload.

    A DELETE here would reintroduce the window this script was written to
    avoid, on campaigns that are live and taking calls.
    """
    lowered = _SOURCE.lower()
    assert "delete from campaign_knowledge_nodes" not in lowered
    assert "truncate" not in lowered
    assert "drop " not in lowered


def test_only_the_enrichment_columns_and_the_search_index_are_written():
    """Headings, bodies, depth, position and path are already correct."""
    update = _SOURCE[_SOURCE.index("UPDATE campaign_knowledge_nodes") :]
    update = update[: update.index("WHERE id = $1")]
    written = {
        "summary",
        "voice_answer",
        "keywords",
        "example_questions",
        "search_text",
        "search_tsv",
        "updated_at",
    }
    for column in ("heading", "content", "depth", "position", "path", "campaign_id"):
        assert f"{column} =" not in update, f"{column} must not be rewritten"
    for column in written:
        assert f"{column} =" in update, f"{column} should be written"


def test_the_search_index_is_rebuilt_not_left_stale():
    """``_search_text`` folds keywords and example questions into the indexed
    text, and retrieval matches a caller's phrasing against it with
    word_similarity. Writing the enrichment without refreshing search_text
    would leave the fuzzy-match path exactly as degraded as it is now.
    """
    assert "_search_text(" in _SOURCE
    assert "to_tsvector('english', $6)" in _SOURCE


def test_it_refuses_while_a_call_is_in_flight():
    assert "REFUSING" in _SOURCE
    assert "'ringing','in_progress','active','initiated'" in _SOURCE


def test_bypass_rls_is_re_applied_on_every_acquire_not_just_at_connect():
    """asyncpg issues RESET ALL when a connection is RELEASED to the pool.

    A GUC set in ``init=`` therefore survives only until the first release.
    The first run of this script against production did exactly that: the
    opening read saw all 43 rows, every later acquire ran with RLS in force,
    and the UPDATEs matched zero rows. asyncpg does not raise when a statement
    affects nothing, so it reported 34 writes and wrote none.

    An earlier version of this test asserted the GUC was present in ``_init``.
    That was true, and the script was still broken -- the test was checking the
    wrong hook. ``setup=`` runs on every acquire; that is the one that matters.
    """
    setup = _SOURCE[_SOURCE.index("async def _setup(") : _SOURCE.index("pool = await")]
    assert "app.bypass_rls" in setup
    assert "setup=_setup" in _SOURCE

    init = _SOURCE[_SOURCE.index("async def _init(") : _SOURCE.index("async def _setup(")]
    assert "app.bypass_rls" not in init, (
        "setting the GUC in init= alone is the bug this test exists for"
    )


def test_a_write_that_affects_no_row_is_an_error_not_a_success():
    """The failure above was silent because the script counted its own calls.

    Under RLS ``UPDATE ... WHERE id = $1`` matching nothing returns "UPDATE 0"
    and raises nothing. Counting execute() calls reported 34 writes against a
    table that still held 43 untouched rows.
    """
    assert 'status = await conn.execute(' in _SOURCE
    assert 'status.strip() != "UPDATE 1"' in _SOURCE
    assert "refusing to continue" in _SOURCE


def test_every_query_is_scoped_by_tenant_as_well_as_campaign():
    """Prod's app role no longer bypasses RLS, but the script sets the GUC, so
    the tenant predicate is the only thing standing between this and another
    tenant's knowledge.
    """
    for marker in (
        "FROM campaign_knowledge_nodes",
        "FROM campaigns",
    ):
        for fragment in _SOURCE.split(marker)[1:]:
            head = fragment[:400]
            if "WHERE" not in head:
                continue
            assert "tenant_id = $2::uuid" in head, marker


def test_batch_size_matches_what_the_enricher_can_actually_parse():
    """25 sections per request produced structurally invalid JSON; the
    2026-09-22 fix settled on 8.
    """
    assert reenrich._BATCH == 8


def test_default_run_tops_up_only_bare_nodes():
    """So re-running costs nothing and a partial document can be topped up."""
    assert "--all" in _SOURCE
    assert 'args.all or not r["summary"].strip()' in _SOURCE


def test_required_arguments_are_enforced():
    for argv in (
        ["--campaign-id", "c"],
        ["--tenant-id", "t"],
    ):
        with pytest.raises(SystemExit):
            reenrich.main(argv)


def test_dry_run_is_available_and_defaults_off():
    parsed = {}

    async def _fake_run(args):
        parsed.update(vars(args))
        return 0

    original = reenrich._run
    reenrich._run = _fake_run
    try:
        reenrich.main(["--tenant-id", "t", "--campaign-id", "c"])
        assert parsed["dry_run"] is False
        assert parsed["all"] is False
    finally:
        reenrich._run = original


def test_zero_enriched_after_a_run_is_reported_as_failure():
    """A script that exits 0 having achieved nothing is how the clone script
    reported success while the model resolved to nothing at all.
    """
    tail = _SOURCE[_SOURCE.index("if after[\"enriched\"] == 0:") :]
    assert "return 1" in tail[:400]

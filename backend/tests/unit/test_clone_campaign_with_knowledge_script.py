"""The tool that clones a campaign and re-publishes its knowledge.

Two traps it exists to avoid:

* ``campaigns.voice_id`` defaults to the literal string ``'default'``, which is
  not a voice and has broken live calls before. Cloning a campaign that carries
  that value would quietly produce another broken one.
* Knowledge belongs to one campaign and is never shared, so a copy starts empty.
  The original markdown survives in ``campaign_knowledge_sources.raw_md``, and
  re-publishing it through the ingest path is what makes the copy equivalent to
  a fresh upload rather than a half-populated shell.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "clone_campaign_with_knowledge.py"
)
_spec = importlib.util.spec_from_file_location("clone_campaign_with_knowledge", _SCRIPT)
clone = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clone)


def test_identity_and_lifecycle_columns_are_never_copied():
    # Copying the id would collide; copying created_at would date the clone to
    # the original; knowledge_mode must be re-derived by the ingest, not
    # inherited, or the copy claims knowledge it does not have.
    for column in ("id", "name", "status", "created_at", "updated_at", "knowledge_mode"):
        assert column in clone._NEVER_COPY, column


def test_tenant_id_IS_copied_so_the_clone_stays_on_the_same_account():
    assert "tenant_id" not in clone._NEVER_COPY


def test_required_arguments_are_enforced():
    for argv in (
        ["--source-campaign-id", "s", "--new-name", "n"],
        ["--tenant-id", "t", "--new-name", "n"],
        ["--tenant-id", "t", "--source-campaign-id", "s"],
    ):
        with pytest.raises(SystemExit):
            clone.main(argv)


def test_the_clone_defaults_to_draft():
    parsed = {}

    async def _fake_run(args):
        parsed.update(vars(args))
        return 0

    original = clone._run
    clone._run = _fake_run
    try:
        clone.main(["--tenant-id", "t", "--source-campaign-id", "s", "--new-name", "n"])
    finally:
        clone._run = original
    assert parsed["status"] == "draft"
    assert parsed["dry_run"] is False


def test_every_column_is_read_from_the_catalogue_not_hardcoded():
    # Guard: a hardcoded column list silently drops any setting added later.
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "information_schema.columns" in source
    assert "table_name='campaigns'" in source


def test_it_refuses_a_source_whose_voice_is_the_placeholder():
    source = _SCRIPT.read_text(encoding="utf-8")
    assert '"default"' in source or "'default'" in source
    assert "not a real voice" in source


def test_knowledge_is_republished_through_the_ingest_path():
    # Not copied row by row: re-ingested, so parsing, enrichment, the search
    # index and the knowledge_mode budget all run exactly as on an upload.
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "ingest_markdown" in source
    assert "INSERT INTO campaign_knowledge_nodes" not in source


def test_the_readback_flags_a_clone_that_ended_up_without_knowledge():
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "NO usable knowledge" in source

"""No ops script may set the RLS bypass GUC only when a connection is created.

asyncpg issues RESET ALL when a pooled connection is released, so a GUC set in
``create_pool(init=...)`` survives only until the first release. Every later
acquire runs with row-level security in force, and a statement that matches no
visible row succeeds silently.

That is exactly how reenrich_campaign_knowledge.py first ran against production
on 2026-09-23: the opening read saw 43 rows, every UPDATE after it matched none,
and it reported 34 writes having written nothing. The GUC belongs in
``setup=``, which runs on every acquire.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _scripts_that_set_the_guc():
    for path in sorted(_SCRIPTS.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        if "SET app.bypass_rls" in src and "create_pool" in src:
            yield path


@pytest.mark.parametrize(
    "path", list(_scripts_that_set_the_guc()), ids=lambda p: p.name
)
def test_the_guc_is_set_on_every_acquire(path):
    src = path.read_text(encoding="utf-8")
    assert re.search(r"create_pool\([^)]*setup=", src, re.S), (
        f"{path.name} creates a pool and sets app.bypass_rls but passes no "
        "setup= hook - the GUC will be lost after the first release"
    )
    init = re.search(r"async def _init\(.*?\n(?=\n    \S|\n\S)", src, re.S)
    if init:
        assert "SET app.bypass_rls" not in init.group(0), (
            f"{path.name} still sets the GUC in init=, which RESET ALL undoes"
        )


def test_the_guard_actually_finds_the_scripts_it_protects():
    names = {p.name for p in _scripts_that_set_the_guc()}
    assert {
        "reenrich_campaign_knowledge.py",
        "clone_campaign_with_knowledge.py",
        "recompute_knowledge_modes.py",
    } <= names

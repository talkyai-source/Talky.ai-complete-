"""Every Alembic revision id must fit alembic_version.version_num (varchar(32)).

2026-09-08: migration 0045 shipped as ``0045_refresh_token_session_binding``
(34 chars). Its DDL and backfill ran, then Alembic's own
``UPDATE alembic_version SET version_num=...`` failed with
StringDataRightTruncationError and the whole transaction rolled back — the
deploy aborted with no useful line in the filtered output. The length limit is
a fact about the version table, not about our schema, so pin it here.
"""
from __future__ import annotations

import re
from pathlib import Path

VERSIONS_DIR = Path(__file__).resolve().parents[2] / "Alembic" / "versions"
ALEMBIC_VERSION_NUM_MAX = 32
_REVISION_RE = re.compile(r'^revision\s*(?::\s*str)?\s*=\s*["\']([^"\']+)["\']', re.M)


def _revision_ids() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        match = _REVISION_RE.search(path.read_text(encoding="utf-8"))
        if match:
            found[path.name] = match.group(1)
    return found


def test_versions_directory_is_where_this_test_thinks_it_is():
    assert VERSIONS_DIR.is_dir(), VERSIONS_DIR
    assert _revision_ids(), "no revision ids parsed — regex or path drifted"


def test_every_revision_id_fits_alembic_version_num():
    too_long = {
        name: rev for name, rev in _revision_ids().items() if len(rev) > ALEMBIC_VERSION_NUM_MAX
    }
    assert not too_long, (
        f"revision ids longer than {ALEMBIC_VERSION_NUM_MAX} chars roll the whole migration "
        f"back at the version bump: {too_long}"
    )


"""Guard: the public sample data dump must not carry real people's details.

``database/seed_data.sql`` is only needed for its ``plans`` rows. The
user, tenant-conversation and password-hash data must never come back.
"""
from __future__ import annotations

import re
from pathlib import Path

SEED = Path(__file__).resolve().parents[2] / "database" / "seed_data.sql"
_END = "\\" + "."


def _rows(table: str) -> list[str]:
    lines = SEED.read_text(encoding="utf-8").splitlines()
    start = next(
        i for i, ln in enumerate(lines)
        if re.match(rf"COPY public\.{table} \(", ln)
    )
    rows: list[str] = []
    for ln in lines[start + 1:]:
        if ln == _END:
            return rows
        rows.append(ln)
    raise AssertionError(f"COPY block for {table} is not terminated")


def test_user_profiles_block_has_no_rows():
    assert _rows("user_profiles") == []


def test_assistant_conversations_block_has_no_rows():
    assert _rows("assistant_conversations") == []


def test_tenants_block_has_no_rows():
    assert _rows("tenants") == []


def test_no_password_hash_markers():
    text = SEED.read_text(encoding="utf-8")
    markers = [
        "$" + "2a" + "$",
        "$" + "2b" + "$",
        "$" + "2y" + "$",
        "$" + "argon2",
    ]
    for marker in markers:
        assert marker not in text


def test_no_personal_mailbox_addresses():
    text = SEED.read_text(encoding="utf-8")
    assert "@" + "gmail" + ".com" not in text


def test_plans_data_is_kept():
    assert len(_rows("plans")) >= 4

"""Literal values written to call_events fit its varchar(30) columns.

Bug hunt 2026-10-10: persist_asterisk_transfer_provider_identity inserted
event_type 'transfer_provider_identity_rebound' (34 characters) into
call_events.event_type varchar(30). Planned against the production schema
lineage, Postgres raises StringDataRightTruncationError, so binding the
channel id Asterisk actually returned for a transfer leg always failed and the
transfer leg was never dialled. This guard keeps every literal in an
INSERT INTO call_events within the narrowest column (event_type, previous_state
and new_state are varchar(30)).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"


def test_every_literal_inserted_into_call_events_fits_varchar_30():
    too_long = []
    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and re.search(r"INSERT\s+INTO\s+call_events\b", node.value, re.I):
                too_long += [f"{path.name}:{node.lineno} {lit!r}"
                             for lit in re.findall(r"'([^']*)'", node.value) if len(lit) > 30]
    assert too_long == []

"""The freeze gate in ``scripts/report_frozen_batch.py`` must not read a
missing value as agreement.

Before this, every row's prompt identity was normalised to the placeholder
``"-"`` for display and the freeze check then counted distinct
``(version, hash)`` pairs. A batch where every call had NULL
``prompt_version`` / ``prompt_hash`` collapsed to the single pair
``("-", "-")`` and the script printed its strongest possible result —
``FROZEN: every call ran prompt - hash -`` — at exactly the moment it had no
evidence at all. Same class as a guard wired to a signal that never varies.

``scripts/`` is not an importable package, so the module is loaded by path.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "report_frozen_batch.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_report_frozen_batch", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["_report_frozen_batch"] = module
    spec.loader.exec_module(module)
    return module


rfb = _load()


def _row(version, hash_, call="aaaaaaaa"):
    """Shaped exactly like the dicts main() builds for the table: NULL columns
    have already been rendered as the '-' placeholder by then."""
    return {"call": call, "version": version, "hash": hash_}


NULL = _row("-", "-")
REAL_A = _row("lead_gen@3", "a1826168", call="11111111")
REAL_A2 = _row("lead_gen@3", "a1826168", call="22222222")
REAL_B = _row("lead_gen@4", "b7c31d90", call="33333333")


def test_all_null_is_not_frozen():
    frozen, lines = rfb.freeze_verdict([NULL, NULL, NULL])
    assert frozen is False
    text = "\n".join(lines)
    assert "NOT FROZEN" in text
    assert "NO EVIDENCE" in text
    assert "FROZEN: every call ran prompt - hash -" not in text


def test_mixed_real_and_null_is_not_frozen():
    frozen, lines = rfb.freeze_verdict([REAL_A, NULL, REAL_A2])
    assert frozen is False
    text = "\n".join(lines)
    assert "NOT FROZEN" in text
    assert "1/3" in text, text


def test_two_real_versions_is_not_frozen():
    frozen, lines = rfb.freeze_verdict([REAL_A, REAL_B])
    assert frozen is False
    text = "\n".join(lines)
    assert "NOT FROZEN" in text
    assert "lead_gen@3" in text and "lead_gen@4" in text


def test_one_consistent_real_version_is_frozen():
    frozen, lines = rfb.freeze_verdict([REAL_A, REAL_A2])
    assert frozen is True
    assert lines == ["FROZEN: every call ran prompt lead_gen@3 hash a1826168"]


def test_empty_batch_is_not_frozen():
    frozen, lines = rfb.freeze_verdict([])
    assert frozen is False
    assert "NO EVIDENCE" in "\n".join(lines)


def test_blank_and_none_count_as_missing():
    """main() renders NULL as '-', but be robust to a row that reaches the
    verdict with the raw None / empty string."""
    for bad in (None, "", "   "):
        frozen, lines = rfb.freeze_verdict([_row(bad, "a1826168"), REAL_A])
        assert frozen is False, bad
        assert "NO EVIDENCE" in "\n".join(lines), bad
        frozen, lines = rfb.freeze_verdict([_row("lead_gen@3", bad), REAL_A])
        assert frozen is False, bad


def test_exit_code_is_non_zero_unless_frozen():
    """The gate has to be usable from CI: `python report_frozen_batch.py ...`
    must exit non-zero when the batch is not proven frozen."""
    assert rfb.exit_code(False) != 0
    assert rfb.exit_code(True) == 0

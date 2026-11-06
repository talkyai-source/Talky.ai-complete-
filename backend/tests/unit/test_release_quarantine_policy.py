"""F16-02: read-only growth/age ratchet; all history fixtures are disposable."""
from __future__ import annotations

from datetime import date
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend/scripts/check_release_quarantine.py"
LIST = Path("backend/tests/unit/release_quarantine_20261008.txt")
A = "tests/unit/test_example.py::test_a"
B = "tests/unit/test_example.py::test_b"
TODAY = date(2026, 11, 6)


@pytest.fixture
def policy():
    assert SCRIPT.exists(), "F16-02 is missing the read-only quarantine policy gate"
    spec = importlib.util.spec_from_file_location("round3_quarantine_policy", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_comments_and_order_are_not_quarantine_identity(policy):
    assert policy.parse_entries(f"# owner note\n\n{B}\n{A}\n") == {A, B}


@pytest.mark.parametrize("text", [f"{A}\n{A}\n", "../test_escape.py\n", "tests/unit/not_a_test.txt\n", "/tmp/test_a.py\n"])
def test_ambiguous_or_unsafe_entries_refused(policy, text):
    with pytest.raises(ValueError):
        policy.parse_entries(text)


def test_exact_fourteen_day_boundary_is_allowed(policy):
    assert policy.evaluate({A}, {A}, {A: date(2026, 10, 23)}, TODAY) == []


def test_fifteen_day_entry_fails_without_changing_list(policy):
    violations = policy.evaluate({A}, {A}, {A: date(2026, 10, 22)}, TODAY)
    assert len(violations) == 1 and A in violations[0] and "15" in violations[0]


def test_same_size_replacement_still_counts_as_growth(policy):
    violations = policy.evaluate({B}, {A}, {B: TODAY}, TODAY)
    assert any(B in value and "new" in value.lower() for value in violations)


def test_removal_and_empty_list_are_allowed(policy):
    assert policy.evaluate({A}, {A, B}, {A: TODAY, B: TODAY}, TODAY) == []
    assert policy.evaluate(set(), {A}, {A: TODAY}, TODAY) == []


@pytest.mark.parametrize("first_seen", [{}, {A: date(2026, 11, 7)}])
def test_unknown_or_future_history_cannot_certify_age(policy, first_seen):
    assert policy.evaluate({A}, {A}, first_seen, TODAY)


def _git(repo, *args, stamp=None):
    env = dict(os.environ)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="QA Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
               GIT_COMMITTER_NAME="QA Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
    if stamp:
        env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    result = subprocess.run(["git", "-C", str(repo), *args], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(repo, text, stamp):
    target = repo / LIST
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(repo, "add", "--", LIST.as_posix())
    _git(repo, "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
         "commit", "-m", "synthetic quarantine history", stamp=stamp)
    return _git(repo, "rev-parse", "HEAD")


def test_real_history_rewrite_and_remove_readd_do_not_reset_age(policy, tmp_path):
    _git(tmp_path, "init")
    _commit(tmp_path, A + "\n", "2026-10-08T12:00:00+00:00")
    _commit(tmp_path, "# temporarily empty\n", "2026-11-01T12:00:00+00:00")
    _commit(tmp_path, "# fresh comment\n" + A + "\n", "2026-11-05T12:00:00+00:00")
    history = policy.history_first_seen(tmp_path, LIST)
    assert history[A] == date(2026, 10, 8)


def test_cli_stale_fixture_is_nonzero_and_read_only(policy, tmp_path, monkeypatch, capsys):
    _git(tmp_path, "init")
    base = _commit(tmp_path, A + "\n", "2026-10-08T12:00:00+00:00")
    before = (tmp_path / LIST).read_bytes()
    monkeypatch.setattr(policy, "_utc_today", lambda: TODAY)
    assert policy.main(["--repo", str(tmp_path), "--base-ref", base]) == 1
    output = capsys.readouterr()
    assert A in output.out + output.err
    assert (tmp_path / LIST).read_bytes() == before
    assert _git(tmp_path, "status", "--porcelain") == ""


def test_cli_unknown_base_ref_fails_closed(policy, tmp_path, capsys):
    _git(tmp_path, "init")
    _commit(tmp_path, A + "\n", "2026-11-05T12:00:00+00:00")
    assert policy.main(["--repo", str(tmp_path), "--base-ref", "missing-base"]) != 0
    assert "refused" in (capsys.readouterr().out).lower()


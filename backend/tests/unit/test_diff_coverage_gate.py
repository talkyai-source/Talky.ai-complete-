"""F16-02: changed backend executable lines, independent of overall coverage."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend/scripts/check_diff_coverage.py"
FILE = "backend/app/core/example.py"


@pytest.fixture
def gate():
    assert SCRIPT.exists(), "F16-02 is missing the changed-line coverage gate"
    spec = importlib.util.spec_from_file_location("round3_diff_coverage", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _xml(tmp_path, lines, *, filename="core/example.py", source="backend/app"):
    target = tmp_path / "coverage.xml"
    body = "".join(f'<line number="{number}" hits="{hits}"/>' for number, hits in lines)
    target.write_text(f'<coverage><sources><source>{source}</source></sources><packages><package><classes>'
                      f'<class filename="{filename}"><lines>{body}</lines></class>'
                      '</classes></package></packages></coverage>', encoding="utf-8")
    # Keep a real source fixture independent of the guard implementation. Blank
    # gaps are comments, while the explicitly measured lines are statements.
    source_path = Path(source) / filename
    if not source_path.is_absolute():
        source_path = tmp_path / source_path
    source_path.parent.mkdir(parents=True, exist_ok=True)
    measured = {number for number, _ in lines}
    source_path.write_text("\n".join("value = 1" if number in measured else "# comment only"
                                    for number in range(1, max(measured, default=0) + 1)) + "\n", encoding="utf-8")
    return target


def test_diff_parser_counts_new_sides_only_and_scopes_backend_app(gate):
    diff = """diff --git a/backend/app/core/example.py b/backend/app/core/example.py
--- a/backend/app/core/example.py
+++ b/backend/app/core/example.py
@@ -10,2 +10,3 @@
-old
+one
+two
+three
@@ -30 +33,0 @@
-deleted
diff --git a/backend/tests/test_a.py b/backend/tests/test_a.py
--- a/backend/tests/test_a.py
+++ b/backend/tests/test_a.py
@@ -0,0 +1 @@
+test
"""
    assert gate.parse_diff(diff) == {FILE: {10, 11, 12}}


def test_eighty_percent_passes_and_seventy_nine_does_not(gate, tmp_path):
    path = _xml(tmp_path, [(number, int(number <= 80)) for number in range(1, 101)])
    result = gate.check_coverage(path, {FILE: set(range(1, 101))}, repo_root=tmp_path)
    assert result["ok"] and result["covered"] == 80 and result["total"] == 100
    path = _xml(tmp_path, [(number, int(number <= 79)) for number in range(1, 101)])
    result = gate.check_coverage(path, {FILE: set(range(1, 101))}, repo_root=tmp_path)
    assert not result["ok"] and result["covered"] == 79 and result["total"] == 100


def test_uncovered_unchanged_lines_do_not_lower_diff_result(gate, tmp_path):
    path = _xml(tmp_path, [(1, 0), (2, 0), (3, 1)])
    result = gate.check_coverage(path, {FILE: {3}}, repo_root=tmp_path)
    assert result["ok"] and result["covered"] == result["total"] == 1


def test_new_file_absent_from_coverage_fails_closed(gate, tmp_path):
    path = _xml(tmp_path, [(1, 1)], filename="other.py")
    result = gate.check_coverage(path, {FILE: {1}}, repo_root=tmp_path)
    assert not result["ok"] and any(FILE in value for value in result["violations"])


def test_present_class_missing_new_executable_function_fails_closed(gate, tmp_path):
    path = _xml(tmp_path, [(1, 1)])
    (tmp_path / FILE).write_text(
        "existing = 1\n\n\ndef added(flag):\n    if flag:\n        return (\n"
        "            10\n            + 20\n        )\n    return 0\n", encoding="utf-8")
    result = gate.check_coverage(path, {FILE: set(range(4, 11))}, repo_root=tmp_path)
    assert not result["ok"], "A present XML class with missing new executable lines cannot pass0/0"
    assert any(FILE in value for value in result["violations"])


def test_actual_coverage_of_multiline_branches_passes_without_counting_comments(gate, tmp_path):
    import coverage
    import runpy

    source = tmp_path / FILE
    source.parent.mkdir(parents=True)
    source.write_text(
        "base = 1\n\n# changed comment\ndef choose(value):\n    if value:\n"
        "        return (\n            base\n            + 2\n        )\n"
        "    return 0\n\nchoose(True)\nchoose(False)\n", encoding="utf-8")
    measurement = coverage.Coverage(source=[str(tmp_path / "backend/app")],
                                    config_file=False, data_file=None, branch=True)
    measurement.start()
    try:
        runpy.run_path(str(source))
    finally:
        measurement.stop()
    report = tmp_path / "actual-coverage.xml"
    measurement.xml_report(outfile=str(report))
    result = gate.check_coverage(report, {FILE: set(range(1, 14))}, repo_root=tmp_path)
    assert result["ok"], result
    assert 0 < result["covered"] == result["total"] < 13


def test_comments_and_removed_only_diff_need_no_executable_coverage(gate, tmp_path):
    path = _xml(tmp_path, [(2, 1)])
    result = gate.check_coverage(path, {FILE: {1}}, repo_root=tmp_path)
    assert result["ok"] and result["total"] == 0
    result = gate.check_coverage(path, {}, repo_root=tmp_path)
    assert result["ok"] and result["total"] == 0


@pytest.mark.parametrize("filename,source", [("app/core/example.py", "backend"),
                                            ("backend/app/core/example.py", ".")])
def test_coverage_relative_filename_forms_are_unambiguous(gate, tmp_path, filename, source):
    path = _xml(tmp_path, [(5, 1)], filename=filename, source=source)
    result = gate.check_coverage(path, {FILE: {5}}, repo_root=tmp_path)
    assert result["ok"] and result["total"] == 1


def test_absolute_coverage_source_matches_current_checkout(gate, tmp_path):
    path = _xml(tmp_path, [(5, 1)], source=(tmp_path / "backend/app").as_posix())
    assert gate.check_coverage(path, {FILE: {5}}, repo_root=tmp_path)["ok"]


@pytest.mark.parametrize("content", ["broken xml", '<coverage/>',
    '<coverage><packages><class filename="../../escape.py"><lines><line number="1" hits="1"/></lines></class></packages></coverage>'])
def test_malformed_or_unmapped_coverage_cannot_certify(gate, tmp_path, content):
    path = tmp_path / "coverage.xml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        gate.check_coverage(path, {FILE: {1}}, repo_root=tmp_path)


def test_cli_missing_coverage_is_failure_not_a_waiver(gate, tmp_path, capsys):
    assert gate.main(["--repo", str(tmp_path), "--base-ref", "missing",
                      "--coverage", str(tmp_path / "missing.xml")]) != 0
    assert "refused" in capsys.readouterr().out.lower()


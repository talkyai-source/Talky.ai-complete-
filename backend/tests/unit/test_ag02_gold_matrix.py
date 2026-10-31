"""Dataset integrity and evaluator controls; aggregate target is reported separately."""

import json
from pathlib import Path
import subprocess
import sys

from scripts.evaluate_ag02_knowledge import (
    effective_query,
    evaluate_case,
    load_matrix,
    summarize,
)
from app.services.scripts.knowledge.retrieval import retrieve_pinned_knowledge


def test_gold_matrix_has_predeclared_nonzero_targets_and_no_fabricated_approval():
    matrix = load_matrix()
    assert len(matrix["cases"]) == 60
    assert len({c["id"] for c in matrix["cases"]}) == 60
    assert matrix["human_approval"] is None
    assert matrix["provider_answer_fidelity"]["status"] == "not_run"
    targets = matrix["targets_predeclared_before_first_matrix_run"]
    assert targets["answerable_top3_expected_source_recall_min"] == .9
    assert targets["answerable_sufficient_passage_rate_min"] == .85
    assert sum(c["answerable"] for c in matrix["cases"]) == 45


def test_all_abstention_cannot_pass_the_gold_retrieval_target():
    matrix = load_matrix()
    results = [evaluate_case(case, []) for case in matrix["cases"]]
    report = summarize(matrix, results, path="negative evaluator control")
    assert report["recall_at_3"] == 0
    assert report["sufficient_passage_rate"] == 0
    assert report["retrieval_targets_met"] is False


def test_current_price_evidence_keeps_conditions_and_version():
    matrix = load_matrix()
    case = matrix["cases"][0]
    hits = retrieve_pinned_knowledge(matrix["nodes"], effective_query(case), k=3)
    result = evaluate_case(case, hits)
    assert result["expected_source_found"] is True
    assert result["sufficient_passage"] is True
    assert result["passages"][0]["source_version"] == 3
    assert result["model_answer_fidelity"] is None


def test_embedded_instruction_is_never_a_submitted_evidence_passage():
    matrix = load_matrix()
    for case in matrix["cases"][-2:]:
        result = evaluate_case(case, retrieve_pinned_knowledge(
            matrix["nodes"], effective_query(case), k=3))
        assert all(p["node_id"] != "injected" for p in result["passages"])


def test_a_wrong_source_version_cannot_count_as_recall_or_sufficiency():
    matrix = load_matrix()
    case = matrix["cases"][0]
    hits = [{**matrix["nodes"][0], "source_version": 1, "coverage": 1}]
    result = evaluate_case(case, hits)
    assert result["expected_source_found"] is False
    assert result["sufficient_passage"] is False


def test_standalone_cli_saves_failed_gate_report_before_exiting_nonzero(tmp_path):
    output = tmp_path / "failed-gate.json"
    result = subprocess.run(
        [sys.executable, "-m", "scripts.evaluate_ag02_knowledge", "--output", str(output)],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["question_count"] == 60
    assert report["retrieval_targets_met"] is False
    assert report["targets_predeclared"]["answerable_sufficient_passage_rate_min"] == .85
    assert report["sufficient_passage_rate"] < .85
    assert len(report["cases"]) == 60

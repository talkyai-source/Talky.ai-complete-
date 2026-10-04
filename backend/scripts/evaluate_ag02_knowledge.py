"""Synthetic AG02 retrieval/evidence evaluation, never a model-quality claim.

Default run exercises the production pinned retriever. The PostgreSQL integration
fixture reuses ``evaluate_case`` with actual tenant-scoped SQL hits. No provider
requests, embeddings or model-generated judge answers are involved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from app.domain.services.voice_pipeline.kb_budget import (
    needs_previous_turn_context,
    prepare_knowledge_evidence,
)
from app.services.scripts.knowledge.retrieval import retrieve_pinned_knowledge

MATRIX_PATH = Path(__file__).resolve().parents[1] / "tests/fixtures/knowledge/ag02_gold.json"


def load_matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def effective_query(case: dict) -> str:
    query = case["query"]
    if case.get("previous_query") and needs_previous_turn_context(query):
        query = f"{query} {case['previous_query']}"
    return query


def evaluate_case(case: dict, hits: list[dict], *, aliases: dict | None = None) -> dict:
    aliases = aliases or {}
    query = effective_query(case)
    evidence = prepare_knowledge_evidence(hits, query)
    expected = set(case["expected_node_ids"])
    expected_version = case["expected_source_version"]
    hit_ids = [aliases.get(str(h["id"]), str(h["id"])) for h in hits]
    found = any(
        aliases.get(str(h["id"]), str(h["id"])) in expected
        and h.get("source_version", h.get("version")) == expected_version
        for h in hits
    )
    selected = [p for p in evidence["passages"]
                if aliases.get(p["node_id"], p["node_id"]) in expected
                and p.get("source_version", p.get("version")) == expected_version]
    source_text = "\n".join(p["text"] for p in selected).casefold()
    missing = [fragment for fragment in case["required_source_fragments"]
               if fragment.casefold() not in source_text]
    sufficient = bool(selected) and not missing and evidence["status"] == "matched"
    return {
        "id": case["id"], "category": case["category"], "query": query,
        "answerable": case["answerable"], "hit_node_ids": hit_ids,
        "expected_source_found": found if case["answerable"] else None,
        "sufficient_passage": sufficient if case["answerable"] else None,
        "missing_source_fragments": missing,
        "evidence_status": evidence["status"], "passages": evidence["passages"],
        "model_answer_fidelity": None, "human_review": None,
    }


def summarize(matrix: dict, results: list[dict], *, path: str) -> dict:
    answerable = [r for r in results if r["answerable"]]
    recall = sum(r["expected_source_found"] for r in answerable) / len(answerable)
    sufficient = sum(r["sufficient_passage"] for r in answerable) / len(answerable)
    targets = matrix["targets_predeclared_before_first_matrix_run"]
    return {
        "path": path, "matrix_sha256": hashlib.sha256(MATRIX_PATH.read_bytes()).hexdigest(),
        "corpus_mode": matrix["corpus_mode"],
        "metric_definitions": matrix["metric_definitions"],
        "question_count": len(results), "answerable_count": len(answerable),
        "targets_predeclared": targets,
        "recall_at_3": recall, "sufficient_passage_rate": sufficient,
        "retrieval_targets_met": recall >= targets["answerable_top3_expected_source_recall_min"]
        and sufficient >= targets["answerable_sufficient_passage_rate_min"],
        "provider_calls": 0, "model_answer_fidelity": "not evaluated",
        "human_approval": None,
        "limitations": [
            "Source presence and query coverage are not proof that a model answers faithfully.",
            "A matched related passage may still require abstention; human answer review remains required.",
            "Pinned evaluation accepts an already scoped admission snapshot; it does not prove SQL tenant/source selection.",
        ],
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    matrix = load_matrix()
    results = [evaluate_case(case, retrieve_pinned_knowledge(
        matrix["nodes"], effective_query(case), k=3)) for case in matrix["cases"]]
    report = summarize(matrix, results, path="production pinned retrieval + shared evidence")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, indent=2))
    print("Missed source:", [r["id"] for r in results if r["expected_source_found"] is False])
    print("Insufficient passage:", [r["id"] for r in results if r["sufficient_passage"] is False])
    return 0 if report["retrieval_targets_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Uninstalled, test-only paired-plural proposal; application files unchanged.

This prototype rebinds the actual source functions' lexical comparisons in
memory, keeping all score weights, stopwords, fuzzy logic, safety checks, source
text and provenance. It does not normalize arbitrary equal stems into matches.
"""
from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
import re
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--controls", action="store_true")
    parser.add_argument("--gold-output", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.wheel.resolve()))
    from snowballstemmer.english_stemmer import EnglishStemmer
    from app.services.scripts.knowledge import passages, retrieval
    from app.domain.services.voice_pipeline import kb_budget

    def matching_terms(query_terms, document_terms):
        """Exact words or direct regular plural AND established stem agreement.

        Fresh mutable stemmer per comparison. No global/caller-term cache;
        long/nonalphabetic tokens remain exact and are never removed.
        """
        query, document = set(query_terms), set(document_terms)
        matched = query & document
        stemmer = EnglishStemmer()
        for word in query - matched:
            if not re.fullmatch(r"[a-z]{2,64}", word):
                continue
            partners = {word + "s", word + "es"}
            if word.endswith("y"):
                partners.add(word[:-1] + "ies")
            if word.endswith("s"):
                partners.add(word[:-1])
            if word.endswith("es"):
                partners.add(word[:-2])
            if word.endswith("ies"):
                partners.add(word[:-3] + "y")
            for partner in partners & document:
                if (re.fullmatch(r"[a-z]{2,64}", partner)
                        and stemmer.stemWord(word) == stemmer.stemWord(partner)):
                    matched.add(word)
                    break
        return matched

    # Prototype the one comparison contract across all actual boundaries.
    # Fail loudly if production source moved instead of silently testing a fork.
    source = inspect.getsource(retrieval.retrieve_pinned_knowledge)
    substitutions = {
        "q_tokens & doc_tokens": "matching_terms(q_tokens, doc_tokens)",
        "q_tokens - doc_tokens": "q_tokens - matching_terms(q_tokens, doc_tokens)",
        "content_tokens & doc_tokens": "matching_terms(content_tokens, doc_tokens)",
    }
    for before, after in substitutions.items():
        assert source.count(before) == 1, before
        source = source.replace(before, after)
    globals_ = dict(vars(retrieval), matching_terms=matching_terms)
    exec(compile(source, "<paired-plural-pinned-prototype>", "exec"), globals_)
    candidate_retrieve = globals_["retrieve_pinned_knowledge"]
    source = inspect.getsource(passages.select_passage)
    assert source.count("terms.intersection(") == 3
    source = source.replace("terms.intersection(", "matching_terms(terms, ")
    globals_ = dict(vars(passages), matching_terms=matching_terms)
    exec(compile(source, "<paired-plural-passage-prototype>", "exec"), globals_)
    candidate_passage = globals_["select_passage"]
    original_retrieve = retrieval.retrieve_pinned_knowledge
    original_passage = kb_budget.select_passage

    if args.controls:
        import pytest

        class Overlay:
            def pytest_runtest_setup(self, item):
                item.module.retrieve_pinned_knowledge = candidate_retrieve
                kb_budget.select_passage = candidate_passage

            def pytest_runtest_teardown(self, item):
                item.module.retrieve_pinned_knowledge = original_retrieve
                kb_budget.select_passage = original_passage

        return pytest.main([str(Path(__file__).with_name("test_normalization_controls.py")),
                            "-q", "-o", "addopts="], plugins=[Overlay()])

    if args.gold_output:
        from scripts.evaluate_ag02_knowledge import (
            load_matrix, effective_query, evaluate_case, summarize,
        )
        matrix = load_matrix()
        baseline_results = [evaluate_case(case, original_retrieve(
            matrix["nodes"], effective_query(case), k=3)) for case in matrix["cases"]]
        baseline = summarize(matrix, baseline_results, path="unchanged production pinned baseline")
        kb_budget.select_passage = candidate_passage
        try:
            candidate_results = [evaluate_case(case, candidate_retrieve(
                matrix["nodes"], effective_query(case), k=3)) for case in matrix["cases"]]
        finally:
            kb_budget.select_passage = original_passage
        candidate = summarize(matrix, candidate_results,
                              path="paired-plural in-memory prototype; no application edit")
        changed = [{"id": before["id"], "baseline": before, "prototype": after}
                   for before, after in zip(baseline_results, candidate_results)
                   if before != after]
        report = {"scope": "test-only experiment; no model, SQL retrieval or caller-hearing proof",
                  "baseline": baseline, "prototype": candidate, "changed_cases": changed}
        args.gold_output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
        for name, value in [("baseline", baseline), ("prototype", candidate)]:
            print(name, {k: value[k] for k in ["matrix_sha256", "recall_at_3",
                  "sufficient_passage_rate", "retrieval_targets_met"]})
        return 0 if candidate["retrieval_targets_met"] else 1
    parser.error("Select --controls or --gold-output")


if __name__ == "__main__":
    raise SystemExit(main())

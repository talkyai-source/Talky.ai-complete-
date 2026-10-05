"""Wheel-only diagnostic: no application edits, installation or provider calls.

Run from backend with the original project interpreter. The explicit wheel path
is used only in this process. --candidate overlays coverage and passage lexical
comparison in memory; original retrieval selection/ranking remains unchanged.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import time
import tracemalloc
import zipfile


def cold(wheel, *, trace):
    sys.path.insert(0, str(wheel))
    if trace:
        tracemalloc.start()
    start = time.perf_counter()
    from snowballstemmer.english_stemmer import EnglishStemmer
    stemmer = EnglishStemmer()
    elapsed = (time.perf_counter() - start) * 1000
    current, peak = tracemalloc.get_traced_memory() if trace else (None, None)
    print(json.dumps({"import_and_instance_ms": elapsed,
                      "tracemalloc_enabled": trace,
                      "python_traced_current_bytes": current,
                      "python_traced_peak_bytes": peak,
                      "class": type(stemmer).__name__}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cold", action="store_true")
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--candidate", action="store_true")
    parser.add_argument("--postgres", action="store_true")
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    if args.cold:
        cold(wheel, trace=args.trace)
        return 0
    sys.path.insert(0, str(wheel))
    from snowballstemmer.english_stemmer import EnglishStemmer
    from app.services.scripts.knowledge import passages, retrieval
    from app.domain.services.voice_pipeline.kb_budget import prepare_knowledge_evidence

    # Prototype only. Nonalphabetic/long tokens retain exact identity AND remain
    # in the coverage denominator. No cross-request object or caller-term cache.
    def keys(tokens):
        stemmer = EnglishStemmer()
        return {stemmer.stemWord(t) if re.fullmatch(r"[a-z]{2,64}", t) else t
                for t in tokens}

    original_words = passages.content_words
    original_retrieve = retrieval.retrieve_pinned_knowledge

    def candidate_retrieve(nodes, query, *, k=2):
        hits = original_retrieve(nodes, query, k=k)
        q_tokens = set(original_words(query)) - retrieval._NUMBER_WORDS
        terms = {t for t in q_tokens if t not in retrieval._COVERAGE_STOPWORDS
                 and not t.isdigit()}
        for node in hits:
            blob = str(node.get("search_text") or " ".join(
                str(node.get(key) or "") for key in
                ("heading", "summary", "voice_answer", "content"))).lower()
            source_keys = keys(re.findall(r"[a-z0-9]+", blob))
            # Preserve original distinct-word denominator, even if two terms
            # happen to share a stem. Stemming never drops an unknown word.
            if terms:
                node["coverage"] = sum(bool(keys([t]) & source_keys)
                                       for t in terms) / len(terms)
        return hits

    def candidate_words(text):
        return list(keys(original_words(text)))

    if args.candidate:
        import pytest

        class Overlay:
            def pytest_runtest_setup(self, item):
                item.module.retrieve_pinned_knowledge = candidate_retrieve
                passages.content_words = candidate_words

            def pytest_runtest_teardown(self, item):
                item.module.retrieve_pinned_knowledge = original_retrieve
                passages.content_words = original_words

        return pytest.main([str(Path(__file__).with_name("test_normalization_controls.py")),
                            "-q", "-o", "addopts="], plugins=[Overlay()])

    pairs = [("account", "accounts"), ("fee", "fees"), ("booking", "bookings"),
             ("delivery", "deliveries"), ("batch", "batches"), ("invoice", "invoices"),
             ("fee", "free"), ("new", "news"), ("policy", "police"),
             ("form", "from"), ("business", "bus"), ("status", "statue"),
             ("series", "species"), ("model500", "model500s"),
             ("universe", "university"), ("organ", "organization")]
    tokens = sorted({t for pair in pairs for t in pair} | {"not", "no", "the", "and"})
    with zipfile.ZipFile(wheel) as z:
        meta_name = next(n for n in z.namelist() if n.endswith("/METADATA"))
        meta = z.read(meta_name).decode()
        footprint = {"wheel_bytes": wheel.stat().st_size,
                     "expanded_bytes": sum(i.file_size for i in z.infolist()),
                     "archive_members": len(z.infolist()),
                     "requires_dist": [line for line in meta.splitlines()
                                       if line.startswith("Requires-Dist:")],
                     "license": [line for line in meta.splitlines()
                                 if line.startswith("License:")]}
    imports = [json.loads(subprocess.check_output(
        [sys.executable, str(Path(__file__).resolve()), "--wheel", str(wheel), "--cold"]
        + (["--trace"] if trace else []), text=True, timeout=30))
        for trace in (False, True) for _ in range(3)]
    sample = ("Deliveries take five days This excludes remote islands "
              "Invoices include accounts booking fees and payment information ").lower().split()
    timings = []
    for _ in range(7):
        start = time.perf_counter()
        keys(sample * 100)
        timings.append((time.perf_counter() - start) * 1000)
    long_token = "a" * 100_000
    start = time.perf_counter()
    long_result = keys([long_token])
    long_ms = (time.perf_counter() - start) * 1000
    result = {
        "scope": "wheel-only proposal assessment; no application change or model/voice proof",
        "candidate": "snowballstemmer==2.2.0 direct EnglishStemmer; fresh per helper call",
        "python": sys.version,
        "footprint": footprint,
        "cold_import_samples": imports,
        "runtime": {"input_tokens": len(sample) * 100, "runs_ms": timings,
                    "median_ms": statistics.median(timings),
                    "long_token_length": len(long_token), "long_token_ms": long_ms,
                    "long_token_retained_exactly": long_result == {long_token}},
        "token_comparisons": [{"word": t, "snowball": EnglishStemmer().stemWord(t),
                               "prototype_key": next(iter(keys([t])))} for t in tokens],
        "boundary_collisions": [],
    }
    for query, source in [
        ("Universe", "University tuition costs 100 units."),
        ("Organ", "Organization membership costs 50 units."),
        ("Universe courses", "University course fees are 100 units."),
        ("Organ memberships", "Organization membership costs 50 units."),
        ("Universes", "Universities are listed here."),
    ]:
        node = {"id": "diagnostic", "source_id": "synthetic", "source_version": 1,
                "heading": "Reference", "content": source}
        baseline = prepare_knowledge_evidence(original_retrieve([node], query), query)
        passages.content_words = candidate_words
        try:
            candidate = prepare_knowledge_evidence(candidate_retrieve([node], query), query)
        finally:
            passages.content_words = original_words
        result["boundary_collisions"].append({"query": query, "source": source,
                                              "baseline": baseline, "prototype": candidate})

    if args.postgres:
        async def compare():
            import asyncpg
            # Fixed, authorized disposable loopback target; never load .env.
            conn = await asyncpg.connect(
                "postgresql://talky@127.0.0.1:55434/cp04_acceptance_test", timeout=10)
            try:
                async with conn.transaction(readonly=True):
                    version = await conn.fetchval("SHOW server_version")
                    rows = await conn.fetch(
                        "SELECT word, ts_lexize('english_stem', word) AS lexemes "
                        "FROM unnest($1::text[]) AS word", tokens, timeout=10)
                return {"server_version": version, "transaction_read_only": True,
                        "rows": [dict(row) for row in rows]}
            finally:
                await conn.close()
        result["postgres"] = asyncio.run(compare())
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

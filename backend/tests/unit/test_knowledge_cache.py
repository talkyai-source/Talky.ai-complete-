"""Case 3 fix — the KB retrieval TTL cache that collapses concurrent
same-topic queries. Pure, clock-injected (no real time)."""
from __future__ import annotations

from app.services.scripts.knowledge import cache


def setup_function():
    cache.clear()


def test_miss_then_hit_within_ttl():
    rows = [{"id": "n1", "heading": "Rates"}]
    assert cache.get("t1", "c1", "what are your rates", now=100.0) is None
    cache.put("t1", "c1", "what are your rates", rows, now=100.0)
    hit = cache.get("t1", "c1", "what are your rates", now=110.0)
    assert hit == rows


def test_normalized_query_shares_key():
    rows = [{"id": "n1"}]
    cache.put("t1", "c1", "What ARE your Rates??", rows, now=0.0)
    # Different punctuation/case, same normalized query → hit.
    assert cache.get("t1", "c1", "what are your rates", now=1.0) == rows


def test_expiry():
    cache.put("t1", "c1", "q", [{"id": "n1"}], now=0.0, ttl_seconds=45.0)
    assert cache.get("t1", "c1", "q", now=44.0) is not None
    assert cache.get("t1", "c1", "q", now=46.0) is None  # expired


def test_tenant_and_campaign_isolation():
    cache.put("t1", "c1", "q", [{"id": "a"}], now=0.0)
    assert cache.get("t2", "c1", "q", now=1.0) is None  # other tenant
    assert cache.get("t1", "c2", "q", now=1.0) is None  # other campaign


def test_empty_results_not_cached():
    cache.put("t1", "c1", "q", [], now=0.0)
    assert cache.get("t1", "c1", "q", now=1.0) is None  # miss re-computes


def test_returned_list_is_a_copy():
    rows = [{"id": "n1", "heading": "X"}]
    cache.put("t1", "c1", "q", rows, now=0.0)
    got = cache.get("t1", "c1", "q", now=1.0)
    got[0]["heading"] = "MUTATED"
    # Cache must be unaffected by a caller mutating the returned list.
    again = cache.get("t1", "c1", "q", now=2.0)
    assert again[0]["heading"] == "X"


def test_invalidate_campaign():
    cache.put("t1", "c1", "q1", [{"id": "a"}], now=0.0)
    cache.put("t1", "c1", "q2", [{"id": "b"}], now=0.0)
    cache.put("t1", "c2", "q1", [{"id": "c"}], now=0.0)
    removed = cache.invalidate_campaign("t1", "c1")
    assert removed == 2
    assert cache.get("t1", "c1", "q1", now=1.0) is None
    assert cache.get("t1", "c2", "q1", now=1.0) is not None  # untouched


def test_bounded_eviction():
    for i in range(cache._MAX_ENTRIES + 50):
        cache.put("t1", "c1", f"query number {i}", [{"id": i}], now=0.0)
    assert cache.stats()["entries"] <= cache._MAX_ENTRIES

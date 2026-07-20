"""Tiny in-process TTL cache for campaign-knowledge retrieval (Case 3).

The retrieval query sits on the first-token critical path, and N concurrent
calls asking the SAME popular question run N identical heavy FTS+trigram
queries — the "degrades under concurrent same-topic load" symptom. A short-TTL
cache keyed on (tenant, campaign, normalized query) collapses those N queries to
~1 for the TTL window, so a spike of same-topic calls stops amplifying DB load.

Design notes:
 * TTL is deliberately short (default 45 s) — knowledge is edited rarely, but a
   stale answer for even a minute is undesirable on a live sales call, and the
   value here is absorbing a *burst*, not long-term memoization.
 * Bounded size with cheap FIFO-ish eviction (dict insertion order) so a
   long-running process can't grow this without bound.
 * Only NON-EMPTY hit lists are cached. A miss ("no knowledge for this query")
   is cheap to recompute and caching it would pin a wrong answer if the node was
   just added; recomputing keeps freshly-authored knowledge reachable promptly.
 * Process-global, single-worker (talky-api runs uvicorn --workers 1, the same
   invariant proposals.py relies on). No cross-process coherence needed.
 * `now` is injected by callers (monotonic seconds) so this stays pure and
   testable — no time import here, matching the workflow/no-clock constraints.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

_TTL_SECONDS = 45.0
_MAX_ENTRIES = 512

# key -> (expires_at_monotonic, rows)
_STORE: "OrderedDict[Tuple[str, str, str], Tuple[float, List[dict]]]" = OrderedDict()


def _norm_query(query: str) -> str:
    """Normalize so trivially-different phrasings of the same ask share a key:
    casefold + whitespace/punctuation collapse."""
    q = (query or "").casefold()
    q = re.sub(r"[^\w\s]", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def _key(tenant_id: str, campaign_id: str, query: str) -> Tuple[str, str, str]:
    return (str(tenant_id), str(campaign_id), _norm_query(query))


def get(
    tenant_id: str, campaign_id: str, query: str, *, now: float
) -> Optional[List[dict]]:
    """Return cached rows for this query if present and unexpired, else None.
    Expired/!present entries return None (the caller then hits the DB)."""
    k = _key(tenant_id, campaign_id, query)
    entry = _STORE.get(k)
    if entry is None:
        return None
    expires_at, rows = entry
    if now >= expires_at:
        _STORE.pop(k, None)
        return None
    # Refresh recency so hot keys survive eviction.
    _STORE.move_to_end(k)
    # Return a shallow copy so a caller mutating its list can't corrupt the cache.
    return [dict(r) for r in rows]


def put(
    tenant_id: str,
    campaign_id: str,
    query: str,
    rows: List[dict],
    *,
    now: float,
    ttl_seconds: float = _TTL_SECONDS,
) -> None:
    """Cache a NON-EMPTY hit list under (tenant, campaign, normalized query).
    Empty results are intentionally not cached (see module docstring)."""
    if not rows:
        return
    k = _key(tenant_id, campaign_id, query)
    _STORE[k] = (now + ttl_seconds, [dict(r) for r in rows])
    _STORE.move_to_end(k)
    while len(_STORE) > _MAX_ENTRIES:
        _STORE.popitem(last=False)  # evict oldest


def clear() -> None:
    """Drop everything — for tests and after a knowledge edit if a caller wants
    to force-refresh."""
    _STORE.clear()


def invalidate_campaign(tenant_id: str, campaign_id: str) -> int:
    """Drop all cached queries for one campaign (call after a knowledge-node
    edit so the next lookup re-reads). Returns how many entries were removed."""
    prefix = (str(tenant_id), str(campaign_id))
    doomed = [k for k in _STORE if k[0] == prefix[0] and k[1] == prefix[1]]
    for k in doomed:
        _STORE.pop(k, None)
    return len(doomed)


def stats() -> Dict[str, Any]:
    return {"entries": len(_STORE), "max_entries": _MAX_ENTRIES, "ttl_seconds": _TTL_SECONDS}

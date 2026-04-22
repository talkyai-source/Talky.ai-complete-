# `campaign_transcript_query.py`

**Module:** `app.services.scripts.campaign_transcript_query`
**Tests:** `backend/tests/unit/test_campaign_transcript_query.py`
**Line budget:** 600 — currently ≈ 115 lines.

## Purpose

Paginated read of a campaign's calls plus their transcripts, used by the
Script Card UI (`GET /campaigns/{id}/calls`). Runs one `SELECT` and one
`COUNT(*)` — no N+1, no secondary lookups.

## Public API

### `async fetch_campaign_transcripts(*, pool, tenant_id, campaign_id, page=1, page_size=20) -> dict`

Returns:

```python
{
  "items": [
    {
      "call_id": str,
      "to_number": str,
      "started_at": str,      # ISO-8601
      "duration_seconds": int | None,
      "outcome": str | None,
      "turns": [ { "role": "user"|"assistant", "content": str, "timestamp": str } ]
    }
  ],
  "page": int,
  "page_size": int,
  "total": int,
}
```

Raises `ValueError` for non-UUID `campaign_id` or `tenant_id` — the
FastAPI layer maps that to HTTP 400.

### `_coerce_turns(raw) -> list[dict]`

Internal: normalises `transcript_json` to `list[dict]`. asyncpg can
return JSONB as either a parsed object or a JSON string depending on
codec configuration; this shim handles both and returns `[]` on garbage.
Covered by `test_coerce_turns_*`.

## SQL

```sql
SELECT c.id,
       c.phone_number,
       c.created_at,
       c.duration_seconds,
       c.outcome,
       c.transcript_json
FROM calls c
WHERE c.tenant_id = $1
  AND c.campaign_id = $2
ORDER BY c.created_at DESC
LIMIT $3 OFFSET $4
```

Plus:

```sql
SELECT COUNT(*) FROM calls c
WHERE c.tenant_id = $1 AND c.campaign_id = $2
```

## Guarantees

- Tenant scoping is enforced at the SQL layer — even if a caller somehow
  bypasses the endpoint's campaign-ownership check, the query cannot
  read another tenant's data.
- Partial STT frames never reach the response — `format_transcript_turns`
  filters them out.
- `page` and `page_size` are clamped at `max(x, 1)` inside the helper;
  the caller should still clamp to a sane `page_size` upper bound (the
  endpoint uses FastAPI's `Query(..., le=100)`).

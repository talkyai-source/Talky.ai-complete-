# `backfill_call_summaries.py`

**Module:** `app.services.scripts.backfill_call_summaries`
**Reuses:** `app.domain.services.call_summary.store.generate_and_store` (the same
idempotent path used by the call-end hook and the lazy `GET /calls/{id}/summary`).
**Line budget:** 600 — currently ≈ 282 lines.

## Purpose

One-shot, idempotent, **rate-limit-aware** backfill of AI call summaries for
*existing* calls. New calls are summarized automatically at call-end, and any
call opened in the UI is summarized lazily — the only gap is existing,
never-opened calls, whose list row shows no headline. This closes that gap.

It is the production-correct alternative to ad-hoc psql/python on prod:
idempotent, re-runnable, bounded, and paced for Groq's limits.

## Why paced

Groq free tier is **30 RPM / 6,000 TPM**; one summary is ~2k tokens, so a naive
loop hits the TPM cap after ~2–3 calls and 429s. A 429 is fail-soft in the
summarizer (returns the `"Summary unavailable"` sentinel), so the script:

1. **Paces** requests `--pace` seconds apart (default 20) to stay under TPM.
2. On a transient failure that still slips through, waits `--retry-backoff`
   seconds (default 65 — clears the per-minute window) and retries **once**.
3. Relies on the store-layer guard that **refuses to persist the sentinel**, so
   a failed attempt never poisons the row — it stays a candidate for re-runs.

## CLI

```
--limit N          Max calls (most-recent-first). Default: all.
--days N           Only calls created within the last N days. Default: all.
--tenant UUID      Restrict to one tenant. Default: all tenants.
--pace SECS        Seconds between calls (TPM guard). Default: 20.
--retry-backoff S  Wait before the single retry after a transient fail. Default: 65.
--force            Re-summarize EVERY transcript-bearing call (e.g. after a schema change).
--dry-run          List candidates and exit — no API calls, no writes.
```

Exit code: `0` on success/clean, `1` if any call ended `failed`, `2` on missing
`DATABASE_URL` / `GROQ_API_KEY`.

## Usage

```bash
cd /opt/talky/backend && source venv/bin/activate
export $(grep -E '^(DATABASE_URL|GROQ_API_KEY)=' .env | xargs)

python -m app.services.scripts.backfill_call_summaries --dry-run        # preview
python -m app.services.scripts.backfill_call_summaries --pace 20         # run
```

## Candidate selection

A candidate has a non-empty transcript AND (without `--force`) either no
`summary_json` yet **or** a poisoned `summary_json->>'headline' = 'Summary
unavailable'` (re-attempted with `force=True` so idempotency doesn't return the
stale sentinel). Ordered most-recent-first.

## Guarantees

- **Tenant-correct writes:** the candidate `SELECT` is cross-tenant (runs with
  `app.bypass_rls`), but every write goes through `generate_and_store` →
  `acquire_with_tenant`, so RLS scoping on the `calls` UPDATE is preserved.
- **Idempotent:** a call that already has a real summary is returned untouched
  (no re-summarize) unless `--force`.
- **Safe to interrupt / re-run:** progress is per-row and durable; a second run
  only picks up what's still missing or poisoned.

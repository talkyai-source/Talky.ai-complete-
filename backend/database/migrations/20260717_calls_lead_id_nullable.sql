-- 2026-07-17: make calls.lead_id nullable.
--
-- Root-cause fix for "non-dialer recordings never get a DB row": the stub
-- `calls` row inserted for manual/PBX-originated calls that never went
-- through the dialer (app/domain/services/telephony/recording.py, the
-- "stub calls row" INSERT) has no lead to attach — those calls are placed
-- directly from the PBX, not from a `leads` row. The column was declared
-- `NOT NULL REFERENCES leads(id) ON DELETE CASCADE`, so that INSERT could
-- never succeed; every non-dialer call fell back to disk-only storage
-- (logged as stub_calls_row_insert_failed) and never appeared in the
-- recordings UI.
--
-- A manual/test call legitimately has no lead — NULL is the correct model,
-- not a placeholder lead row. The FK + ON DELETE CASCADE stay as-is: a
-- nullable FK column with ON DELETE CASCADE is fine, NULL rows are simply
-- never referenced by the CASCADE.
--
-- Every code path that reads calls.lead_id was audited (see the recording
-- correctness fix commit for the full file list) and already treats it as
-- optional (`if row["lead_id"] else None`, LEFT JOINs, `if lead_id:` guards
-- before use) — no code assumed calls.lead_id is always present.
--
-- Additive + idempotent. Applied manually via psql on prod — there is NO
-- auto-runner (mirrors the rest of this directory).

ALTER TABLE calls ALTER COLUMN lead_id DROP NOT NULL;

-- Same reasoning for campaign_id: the stub insert passes NULL campaign when
-- the session campaign is the "telephony" placeholder (standalone PBX test
-- calls, see recording.py — `if session_campaign != "telephony" else None`),
-- so with lead_id fixed the stub would just fail on campaign_id instead.
-- All calls.campaign_id readers are already null-guarded
-- (`if row["campaign_id"] else None`) and campaign joins are LEFT JOINs.
ALTER TABLE calls ALTER COLUMN campaign_id DROP NOT NULL;

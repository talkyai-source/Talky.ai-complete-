-- Prevent duplicate LIVE contacts: at most one non-deleted lead per
-- (campaign_id, phone_number). Soft-deleted rows are excluded so a phone can be
-- deleted and later revived in place (the app revives the deleted row instead of
-- inserting a new one — see add_contact + upload_campaign_contacts).
-- Additive + idempotent. Applied manually via psql on prod (no auto-runner).
--
-- Safe to create: verified there are currently no live duplicates
--   SELECT campaign_id, phone_number, count(*) FROM leads
--   WHERE status <> 'deleted' GROUP BY 1,2 HAVING count(*) > 1;  -- 0 rows
CREATE UNIQUE INDEX IF NOT EXISTS uq_leads_campaign_phone_active
    ON leads (campaign_id, phone_number)
    WHERE status <> 'deleted';

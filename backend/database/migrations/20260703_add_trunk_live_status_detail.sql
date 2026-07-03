-- Human-readable reason behind live_registration_status (e.g. "403 Forbidden").
-- Captured by the trunk-status updater from the Asterisk registration log so the
-- card can show the REAL backend error, not just a red "Rejected".
ALTER TABLE tenant_sip_trunks
    ADD COLUMN IF NOT EXISTS live_status_detail TEXT;

COMMENT ON COLUMN tenant_sip_trunks.live_status_detail IS
    'Reason for live_registration_status, e.g. "403 Forbidden" — parsed from the Asterisk registration log by the trunk-status updater.';

-- Live SIP-trunk registration status (real Asterisk state, not the frozen Test
-- snapshot). A server-side updater (talky-trunk-status.timer) reads
-- `pjsip show registrations` every ~15s and writes the real status here; the
-- Settings trunk card renders this, auto-refreshing. No dummy data.
ALTER TABLE tenant_sip_trunks
    ADD COLUMN IF NOT EXISTS live_registration_status TEXT,
    ADD COLUMN IF NOT EXISTS live_status_checked_at   TIMESTAMPTZ;

COMMENT ON COLUMN tenant_sip_trunks.live_registration_status IS
    'Real-time Asterisk registration state: registered | rejected | unregistered | inactive | unknown. Written by the trunk-status updater from `pjsip show registrations`.';
COMMENT ON COLUMN tenant_sip_trunks.live_status_checked_at IS
    'When the updater last refreshed live_registration_status (UTC).';

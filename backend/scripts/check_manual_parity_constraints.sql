-- Owner-only dry run before 0087. Counts only; no row identifiers or values.
-- Use an authorized maintenance role able to see every row. row_security=off
-- makes restricted-role execution fail rather than returning filtered zeroes.
\set ON_ERROR_STOP on
BEGIN READ ONLY;
SET LOCAL statement_timeout = '60s';
SET LOCAL lock_timeout = '5s';
SET LOCAL app.bypass_rls = 'true';
SET LOCAL row_security = off;
DO $preflight$
DECLARE invalid_count bigint;
BEGIN
    IF to_regclass('public.refresh_tokens') IS NULL THEN
        RAISE EXCEPTION 'Required refresh_tokens table is missing';
    END IF;
    SELECT count(*) INTO invalid_count FROM public.refresh_tokens
    WHERE revoked_reason IS NOT NULL AND revoked_reason NOT IN
        ('rotated','reuse_detected','logout','admin','expired','password_change',
         'password_reset','mfa_disabled','expired_with_subsequent_use');
    RAISE NOTICE 'refresh reason rejected rows: %', invalid_count;
    IF EXISTS (SELECT 1 FROM pg_attribute
        WHERE attrelid=to_regclass('public.tenants')
          AND attname='active_telephony_provider' AND NOT attisdropped) THEN
        EXECUTE 'SELECT count(*) FROM public.tenants WHERE active_telephony_provider IS NOT NULL
                 AND active_telephony_provider NOT IN (''twilio'',''vonage'',''sip'',''none'')'
            INTO invalid_count;
        RAISE NOTICE 'telephony provider rejected rows: %', invalid_count;
    ELSE
        RAISE NOTICE 'telephony provider column absent: 0087 adds the empty column';
    END IF;
    IF to_regclass('public.tenant_ai_credentials') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM (SELECT 1 FROM public.tenant_ai_credentials
                 WHERE status=''active'' GROUP BY tenant_id,provider,credential_kind
                 HAVING count(*)>1) AS duplicate_groups' INTO invalid_count;
        RAISE NOTICE 'duplicate active AI credential groups: %', invalid_count;
    ELSE
        RAISE NOTICE 'AI credentials absent: 0087 creates the empty table';
    END IF;
    IF to_regclass('public.leads') IS NULL THEN
        RAISE EXCEPTION 'Required leads table is missing';
    END IF;
    SELECT count(*) INTO invalid_count FROM (
        SELECT 1 FROM public.leads WHERE status<>'deleted'
          AND campaign_id IS NOT NULL AND phone_number IS NOT NULL
        GROUP BY campaign_id,phone_number HAVING count(*)>1
    ) AS duplicate_groups;
    RAISE NOTICE 'duplicate live campaign/phone groups: %', invalid_count;
END $preflight$;
ROLLBACK;

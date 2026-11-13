"""Converge missing bootstrap/manual objects without replacing adopted data.

Late creators use the post-0065/0067 contracts. Existing compatible objects are
adopted without replacement; incompatible shapes abort the whole transaction.
Schema rollback is forward-only because objects may predate Alembic. Keep this
additive schema when rolling back application code; never drop adopted history.
"""
from alembic import op


revision = "0087_bootstrap_manual_parity"
down_revision = "0086_call_workflows"
branch_labels = None
depends_on = None

SCOPE = """COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)
    OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"""


def _table(name, body, *, tenant=False, comments="", foreign_keys=()):
    """Fixed source definitions; PostgreSQL compares parsed catalog metadata."""
    # PostgreSQL forbids TEMP -> permanent FKs. Declare each FK once here:
    # the real table receives its DDL, while catalog tuples independently
    # validate references/actions. The TEMP parser still checks every other
    # column/default/PK/CHECK definition without creating permanent scratch data.
    fk_ddl = ""
    fk_rows = []
    for column, parent, parent_column, delete_action in foreign_keys:
        delete_code = {"NO ACTION": "a", "CASCADE": "c"}[delete_action]
        fk_ddl += (f", FOREIGN KEY ({column}) REFERENCES public.{parent}({parent_column})"
                   f" ON DELETE {delete_action}")
        fk_rows.append(
            f"SELECT ARRAY['{column}']::text[], to_regclass('public.{parent}')::oid, "
            f"ARRAY['{parent_column}']::text[], '{delete_code}'::text"
        )
    expected_fks = " UNION ALL ".join(fk_rows) or (
        "SELECT NULL::text[], NULL::oid, NULL::text[], NULL::text WHERE FALSE"
    )
    security = f"""
        CREATE POLICY expected_scope ON qa_0087_reference FOR ALL USING ({SCOPE}) WITH CHECK ({SCOPE});
        IF created THEN
            ALTER TABLE public.{name} ENABLE ROW LEVEL SECURITY;
            ALTER TABLE public.{name} FORCE ROW LEVEL SECURITY;
            CREATE POLICY {name}_tenant_isolation ON public.{name}
                FOR ALL USING ({SCOPE}) WITH CHECK ({SCOPE});
        ELSIF NOT (SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=target)
           OR (SELECT count(*) FROM pg_policy WHERE polrelid=target) <> 1
           OR NOT EXISTS (
                SELECT 1 FROM pg_policy actual JOIN pg_policy expected ON expected.polrelid=reference
                WHERE actual.polrelid=target AND actual.polname='{name}_tenant_isolation'
                  AND actual.polcmd=expected.polcmd AND actual.polpermissive=expected.polpermissive
                  AND actual.polroles=expected.polroles
                  AND pg_get_expr(actual.polqual,target) IS NOT DISTINCT FROM pg_get_expr(expected.polqual,reference)
                  AND pg_get_expr(actual.polwithcheck,target) IS NOT DISTINCT FROM pg_get_expr(expected.polwithcheck,reference)
           ) THEN
            RAISE EXCEPTION '0087 refuses incompatible tenant security on {name}';
        END IF;
    """ if tenant else f"""
        IF (SELECT relrowsecurity OR relforcerowsecurity FROM pg_class WHERE oid=target)
           OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid=target) THEN
            RAISE EXCEPTION '0087 refuses unknown platform security on {name}';
        END IF;
    """
    return f"""DO $repair$
    DECLARE target oid; reference oid; created boolean;
    BEGIN
        target := to_regclass('public.{name}');
        created := target IS NULL;
        CREATE TABLE IF NOT EXISTS public.{name} ({body}{fk_ddl});
        LOCK TABLE public.{name} IN ACCESS EXCLUSIVE MODE;
        target := to_regclass('public.{name}');
        IF (SELECT relkind FROM pg_class WHERE oid=target) <> 'r' THEN
            RAISE EXCEPTION '0087 refuses incompatible relation {name}';
        END IF;
        CREATE TEMPORARY TABLE qa_0087_reference ({body}) ON COMMIT DROP;
        reference := to_regclass('pg_temp.qa_0087_reference');
        IF EXISTS (
            WITH shape AS (
                SELECT a.attrelid, a.attname, format_type(a.atttypid,a.atttypmod) AS type,
                       a.attnotnull, a.attidentity, a.attgenerated,
                       CASE WHEN pg_get_serial_sequence(a.attrelid::regclass::text,a.attname) IS NOT NULL
                            THEN '<owned-sequence>' ELSE pg_get_expr(d.adbin,d.adrelid) END AS expression
                FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE a.attrelid IN (target,reference) AND a.attnum>0 AND NOT a.attisdropped
            ), actual AS (SELECT attname,type,attnotnull,attidentity,attgenerated,expression FROM shape WHERE attrelid=target),
               expected AS (SELECT attname,type,attnotnull,attidentity,attgenerated,expression FROM shape WHERE attrelid=reference)
            (SELECT * FROM actual EXCEPT SELECT * FROM expected)
            UNION ALL (SELECT * FROM expected EXCEPT SELECT * FROM actual)
        ) OR EXISTS (
            WITH actual AS (SELECT contype,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid=target AND contype<>'f'),
                 expected AS (SELECT contype,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid=reference AND contype<>'f')
            (SELECT * FROM actual EXCEPT ALL SELECT * FROM expected)
            UNION ALL (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual)
        ) OR EXISTS (
            WITH actual AS (
                SELECT ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(num,pos)
                             JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.num ORDER BY k.pos),
                       c.confrelid,
                       ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(num,pos)
                             JOIN pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.num ORDER BY k.pos),
                       c.confdeltype::text
                FROM pg_constraint c WHERE c.conrelid=target AND c.contype='f'
            ), expected AS ({expected_fks})
            (SELECT * FROM actual EXCEPT ALL SELECT * FROM expected)
            UNION ALL (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual)
        ) OR EXISTS (
            SELECT 1 FROM pg_constraint WHERE conrelid=target AND contype='f'
                AND (confupdtype<>'a' OR confmatchtype<>'s' OR condeferrable OR condeferred OR NOT convalidated)
        ) THEN
            RAISE EXCEPTION '0087 refuses incompatible columns or constraints on {name}';
        END IF;
        {security}
        IF created THEN
            {comments or 'NULL;'}
        END IF;
        DROP TABLE pg_temp.qa_0087_reference;
    END $repair$"""


def _index(table, name, columns, *, unique=False, predicate=""):
    modifier = "UNIQUE " if unique else ""
    where = f" WHERE {predicate}" if predicate else ""
    return f"""DO $index$
    DECLARE actual oid; expected oid; target oid := to_regclass('public.{table}');
    BEGIN
        IF target IS NULL THEN RAISE EXCEPTION '0087 requires {table} before its index'; END IF;
        CREATE TEMPORARY TABLE qa_0087_index_reference (LIKE public.{table}) ON COMMIT DROP;
        CREATE {modifier}INDEX qa_0087_expected_index ON qa_0087_index_reference ({columns}){where};
        expected := to_regclass('pg_temp.qa_0087_expected_index');
        CREATE {modifier}INDEX IF NOT EXISTS {name} ON public.{table} ({columns}){where};
        actual := to_regclass('public.{name}');
        IF NOT EXISTS (
            SELECT 1 FROM pg_index a JOIN pg_index e ON e.indexrelid=expected
            JOIN pg_class ac ON ac.oid=a.indexrelid JOIN pg_class ec ON ec.oid=e.indexrelid
            WHERE a.indexrelid=actual AND a.indrelid=target AND a.indisvalid AND a.indisready
              AND a.indisunique=e.indisunique AND ac.relam=ec.relam
              AND a.indnatts=e.indnatts AND a.indnkeyatts=e.indnkeyatts
              AND a.indkey::text=e.indkey::text AND a.indcollation::text=e.indcollation::text
              AND a.indclass::text=e.indclass::text AND a.indoption::text=e.indoption::text
              AND pg_get_expr(a.indexprs,a.indrelid) IS NOT DISTINCT FROM pg_get_expr(e.indexprs,e.indrelid)
              AND pg_get_expr(a.indpred,a.indrelid) IS NOT DISTINCT FROM pg_get_expr(e.indpred,e.indrelid)
        ) THEN RAISE EXCEPTION '0087 refuses incompatible index {name}'; END IF;
        DROP TABLE pg_temp.qa_0087_index_reference;
    END $index$"""


TABLES = (
    _table("stream_events", """
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        tenant_id UUID NOT NULL,
        category TEXT NOT NULL CHECK (category IN ('campaign','system','alert','user_action','milestone','call')),
        title TEXT NOT NULL, description TEXT,
        severity TEXT CHECK (severity IS NULL OR severity IN ('info','warning','critical')),
        related_campaign_id UUID, related_call_id UUID, actor_user_id UUID, metadata JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW()+INTERVAL '90 days')
    """, tenant=True, foreign_keys=(("tenant_id", "tenants", "id", "CASCADE"),)),
    _table("tenant_ai_credentials", """
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id UUID NOT NULL,
        provider TEXT NOT NULL CHECK (char_length(provider)>0 AND char_length(provider)<=64),
        credential_kind TEXT NOT NULL DEFAULT 'api_key'
            CHECK (credential_kind IN ('api_key','oauth_refresh_token','service_account_json')),
        encrypted_key TEXT NOT NULL, last4 TEXT CHECK (last4 IS NULL OR char_length(last4)<=8),
        label TEXT, status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_used_at TIMESTAMPTZ, rotated_at TIMESTAMPTZ
    """, tenant=True, foreign_keys=(("tenant_id", "tenants", "id", "CASCADE"),), comments="""
        COMMENT ON TABLE public.tenant_ai_credentials IS
            'Per-tenant AI provider API keys (encrypted). Consulted by '
            'CredentialResolver; env vars are the fallback when no row exists.';
        COMMENT ON COLUMN public.tenant_ai_credentials.encrypted_key IS
            'Envelope-encrypted ciphertext from TokenEncryptionService. '
            'Plaintext never lands in the DB.';
    """),
    _table("tenant_telephony_credentials", """
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        tenant_id UUID NOT NULL,
        provider TEXT NOT NULL CHECK (provider IN ('twilio','vonage')),
        label TEXT, credentials_encrypted TEXT NOT NULL, from_number TEXT,
        status TEXT NOT NULL DEFAULT 'inactive' CHECK (status IN ('active','inactive','failed')),
        last_tested_at TIMESTAMPTZ, last_test_result JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        UNIQUE (tenant_id,provider)
    """, tenant=True, foreign_keys=(("tenant_id", "tenants", "id", "CASCADE"),)),
    _table("emergency_access_requests", """
        request_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        requestor_id UUID NOT NULL, scenario VARCHAR(50) NOT NULL,
        justification TEXT NOT NULL, requested_access TEXT[] NOT NULL, approvers_required INTEGER NOT NULL DEFAULT 2,
        approvals JSONB DEFAULT '[]'::jsonb,
        status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status::text = ANY (ARRAY[
            'pending'::varchar::text,'approved'::varchar::text,'denied'::varchar::text,
            'expired'::varchar::text,'used'::varchar::text,'cancelled'::varchar::text])),
        approved_at TIMESTAMPTZ, expires_at TIMESTAMPTZ NOT NULL, session_created_at TIMESTAMPTZ,
        session_terminated_at TIMESTAMPTZ, session_token_hash VARCHAR(64), actions_taken JSONB DEFAULT '[]'::jsonb,
        reviewed_at TIMESTAMPTZ, reviewed_by UUID, review_notes TEXT
    """, foreign_keys=(("requestor_id", "user_profiles", "id", "NO ACTION"),
                       ("reviewed_by", "user_profiles", "id", "NO ACTION"))),
    _table("worker_status", """
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(), name TEXT NOT NULL DEFAULT 'worker',
        status TEXT NOT NULL DEFAULT 'idle', current_task TEXT, processed_count INTEGER NOT NULL DEFAULT 0,
        failed_count INTEGER NOT NULL DEFAULT 0, last_heartbeat TIMESTAMPTZ, started_at TIMESTAMPTZ
    """, comments="""
        COMMENT ON TABLE public.worker_status IS
            'Background-worker heartbeat/liveness rows for the admin health console. '
            'Shared infra — platform-wide, not tenant-scoped.';
    """),
    # These four inert historical tables account for the rest of QA14's table
    # difference. Keeping their definitions does not enable a new runtime path.
    _table("audit_chain_state", """
        id SERIAL PRIMARY KEY, last_event_id UUID,
        last_event_hash VARCHAR(64) NOT NULL, events_count BIGINT NOT NULL DEFAULT 0,
        verified_at TIMESTAMPTZ, verification_result BOOLEAN, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    """, foreign_keys=(("last_event_id", "audit_logs", "event_id", "NO ACTION"),)),
    _table("audit_event_types", """
        event_type VARCHAR(50) PRIMARY KEY, category VARCHAR(30) NOT NULL,
        severity VARCHAR(10) NOT NULL, description TEXT, retention_days INTEGER NOT NULL
    """),
    _table("rate_limit_events", """
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(), tier TEXT NOT NULL CHECK (tier IN ('ip','user','tenant','global')),
        scope_key TEXT NOT NULL, endpoint TEXT,
        action_taken TEXT NOT NULL CHECK (action_taken IN ('allow','warn','throttle','block')),
        limit_config JSONB, triggered_at TIMESTAMPTZ DEFAULT NOW()
    """),
    _table("suspension_propagation_queue", """
        queue_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        suspension_id UUID NOT NULL,
        target_type VARCHAR(20) NOT NULL, target_id UUID NOT NULL,
        action VARCHAR(20) NOT NULL CHECK (action::text = ANY (ARRAY['suspend'::varchar::text,'restore'::varchar::text])),
        service_name VARCHAR(50) NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status::text = ANY (ARRAY[
            'pending'::varchar::text,'in_progress'::varchar::text,'completed'::varchar::text,'failed'::varchar::text])),
        attempts INTEGER NOT NULL DEFAULT 0, last_attempt_at TIMESTAMPTZ, last_error TEXT,
        completed_at TIMESTAMPTZ, next_attempt_at TIMESTAMPTZ
    """, foreign_keys=(("suspension_id", "suspension_events", "suspension_id", "NO ACTION"),)),
)

INDEXES = (
    _index("stream_events", "idx_se_tenant_recent", "tenant_id,created_at DESC"),
    _index("stream_events", "idx_se_tenant_category", "tenant_id,category,created_at DESC"),
    _index("stream_events", "idx_se_expires", "expires_at"),
    _index("tenant_ai_credentials", "idx_tenant_ai_credentials_lookup", "tenant_id,provider,credential_kind", predicate="status='active'"),
    _index("tenant_ai_credentials", "uq_tenant_ai_credentials_active", "tenant_id,provider,credential_kind", unique=True, predicate="status='active'"),
    _index("tenant_telephony_credentials", "idx_ttc_tenant", "tenant_id"),
    _index("emergency_access_requests", "idx_emergency_access_created", "created_at DESC"),
    _index("emergency_access_requests", "idx_emergency_access_expires", "expires_at", predicate="status='approved'"),
    _index("emergency_access_requests", "idx_emergency_access_requestor", "requestor_id"),
    _index("emergency_access_requests", "idx_emergency_access_status", "status"),
    _index("worker_status", "idx_worker_status_heartbeat", "last_heartbeat DESC"),
    _index("rate_limit_events", "idx_rate_limit_events_scope", "tier,scope_key,triggered_at DESC"),
    _index("rate_limit_events", "idx_rate_limit_events_triggered_at", "triggered_at DESC"),
    _index("suspension_propagation_queue", "idx_suspension_queue_status", "status,next_attempt_at"),
    _index("suspension_propagation_queue", "idx_suspension_queue_suspension", "suspension_id"),
    # Production's manual file added this name alongside the older equivalent
    # idx_leads_campaign_phone_unique. Preserve both historical identities;
    # never drop an existing index or cancel duplicates to force convergence.
    _index("leads", "uq_leads_campaign_phone_active", "campaign_id,phone_number", unique=True, predicate="status<>'deleted'"),
)

TENANT_COLUMNS = """DO $columns$
DECLARE target oid := to_regclass('public.tenants'); actual text; expected text; reference oid;
BEGIN
    IF target IS NULL THEN RAISE EXCEPTION '0087 requires tenants'; END IF;
    LOCK TABLE public.tenants IN ACCESS EXCLUSIVE MODE;
    ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS assistant_model TEXT;
    ALTER TABLE public.tenants ADD COLUMN IF NOT EXISTS active_telephony_provider TEXT DEFAULT 'none';
    IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=target
        AND attname IN ('assistant_model','active_telephony_provider') AND NOT attisdropped
        AND (atttypid<>'text'::regtype OR attnotnull)) THEN
        RAISE EXCEPTION '0087 refuses incompatible tenant configuration columns';
    END IF;
    CREATE TEMPORARY TABLE qa_0087_provider_reference (
        assistant_model TEXT,
        active_telephony_provider TEXT DEFAULT 'none'
        CHECK (active_telephony_provider IN ('twilio','vonage','sip','none'))
    ) ON COMMIT DROP;
    reference := to_regclass('pg_temp.qa_0087_provider_reference');
    IF EXISTS (
        SELECT 1 FROM pg_attribute a JOIN pg_attribute e ON e.attrelid=reference AND e.attname=a.attname
        LEFT JOIN pg_attrdef ad ON ad.adrelid=a.attrelid AND ad.adnum=a.attnum
        LEFT JOIN pg_attrdef ed ON ed.adrelid=e.attrelid AND ed.adnum=e.attnum
        WHERE a.attrelid=target AND a.attname IN ('assistant_model','active_telephony_provider')
          AND pg_get_expr(ad.adbin,ad.adrelid) IS DISTINCT FROM pg_get_expr(ed.adbin,ed.adrelid)
    ) THEN RAISE EXCEPTION '0087 refuses unknown tenant configuration defaults'; END IF;
    SELECT pg_get_expr(conbin,conrelid) INTO expected FROM pg_constraint
        WHERE conrelid=to_regclass('pg_temp.qa_0087_provider_reference') AND contype='c';
    SELECT pg_get_expr(conbin,conrelid) INTO actual FROM pg_constraint
        WHERE conrelid=target AND conname='tenants_active_telephony_provider_check';
    IF actual IS NULL THEN
        IF EXISTS (SELECT 1 FROM public.tenants WHERE active_telephony_provider IS NOT NULL
            AND active_telephony_provider NOT IN ('twilio','vonage','sip','none')) THEN
            RAISE EXCEPTION '0087 provider preflight is nonzero; run the read-only count script';
        END IF;
        ALTER TABLE public.tenants ADD CONSTRAINT tenants_active_telephony_provider_check
            CHECK (active_telephony_provider IN ('twilio','vonage','sip','none')) NOT VALID;
        ALTER TABLE public.tenants VALIDATE CONSTRAINT tenants_active_telephony_provider_check;
    ELSIF actual IS DISTINCT FROM expected THEN
        RAISE EXCEPTION '0087 refuses unknown tenant provider CHECK';
    END IF;
    DROP TABLE pg_temp.qa_0087_provider_reference;
END $columns$"""

REFRESH_REASON_CHECK = """DO $refresh$
DECLARE target oid := to_regclass('public.refresh_tokens'); actual text; old_shape text; new_shape text;
BEGIN
    IF target IS NULL THEN RAISE EXCEPTION '0087 requires refresh_tokens'; END IF;
    LOCK TABLE public.refresh_tokens IN ACCESS EXCLUSIVE MODE;
    CREATE TEMPORARY TABLE qa_0087_refresh_reference (
        revoked_reason TEXT,
        CONSTRAINT old_reason CHECK (revoked_reason IS NULL OR revoked_reason IN
            ('rotated','reuse_detected','logout','admin','expired')),
        CONSTRAINT new_reason CHECK (revoked_reason IS NULL OR revoked_reason IN
            ('rotated','reuse_detected','logout','admin','expired','password_change','password_reset','mfa_disabled','expired_with_subsequent_use'))
    ) ON COMMIT DROP;
    SELECT pg_get_expr(conbin,conrelid) INTO old_shape FROM pg_constraint
        WHERE conrelid=to_regclass('pg_temp.qa_0087_refresh_reference') AND conname='old_reason';
    SELECT pg_get_expr(conbin,conrelid) INTO new_shape FROM pg_constraint
        WHERE conrelid=to_regclass('pg_temp.qa_0087_refresh_reference') AND conname='new_reason';
    SELECT pg_get_expr(conbin,conrelid) INTO actual FROM pg_constraint
        WHERE conrelid=target AND conname='refresh_tokens_revoked_reason_check';
    IF actual IS NOT DISTINCT FROM new_shape THEN
        NULL; -- Preserve an already-current constraint and its OID/validation state.
    ELSIF actual IS NULL OR actual IS NOT DISTINCT FROM old_shape THEN
        IF EXISTS (SELECT 1 FROM public.refresh_tokens WHERE revoked_reason IS NOT NULL AND revoked_reason NOT IN
            ('rotated','reuse_detected','logout','admin','expired','password_change','password_reset','mfa_disabled','expired_with_subsequent_use')) THEN
            RAISE EXCEPTION '0087 refresh reason preflight is nonzero; run the read-only count script';
        END IF;
        ALTER TABLE public.refresh_tokens DROP CONSTRAINT IF EXISTS refresh_tokens_revoked_reason_check;
        ALTER TABLE public.refresh_tokens ADD CONSTRAINT refresh_tokens_revoked_reason_check CHECK
            (revoked_reason IS NULL OR revoked_reason IN
            ('rotated','reuse_detected','logout','admin','expired','password_change','password_reset','mfa_disabled','expired_with_subsequent_use')) NOT VALID;
        ALTER TABLE public.refresh_tokens VALIDATE CONSTRAINT refresh_tokens_revoked_reason_check;
    ELSE
        RAISE EXCEPTION '0087 refuses unknown refresh reason CHECK';
    END IF;
    DROP TABLE pg_temp.qa_0087_refresh_reference;
END $refresh$"""

AI_TOUCH = """DO $touch$
DECLARE function_oid oid := to_regprocedure('public.tenant_ai_credentials_touch_updated_at()');
BEGIN
    IF function_oid IS NULL THEN
        EXECUTE $function$CREATE FUNCTION public.tenant_ai_credentials_touch_updated_at()
            RETURNS TRIGGER AS $body$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$body$ LANGUAGE plpgsql$function$;
        function_oid := to_regprocedure('public.tenant_ai_credentials_touch_updated_at()');
    ELSIF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
        WHERE p.oid=function_oid AND p.prorettype='trigger'::regtype AND p.pronargs=0
          AND l.lanname='plpgsql' AND NOT p.prosecdef
          AND lower(regexp_replace(p.prosrc,'\\s','','g'))='beginnew.updated_at=now();returnnew;end;') THEN
        RAISE EXCEPTION '0087 refuses unknown AI credential timestamp function';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('public.tenant_ai_credentials')
        AND tgname='trg_tenant_ai_credentials_updated_at') THEN
        CREATE TRIGGER trg_tenant_ai_credentials_updated_at BEFORE UPDATE ON public.tenant_ai_credentials
            FOR EACH ROW EXECUTE FUNCTION public.tenant_ai_credentials_touch_updated_at();
    ELSIF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('public.tenant_ai_credentials')
        AND tgname='trg_tenant_ai_credentials_updated_at' AND tgfoid=function_oid
        AND tgtype=19 AND tgenabled='O' AND tgqual IS NULL AND tgnargs=0) THEN
        RAISE EXCEPTION '0087 refuses unknown AI credential timestamp trigger';
    END IF;
END $touch$"""


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='120s'")
    for statement in (TENANT_COLUMNS, REFRESH_REASON_CHECK, *TABLES, *INDEXES, AI_TOUCH):
        op.execute(statement)


def downgrade():
    raise RuntimeError("0087 is forward-only: retain adopted production objects and use a reviewed compensating migration")

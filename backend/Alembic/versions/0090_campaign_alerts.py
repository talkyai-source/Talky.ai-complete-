"""Durable campaign alerts and non-replaying email intents (F3-08).

The three new relation names/claim column must be unused. Refuse collisions
instead of adopting unknown objects. Downgrade refuses once alert/claim data
exists; use code rollback retaining the schema or a reviewed forward migration.
The parent ownership indexes are retained on an empty downgrade.
"""
from alembic import op

revision = "0090_campaign_alerts"
down_revision = "0089_provider_key_validation"
branch_labels = None
depends_on = None
OWNER = "created by 0090_campaign_alerts"
TABLES = ("campaign_alert_state", "tenant_alert_state", "campaign_alert_outbox")


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    op.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_alert_owner
        ON campaigns(tenant_id,id)""")
    op.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_security_events_alert_owner
        ON security_events(tenant_id,event_id)""")
    # Plain CREATE/ADD deliberately fails if a hand-created incompatible object
    # already uses these names. Alembic runs this once inside its transaction.
    op.execute("ALTER TABLE calls ADD COLUMN campaign_alert_processed_at TIMESTAMPTZ")
    op.execute(f"COMMENT ON COLUMN calls.campaign_alert_processed_at IS '{OWNER}'")
    op.execute("""CREATE TABLE campaign_alert_state (
        tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        campaign_id UUID NOT NULL,
        failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count>=0),
        failure_episode UUID,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(tenant_id,campaign_id),
        FOREIGN KEY(tenant_id,campaign_id) REFERENCES campaigns(tenant_id,id) ON DELETE CASCADE,
        CHECK((failure_count=0 AND failure_episode IS NULL) OR (failure_count>0 AND failure_episode IS NOT NULL))
    )""")
    op.execute("""CREATE TABLE tenant_alert_state (
        tenant_id UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
        low_minutes_active BOOLEAN NOT NULL DEFAULT FALSE,
        low_minutes_episode UUID,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CHECK(low_minutes_active=(low_minutes_episode IS NOT NULL))
    )""")
    op.execute("""CREATE TABLE campaign_alert_outbox (
        id UUID PRIMARY KEY,
        tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
        event_id UUID NOT NULL,
        delivery_key TEXT NOT NULL CHECK(char_length(delivery_key)<=200),
        kind TEXT NOT NULL CHECK(kind IN ('campaign_failures','low_minutes','qualified_lead')),
        recipient TEXT,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN
            ('pending','sending','accepted','unknown','failed_before_send','recipient_missing')),
        attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
        claim_token UUID,
        started_at TIMESTAMPTZ,
        accepted_at TIMESTAMPTZ,
        last_error_code TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(tenant_id,delivery_key),
        FOREIGN KEY(tenant_id,event_id) REFERENCES security_events(tenant_id,event_id) ON DELETE CASCADE,
        CHECK(status<>'sending' OR (claim_token IS NOT NULL AND started_at IS NOT NULL)),
        CHECK(status<>'accepted' OR accepted_at IS NOT NULL)
    )""")
    op.execute("""CREATE INDEX idx_campaign_alert_pending
        ON campaign_alert_outbox(tenant_id,status,created_at,id)""")
    op.execute("""CREATE INDEX idx_campaign_alert_event
        ON campaign_alert_outbox(tenant_id,event_id)""")
    for table in TABLES:
        op.execute(f"COMMENT ON TABLE {table} IS '{OWNER}'")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""CREATE POLICY {table}_tenant ON {table}
            USING (tenant_id=NULLIF(current_setting('app.current_tenant_id',TRUE),'')::uuid
                OR COALESCE(NULLIF(current_setting('app.bypass_rls',TRUE),'')::boolean,FALSE))
            WITH CHECK (tenant_id=NULLIF(current_setting('app.current_tenant_id',TRUE),'')::uuid
                OR COALESCE(NULLIF(current_setting('app.bypass_rls',TRUE),'')::boolean,FALSE))""")


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("SET LOCAL statement_timeout='60s'")
    # Hidden rows must raise, never masquerade as an empty downgrade target.
    op.execute("SET LOCAL row_security=off")
    op.execute(f"""DO $q35$
    DECLARE item text; present oid; has_rows boolean;
    BEGIN
        FOREACH item IN ARRAY ARRAY['campaign_alert_state','tenant_alert_state','campaign_alert_outbox'] LOOP
            present:=to_regclass('public.'||item);
            IF present IS NOT NULL THEN
                IF obj_description(present,'pg_class') IS DISTINCT FROM '{OWNER}' THEN
                    RAISE EXCEPTION '0090 refuses to remove an unowned alert object';
                END IF;
                EXECUTE format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE',item);
                EXECUTE format('SELECT EXISTS(SELECT 1 FROM public.%I LIMIT 1)',item) INTO has_rows;
                IF has_rows THEN
                    RAISE EXCEPTION 'Campaign alert data exists; use code rollback or a reviewed forward migration';
                END IF;
            END IF;
        END LOOP;
        LOCK TABLE public.calls IN ACCESS EXCLUSIVE MODE;
        IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='public.calls'::regclass
                   AND attname='campaign_alert_processed_at' AND NOT attisdropped) THEN
            IF EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid='public.calls'::regclass
                       AND attname='campaign_alert_processed_at' AND NOT attisdropped
                       AND col_description(attrelid,attnum) IS DISTINCT FROM '{OWNER}') THEN
                RAISE EXCEPTION '0090 refuses to remove an unowned alert claim';
            END IF;
            IF EXISTS (SELECT 1 FROM calls WHERE campaign_alert_processed_at IS NOT NULL) THEN
                RAISE EXCEPTION 'Campaign alert data exists; use code rollback or a reviewed forward migration';
            END IF;
        END IF;
        IF EXISTS (SELECT 1 FROM security_events WHERE detection_source='campaign_alerts') THEN
            RAISE EXCEPTION 'Campaign alert data exists; use code rollback or a reviewed forward migration';
        END IF;
        DROP TABLE IF EXISTS campaign_alert_outbox;
        DROP TABLE IF EXISTS campaign_alert_state;
        DROP TABLE IF EXISTS tenant_alert_state;
        ALTER TABLE calls DROP COLUMN IF EXISTS campaign_alert_processed_at;
    END $q35$""")

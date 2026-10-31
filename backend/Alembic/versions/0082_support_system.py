"""Customer support: bot conversations, tickets, help articles and receipts.

Every tenant-owned table carries ``tenant_id`` with composite foreign keys, so a
child row can never point at another tenant's parent. RLS follows the existing
``app.bypass_rls`` / ``app.current_tenant_id`` contract, but the application
still filters ``tenant_id`` explicitly on every query.

``support_request_receipts`` stores the response of each idempotent write so a
replayed ``request_id`` returns the original result instead of acting twice.

Rollback drops only empty support tables; it refuses while tickets,
conversations or staff-written articles exist, because those are customer
records. Export them and use a reviewed forward migration instead.
"""
from alembic import op

revision = "0082_support_system"
down_revision = "0081_admin_payment_merge"
branch_labels = None
depends_on = None

BYPASS = "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"

CATEGORIES = ("'billing','calls_campaigns','phone_numbers','ai_agent_voices','integrations',"
              "'account_login','bug','feature_request','privacy_data','other'")

TENANT_TABLES = (
    "support_conversations", "support_conversation_messages", "support_tickets",
    "support_ticket_messages", "support_ticket_attachments", "support_ticket_events",
    "support_ticket_reads", "support_request_receipts",
)

PERMISSION_GRANTS = (
    ("support:report", ("tenant_admin", "partner_admin", "platform_admin",
                        "campaign_manager", "user", "agent", "billing_user")),
    ("support:read_team", ("tenant_admin", "partner_admin", "platform_admin")),
    ("support:manage", ("platform_admin",)),
)
# Grants this migration adds beyond 0050 (which already gave support:report to
# the three admin roles); downgrade removes exactly these.
ADDED_REPORT_ROLES = ("campaign_manager", "user", "agent", "billing_user")

STATEMENTS = (
    "CREATE SEQUENCE public.support_ticket_number_seq AS bigint START WITH 1 MINVALUE 1",
    f"""CREATE TABLE public.support_tickets (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
        number bigint NOT NULL DEFAULT nextval('public.support_ticket_number_seq'),
        requester_user_id uuid REFERENCES public.user_profiles(id) ON DELETE SET NULL,
        conversation_id uuid,
        subject text NOT NULL CHECK (char_length(subject) BETWEEN 3 AND 150),
        category text NOT NULL CHECK (category IN ({CATEGORIES})),
        priority text NOT NULL CHECK (priority IN ('urgent','high','normal','low')),
        status text NOT NULL DEFAULT 'open'
            CHECK (status IN ('open','in_progress','waiting_on_customer','resolved','closed')),
        assignee_user_id uuid REFERENCES public.user_profiles(id) ON DELETE SET NULL,
        related_campaign_id uuid,
        related_call_id uuid,
        diagnostics jsonb NOT NULL DEFAULT '{{}}'::jsonb CHECK (jsonb_typeof(diagnostics) = 'object'),
        first_response_due_at timestamptz NOT NULL,
        resolution_due_at timestamptz NOT NULL,
        first_responded_at timestamptz,
        first_response_breached_at timestamptz,
        resolution_breached_at timestamptz,
        resolved_at timestamptz,
        closed_at timestamptz,
        csat_score smallint CHECK (csat_score BETWEEN 1 AND 5),
        csat_comment text CHECK (char_length(csat_comment) <= 1000),
        version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
        request_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT support_tickets_number_key UNIQUE (number),
        CONSTRAINT support_tickets_id_tenant_key UNIQUE (id, tenant_id),
        CONSTRAINT support_tickets_request_key UNIQUE (tenant_id, request_id),
        CONSTRAINT support_tickets_resolved_receipt
            CHECK (status NOT IN ('resolved','closed') OR resolved_at IS NOT NULL),
        CONSTRAINT support_tickets_closed_receipt CHECK (status <> 'closed' OR closed_at IS NOT NULL),
        CONSTRAINT support_tickets_csat_comment_needs_score CHECK (csat_comment IS NULL OR csat_score IS NOT NULL)
    )""",
    """CREATE TABLE public.support_conversations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
        status text NOT NULL DEFAULT 'active'
            CHECK (status IN ('active','resolved_by_bot','escalated','abandoned')),
        page_context jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(page_context) = 'object'),
        not_solved_count integer NOT NULL DEFAULT 0 CHECK (not_solved_count >= 0),
        escalation jsonb CHECK (escalation IS NULL OR jsonb_typeof(escalation) = 'object'),
        ticket_id uuid,
        request_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        ended_at timestamptz,
        CONSTRAINT support_conversations_id_tenant_key UNIQUE (id, tenant_id),
        CONSTRAINT support_conversations_request_key UNIQUE (tenant_id, request_id),
        CONSTRAINT support_conversations_ticket_fk FOREIGN KEY (ticket_id, tenant_id)
            REFERENCES public.support_tickets(id, tenant_id) ON DELETE SET NULL (ticket_id),
        CONSTRAINT support_conversations_ended_receipt CHECK (status = 'active' OR ended_at IS NOT NULL)
    )""",
    """ALTER TABLE public.support_tickets ADD CONSTRAINT support_tickets_conversation_fk
        FOREIGN KEY (conversation_id, tenant_id) REFERENCES public.support_conversations(id, tenant_id)
        ON DELETE SET NULL (conversation_id)""",
    """CREATE TABLE public.support_conversation_messages (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        conversation_id uuid NOT NULL,
        tenant_id uuid NOT NULL,
        role text NOT NULL CHECK (role IN ('user','assistant')),
        content text NOT NULL CHECK (char_length(content) BETWEEN 1 AND 4000),
        sources jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(sources) = 'array'),
        tool_calls jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(tool_calls) = 'array'),
        diagnostics jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(diagnostics) = 'object'),
        model text,
        latency_ms integer CHECK (latency_ms >= 0),
        in_reply_to uuid,
        request_id uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT support_conversation_messages_id_tenant_key UNIQUE (id, tenant_id),
        CONSTRAINT support_conversation_messages_request_key UNIQUE (tenant_id, request_id),
        CONSTRAINT support_conversation_messages_one_reply UNIQUE (in_reply_to),
        CONSTRAINT support_conversation_messages_conversation_fk FOREIGN KEY (conversation_id, tenant_id)
            REFERENCES public.support_conversations(id, tenant_id) ON DELETE CASCADE,
        CONSTRAINT support_conversation_messages_reply_fk FOREIGN KEY (in_reply_to, tenant_id)
            REFERENCES public.support_conversation_messages(id, tenant_id) ON DELETE CASCADE,
        CONSTRAINT support_conversation_messages_user_request CHECK (role <> 'user' OR request_id IS NOT NULL),
        CONSTRAINT support_conversation_messages_reply_role CHECK (in_reply_to IS NULL OR role = 'assistant')
    )""",
    """CREATE TABLE public.support_ticket_messages (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        ticket_id uuid NOT NULL,
        tenant_id uuid NOT NULL,
        author_user_id uuid REFERENCES public.user_profiles(id) ON DELETE SET NULL,
        author_type text NOT NULL CHECK (author_type IN ('customer','staff','system')),
        body text NOT NULL CHECK (char_length(body) BETWEEN 1 AND 10000),
        is_internal boolean NOT NULL DEFAULT FALSE,
        request_id uuid,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT support_ticket_messages_id_tenant_key UNIQUE (id, tenant_id),
        CONSTRAINT support_ticket_messages_request_key UNIQUE (tenant_id, request_id),
        CONSTRAINT support_ticket_messages_ticket_fk FOREIGN KEY (ticket_id, tenant_id)
            REFERENCES public.support_tickets(id, tenant_id) ON DELETE CASCADE,
        CONSTRAINT support_ticket_messages_internal_is_staff CHECK (NOT is_internal OR author_type = 'staff')
    )""",
    """CREATE TABLE public.support_ticket_attachments (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid REFERENCES public.tenants(id) ON DELETE CASCADE,
        ticket_id uuid,
        message_id uuid,
        uploaded_by uuid REFERENCES public.user_profiles(id) ON DELETE SET NULL,
        storage_backend text NOT NULL CHECK (storage_backend IN ('s3','local')),
        storage_key text NOT NULL,
        filename text NOT NULL CHECK (char_length(filename) BETWEEN 1 AND 160),
        content_type text NOT NULL CHECK (content_type IN
            ('image/png','image/jpeg','image/webp','application/pdf','text/plain','text/csv')),
        size_bytes integer NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 52428800),
        created_at timestamptz NOT NULL DEFAULT now(),
        attached_at timestamptz,
        CONSTRAINT support_ticket_attachments_storage_key UNIQUE (storage_key),
        CONSTRAINT support_ticket_attachments_ticket_fk FOREIGN KEY (ticket_id, tenant_id)
            REFERENCES public.support_tickets(id, tenant_id) ON DELETE CASCADE,
        CONSTRAINT support_ticket_attachments_message_fk FOREIGN KEY (message_id, tenant_id)
            REFERENCES public.support_ticket_messages(id, tenant_id) ON DELETE CASCADE,
        CONSTRAINT support_ticket_attachments_link_pair CHECK ((ticket_id IS NULL) = (message_id IS NULL)),
        CONSTRAINT support_ticket_attachments_attached_receipt CHECK ((message_id IS NULL) = (attached_at IS NULL)),
        CONSTRAINT support_ticket_attachments_attached_tenant CHECK (message_id IS NULL OR tenant_id IS NOT NULL)
    )""",
    """CREATE TABLE public.support_ticket_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        ticket_id uuid NOT NULL,
        tenant_id uuid NOT NULL,
        actor_user_id uuid REFERENCES public.user_profiles(id) ON DELETE SET NULL,
        type text NOT NULL CHECK (type IN ('created','status_changed','priority_changed','category_changed',
            'assigned','customer_replied','staff_replied','internal_note','csat_submitted','sla_breached',
            'notification')),
        from_value text CHECK (char_length(from_value) <= 200),
        to_value text CHECK (char_length(to_value) <= 200),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT support_ticket_events_ticket_fk FOREIGN KEY (ticket_id, tenant_id)
            REFERENCES public.support_tickets(id, tenant_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE public.support_ticket_reads (
        user_id uuid NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
        ticket_id uuid NOT NULL,
        tenant_id uuid NOT NULL,
        last_seen_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (user_id, ticket_id),
        CONSTRAINT support_ticket_reads_ticket_fk FOREIGN KEY (ticket_id, tenant_id)
            REFERENCES public.support_tickets(id, tenant_id) ON DELETE CASCADE
    )""",
    """CREATE TABLE public.support_request_receipts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid REFERENCES public.tenants(id) ON DELETE CASCADE,
        request_id uuid NOT NULL,
        actor_user_id uuid NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
        operation text NOT NULL CHECK (char_length(operation) BETWEEN 1 AND 60),
        resource_id uuid,
        status_code smallint NOT NULL CHECK (status_code BETWEEN 200 AND 299),
        response jsonb NOT NULL CHECK (jsonb_typeof(response) = 'object'),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT support_request_receipts_request_key UNIQUE NULLS NOT DISTINCT (tenant_id, request_id)
    )""",
    f"""CREATE TABLE public.support_kb_articles (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        slug text NOT NULL CHECK (slug ~ '^[a-z0-9][a-z0-9-]{{0,158}}[a-z0-9]$'),
        title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
        section text NOT NULL DEFAULT '' CHECK (char_length(section) <= 200),
        body_md text NOT NULL CHECK (char_length(body_md) BETWEEN 1 AND 50000),
        category text NOT NULL DEFAULT 'other' CHECK (category IN ({CATEGORIES})),
        source text NOT NULL CHECK (source IN ('docs','staff')),
        url text NOT NULL CHECK (url ~ '^/[A-Za-z0-9/_#.-]*$'),
        published boolean NOT NULL DEFAULT TRUE,
        search_tsv tsvector GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(section, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(body_md, '')), 'B')) STORED,
        search_text text GENERATED ALWAYS AS (
            lower(title || ' ' || section || ' ' || body_md)) STORED,
        content_hash text CHECK (content_hash ~ '^[0-9a-f]{{64}}$'),
        request_id uuid,
        updated_by uuid REFERENCES public.user_profiles(id) ON DELETE SET NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT support_kb_articles_slug_key UNIQUE (slug),
        CONSTRAINT support_kb_articles_request_key UNIQUE (request_id)
    )""",
    # Indexes named in the brief plus the access paths the queries use.
    "CREATE INDEX support_tickets_tenant_status_idx ON public.support_tickets (tenant_id, status)",
    "CREATE INDEX support_tickets_status_first_due_idx ON public.support_tickets (status, first_response_due_at)",
    "CREATE INDEX support_tickets_status_resolution_due_idx ON public.support_tickets (status, resolution_due_at)",
    "CREATE INDEX support_tickets_assignee_status_idx ON public.support_tickets (assignee_user_id, status)",
    "CREATE INDEX support_tickets_requester_idx ON public.support_tickets (tenant_id, requester_user_id, updated_at DESC, id DESC)",
    "CREATE INDEX support_tickets_updated_idx ON public.support_tickets (updated_at DESC, id DESC)",
    "CREATE INDEX support_conversations_tenant_status_idx ON public.support_conversations (tenant_id, status)",
    "CREATE INDEX support_conversations_idle_idx ON public.support_conversations (status, updated_at)",
    "CREATE INDEX support_conversation_messages_conversation_idx ON public.support_conversation_messages (conversation_id, created_at, id)",
    "CREATE INDEX support_conversation_messages_tenant_day_idx ON public.support_conversation_messages (tenant_id, created_at) WHERE role = 'user'",
    "CREATE INDEX support_ticket_messages_ticket_idx ON public.support_ticket_messages (ticket_id, created_at, id)",
    "CREATE INDEX support_ticket_attachments_message_idx ON public.support_ticket_attachments (message_id)",
    "CREATE INDEX support_ticket_attachments_unattached_idx ON public.support_ticket_attachments (created_at) WHERE message_id IS NULL",
    "CREATE INDEX support_ticket_events_ticket_idx ON public.support_ticket_events (ticket_id, created_at, id)",
    "CREATE INDEX support_kb_articles_search_idx ON public.support_kb_articles USING gin (search_tsv)",
    "CREATE INDEX support_kb_articles_trgm_idx ON public.support_kb_articles USING gin (search_text gin_trgm_ops)",
)


def _rls_statements():
    for table in TENANT_TABLES:
        yield f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"
        yield f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"
        if table == "support_request_receipts":
            # Platform-scope receipts (article writes) have no tenant.
            predicate = f"{BYPASS} OR (tenant_id IS NOT NULL AND {TENANT})"
        elif table == "support_ticket_attachments":
            predicate = f"{BYPASS} OR (tenant_id IS NOT NULL AND {TENANT})"
        else:
            predicate = f"{BYPASS} OR {TENANT}"
        yield (f"CREATE POLICY {table}_tenant_isolation ON public.{table} "
               f"USING ({predicate}) WITH CHECK ({predicate})")
    yield "ALTER TABLE public.support_kb_articles ENABLE ROW LEVEL SECURITY"
    yield "ALTER TABLE public.support_kb_articles FORCE ROW LEVEL SECURITY"
    yield (f"CREATE POLICY support_kb_articles_read ON public.support_kb_articles FOR SELECT "
           f"USING (published OR {BYPASS})")
    # Written out (not looped) so the RLS inventory reads each policy literally.
    yield f"CREATE POLICY support_kb_articles_insert ON public.support_kb_articles FOR INSERT WITH CHECK ({BYPASS})"
    yield f"CREATE POLICY support_kb_articles_update ON public.support_kb_articles FOR UPDATE USING ({BYPASS})"
    yield f"CREATE POLICY support_kb_articles_delete ON public.support_kb_articles FOR DELETE USING ({BYPASS})"


def _permission_statements():
    yield """INSERT INTO permissions (name, description, resource, action, is_system) VALUES
        ('support:report', 'Use the support bot and open and follow own support tickets', 'support', 'report', true),
        ('support:read_team', 'Read every support ticket of the tenant', 'support', 'read_team', true),
        ('support:manage', 'Handle support tickets and help articles for all tenants', 'support', 'manage', true)
        ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description"""
    for permission, roles in PERMISSION_GRANTS:
        names = ",".join(f"'{role}'" for role in roles)
        yield f"""INSERT INTO role_permissions (role_id, permission_id)
            SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
            WHERE r.name IN ({names}) AND p.name = '{permission}'
            ON CONFLICT DO NOTHING"""


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    for statement in (*STATEMENTS, *_rls_statements(), *_permission_statements()):
        op.execute(statement)


def downgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL app.bypass_rls = 'on'")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.support_tickets)
           OR EXISTS (SELECT 1 FROM public.support_conversations)
           OR EXISTS (SELECT 1 FROM public.support_kb_articles WHERE source = 'staff') THEN
            RAISE EXCEPTION 'Refusing to drop support records; export them and use a reviewed forward migration';
        END IF;
    END $$""")
    for table in ("support_request_receipts", "support_ticket_reads", "support_ticket_events",
                  "support_ticket_attachments", "support_ticket_messages", "support_conversation_messages"):
        op.execute(f"DROP TABLE public.{table}")
    op.execute("ALTER TABLE public.support_tickets DROP CONSTRAINT support_tickets_conversation_fk")
    op.execute("DROP TABLE public.support_conversations")
    op.execute("DROP TABLE public.support_tickets")
    op.execute("DROP TABLE public.support_kb_articles")
    op.execute("DROP SEQUENCE public.support_ticket_number_seq")
    added = ",".join(f"'{role}'" for role in ADDED_REPORT_ROLES)
    op.execute(f"""DELETE FROM role_permissions rp USING roles r, permissions p
        WHERE rp.role_id = r.id AND rp.permission_id = p.id
          AND ((p.name = 'support:report' AND r.name IN ({added}))
               OR p.name IN ('support:read_team','support:manage'))""")
    op.execute("""DELETE FROM user_permissions up USING permissions p
        WHERE up.permission_id = p.id AND p.name IN ('support:read_team','support:manage')""")
    op.execute("DELETE FROM permissions WHERE name IN ('support:read_team','support:manage')")

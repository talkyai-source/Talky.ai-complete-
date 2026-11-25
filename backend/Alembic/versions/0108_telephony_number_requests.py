"""Telephony number requests: the documents a customer sends to buy a phone number.

Two new tables:

* ``telephony_number_requests``: one row per submission. ``(tenant_id,
  idempotency_key)`` is unique, so a retry is the same request;
  ``request_fingerprint`` tells a true replay from a reused key. Rows are never
  deleted: ``files_deleted_at`` marks that the documents were removed.
  ``number_released_at`` is reserved for a later task.
* ``telephony_number_request_documents``: one row per uploaded document. The
  file lives encrypted outside the database; ``wrapped_key`` is its wrapped
  data key. Deleting a document sets ``files_deleted_at`` and clears
  ``wrapped_key`` and ``file_name``, so a leftover copy can no longer be read.
  No email, address or phone column is stored.

Row security is enabled and forced on both. No GRANT is issued (the runtime
role's rights come from runtime_role_grants.sql). Old code never reads these
tables, so rolling the code back without a downgrade is safe.
"""
from alembic import op

revision = "0108_telephony_number_requests"
down_revision = "0107_staff_suspension"
branch_labels = None
depends_on = None

BYPASS = "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
TENANT = "tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.telephony_number_requests (
        id                   UUID PRIMARY KEY,
        tenant_id            UUID NOT NULL REFERENCES tenants(id),
        requested_by_user_id UUID NOT NULL,
        use_type             TEXT NOT NULL CHECK (use_type IN ('business', 'personal')),
        status               TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'approved', 'rejected')),
        review_note          TEXT CHECK (review_note IS NULL OR char_length(review_note) <= 1000),
        reviewed_by_user_id  UUID,
        reviewed_at          TIMESTAMPTZ,
        idempotency_key      TEXT NOT NULL CHECK (idempotency_key ~ '^[A-Za-z0-9_-]{8,128}$'),
        request_fingerprint  TEXT NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        number_released_at   TIMESTAMPTZ,
        files_deleted_at     TIMESTAMPTZ,
        submitted_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT telephony_number_requests_tenant_key UNIQUE (tenant_id, idempotency_key),
        CONSTRAINT telephony_number_requests_tenant_id_id UNIQUE (tenant_id, id),
        CHECK ((status = 'submitted') = (reviewed_at IS NULL)),
        CHECK ((status = 'submitted') = (reviewed_by_user_id IS NULL)),
        CHECK (number_released_at IS NULL OR status = 'approved'),
        CHECK (files_deleted_at IS NULL OR status <> 'submitted')
    )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS telephony_number_requests_tenant_status_submitted "
        "ON public.telephony_number_requests (tenant_id, status, submitted_at DESC, id DESC)"
    )
    op.execute("""
    CREATE TABLE IF NOT EXISTS public.telephony_number_request_documents (
        id               UUID PRIMARY KEY,
        tenant_id        UUID NOT NULL REFERENCES tenants(id),
        request_id       UUID NOT NULL,
        kind             TEXT NOT NULL CHECK (kind IN ('business_registration', 'business_photo_id',
                             'utility_bill', 'personal_photo_id')),
        storage_key      TEXT NOT NULL UNIQUE CHECK (storage_key ~ '^[0-9a-f-]{36}/[0-9a-f-]{36}[.]tnr$'),
        file_name        TEXT CHECK (file_name IS NULL OR char_length(file_name) BETWEEN 1 AND 120),
        content_type     TEXT NOT NULL CHECK (content_type ~ '^[a-z]+/[a-z0-9.+-]+$'),
        size_bytes       BIGINT NOT NULL CHECK (size_bytes > 0),
        sha256           TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
        wrapped_key      BYTEA,
        key_provider     TEXT NOT NULL CHECK (key_provider ~ '^[a-z_]{1,32}$'),
        scan_status      TEXT NOT NULL DEFAULT 'not_scanned' CHECK (scan_status IN ('not_scanned', 'clean', 'infected')),
        files_deleted_at TIMESTAMPTZ,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT telephony_number_request_documents_request FOREIGN KEY (tenant_id, request_id)
            REFERENCES telephony_number_requests (tenant_id, id),
        CONSTRAINT telephony_number_request_documents_one_kind UNIQUE (tenant_id, request_id, kind),
        CHECK ((files_deleted_at IS NULL) = (wrapped_key IS NOT NULL)),
        CHECK (files_deleted_at IS NOT NULL OR file_name IS NOT NULL)
    )
    """)
    for table in ("telephony_number_requests", "telephony_number_request_documents"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON public.{table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON public.{table} "
            f"FOR ALL USING ({BYPASS} OR {TENANT}) WITH CHECK ({BYPASS} OR {TENANT})"
        )


def downgrade():
    raise RuntimeError(
        "Telephony number requests hold customer documents and must be retained; "
        "use a reviewed forward migration"
    )

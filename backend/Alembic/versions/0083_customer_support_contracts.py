"""Repair four existing customer/support contracts missing from fresh bootstrap.

complete_schema.sql plus the documented 0008 stamp skips the telephony creator;
AI credentials and recording policy existed only in manually applied SQL. The
registered emergency-access routes likewise depend on a historical table.

Only absent objects are created. Existing objects must match the reviewed
contract, including ownership, constraints, indexes, triggers and tenant RLS.
PostgreSQL parses temporary reference objects so expression comparisons cannot
accidentally accept a different CHECK/default/policy through text rewriting.
Unknown objects are not adopted, rewritten, granted, or removed. All validation
and creation runs in Alembic's transaction with a bounded lock wait. The sole
data-hygiene change removes historical plaintext verification_code fields from
emergency approvals, retaining every other field and approval order.
"""
from __future__ import annotations

import logging

from alembic import op
from sqlalchemy import text

revision = "0083_customer_support_contracts"
down_revision = "0082_ubuntu_admin_merge"
branch_labels = None
depends_on = None

TENANT_POLICY = (
    "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
    " OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"
)
TOUCH_BODY = "\nBEGIN\n    NEW.updated_at = NOW();\n    RETURN NEW;\nEND;\n"

# Explicitly copied from the original creators, with 0067's ACTIVE-only unique
# index. FKs are separate so temporary references need no permanent-table FK.
TABLES = {
    "tenant_ai_credentials": dict(
        columns="""
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL,
            provider text NOT NULL CHECK (char_length(provider)>0 AND char_length(provider)<=64),
            credential_kind text NOT NULL DEFAULT 'api_key'
                CHECK (credential_kind IN ('api_key','oauth_refresh_token','service_account_json')),
            encrypted_key text NOT NULL,
            last4 text CHECK (last4 IS NULL OR char_length(last4)<=8),
            label text,
            status text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            last_used_at timestamptz,
            rotated_at timestamptz
        """,
        foreign_keys=("FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE",),
        indexes=(
            ("idx_tenant_ai_credentials_lookup", "(tenant_id, provider, credential_kind) WHERE status='active'", False),
            ("uq_tenant_ai_credentials_active", "(tenant_id, provider, credential_kind) WHERE status='active'", True),
        ), tenant=True, touch=True,
    ),
    "tenant_recording_policy": dict(
        columns="""
            tenant_id uuid PRIMARY KEY,
            default_consent_mode text NOT NULL DEFAULT 'two_party'
                CHECK (default_consent_mode IN ('one_party','two_party','disabled')),
            announcement_text text NOT NULL DEFAULT
                'This call may be recorded for quality and training purposes. Press 9 at any time to opt out of recording.',
            opt_out_dtmf_digit text DEFAULT '9'
                CHECK (opt_out_dtmf_digit IS NULL OR opt_out_dtmf_digit ~ '^[0-9*#]$'),
            two_party_country_codes text[] NOT NULL DEFAULT ARRAY[
                'US-CA','US-MA','US-IL','US-WA','US-FL','US-MD','US-MT','US-NH',
                'US-PA','US-CT','US-DE','US-MI','US-NV','US-OR','CA','AT','BE','BG',
                'HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IE','IT','LV','LT',
                'LU','MT','NL','PL','PT','RO','SK','SI','ES','SE','GB','AU']::text[],
            retention_days integer NOT NULL DEFAULT 90 CHECK (retention_days>0 AND retention_days<=3650),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        """,
        foreign_keys=("FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE",),
        indexes=(), tenant=True, touch=True,
    ),
    "tenant_telephony_credentials": dict(
        columns="""
            id uuid PRIMARY KEY DEFAULT public.uuid_generate_v4(),
            tenant_id uuid NOT NULL,
            provider text NOT NULL CHECK (provider IN ('twilio','vonage')),
            label text,
            credentials_encrypted text NOT NULL,
            from_number text,
            status text NOT NULL DEFAULT 'inactive' CHECK (status IN ('active','inactive','failed')),
            last_tested_at timestamptz,
            last_test_result jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id,provider)
        """,
        foreign_keys=("FOREIGN KEY (tenant_id) REFERENCES public.tenants(id) ON DELETE CASCADE",),
        indexes=(("idx_ttc_tenant", "(tenant_id)", False),), tenant=True, touch=False,
    ),
    "emergency_access_requests": dict(
        columns="""
            request_id uuid PRIMARY KEY DEFAULT public.uuid_generate_v4(),
            created_at timestamptz NOT NULL DEFAULT now(),
            requestor_id uuid NOT NULL,
            scenario varchar(50) NOT NULL,
            justification text NOT NULL,
            requested_access text[] NOT NULL,
            approvers_required integer NOT NULL DEFAULT 2,
            approvals jsonb DEFAULT '[]'::jsonb,
            status varchar(20) NOT NULL DEFAULT 'pending'
                CHECK (status::text=ANY(ARRAY['pending'::varchar::text,'approved'::varchar::text,
                    'denied'::varchar::text,'expired'::varchar::text,'used'::varchar::text,'cancelled'::varchar::text])),
            approved_at timestamptz,
            expires_at timestamptz NOT NULL,
            session_created_at timestamptz,
            session_terminated_at timestamptz,
            session_token_hash varchar(64),
            actions_taken jsonb DEFAULT '[]'::jsonb,
            reviewed_at timestamptz,
            reviewed_by uuid,
            review_notes text
        """,
        foreign_keys=(
            "FOREIGN KEY (requestor_id) REFERENCES public.user_profiles(id)",
            "FOREIGN KEY (reviewed_by) REFERENCES public.user_profiles(id)",
        ),
        indexes=(
            ("idx_emergency_access_created", "(created_at DESC)", False),
            ("idx_emergency_access_expires", "(expires_at) WHERE status='approved'", False),
            ("idx_emergency_access_requestor", "(requestor_id)", False),
            ("idx_emergency_access_status", "(status)", False),
        ), tenant=False, touch=False,
    ),
}


def _rows(conn, query, **params):
    return list(conn.execute(text(query), params).tuples())


def _refuse(condition, table, part):
    if not condition:
        raise RuntimeError(f"0083 refuses unreviewed {part} on {table}; no adoption or ownership changes performed")


def _shape(conn, relation):
    columns = _rows(conn, """
        SELECT attnum,attname,atttypid,atttypmod,attnotnull,attidentity,attgenerated,
               attcollation,pg_get_expr(d.adbin,d.adrelid)
        FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE a.attrelid=to_regclass(:rel) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum
    """, rel=relation)
    constraints = _rows(conn, """
        SELECT contype,condeferrable,condeferred,convalidated,pg_get_constraintdef(oid,true)
        FROM pg_constraint WHERE conrelid=to_regclass(:rel) AND contype<>'f'
        ORDER BY contype,pg_get_constraintdef(oid,true)
    """, rel=relation)
    indexes = _rows(conn, """
        SELECT i.indisunique,i.indisprimary,i.indisexclusion,i.indimmediate,i.indisvalid,i.indisready,
               i.indnullsnotdistinct,i.indnkeyatts,i.indnatts,i.indkey::text,i.indcollation::text,
               i.indclass::text,i.indoption::text,pg_get_expr(i.indexprs,i.indrelid),
               pg_get_expr(i.indpred,i.indrelid),am.amname
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am am ON am.oid=c.relam
        WHERE i.indrelid=to_regclass(:rel)
        ORDER BY i.indisprimary,i.indisunique,i.indkey::text,pg_get_expr(i.indpred,i.indrelid)
    """, rel=relation)
    return columns, constraints, indexes


def _create_reference(conn, table, contract):
    ref = f"pg_temp.support_0083_{table}"
    conn.execute(text(f"CREATE TEMPORARY TABLE {ref} ({contract['columns']}) ON COMMIT DROP"))
    for number, (_, clause, unique) in enumerate(contract["indexes"]):
        conn.execute(text(f"CREATE {'UNIQUE ' if unique else ''}INDEX support_0083_{table}_{number} ON {ref} {clause}"))
    if contract["tenant"]:
        conn.execute(text(f"CREATE POLICY reference ON {ref} FOR ALL USING ({TENANT_POLICY}) WITH CHECK ({TENANT_POLICY})"))
    return ref


def _check_policy(conn, table, ref, contract):
    def policies(rel):
        return _rows(conn, """SELECT polcmd,polpermissive,polroles::text,
            pg_get_expr(polqual,polrelid),pg_get_expr(polwithcheck,polrelid)
            FROM pg_policy WHERE polrelid=to_regclass(:rel) ORDER BY polname""", rel=rel)
    _refuse(policies(f"public.{table}") == policies(ref), table, "RLS policy semantics")
    expected_names = [(f"{table}_tenant_isolation",)] if contract["tenant"] else []
    _refuse(_rows(conn, "SELECT polname FROM pg_policy WHERE polrelid=to_regclass(:rel) ORDER BY polname",
                  rel=f"public.{table}") == expected_names, table, "RLS policy ownership")


def _check_touch_function(conn, table, must_exist):
    function = f"{table}_touch_updated_at"
    rows = _rows(conn, """SELECT pg_get_userbyid(p.proowner)=current_user,p.prosecdef,p.prokind,
        p.prorettype='trigger'::regtype,p.pronargs,p.prosrc,p.proconfig,l.lanname,
        p.provolatile,p.proparallel
        FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang
        WHERE p.pronamespace='public'::regnamespace AND p.proname=:name""", name=function)
    if not rows and not must_exist:
        return False
    _refuse(len(rows) == 1, table, "trigger function overloads")
    row = rows[0]
    _refuse(row[:5] == (True, False, 'f', True, 0) and row[5].strip() == TOUCH_BODY.strip()
            and row[6:] == (None, 'plpgsql', 'v', 'u'), table, "trigger function body/ownership")
    return True


def _check_existing(conn, table, ref, contract):
    # Locks precede catalog reads so concurrent DDL cannot invalidate the guard.
    conn.execute(text(f"LOCK TABLE public.{table} IN ACCESS EXCLUSIVE MODE"))
    state = _rows(conn, """SELECT relkind,pg_get_userbyid(relowner)=current_user,
        relrowsecurity,relforcerowsecurity,relpersistence,relispartition
        FROM pg_class WHERE oid=to_regclass(:rel)""", rel=f"public.{table}")[0]
    tenant = contract["tenant"]
    _refuse(state == ('r', True, tenant, tenant, 'p', False), table, "table ownership/RLS flags")
    _refuse(not _rows(conn, "SELECT 1 FROM pg_inherits WHERE inhrelid=to_regclass(:rel) OR inhparent=to_regclass(:rel)",
                      rel=f"public.{table}"), table, "inheritance")
    actual = _shape(conn, f"public.{table}")
    expected = _shape(conn, ref)
    for name, a, e in zip(("columns/defaults", "constraints", "indexes"), actual, expected):
        _refuse(a == e, table, name)
    fks = _rows(conn, """SELECT pg_get_constraintdef(oid,true),convalidated,condeferrable,condeferred
        FROM pg_constraint WHERE conrelid=to_regclass(:rel) AND contype='f' ORDER BY pg_get_constraintdef(oid,true)""",
                rel=f"public.{table}")
    # pg_get_constraintdef omits the visible public schema and formats columns.
    expected_fks = sorted((fk.replace("public.", ""), True, False, False)
                          for fk in contract["foreign_keys"])
    _refuse(fks == expected_fks, table, "foreign keys")
    _check_policy(conn, table, ref, contract)
    triggers = _rows(conn, """SELECT t.tgname,t.tgenabled,t.tgtype,t.tgnargs,t.tgargs,
        t.tgattr::text,pg_get_expr(t.tgqual,t.tgrelid),n.nspname,p.proname
        FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE t.tgrelid=to_regclass(:rel) AND NOT t.tgisinternal ORDER BY t.tgname""", rel=f"public.{table}")
    if contract["touch"]:
        _check_touch_function(conn, table, True)
        _refuse(len(triggers) == 1, table, "trigger inventory")
        t = triggers[0]
        _refuse(t[:4] == (f"trg_{table}_updated_at", 'O', 19, 0) and bytes(t[4]) == b''
                and t[5:] == ('', None, 'public', f"{table}_touch_updated_at"), table, "trigger behavior")
    else:
        _refuse(not triggers, table, "trigger inventory")


def _check_selection_column(conn):
    """0005's tenant selection pointer is missing from the same 0008 snapshot."""
    conn.execute(text("LOCK TABLE public.tenants IN ACCESS EXCLUSIVE MODE"))
    _refuse(conn.execute(text("SELECT relkind='r' AND pg_get_userbyid(relowner)=current_user FROM pg_class WHERE oid='public.tenants'::regclass")).scalar(),
            "tenants", "table ownership/kind")
    conn.execute(text("""CREATE TEMP TABLE support_0083_selection (
        active_telephony_provider text DEFAULT 'none'
        CHECK (active_telephony_provider IN ('twilio','vonage','sip','none'))
    ) ON COMMIT DROP"""))
    def column(rel):
        return _rows(conn, """SELECT a.atttypid,a.atttypmod,a.attnotnull,a.attidentity,a.attgenerated,
            a.attcollation,pg_get_expr(d.adbin,d.adrelid)
            FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=to_regclass(:rel) AND a.attname='active_telephony_provider'
                AND a.attnum>0 AND NOT a.attisdropped""", rel=rel)
    existing = column("public.tenants")
    if existing:
        _refuse(existing == column("pg_temp.support_0083_selection"), "tenants", "active provider column/default")
        def constraints(rel):
            return _rows(conn, """SELECT c.contype,c.convalidated,c.condeferrable,c.condeferred,
                pg_get_constraintdef(c.oid,true)
                FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=ANY(c.conkey)
                WHERE c.conrelid=to_regclass(:rel) AND a.attname='active_telephony_provider'
                ORDER BY pg_get_constraintdef(c.oid,true)""", rel=rel)
        _refuse(constraints("public.tenants") == constraints("pg_temp.support_0083_selection"),
                "tenants", "active provider constraint")
    else:
        _refuse(not _rows(conn, """SELECT 1 FROM pg_constraint WHERE conrelid='public.tenants'::regclass
            AND conname='tenants_active_telephony_provider_check'"""), "tenants", "active provider constraint collision")
    conn.execute(text("DROP TABLE pg_temp.support_0083_selection"))
    return not existing


def upgrade():
    conn = op.get_bind()
    conn.execute(text("SET LOCAL lock_timeout='5s'"))
    missing = []
    references = []
    # Validate every existing contract before creating any persistent object.
    missing_selection = _check_selection_column(conn)
    for table, contract in sorted(TABLES.items()):
        ref = _create_reference(conn, table, contract)
        references.append(ref)
        exists = conn.execute(text("SELECT to_regclass(:rel) IS NOT NULL"), {"rel": f"public.{table}"}).scalar()
        if exists:
            _check_existing(conn, table, ref, contract)
        else:
            if contract["touch"]:
                _check_touch_function(conn, table, False)
            missing.append(table)
    if "emergency_access_requests" not in missing:
        _refuse(not conn.execute(text("""SELECT EXISTS(SELECT 1 FROM public.emergency_access_requests
            WHERE approvals IS NOT NULL AND jsonb_typeof(approvals)<>'array')""")).scalar(),
            "emergency_access_requests", "non-array approvals; reconcile privately")
    if missing_selection:
        conn.execute(text("ALTER TABLE public.tenants ADD COLUMN active_telephony_provider text DEFAULT 'none'"))
        conn.execute(text("""ALTER TABLE public.tenants ADD CONSTRAINT tenants_active_telephony_provider_check
            CHECK (active_telephony_provider IN ('twilio','vonage','sip','none'))"""))
    for table in missing:
        contract = TABLES[table]
        conn.execute(text(f"CREATE TABLE public.{table} ({contract['columns']}, {', '.join(contract['foreign_keys'])})"))
        for name, clause, unique in contract["indexes"]:
            conn.execute(text(f"CREATE {'UNIQUE ' if unique else ''}INDEX {name} ON public.{table} {clause}"))
        if contract["touch"]:
            if not _check_touch_function(conn, table, False):
                conn.execute(text(f"CREATE FUNCTION public.{table}_touch_updated_at() RETURNS trigger LANGUAGE plpgsql AS $touch${TOUCH_BODY}$touch$"))
            conn.execute(text(f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON public.{table} FOR EACH ROW EXECUTE FUNCTION public.{table}_touch_updated_at()"))
        if contract["tenant"]:
            conn.execute(text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
            conn.execute(text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))
            conn.execute(text(f"CREATE POLICY {table}_tenant_isolation ON public.{table} FOR ALL USING ({TENANT_POLICY}) WITH CHECK ({TENANT_POLICY})"))
    # Never select approval contents into Python/logs. No status, session, actor,
    # review or verification marker is changed, and no legacy approval is blessed.
    scrubbed = conn.execute(text("""
        UPDATE public.emergency_access_requests AS request
        SET approvals=(SELECT jsonb_agg(
            CASE WHEN jsonb_typeof(item.value)='object'
                THEN item.value - 'verification_code' ELSE item.value END ORDER BY item.ordinality)
            FROM jsonb_array_elements(request.approvals) WITH ORDINALITY AS item(value,ordinality))
        WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(request.approvals) AS item(value)
            WHERE jsonb_typeof(item.value)='object' AND item.value ? 'verification_code')
    """))
    logging.getLogger("alembic.runtime.migration").info(
        "0083 removed historical emergency verification codes from %s request(s)", scrubbed.rowcount
    )
    for ref in references:
        conn.execute(text(f"DROP TABLE {ref}"))


def downgrade():
    raise RuntimeError("0083 is forward-only: refusing to erase customer credentials, recording policy or support evidence")

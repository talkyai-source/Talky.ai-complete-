"""Reconstruct reviewed legacy schema with synthetic contents; no live inputs.

Four definitions come from the checked-in historical snapshot, worker_status
from its original creator, and the backup's reviewed nullable column layout.
The resulting six definitions must match the independently observed catalog
fingerprints before the explicit preserved-inventory profile can be exercised.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]


def schema_sql():
    snapshot = (ROOT / "backend/database/schema/baseline_2026-06-02.sql").read_text(encoding="utf-8")
    statements = []
    names = ("audit_chain_state", "audit_event_types", "rate_limit_events", "suspension_propagation_queue")
    for table in names:
        match = re.search(rf"CREATE TABLE public\.{table} \([\s\S]*?\n\);", snapshot)
        if not match:
            raise RuntimeError("Historical fixture table definition missing")
        statements.append(match.group())
    for pattern in (
        r"CREATE SEQUENCE public\.audit_chain_state_id_seq[\s\S]*?;",
        r"ALTER SEQUENCE public\.audit_chain_state_id_seq OWNED BY[^;]*;",
        r"ALTER TABLE ONLY public\.audit_chain_state ALTER COLUMN id SET DEFAULT[^;]*;",
    ):
        statements.append(re.search(pattern, snapshot).group())
    for table in names:
        statements += re.findall(rf"ALTER TABLE ONLY public\.{table}\s+ADD CONSTRAINT[\s\S]*?;", snapshot)
        statements += re.findall(rf"CREATE (?:UNIQUE )?INDEX [^\n]+ ON public\.{table} [^;]+;", snapshot)
    workers = (ROOT / "backend/database/migrations/20260706_add_admin_health_tables.sql").read_text(encoding="utf-8")
    statements.append(re.search(r"CREATE TABLE IF NOT EXISTS worker_status \([\s\S]*?\n\);", workers).group().replace(
        "CREATE TABLE IF NOT EXISTS worker_status", "CREATE TABLE public.worker_status", 1))
    statements.append(re.search(r"CREATE INDEX IF NOT EXISTS idx_worker_status_heartbeat[\s\S]*?;", workers).group())
    statements.append("""CREATE TABLE public.tenant_ai_configs_backup_20260907 (
        id uuid, tenant_id uuid, llm_provider varchar(50), llm_model text,
        llm_temperature double precision, llm_max_tokens integer,
        stt_provider varchar(50), stt_model text, stt_language varchar(16),
        tts_provider varchar(50), tts_model text, tts_voice_id text, tts_sample_rate integer,
        created_at timestamptz, updated_at timestamptz, voice_tuning jsonb,
        stt_engine text, pipeline_mode text, realtime_model text, realtime_voice text,
        realtime_settings jsonb
    );
    ALTER TABLE public.tenant_ai_configs_backup_20260907 ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.tenant_ai_configs_backup_20260907 FORCE ROW LEVEL SECURITY;
    CREATE POLICY tenant_ai_configs_backup_20260907_tenant_isolation
        ON public.tenant_ai_configs_backup_20260907 FOR ALL
        USING (COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)
          OR tenant_id=NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid)
        WITH CHECK (COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)
          OR tenant_id=NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid);
    """)
    return "\n".join(statements)


def run_checks(grants, owner, runtime):
    import psycopg2
    profile = grants.PRESERVED_PROFILE["profile"]
    tables = list(grants.PRESERVED_PROFILE["owner_only_tables"])

    def sql(statement):
        with grants.connect(owner) as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement)
            connection.commit()

    def denied(mode="verify", fragment=None, selected=profile):
        try:
            grants.execute(owner, runtime, mode, selected)
        except grants.GrantsError as error:
            if fragment:
                grants.require(fragment in str(error), "Preserved inventory refused for an unexpected boundary")
        else:
            raise RuntimeError("Unreviewed preserved inventory or privilege was accepted")

    sql(schema_sql())
    denied("plan", "object inventory", selected=grants.PROFILE["profile"])
    grants.require(grants.execute(owner, runtime, "plan", profile)["status"] == "plan_ready", "Explicit preserved profile did not validate")
    sql("""SET LOCAL app.bypass_rls='true';
        INSERT INTO audit_chain_state(last_event_hash) VALUES('synthetic-checkpoint');
        INSERT INTO audit_event_types(event_type,category,severity,retention_days)
            VALUES('synthetic-support','synthetic','info',30);
        INSERT INTO rate_limit_events(tier,scope_key,action_taken) VALUES('ip','synthetic-scope','allow');
        INSERT INTO worker_status(name) VALUES('synthetic-worker');
        INSERT INTO tenant_ai_configs_backup_20260907(tenant_id,llm_model)
            VALUES('11111111-1111-4111-8111-111111111111','synthetic-history');
    """)
    snapshot_sql = "SET LOCAL app.bypass_rls='true'; SELECT jsonb_build_object(" + ",".join(
        f"'{table}',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM public.{table} t)"
        for table in tables) + ")"
    with grants.connect(owner) as connection:
        with connection.cursor() as cursor:
            cursor.execute(snapshot_sql)
            before = cursor.fetchone()[0]
    initial = grants.execute(owner, runtime, "apply", profile)
    grants.require(initial["tables"] == 123 and initial["preserved_tables"] == 6, "Preserved inventory count incorrect")
    grants.require(grants.execute(owner, runtime, "apply", profile)["policies_sha256"] == initial["policies_sha256"], "Repeat apply changed policies")
    grants.require(grants.execute(owner, runtime, "verify", profile)["status"] == "verified", "Preserved profile independent verify failed")
    with grants.connect(runtime) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL app.bypass_rls='true'")
            for statement in [f"SELECT * FROM public.{table}" for table in tables] + ["SELECT nextval('public.audit_chain_state_id_seq')"]:
                cursor.execute("SAVEPOINT denied_preserved_read")
                try:
                    cursor.execute(statement)
                except psycopg2.errors.InsufficientPrivilege:
                    cursor.execute("ROLLBACK TO SAVEPOINT denied_preserved_read")
                else:
                    raise RuntimeError("Runtime read a preserved object without authorization")
                cursor.execute("RELEASE SAVEPOINT denied_preserved_read")
        connection.rollback()
    # PUBLIC column-only rights are independent evidence: verify must reject
    # them, and an explicit apply must remove both PUBLIC and runtime rights.
    sql(f"GRANT SELECT(tenant_id) ON tenant_ai_configs_backup_20260907 TO PUBLIC,{grants.ident(runtime.values['user'])}")
    denied(fragment="column-specific")
    grants.execute(owner, runtime, "apply", profile)
    sql("CREATE TABLE public.unreviewed_preserved_extra(id integer)")
    denied("plan", "object inventory")
    sql("DROP TABLE public.unreviewed_preserved_extra")
    sql("ALTER TABLE worker_status ALTER COLUMN status SET DEFAULT 'unreviewed'")
    denied("apply", "structural fingerprint")
    sql("ALTER TABLE worker_status ALTER COLUMN status SET DEFAULT 'idle'")
    grants.execute(owner, runtime, "verify", profile)
    with grants.connect(owner) as connection:
        with connection.cursor() as cursor:
            cursor.execute(snapshot_sql)
            grants.require(cursor.fetchone()[0] == before, "Preserved inventory row contents changed")
    return ["explicit 123-table TLS profile: six reviewed objects/sequence preserved with runtime/PUBLIC access denied, runtime/PUBLIC column privileges denied, schema drift/unknown inventory refused, repeat apply and synthetic contents unchanged"]

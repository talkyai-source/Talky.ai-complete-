"""Customer settings contracts under the actual restricted TLS runtime login.

Called only by the disposable canonical grants rehearsal. Synthetic writes all
roll back; no credentials, customer data, provider network calls or live target.
"""
import importlib.util
from pathlib import Path


def run_checks(runtime, tenants):
    spec = importlib.util.spec_from_file_location("support_runtime_grants", Path(__file__).parents[1] / "runtime_grants.py")
    grants = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(grants)
    import psycopg2

    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    with grants.connect(runtime) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL app.bypass_rls='true'")
                for tenant in tenants:
                    cursor.execute("""INSERT INTO tenant_ai_credentials
                        (tenant_id,provider,encrypted_key,status,updated_at)
                        VALUES (%s,'groq','synthetic-disabled-1','disabled','2000-01-01'),
                               (%s,'groq','synthetic-disabled-2','disabled','2000-01-01'),
                               (%s,'groq','synthetic-active','active','2000-01-01')""", (tenant, tenant, tenant))
                    cursor.execute("INSERT INTO tenant_recording_policy(tenant_id) VALUES (%s)", (tenant,))
                    cursor.execute("""INSERT INTO tenant_telephony_credentials
                        (tenant_id,provider,credentials_encrypted) VALUES (%s,'vonage','synthetic-ciphertext')""", (tenant,))
                cursor.execute("SELECT active_telephony_provider FROM tenants WHERE id=%s", (tenants[0],))
                require(cursor.fetchone() == ('none',), "Fresh telephony selection contract is missing")
                cursor.execute("SELECT request_id,status,expires_at FROM emergency_access_requests LIMIT 0")
                cursor.execute("SET LOCAL app.bypass_rls='false'")
                for tenant in tenants:
                    cursor.execute("SELECT set_config('app.current_tenant_id',%s,true)", (tenant,))
                    for table, count in (("tenant_ai_credentials", 3), ("tenant_recording_policy", 1), ("tenant_telephony_credentials", 1)):
                        cursor.execute(f"SELECT count(*),count(DISTINCT tenant_id) FROM {table}")
                        require(cursor.fetchone() == (count, 1), "Customer settings tenant scope is incorrect")
                    cursor.execute("SELECT default_consent_mode,retention_days FROM tenant_recording_policy")
                    require(cursor.fetchone() == ('two_party', 90), "Recording policy defaults changed")
                cursor.execute("SELECT set_config('app.current_tenant_id',%s,true)", (tenants[0],))
                cursor.execute("UPDATE tenant_recording_policy SET retention_days=30 WHERE tenant_id=%s RETURNING tenant_id", (tenants[1],))
                require(cursor.fetchone() is None, "Customer settings update crossed tenant boundary")
                cursor.execute("SAVEPOINT duplicate_active")
                try:
                    cursor.execute("INSERT INTO tenant_ai_credentials(tenant_id,provider,encrypted_key) VALUES(%s,'groq','duplicate')", (tenants[0],))
                except psycopg2.errors.UniqueViolation:
                    cursor.execute("ROLLBACK TO SAVEPOINT duplicate_active")
                else:
                    raise RuntimeError("More than one active credential was accepted")
                cursor.execute("RELEASE SAVEPOINT duplicate_active")
                cursor.execute("UPDATE tenant_ai_credentials SET label='Synthetic touch' RETURNING updated_at>'2000-01-01'::timestamptz")
                require(all(row[0] for row in cursor.fetchall()), "Credential timestamp trigger did not run under runtime grants")
                cursor.execute("SET LOCAL app.current_tenant_id=''")
                for table in ("tenant_ai_credentials", "tenant_recording_policy", "tenant_telephony_credentials"):
                    cursor.execute(f"SELECT count(*) FROM {table}")
                    require(cursor.fetchone() == (0,), "Blank customer scope exposed settings")
        finally:
            connection.rollback()
    return ["restricted TLS runtime: AI credential history/active uniqueness, recording defaults, telephony selection, emergency schema, tenant/blank-scope isolation and timestamp triggers"]

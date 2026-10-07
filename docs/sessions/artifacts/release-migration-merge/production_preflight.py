"""Production read-only migration preflight. Print aggregates/catalogs only."""
import asyncio
import json

import asyncpg
from dotenv import dotenv_values


async def inspect(conn):
    output = {}

    async def rows(key, sql, *args):
        try:
            async with conn.transaction():
                value = [dict(r) for r in await conn.fetch(sql, *args)]
            output[key] = value
            return value
        except Exception as exc:
            output[key] = {"error_type": type(exc).__name__}
            return None

    await rows("database_state", "SELECT version_num FROM public.alembic_version ORDER BY version_num")
    await rows("transaction", "SELECT current_setting('transaction_read_only') AS read_only, current_setting('transaction_isolation') AS isolation")
    await rows("current_role", """SELECT r.rolname,r.rolsuper,r.rolbypassrls,r.rolinherit,
        has_database_privilege(current_user,current_database(),'CONNECT') AS database_connect,
        has_schema_privilege(current_user,'public','USAGE') AS schema_usage,
        has_schema_privilege(current_user,'public','CREATE') AS schema_create
        FROM pg_roles r WHERE r.rolname=current_user""")
    await rows("existing_table_ownership", """SELECT c.relname,pg_get_userbyid(c.relowner) AS owner,
        pg_has_role(current_user,c.relowner,'USAGE') AS inherits_owner,
        has_table_privilege(current_user,c.oid,'SELECT') AS can_select,
        has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,
        has_table_privilege(current_user,c.oid,'UPDATE') AS can_update,
        c.relrowsecurity,c.relforcerowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY($1::text[]) ORDER BY c.relname""",
        ["user_profiles", "mfa_challenges", "dialer_jobs", "dnc_entries", "calls", "leads", "lead_details",
         "invoices", "topup_orders", "assistant_actions", "processed_webhook_events", "plans", "permissions", "role_permissions"])
    await rows("default_grants", """SELECT pg_get_userbyid(d.defaclrole) AS owner,
        COALESCE(n.nspname,'all_schemas') AS schema,d.defaclobjtype::text AS object_type,
        CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS grantee,
        a.privilege_type,a.is_grantable
        FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid=d.defaclnamespace,
        LATERAL aclexplode(d.defaclacl) a
        WHERE d.defaclrole=(SELECT oid FROM pg_roles WHERE rolname=current_user)
        ORDER BY schema,object_type,grantee,privilege_type""")
    await rows("new_table_name_collisions", """SELECT name,to_regclass('public.'||name) IS NOT NULL AS already_exists
        FROM unnest($1::text[]) name ORDER BY name""", ["crm_deliveries", "public_contact_enquiries",
        "plan_price_options", "billing_checkout_attempts", "billing_webhook_notifications", "billing_webhook_review_log",
        "invoice_snapshots", "billing_refund_snapshots", "assistant_action_resolutions"])
    columns = await rows("identity_columns", """SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped AND
        ((c.relname='user_profiles' AND a.attname=ANY(ARRAY['is_verified','verification_token','email_verified_at']))
         OR (c.relname='mfa_challenges' AND a.attname='attempts')) ORDER BY c.relname,a.attname""")
    names = {(r["relname"],r["attname"]) for r in columns or []}
    if ("user_profiles", "is_verified") in names:
        stamp = "email_verified_at IS NULL" if ("user_profiles", "email_verified_at") in names else "TRUE"
        token = "verification_token IS NOT NULL" if ("user_profiles", "verification_token") in names else "FALSE"
        await rows("verification_counts", f"""SELECT count(*) AS total_profiles,
            count(*) FILTER(WHERE is_verified IS TRUE) AS verified_profiles,
            count(*) FILTER(WHERE is_verified IS TRUE AND {stamp}) AS verified_missing_timestamp,
            count(*) FILTER(WHERE is_verified IS TRUE AND {token}) AS verified_with_token,
            count(*) FILTER(WHERE is_verified IS TRUE AND ({stamp} OR {token})) AS constraint_violations
            FROM public.user_profiles""")
    else:
        output["verification_counts"] = {"is_verified_absent": True, "migration_default": False}
    if ("mfa_challenges", "attempts") in names:
        await rows("mfa_attempt_counts", """SELECT count(*) FILTER(WHERE attempts<0 OR attempts>100) AS invalid_bounds,
            count(*) FILTER(WHERE attempts IS NULL) AS null_attempts FROM public.mfa_challenges""")
    else:
        output["mfa_attempt_counts"] = {"attempts_absent": True, "migration_default": 0}
    await rows("identity_named_constraints", """SELECT conname,convalidated,pg_get_constraintdef(oid) AS definition
        FROM pg_constraint WHERE (conrelid='public.user_profiles'::regclass AND conname='chk_email_verification_consistency')
        OR (conrelid='public.mfa_challenges'::regclass AND conname='mfa_challenges_attempts_bounds') ORDER BY conname""")
    await rows("active_job_counts", """SELECT count(*) AS active_jobs FROM public.dialer_jobs
        WHERE status IN ('pending','queued','retry_scheduled','processing','calling')""")
    await rows("active_job_duplicates", """SELECT count(*) AS duplicate_lead_groups,COALESCE(sum(n-1),0)::bigint AS excess_jobs
        FROM (SELECT count(*) AS n FROM public.dialer_jobs
        WHERE status IN ('pending','queued','retry_scheduled','processing','calling') GROUP BY lead_id HAVING count(*)>1) s""")
    await rows("active_job_named_index", """SELECT c.relname,c.relkind::text,i.indisunique,i.indisvalid,i.indisready,
        i.indislive,i.indimmediate,i.indnkeyatts,i.indnatts,am.amname,
        i.indrelid='public.dialer_jobs'::regclass AS correct_table,
        pg_get_indexdef(i.indexrelid,1,true) AS first_key,pg_get_expr(i.indpred,i.indrelid) AS predicate
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_index i ON i.indexrelid=c.oid LEFT JOIN pg_am am ON am.oid=c.relam
        WHERE n.nspname='public' AND c.relname='uq_dialer_jobs_one_active_per_lead'""")
    await rows("dnc_columns", """SELECT attname,format_type(atttypid,atttypmod) AS type,attnotnull
        FROM pg_attribute WHERE attrelid='public.dnc_entries'::regclass AND NOT attisdropped
        AND attname=ANY(ARRAY['tenant_id','source','normalized_number','phone_number','added_by','updated_at']) ORDER BY attname""")
    await rows("dnc_source_checks", """SELECT conname,convalidated,pg_get_constraintdef(oid) AS definition
        FROM pg_constraint WHERE conrelid='public.dnc_entries'::regclass AND contype='c'
        AND (SELECT attnum FROM pg_attribute WHERE attrelid='public.dnc_entries'::regclass AND attname='source')=ANY(conkey)
        ORDER BY conname""")
    await rows("dnc_unique_indexes", """SELECT c.relname,k.conname,k.condeferrable,i.indisprimary,i.indisunique,
        i.indisvalid,i.indisready,i.indislive,i.indimmediate,i.indnkeyatts,i.indnatts,am.amname,
        ARRAY(SELECT pg_get_indexdef(i.indexrelid,n,true) FROM generate_series(1,i.indnkeyatts) n) AS keys,
        pg_get_expr(i.indpred,i.indrelid) AS predicate,i.indexprs IS NOT NULL AS has_expressions
        FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_am am ON am.oid=c.relam
        LEFT JOIN pg_constraint k ON k.conindid=i.indexrelid AND k.contype='u'
        WHERE i.indrelid='public.dnc_entries'::regclass AND i.indisunique ORDER BY c.relname""")
    await rows("dnc_named_index_collisions", """SELECT c.relname,c.relkind::text,
        i.indrelid='public.dnc_entries'::regclass AS correct_table FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_index i ON i.indexrelid=c.oid
        WHERE n.nspname='public' AND c.relname IN ('uq_dnc_entries_tenant_number_source','uq_dnc_entries_global_number_source')
        ORDER BY c.relname""")
    await rows("dnc_duplicate_counts", """SELECT count(*) AS duplicate_source_groups,COALESCE(sum(n-1),0)::bigint AS excess_rows
        FROM (SELECT count(*) AS n FROM public.dnc_entries GROUP BY tenant_id,normalized_number,source HAVING count(*)>1) s""")
    await rows("dnc_data_shape_counts", """SELECT count(*) AS total_rows,
        count(*) FILTER(WHERE source IS NULL OR normalized_number IS NULL) AS missing_required_values,
        count(*) FILTER(WHERE length(normalized_number)>50) AS normalized_over_50_chars FROM public.dnc_entries""")
    await rows("existing_additive_column_collisions", """SELECT table_name,column_name FROM information_schema.columns
        WHERE table_schema='public' AND ((table_name='processed_webhook_events' AND column_name=ANY(ARRAY[
          'state','event_payload','legacy_claim','payload_hash','provider_mode','received_at','completed_at','attempt_count','last_error_code','tenant_id']))
        OR (table_name='invoices' AND column_name='notification_history_known')
        OR (table_name='crm_deliveries' AND column_name='contact_effect')) ORDER BY table_name,column_name""")
    await rows("paid_plan_counts", """SELECT count(*) FILTER(WHERE price>0) AS existing_paid_plans,
        count(*) FILTER(WHERE price=0 AND stripe_price_id IS NULL) AS free_options_seeded
        FROM public.plans""")
    return output


async def main():
    values = dotenv_values('/opt/talky/backend/.env')
    dsn = values.get('DATABASE_URL')
    if not isinstance(dsn,str) or not dsn:
        return {"error_type": "MissingDatabaseURL"}
    conn = await asyncpg.connect(dsn, timeout=10, command_timeout=20)
    try:
        async with conn.transaction(isolation='repeatable_read', readonly=True):
            await conn.execute("SET LOCAL statement_timeout='15s'")
            await conn.execute("SET LOCAL lock_timeout='3s'")
            await conn.execute("SET LOCAL app.bypass_rls='true'")
            return await inspect(conn)
    finally:
        await conn.close()


if __name__ == '__main__':
    try:
        result = asyncio.run(main())
    except Exception as exc:
        result = {"error_type": type(exc).__name__}
    print(json.dumps(result, indent=2, default=str))

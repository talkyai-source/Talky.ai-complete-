"""Stop auditing heartbeat-only updates on the tenant policy tables.

Production, 2026-10-07: ``tenant_policy_audit_log`` held 3,663,542 rows and
8.5 GB, which was 94% of the whole database. 3,663,315 of those rows recorded
an UPDATE on ``tenant_sip_trunks`` whose only changed columns were
``live_status_checked_at`` and ``updated_at``:

  * ``scripts/trunk_live_status_updater.py`` runs every 15 seconds
    (``talky-trunk-status.timer``) and stamps ``live_status_checked_at`` on
    every trunk, so the Settings card can show "checked N seconds ago";
  * ``trg_audit_tenant_sip_trunks`` wrote a full before/after copy of the row
    for every one of those stamps: about 130,000 rows a day, retained to 2027.

A timestamp that says "we looked" is not a policy mutation. The trigger now
skips an UPDATE when nothing changed except the operational timestamps below.
Every other change is still audited exactly as before, including the real
trunk events found in the same table: registration status flips, is_active
toggles, test results, metadata and user edits. INSERT and DELETE are always
audited.

The function keeps its signature (RETURNS trigger, no arguments), which is the
contract 0033's bootstrap verifier checks. Downgrade restores the body that
was live on production before this migration, byte for byte.

Existing heartbeat rows are NOT touched here: the table is append-only by RLS
(UPDATE/DELETE policies are ``false`` under FORCE), and removing them is a
separate, owner-approved maintenance step.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0048_audit_skip_heartbeat_updates"
down_revision: Union[str, None] = "0047_protect_ai_config_backup"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Columns that only record that a row was looked at or touched. An UPDATE that
# changes nothing else is not audited.
HEARTBEAT_COLUMNS = ("live_status_checked_at", "updated_at")

FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION public.log_tenant_policy_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
            DECLARE
                event_tenant_id UUID;
                event_record_id UUID;
                before_data JSONB := NULL;
                after_data JSONB := NULL;
                actor_setting TEXT;
                actor_uuid UUID := NULL;
                request_id_setting TEXT;
                correlation_id_setting TEXT;
                merged_data JSONB;
                changed_cols TEXT[] := ARRAY[]::TEXT[];
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    event_tenant_id := NEW.tenant_id;
                    event_record_id := NEW.id;
                    after_data := to_jsonb(NEW);
                ELSIF TG_OP = 'UPDATE' THEN
                    event_tenant_id := NEW.tenant_id;
                    event_record_id := NEW.id;
                    before_data := to_jsonb(OLD);
                    after_data := to_jsonb(NEW);
                ELSIF TG_OP = 'DELETE' THEN
                    event_tenant_id := OLD.tenant_id;
                    event_record_id := OLD.id;
                    before_data := to_jsonb(OLD);
                ELSE
                    RAISE EXCEPTION 'Unsupported TG_OP: %', TG_OP;
                END IF;

                merged_data := COALESCE(before_data, '{}'::jsonb)
                    || COALESCE(after_data, '{}'::jsonb);
                SELECT COALESCE(
                    array_agg(keys.key ORDER BY keys.key), ARRAY[]::TEXT[]
                )
                INTO changed_cols
                FROM jsonb_object_keys(merged_data) AS keys(key)
                WHERE COALESCE(before_data -> keys.key, 'null'::jsonb)
                    IS DISTINCT FROM
                    COALESCE(after_data -> keys.key, 'null'::jsonb);

                -- Heartbeat-only update (or no change at all): not a policy
                -- mutation, so nothing is written. See migration 0048.
                IF TG_OP = 'UPDATE' AND changed_cols
                    <@ ARRAY['live_status_checked_at', 'updated_at']::TEXT[] THEN
                    RETURN NEW;
                END IF;

                actor_setting := NULLIF(
                    current_setting('app.current_user_id', true), ''
                );
                IF actor_setting IS NOT NULL THEN
                    BEGIN
                        actor_uuid := actor_setting::UUID;
                    EXCEPTION WHEN others THEN
                        actor_uuid := NULL;
                    END;
                END IF;

                request_id_setting := NULLIF(
                    current_setting('app.current_request_id', true), ''
                );
                correlation_id_setting := request_id_setting;

                INSERT INTO public.tenant_policy_audit_log (
                    tenant_id, table_name, record_id, action, actor_user_id,
                    actor_type, request_id, correlation_id, before_payload,
                    after_payload, changed_fields, source
                )
                VALUES (
                    event_tenant_id, TG_TABLE_NAME, event_record_id, TG_OP,
                    actor_uuid,
                    CASE WHEN actor_uuid IS NULL THEN 'system' ELSE 'user' END,
                    request_id_setting, correlation_id_setting, before_data,
                    after_data, changed_cols, 'db_trigger'
                );

                RETURN COALESCE(NEW, OLD);
            END;
            $function$
"""

# The body live on production before 0048 (pg_get_functiondef, 2026-10-07).
PREVIOUS_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION public.log_tenant_policy_mutation()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
            DECLARE
                event_tenant_id UUID;
                event_record_id UUID;
                before_data JSONB := NULL;
                after_data JSONB := NULL;
                actor_setting TEXT;
                actor_uuid UUID := NULL;
                request_id_setting TEXT;
                correlation_id_setting TEXT;
                merged_data JSONB;
                changed_cols TEXT[] := ARRAY[]::TEXT[];
            BEGIN
                IF TG_OP = 'INSERT' THEN
                    event_tenant_id := NEW.tenant_id;
                    event_record_id := NEW.id;
                    after_data := to_jsonb(NEW);
                ELSIF TG_OP = 'UPDATE' THEN
                    event_tenant_id := NEW.tenant_id;
                    event_record_id := NEW.id;
                    before_data := to_jsonb(OLD);
                    after_data := to_jsonb(NEW);
                ELSIF TG_OP = 'DELETE' THEN
                    event_tenant_id := OLD.tenant_id;
                    event_record_id := OLD.id;
                    before_data := to_jsonb(OLD);
                ELSE
                    RAISE EXCEPTION 'Unsupported TG_OP: %', TG_OP;
                END IF;

                actor_setting := NULLIF(
                    current_setting('app.current_user_id', true), ''
                );
                IF actor_setting IS NOT NULL THEN
                    BEGIN
                        actor_uuid := actor_setting::UUID;
                    EXCEPTION WHEN others THEN
                        actor_uuid := NULL;
                    END;
                END IF;

                request_id_setting := NULLIF(
                    current_setting('app.current_request_id', true), ''
                );
                correlation_id_setting := request_id_setting;

                merged_data := COALESCE(before_data, '{}'::jsonb)
                    || COALESCE(after_data, '{}'::jsonb);
                SELECT COALESCE(
                    array_agg(keys.key ORDER BY keys.key), ARRAY[]::TEXT[]
                )
                INTO changed_cols
                FROM jsonb_object_keys(merged_data) AS keys(key)
                WHERE COALESCE(before_data -> keys.key, 'null'::jsonb)
                    IS DISTINCT FROM
                    COALESCE(after_data -> keys.key, 'null'::jsonb);

                INSERT INTO public.tenant_policy_audit_log (
                    tenant_id, table_name, record_id, action, actor_user_id,
                    actor_type, request_id, correlation_id, before_payload,
                    after_payload, changed_fields, source
                )
                VALUES (
                    event_tenant_id, TG_TABLE_NAME, event_record_id, TG_OP,
                    actor_uuid,
                    CASE WHEN actor_uuid IS NULL THEN 'system' ELSE 'user' END,
                    request_id_setting, correlation_id_setting, before_data,
                    after_data, changed_cols, 'db_trigger'
                );

                RETURN COALESCE(NEW, OLD);
            END;
            $function$
"""


def upgrade() -> None:
    op.execute(FUNCTION_SQL)


def downgrade() -> None:
    op.execute(PREVIOUS_FUNCTION_SQL)

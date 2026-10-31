"""repair schema contracts skipped by the documented 0021 bootstrap stamp

Revision ID: 0033_bootstrap_contract_repair
Revises: 0032_inbound_billing_hold
Create Date: 2026-08-28 00:00:00.000000

Older bootstrap instructions loaded ``database/complete_schema.sql`` and
falsely stamped it at ``0021_billing_topup`` even though the snapshot had
drifted behind migrations 0009-0021.  The maintained fresh path now stamps the
conservative ``0008_tenant_voice_tuning`` floor and runs the full chain.  This
repair exists for databases already advanced by the historical false stamp.
Most critically, inbound admission selects the skipped campaign knowledge,
TTS-provider and prompt-pin columns unconditionally; quota accounting selects
the skipped ``calls.is_test`` flag.

This migration is an additive, idempotent forward repair for databases already
stamped past those revisions.  It intentionally does NOT replay 0019's
tenant-specific AI-model data move.  The empty AI-migration audit table is
schema; changing six historical tenant configurations is not.

CREATE IF NOT EXISTS is followed by catalog checks for every required column,
index and named constraint, the runtime-critical column semantics, and the
exact forced-RLS policy set.  Detected partial drift aborts the transaction for
operator reconciliation rather than leaving a database that reports head while
known runtime contracts still fail.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from alembic import op
from sqlalchemy import text


revision: str = "0033_bootstrap_contract_repair"
down_revision: str | None = "0032_inbound_billing_hold"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TENANT_POLICY = (
    "COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)"
    " OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid"
)

_RLS_TABLES = (
    "campaign_knowledge_sources",
    "campaign_knowledge_nodes",
    "call_feedback",
    "conversation_reviews",
    "review_reward_ledger",
    "campaign_lead_fields",
    "call_lead_details",
    "topup_orders",
    "billing_ledger",
    # These snapshot tables had four legacy policies that did not honor the
    # transaction-scoped worker/admin bypass.  Migration 0013 was stamped past.
    "tenant_sip_trunks",
    "tenant_codec_policies",
    "tenant_route_policies",
    "tenant_telephony_idempotency",
    "tenant_runtime_policy_versions",
    "tenant_runtime_policy_events",
    "tenant_sip_trust_policies",
    "tenant_telephony_threshold_policies",
    "tenant_telephony_quota_events",
    "tenant_policy_audit_log",
)

# These ledgers may be read and appended in the tenant/bypass domains, but an
# existing row is immutable.  Keep denial in both RLS (least privilege for
# ordinary roles) and a BEFORE trigger (defence for owners/BYPASSRLS roles).
_APPEND_ONLY_RLS_TABLES = frozenset(
    {
        "billing_ledger",
        "tenant_policy_audit_log",
    }
)

_POLICY_AUDIT_TRIGGER_TARGETS = {
    "tenant_sip_trunks": "trg_audit_tenant_sip_trunks",
    "tenant_codec_policies": "trg_audit_tenant_codec_policies",
    "tenant_route_policies": "trg_audit_tenant_route_policies",
    "tenant_sip_trust_policies": "trg_audit_tenant_sip_trust_policies",
    "tenant_runtime_policy_versions": "trg_audit_tenant_runtime_policy_versions",
    "tenant_telephony_threshold_policies": ("trg_audit_tenant_telephony_threshold_policies"),
}

_REQUIRED_COLUMNS = {
    "dialer_jobs": ("failure_category", "failure_reason"),
    "campaigns": (
        "knowledge_mode",
        "knowledge_model",
        "tts_provider",
        "prompt_version_pin",
    ),
    "calls": (
        "lead_id",
        "prompt_template",
        "prompt_version",
        "prompt_hash",
        "is_test",
    ),
    "leads": (
        "business_number",
        "company_name",
        "job_title",
        "best_time_to_call",
        "timezone",
        "calling_notes",
        "preferred_contact_method",
        "do_not_call",
        "full_name",
    ),
    "campaign_knowledge_sources": (
        "id",
        "campaign_id",
        "tenant_id",
        "filename",
        "raw_md",
        "token_count",
        "version",
        "status",
        "error",
        "created_at",
        "updated_at",
    ),
    "campaign_knowledge_nodes": (
        "id",
        "campaign_id",
        "tenant_id",
        "source_id",
        "parent_id",
        "depth",
        "path",
        "position",
        "heading",
        "content",
        "summary",
        "voice_answer",
        "keywords",
        "example_questions",
        "search_text",
        "search_tsv",
        "priority",
        "hit_count",
        "enabled",
        "created_at",
        "updated_at",
    ),
    "call_feedback": (
        "id",
        "tenant_id",
        "call_id",
        "created_by",
        "audio_storage_provider",
        "audio_bucket",
        "audio_key",
        "audio_mime_type",
        "audio_size_bytes",
        "audio_sha256",
        "duration_seconds",
        "transcript",
        "transcript_status",
        "transcript_error",
        "transcription_attempts",
        "transcript_provider",
        "transcript_provider_request_id",
        "transcription_started_at",
        "transcribed_at",
        "created_at",
        "updated_at",
    ),
    "conversation_reviews": (
        "id",
        "tenant_id",
        "call_id",
        "campaign_id",
        "user_id",
        "rating",
        "review_tags",
        "comment",
        "prompt_template",
        "prompt_version",
        "prompt_hash",
        "llm_model",
        "created_at",
        "updated_at",
    ),
    "review_reward_ledger": (
        "id",
        "tenant_id",
        "user_id",
        "review_id",
        "points",
        "reason",
        "awarded_at",
    ),
    "prompt_template_versions": (
        "id",
        "persona_type",
        "template",
        "version",
        "body",
        "body_sha",
        "approved",
        "recorded_at",
    ),
    "ai_config_migrations": (
        "id",
        "tenant_id",
        "old_provider",
        "old_model",
        "new_provider",
        "new_model",
        "reason",
        "batch",
        "migrated_at",
        "rolled_back_at",
    ),
    "campaign_lead_fields": (
        "id",
        "tenant_id",
        "campaign_id",
        "field_key",
        "label",
        "field_type",
        "is_required",
        "agent_visible",
        "user_visible",
        "options",
        "sort_order",
        "created_at",
        "updated_at",
    ),
    "call_lead_details": (
        "id",
        "tenant_id",
        "call_id",
        "campaign_id",
        "lead_id",
        "field_key",
        "field_type",
        "value",
        "source",
        "confirmed",
        "is_required",
        "created_at",
        "updated_at",
    ),
    "topup_packages": (
        "id",
        "code",
        "name",
        "minutes",
        "price_cents",
        "currency",
        "expires_days",
        "is_active",
        "sort_order",
        "created_at",
    ),
    "topup_orders": (
        "id",
        "tenant_id",
        "user_id",
        "package_code",
        "minutes",
        "price_cents",
        "currency",
        "status",
        "provider",
        "provider_session_id",
        "provider_payment_id",
        "created_at",
        "updated_at",
        "paid_at",
    ),
    "billing_ledger": (
        "id",
        "tenant_id",
        "order_id",
        "kind",
        "minutes_delta",
        "amount_cents",
        "currency",
        "provider_event_id",
        "note",
        "created_at",
    ),
    "tenant_policy_audit_log": (
        "id",
        "tenant_id",
        "table_name",
        "record_id",
        "action",
        "actor_user_id",
        "actor_type",
        "request_id",
        "correlation_id",
        "before_payload",
        "after_payload",
        "changed_fields",
        "source",
        "created_at",
        "retention_until",
    ),
}

_REQUIRED_INDEXES = (
    "idx_dialer_jobs_failure_category",
    "idx_cks_campaign",
    "idx_ckn_fts",
    "idx_ckn_trgm",
    "idx_ckn_campaign",
    "idx_ckn_tree",
    "idx_call_feedback_tenant_created",
    "idx_call_feedback_retryable",
    "idx_calls_prompt_version",
    "idx_conversation_reviews_tenant_created",
    "idx_conversation_reviews_call",
    "idx_conversation_reviews_prompt_version",
    "idx_conversation_reviews_tags",
    "idx_review_reward_user_day",
    "idx_prompt_template_versions_persona",
    "idx_campaigns_prompt_pin",
    "idx_calls_is_test",
    "idx_ai_config_migrations_batch",
    "idx_leads_do_not_call",
    "idx_campaign_lead_fields_campaign",
    "idx_call_lead_details_call",
    "idx_call_lead_details_lead",
    "idx_topup_orders_tenant",
    "idx_topup_orders_session",
    "idx_billing_ledger_event",
    "idx_billing_ledger_tenant",
    "idx_tenant_policy_audit_log_tenant_created",
    "idx_tenant_policy_audit_log_tenant_table_created",
    "idx_tenant_policy_audit_log_request_id",
    "idx_tenant_policy_audit_log_retention_until",
)

_REQUIRED_CONSTRAINT_DEFINITIONS = {
    ("campaign_knowledge_sources", "campaign_knowledge_sources_pkey"): ("PRIMARY KEY (id)"),
    (
        "campaign_knowledge_sources",
        "campaign_knowledge_sources_campaign_id_fkey",
    ): "FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE",
    ("campaign_knowledge_sources", "campaign_knowledge_sources_status_check"): (
        "CHECK (status = ANY (ARRAY['processing'::text, 'ready'::text, " "'failed'::text]))"
    ),
    ("campaign_knowledge_nodes", "campaign_knowledge_nodes_pkey"): ("PRIMARY KEY (id)"),
    ("campaign_knowledge_nodes", "campaign_knowledge_nodes_campaign_id_fkey"): (
        "FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE"
    ),
    ("campaign_knowledge_nodes", "campaign_knowledge_nodes_source_id_fkey"): (
        "FOREIGN KEY (source_id) REFERENCES campaign_knowledge_sources(id) " "ON DELETE CASCADE"
    ),
    ("campaign_knowledge_nodes", "campaign_knowledge_nodes_parent_id_fkey"): (
        "FOREIGN KEY (parent_id) REFERENCES campaign_knowledge_nodes(id) " "ON DELETE CASCADE"
    ),
    ("call_feedback", "call_feedback_pkey"): "PRIMARY KEY (id)",
    ("call_feedback", "call_feedback_tenant_id_fkey"): (
        "FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE"
    ),
    ("call_feedback", "call_feedback_call_id_fkey"): (
        "FOREIGN KEY (call_id) REFERENCES calls(id) ON DELETE CASCADE"
    ),
    ("call_feedback", "call_feedback_created_by_fkey"): (
        "FOREIGN KEY (created_by) REFERENCES user_profiles(id) ON DELETE SET NULL"
    ),
    ("call_feedback", "call_feedback_one_note_per_call"): "UNIQUE (call_id)",
    ("call_feedback", "call_feedback_audio_object_unique"): ("UNIQUE (audio_bucket, audio_key)"),
    ("call_feedback", "call_feedback_audio_size_positive"): ("CHECK (audio_size_bytes > 0)"),
    ("call_feedback", "call_feedback_sha256_valid"): (
        "CHECK (audio_sha256::text ~ '^[0-9a-f]{64}$'::text)"
    ),
    ("call_feedback", "call_feedback_duration_nonnegative"): (
        "CHECK (duration_seconds IS NULL OR duration_seconds >= " "0::double precision)"
    ),
    ("call_feedback", "call_feedback_attempts_nonnegative"): (
        "CHECK (transcription_attempts >= 0)"
    ),
    ("call_feedback", "call_feedback_status_valid"): (
        "CHECK (transcript_status::text = ANY (ARRAY['pending'::character "
        "varying, 'done'::character varying, 'failed'::character "
        "varying]::text[]))"
    ),
    ("call_feedback", "call_feedback_storage_provider_valid"): (
        "CHECK (audio_storage_provider::text = ANY (ARRAY['s3'::character "
        "varying, 'local'::character varying]::text[]))"
    ),
    ("conversation_reviews", "conversation_reviews_pkey"): "PRIMARY KEY (id)",
    ("conversation_reviews", "conversation_reviews_tenant_id_fkey"): (
        "FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE"
    ),
    ("conversation_reviews", "conversation_reviews_call_id_fkey"): (
        "FOREIGN KEY (call_id) REFERENCES calls(id) ON DELETE CASCADE"
    ),
    ("conversation_reviews", "conversation_reviews_campaign_id_fkey"): (
        "FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE SET NULL"
    ),
    ("conversation_reviews", "conversation_reviews_user_id_fkey"): (
        "FOREIGN KEY (user_id) REFERENCES user_profiles(id) ON DELETE CASCADE"
    ),
    ("conversation_reviews", "conversation_reviews_one_per_user_per_call"): (
        "UNIQUE (call_id, user_id)"
    ),
    ("conversation_reviews", "conversation_reviews_rating_range"): (
        "CHECK (rating >= 1 AND rating <= 5)"
    ),
    ("conversation_reviews", "conversation_reviews_comment_length"): (
        "CHECK (comment IS NULL OR length(comment) <= 4000)"
    ),
    ("conversation_reviews", "conversation_reviews_tags_known"): (
        "CHECK (review_tags <@ ARRAY['agent_did_not_understand'::text, "
        "'agent_interrupted_caller'::text, "
        "'agent_did_not_answer_question'::text, 'response_too_long'::text, "
        "'response_too_slow'::text, 'agent_repeated_itself'::text, "
        "'wrong_qualification_question'::text, 'wrong_call_outcome'::text, "
        "'poor_objection_handling'::text, 'incorrect_information'::text, "
        "'good_conversation'::text])"
    ),
    ("review_reward_ledger", "review_reward_ledger_pkey"): "PRIMARY KEY (id)",
    ("review_reward_ledger", "review_reward_ledger_tenant_id_fkey"): (
        "FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE"
    ),
    ("review_reward_ledger", "review_reward_ledger_user_id_fkey"): (
        "FOREIGN KEY (user_id) REFERENCES user_profiles(id) ON DELETE CASCADE"
    ),
    ("review_reward_ledger", "review_reward_ledger_review_id_fkey"): (
        "FOREIGN KEY (review_id) REFERENCES conversation_reviews(id) " "ON DELETE CASCADE"
    ),
    ("review_reward_ledger", "review_reward_once_per_review"): ("UNIQUE (review_id)"),
    ("review_reward_ledger", "review_reward_points_positive"): ("CHECK (points > 0)"),
    ("prompt_template_versions", "prompt_template_versions_pkey"): ("PRIMARY KEY (id)"),
    ("prompt_template_versions", "prompt_template_versions_version_unique"): ("UNIQUE (version)"),
    ("prompt_template_versions", "prompt_template_versions_body_not_empty"): (
        "CHECK (length(body) > 0)"
    ),
    ("ai_config_migrations", "ai_config_migrations_pkey"): "PRIMARY KEY (id)",
    ("campaign_lead_fields", "campaign_lead_fields_pkey"): "PRIMARY KEY (id)",
    ("campaign_lead_fields", "campaign_lead_fields_campaign_id_fkey"): (
        "FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE"
    ),
    ("campaign_lead_fields", "campaign_lead_fields_key_unique"): (
        "UNIQUE (campaign_id, field_key)"
    ),
    ("campaign_lead_fields", "campaign_lead_fields_type_valid"): (
        "CHECK (field_type::text = ANY (ARRAY['text'::character varying, "
        "'number'::character varying, 'email'::character varying, "
        "'phone'::character varying, 'datetime'::character varying, "
        "'single_select'::character varying, 'multi_select'::character "
        "varying, 'notes'::character varying]::text[]))"
    ),
    ("call_lead_details", "call_lead_details_pkey"): "PRIMARY KEY (id)",
    ("call_lead_details", "call_lead_details_call_id_fkey"): (
        "FOREIGN KEY (call_id) REFERENCES calls(id) ON DELETE CASCADE"
    ),
    ("call_lead_details", "call_lead_details_unique"): ("UNIQUE (call_id, field_key)"),
    ("call_lead_details", "call_lead_details_source_valid"): (
        "CHECK (source::text = ANY (ARRAY['agent_inferred'::character varying, "
        "'caller_stated'::character varying, 'imported'::character varying, "
        "'manual_edit'::character varying]::text[]))"
    ),
    ("call_lead_details", "call_lead_details_type_valid"): (
        "CHECK (field_type::text = ANY (ARRAY['text'::character varying, "
        "'number'::character varying, 'email'::character varying, "
        "'phone'::character varying, 'datetime'::character varying, "
        "'single_select'::character varying, 'multi_select'::character "
        "varying, 'notes'::character varying]::text[]))"
    ),
    ("topup_packages", "topup_packages_pkey"): "PRIMARY KEY (id)",
    ("topup_packages", "topup_packages_code_key"): "UNIQUE (code)",
    ("topup_packages", "topup_packages_minutes_check"): "CHECK (minutes > 0)",
    ("topup_packages", "topup_packages_price_cents_check"): ("CHECK (price_cents >= 0)"),
    ("topup_packages", "topup_packages_expires_days_check"): (
        "CHECK (expires_days IS NULL OR expires_days > 0)"
    ),
    ("topup_orders", "topup_orders_pkey"): "PRIMARY KEY (id)",
    ("topup_orders", "topup_orders_minutes_check"): "CHECK (minutes > 0)",
    ("topup_orders", "topup_orders_price_cents_check"): ("CHECK (price_cents >= 0)"),
    ("topup_orders", "topup_orders_status_valid"): (
        "CHECK (status::text = ANY (ARRAY['pending'::character varying, "
        "'paid'::character varying, 'failed'::character varying, "
        "'cancelled'::character varying, 'refunded'::character varying, "
        "'disputed'::character varying]::text[]))"
    ),
    ("billing_ledger", "billing_ledger_pkey"): "PRIMARY KEY (id)",
    ("billing_ledger", "billing_ledger_order_id_fkey"): (
        "FOREIGN KEY (order_id) REFERENCES topup_orders(id)"
    ),
    ("billing_ledger", "billing_ledger_kind_valid"): (
        "CHECK (kind::text = ANY (ARRAY['topup'::character varying, "
        "'refund'::character varying, 'adjustment'::character varying, "
        "'dispute'::character varying]::text[]))"
    ),
    ("tenant_policy_audit_log", "tenant_policy_audit_log_pkey"): "PRIMARY KEY (id)",
    ("tenant_policy_audit_log", "tenant_policy_audit_log_tenant_id_fkey"): (
        "FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE"
    ),
    ("tenant_policy_audit_log", "tenant_policy_audit_log_actor_user_id_fkey"): (
        "FOREIGN KEY (actor_user_id) REFERENCES user_profiles(id) ON DELETE SET NULL"
    ),
    ("tenant_policy_audit_log", "tenant_policy_audit_log_action_check"): (
        "CHECK (action::text = ANY (ARRAY['INSERT'::character varying, "
        "'UPDATE'::character varying, 'DELETE'::character varying]::text[]))"
    ),
    ("tenant_policy_audit_log", "tenant_policy_audit_log_actor_type_check"): (
        "CHECK (actor_type::text = ANY (ARRAY['user'::character varying, "
        "'system'::character varying]::text[]))"
    ),
}

# (PostgreSQL format_type, NOT NULL, default/generated expression, generated)
# ``None`` means the column must not acquire a default.  These are the fields
# that drive routing, test-call exclusion, reward idempotency, or money.
_COLUMN_CONTRACTS = {
    ("campaigns", "knowledge_mode"): ("text", True, "'none'::text", ""),
    ("campaigns", "knowledge_model"): ("text", False, None, ""),
    ("campaigns", "tts_provider"): ("text", False, None, ""),
    ("campaigns", "prompt_version_pin"): (
        "character varying(64)",
        False,
        None,
        "",
    ),
    ("calls", "lead_id"): ("uuid", False, None, ""),
    ("calls", "prompt_template"): ("character varying(64)", False, None, ""),
    ("calls", "prompt_version"): ("character varying(64)", False, None, ""),
    ("calls", "prompt_hash"): ("character varying(32)", False, None, ""),
    ("calls", "is_test"): ("boolean", True, "false", ""),
    ("leads", "do_not_call"): ("boolean", True, "false", ""),
    ("leads", "full_name"): (
        "character varying(511)",
        False,
        "NULLIF(TRIM(BOTH FROM (((COALESCE(first_name, ''::character varying))::text "
        "|| ' '::text) || (COALESCE(last_name, ''::character varying))::text)), "
        "''::text)",
        "s",
    ),
    ("review_reward_ledger", "id"): ("uuid", True, "gen_random_uuid()", ""),
    ("review_reward_ledger", "tenant_id"): ("uuid", True, None, ""),
    ("review_reward_ledger", "user_id"): ("uuid", True, None, ""),
    ("review_reward_ledger", "review_id"): ("uuid", True, None, ""),
    ("review_reward_ledger", "points"): ("integer", True, None, ""),
    ("review_reward_ledger", "reason"): (
        "character varying(64)",
        True,
        "'conversation_review'::character varying",
        "",
    ),
    ("review_reward_ledger", "awarded_at"): (
        "timestamp with time zone",
        True,
        "now()",
        "",
    ),
    ("topup_packages", "id"): ("uuid", True, "gen_random_uuid()", ""),
    ("topup_packages", "code"): ("character varying(64)", True, None, ""),
    ("topup_packages", "name"): ("character varying(128)", True, None, ""),
    ("topup_packages", "minutes"): ("integer", True, None, ""),
    ("topup_packages", "price_cents"): ("integer", True, None, ""),
    ("topup_packages", "currency"): (
        "character varying(3)",
        True,
        "'GBP'::character varying",
        "",
    ),
    ("topup_packages", "expires_days"): ("integer", False, None, ""),
    ("topup_packages", "is_active"): ("boolean", True, "true", ""),
    ("topup_packages", "sort_order"): ("integer", True, "0", ""),
    ("topup_packages", "created_at"): (
        "timestamp with time zone",
        True,
        "now()",
        "",
    ),
    ("topup_orders", "id"): ("uuid", True, "gen_random_uuid()", ""),
    ("topup_orders", "tenant_id"): ("uuid", True, None, ""),
    ("topup_orders", "user_id"): ("uuid", False, None, ""),
    ("topup_orders", "package_code"): (
        "character varying(64)",
        True,
        None,
        "",
    ),
    ("topup_orders", "minutes"): ("integer", True, None, ""),
    ("topup_orders", "price_cents"): ("integer", True, None, ""),
    ("topup_orders", "currency"): ("character varying(3)", True, None, ""),
    ("topup_orders", "status"): (
        "character varying(16)",
        True,
        "'pending'::character varying",
        "",
    ),
    ("topup_orders", "provider"): (
        "character varying(32)",
        True,
        "'stripe'::character varying",
        "",
    ),
    ("topup_orders", "provider_session_id"): (
        "character varying(255)",
        False,
        None,
        "",
    ),
    ("topup_orders", "provider_payment_id"): (
        "character varying(255)",
        False,
        None,
        "",
    ),
    ("topup_orders", "created_at"): (
        "timestamp with time zone",
        True,
        "now()",
        "",
    ),
    ("topup_orders", "updated_at"): (
        "timestamp with time zone",
        True,
        "now()",
        "",
    ),
    ("topup_orders", "paid_at"): (
        "timestamp with time zone",
        False,
        None,
        "",
    ),
    ("billing_ledger", "id"): (
        "bigint",
        True,
        "nextval('billing_ledger_id_seq'::regclass)",
        "",
    ),
    ("billing_ledger", "tenant_id"): ("uuid", True, None, ""),
    ("billing_ledger", "order_id"): ("uuid", False, None, ""),
    ("billing_ledger", "kind"): ("character varying(16)", True, None, ""),
    ("billing_ledger", "minutes_delta"): ("integer", True, None, ""),
    ("billing_ledger", "amount_cents"): ("integer", True, "0", ""),
    ("billing_ledger", "currency"): ("character varying(3)", False, None, ""),
    ("billing_ledger", "provider_event_id"): (
        "character varying(255)",
        False,
        None,
        "",
    ),
    ("billing_ledger", "note"): ("text", False, None, ""),
    ("billing_ledger", "created_at"): (
        "timestamp with time zone",
        True,
        "now()",
        "",
    ),
}


# A cast chain that is value-preserving for a *string literal*: only the
# unqualified string types.  ``character(n)`` pads, ``character varying(n)``
# truncates and every non-string type can renormalize the text, so an element
# carrying one of those keeps its cast and is compared verbatim.
_LOSSLESS_STRING_CAST = r"(?:::(?:text|character varying))"
_ARRAY_STRING_ELEMENT = re.compile(r"^('(?:[^']|'')*')(" + _LOSSLESS_STRING_CAST + r"*)$")
_ARRAY_LEVEL_CAST = re.compile(r"::[a-z_][a-z0-9_]*(?: [a-z_][a-z0-9_]*)*\[\]")


def _end_of_string_literal(value: str, start: int) -> int:
    """Index just past the closing quote of the literal opening at ``start``."""
    index = start + 1
    while index < len(value):
        if value[index] == "'":
            if value.startswith("''", index):
                index += 2
                continue
            return index + 1
        index += 1
    return len(value)


def _matching_bracket(value: str, start: int) -> int:
    """Index of the ``]`` matching the ``[`` at ``start``, or ``-1``."""
    depth = 0
    index = start
    while index < len(value):
        char = value[index]
        if char == "'":
            index = _end_of_string_literal(value, index)
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _split_array_elements(inner: str) -> list[str]:
    elements: list[str] = []
    depth = 0
    start = 0
    index = 0
    while index < len(inner):
        char = inner[index]
        if char == "'":
            index = _end_of_string_literal(inner, index)
            continue
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == "," and depth == 0:
            elements.append(inner[start:index])
            start = index + 1
        index += 1
    elements.append(inner[start:])
    return elements


def _canonical_string_array(inner: str, array_cast: str | None) -> str | None:
    """Lift per-element string casts onto one array-level cast.

    ``ARRAY['a'::character varying, 'b'::character varying]::text[]`` and
    ``ARRAY['a'::character varying::text, 'b'::character varying::text]`` are
    the same ``text[]`` value; PostgreSQL just renders the cast at a different
    level depending on whether the CHECK was written as ``IN (...)`` (the
    ``complete_schema.sql`` bootstrap) or as ``= ANY (ARRAY[...]::text[])`` (the
    migration chain).  Returns ``None`` - leave the literal untouched - unless
    every element is a plain string literal with a value-preserving cast chain
    and the resulting array type is unambiguous.
    """
    elements = _split_array_elements(inner)
    literals: list[str] = []
    final_casts: set[str | None] = set()
    for element in elements:
        match = _ARRAY_STRING_ELEMENT.match(element.strip())
        if match is None:
            return None
        literals.append(match.group(1))
        cast_chain = match.group(2)
        final_casts.add(cast_chain.rsplit("::", 1)[-1] if cast_chain else None)
    if len(final_casts) != 1:
        # A heterogeneous element list cannot be summarized by one array cast.
        return None
    element_cast = final_casts.pop()
    if array_cast is None:
        if element_cast is None:
            return None
        array_cast = f"::{element_cast}[]"
    return "array[" + ", ".join(literals) + "]" + array_cast


def _canonicalize_string_array_literals(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "'":
            end = _end_of_string_literal(value, index)
            result.append(value[index:end])
            index = end
            continue
        if value.startswith("array[", index) and (
            index == 0 or not (value[index - 1].isalnum() or value[index - 1] == "_")
        ):
            close = _matching_bracket(value, index + len("array"))
            if close != -1:
                cast_match = _ARRAY_LEVEL_CAST.match(value, close + 1)
                canonical = _canonical_string_array(
                    value[index + len("array[") : close],
                    cast_match.group(0) if cast_match else None,
                )
                if canonical is not None:
                    result.append(canonical)
                    index = cast_match.end() if cast_match else close + 1
                    continue
        result.append(char)
        index += 1
    return "".join(result)


def _normalize_sql(value: str | None) -> str:
    # pg_get_constraintdef's canonical spelling for the constraint forms used
    # here is stable across the supported PostgreSQL 15/16 releases, but it is
    # NOT stable across bootstrap paths: a CHECK written as ``col IN ('a','b')``
    # (database/complete_schema.sql) renders the ``::text`` cast on each array
    # element, while the same CHECK written as ``= ANY (ARRAY[...]::text[])``
    # (the migration chain) renders it once on the array.  Both denote the same
    # text[] value, so the array literal is canonicalized to one shape.
    # Everything else is case- and layout-folded only: stripping the left-hand
    # cast, the operator or grouping parentheses would make a materially weaker
    # CHECK look equivalent.  CI executes this catalog validator on PostgreSQL
    # 15; the release-gate smoke also runs on 16.
    return _canonicalize_string_array_literals(" ".join((value or "").lower().split()))


def _execute(sql: str) -> None:
    op.execute(text(sql))


def _canonical_rls(table: str) -> None:
    # Drop every legacy per-command policy, not merely the canonical name.  A
    # permissive leftover would be ORed with the repaired policy and reopen the
    # tenant boundary.
    _execute(
        f"""
        DO $policy$
        DECLARE existing_policy record;
        BEGIN
            FOR existing_policy IN
                SELECT policyname
                FROM pg_policies
                WHERE schemaname = 'public' AND tablename = '{table}'
            LOOP
                EXECUTE format(
                    'DROP POLICY IF EXISTS %I ON public.%I',
                    existing_policy.policyname,
                    '{table}'
                );
            END LOOP;
        END;
        $policy$;
        """
    )
    _execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    _execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    if table in _APPEND_ONLY_RLS_TABLES:
        # Append-only ledgers never receive the generic ALL policy. A caller
        # with a valid tenant context or the service bypass may read/append,
        # while UPDATE/DELETE remain denied at the RLS boundary.
        _execute(f"CREATE POLICY {table}_select ON {table} FOR SELECT " f"USING ({_TENANT_POLICY})")
        _execute(
            f"CREATE POLICY {table}_insert ON {table} FOR INSERT " f"WITH CHECK ({_TENANT_POLICY})"
        )
        _execute(f"CREATE POLICY {table}_update ON {table} FOR UPDATE USING (FALSE)")
        _execute(f"CREATE POLICY {table}_delete ON {table} FOR DELETE USING (FALSE)")
        return
    _execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL "
        f"USING ({_TENANT_POLICY}) WITH CHECK ({_TENANT_POLICY})"
    )


def _repair_columns() -> None:
    _execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    _execute(
        "ALTER TABLE dialer_jobs "
        "ADD COLUMN IF NOT EXISTS failure_category TEXT, "
        "ADD COLUMN IF NOT EXISTS failure_reason TEXT"
    )
    _execute(
        "CREATE INDEX IF NOT EXISTS idx_dialer_jobs_failure_category "
        "ON dialer_jobs (failure_category) WHERE failure_category IS NOT NULL"
    )
    _execute(
        "ALTER TABLE campaigns "
        "ADD COLUMN IF NOT EXISTS knowledge_mode TEXT NOT NULL DEFAULT 'none', "
        "ADD COLUMN IF NOT EXISTS knowledge_model TEXT, "
        "ADD COLUMN IF NOT EXISTS tts_provider TEXT, "
        "ADD COLUMN IF NOT EXISTS prompt_version_pin VARCHAR(64)"
    )
    _execute(
        "ALTER TABLE calls "
        "ADD COLUMN IF NOT EXISTS prompt_template VARCHAR(64), "
        "ADD COLUMN IF NOT EXISTS prompt_version VARCHAR(64), "
        "ADD COLUMN IF NOT EXISTS prompt_hash VARCHAR(32), "
        "ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE"
    )
    # Migration 0018 widened this relationship for manual, PBX and test calls
    # that legitimately have no lead.  Reasserting NULLability loses no data.
    _execute("ALTER TABLE calls ALTER COLUMN lead_id DROP NOT NULL")
    # Normalize only NULLs in a hand-applied partial is_test repair.  Changing
    # TRUE would silently rebill retained test sessions.
    _execute("ALTER TABLE calls ALTER COLUMN is_test SET DEFAULT FALSE")
    _execute("UPDATE calls SET is_test = FALSE WHERE is_test IS NULL")
    _execute("ALTER TABLE calls ALTER COLUMN is_test SET NOT NULL")
    _execute(
        """
        ALTER TABLE leads
            ADD COLUMN IF NOT EXISTS business_number VARCHAR(32),
            ADD COLUMN IF NOT EXISTS company_name VARCHAR(255),
            ADD COLUMN IF NOT EXISTS job_title VARCHAR(255),
            ADD COLUMN IF NOT EXISTS best_time_to_call VARCHAR(64),
            ADD COLUMN IF NOT EXISTS timezone VARCHAR(64),
            ADD COLUMN IF NOT EXISTS calling_notes TEXT,
            ADD COLUMN IF NOT EXISTS preferred_contact_method VARCHAR(32),
            ADD COLUMN IF NOT EXISTS do_not_call BOOLEAN NOT NULL DEFAULT FALSE
        """
    )
    _execute(
        """
        ALTER TABLE leads
            ADD COLUMN IF NOT EXISTS full_name VARCHAR(511)
            GENERATED ALWAYS AS (
                NULLIF(
                    TRIM(COALESCE(first_name, '') || ' ' || COALESCE(last_name, '')),
                    ''
                )
            ) STORED
        """
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_calls_prompt_version "
        "ON calls (prompt_version) WHERE prompt_version IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_campaigns_prompt_pin "
        "ON campaigns (prompt_version_pin) WHERE prompt_version_pin IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_calls_is_test "
        "ON calls (tenant_id, created_at DESC) WHERE is_test",
        "CREATE INDEX IF NOT EXISTS idx_leads_do_not_call "
        "ON leads (tenant_id) WHERE do_not_call",
    ):
        _execute(statement)
    _execute(
        "COMMENT ON COLUMN calls.is_test IS "
        "'TRUE for test sessions. Never bill, meter, rate-limit or include in "
        "customer-facing call statistics; retain for review.'"
    )
    _execute(
        "COMMENT ON COLUMN leads.do_not_call IS "
        "'Per-contact suppression. ADDITIVE to the tenant DNC list, never a "
        "replacement; dialers must still check the authoritative list.'"
    )
    _execute(
        "COMMENT ON COLUMN leads.timezone IS "
        "'IANA timezone used for per-contact civil calling hours.'"
    )


def _create_knowledge_and_feedback() -> None:
    _execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_knowledge_sources (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL,
            filename TEXT,
            raw_md TEXT NOT NULL,
            token_count INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'processing'
                CHECK (status IN ('processing','ready','failed')),
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_knowledge_nodes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL,
            source_id UUID NOT NULL
                REFERENCES campaign_knowledge_sources(id) ON DELETE CASCADE,
            parent_id UUID REFERENCES campaign_knowledge_nodes(id) ON DELETE CASCADE,
            depth INTEGER NOT NULL DEFAULT 0,
            path TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            heading TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            summary TEXT,
            voice_answer TEXT,
            keywords TEXT[],
            example_questions TEXT[],
            search_text TEXT NOT NULL DEFAULT '',
            search_tsv TSVECTOR,
            priority SMALLINT NOT NULL DEFAULT 0,
            hit_count BIGINT NOT NULL DEFAULT 0,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_cks_campaign "
        "ON campaign_knowledge_sources (campaign_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_ckn_fts "
        "ON campaign_knowledge_nodes USING GIN (search_tsv)",
        "CREATE INDEX IF NOT EXISTS idx_ckn_trgm "
        "ON campaign_knowledge_nodes USING GIN (search_text gin_trgm_ops)",
        "CREATE INDEX IF NOT EXISTS idx_ckn_campaign "
        "ON campaign_knowledge_nodes (campaign_id, enabled)",
        "CREATE INDEX IF NOT EXISTS idx_ckn_tree "
        "ON campaign_knowledge_nodes (campaign_id, parent_id, position)",
    ):
        _execute(statement)
    _execute(
        """
        CREATE TABLE IF NOT EXISTS call_feedback (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
            created_by UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
            audio_storage_provider VARCHAR(16) NOT NULL,
            audio_bucket VARCHAR(255) NOT NULL,
            audio_key VARCHAR(1024) NOT NULL,
            audio_mime_type VARCHAR(100) NOT NULL,
            audio_size_bytes BIGINT NOT NULL,
            audio_sha256 VARCHAR(64) NOT NULL,
            duration_seconds DOUBLE PRECISION,
            transcript TEXT,
            transcript_status VARCHAR(16) NOT NULL DEFAULT 'pending',
            transcript_error TEXT,
            transcription_attempts INTEGER NOT NULL DEFAULT 0,
            transcript_provider VARCHAR(32) NOT NULL DEFAULT 'deepgram',
            transcript_provider_request_id VARCHAR(128),
            transcription_started_at TIMESTAMPTZ,
            transcribed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT call_feedback_one_note_per_call UNIQUE (call_id),
            CONSTRAINT call_feedback_audio_object_unique UNIQUE (audio_bucket, audio_key),
            CONSTRAINT call_feedback_audio_size_positive CHECK (audio_size_bytes > 0),
            CONSTRAINT call_feedback_sha256_valid CHECK (audio_sha256 ~ '^[0-9a-f]{64}$'),
            CONSTRAINT call_feedback_duration_nonnegative
                CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
            CONSTRAINT call_feedback_attempts_nonnegative
                CHECK (transcription_attempts >= 0),
            CONSTRAINT call_feedback_status_valid
                CHECK (transcript_status IN ('pending', 'done', 'failed')),
            CONSTRAINT call_feedback_storage_provider_valid
                CHECK (audio_storage_provider IN ('s3', 'local'))
        )
        """
    )
    _execute(
        "CREATE INDEX IF NOT EXISTS idx_call_feedback_tenant_created "
        "ON call_feedback (tenant_id, created_at DESC)"
    )
    _execute(
        "CREATE INDEX IF NOT EXISTS idx_call_feedback_retryable "
        "ON call_feedback (tenant_id, updated_at) "
        "WHERE transcript_status IN ('pending', 'failed')"
    )
    _execute(
        "COMMENT ON TABLE call_feedback IS "
        "'Durable reviewer audio notes and transcription state; one note per call.'"
    )


def _create_review_and_prompt_tables() -> None:
    _execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_reviews (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
            campaign_id UUID REFERENCES campaigns(id) ON DELETE SET NULL,
            user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
            rating SMALLINT NOT NULL,
            review_tags TEXT[] NOT NULL DEFAULT '{}',
            comment TEXT,
            prompt_template VARCHAR(64),
            prompt_version VARCHAR(64),
            prompt_hash VARCHAR(32),
            llm_model VARCHAR(128),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT conversation_reviews_one_per_user_per_call
                UNIQUE (call_id, user_id),
            CONSTRAINT conversation_reviews_rating_range CHECK (rating BETWEEN 1 AND 5),
            CONSTRAINT conversation_reviews_comment_length
                CHECK (comment IS NULL OR length(comment) <= 4000),
            CONSTRAINT conversation_reviews_tags_known CHECK (
                review_tags <@ ARRAY[
                    'agent_did_not_understand',
                    'agent_interrupted_caller',
                    'agent_did_not_answer_question',
                    'response_too_long',
                    'response_too_slow',
                    'agent_repeated_itself',
                    'wrong_qualification_question',
                    'wrong_call_outcome',
                    'poor_objection_handling',
                    'incorrect_information',
                    'good_conversation'
                ]::TEXT[]
            )
        )
        """
    )
    _execute(
        """
        CREATE TABLE IF NOT EXISTS review_reward_ledger (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES user_profiles(id) ON DELETE CASCADE,
            review_id UUID NOT NULL REFERENCES conversation_reviews(id) ON DELETE CASCADE,
            points INTEGER NOT NULL,
            reason VARCHAR(64) NOT NULL DEFAULT 'conversation_review',
            awarded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT review_reward_once_per_review UNIQUE (review_id),
            CONSTRAINT review_reward_points_positive CHECK (points > 0)
        )
        """
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_conversation_reviews_tenant_created "
        "ON conversation_reviews (tenant_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_conversation_reviews_call "
        "ON conversation_reviews (call_id)",
        "CREATE INDEX IF NOT EXISTS idx_conversation_reviews_prompt_version "
        "ON conversation_reviews (tenant_id, prompt_version, rating) "
        "WHERE prompt_version IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_conversation_reviews_tags "
        "ON conversation_reviews USING GIN (review_tags)",
        "CREATE INDEX IF NOT EXISTS idx_review_reward_user_day "
        "ON review_reward_ledger (user_id, awarded_at DESC)",
    ):
        _execute(statement)
    _execute(
        "COMMENT ON TABLE conversation_reviews IS "
        "'One reviewer rating per user and call with snapshotted prompt identity.'"
    )
    _execute(
        "COMMENT ON TABLE review_reward_ledger IS "
        "'Append-only rewards; UNIQUE(review_id) prevents double credit.'"
    )
    _execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_template_versions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            persona_type VARCHAR(32) NOT NULL,
            template VARCHAR(64) NOT NULL,
            version VARCHAR(64) NOT NULL,
            body TEXT NOT NULL,
            body_sha VARCHAR(64) NOT NULL,
            approved BOOLEAN NOT NULL DEFAULT TRUE,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT prompt_template_versions_version_unique UNIQUE (version),
            CONSTRAINT prompt_template_versions_body_not_empty CHECK (length(body) > 0)
        )
        """
    )
    _execute(
        "CREATE INDEX IF NOT EXISTS idx_prompt_template_versions_persona "
        "ON prompt_template_versions (persona_type, recorded_at DESC)"
    )
    _execute(
        "COMMENT ON TABLE prompt_template_versions IS "
        "'Platform-global raw prompt bodies retained for campaign rollback.'"
    )


def _create_ai_audit_and_contact_tables() -> None:
    # Empty schema only.  Never replay 0019's six tenant-specific model moves.
    _execute(
        """
        CREATE TABLE IF NOT EXISTS ai_config_migrations (
            id BIGSERIAL PRIMARY KEY,
            tenant_id UUID NOT NULL,
            old_provider VARCHAR(32),
            old_model VARCHAR(128),
            new_provider VARCHAR(32) NOT NULL,
            new_model VARCHAR(128) NOT NULL,
            reason TEXT NOT NULL,
            batch VARCHAR(64) NOT NULL,
            migrated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            rolled_back_at TIMESTAMPTZ
        )
        """
    )
    _execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_config_migrations_batch "
        "ON ai_config_migrations (batch, tenant_id)"
    )
    _execute(
        "COMMENT ON TABLE ai_config_migrations IS "
        "'Per-tenant AI model changes with prior values retained for rollback.'"
    )
    _execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_lead_fields (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            campaign_id UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            field_key VARCHAR(64) NOT NULL,
            label VARCHAR(255) NOT NULL,
            field_type VARCHAR(32) NOT NULL DEFAULT 'text',
            is_required BOOLEAN NOT NULL DEFAULT FALSE,
            agent_visible BOOLEAN NOT NULL DEFAULT TRUE,
            user_visible BOOLEAN NOT NULL DEFAULT TRUE,
            options JSONB,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT campaign_lead_fields_key_unique UNIQUE (campaign_id, field_key),
            CONSTRAINT campaign_lead_fields_type_valid CHECK (
                field_type IN (
                    'text','number','email','phone','datetime',
                    'single_select','multi_select','notes'
                )
            )
        )
        """
    )
    _execute(
        """
        CREATE TABLE IF NOT EXISTS call_lead_details (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            call_id UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
            campaign_id UUID,
            lead_id UUID,
            field_key VARCHAR(64) NOT NULL,
            field_type VARCHAR(32) NOT NULL DEFAULT 'text',
            value TEXT,
            source VARCHAR(24) NOT NULL,
            confirmed BOOLEAN NOT NULL DEFAULT FALSE,
            is_required BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT call_lead_details_unique UNIQUE (call_id, field_key),
            CONSTRAINT call_lead_details_source_valid CHECK (
                source IN ('agent_inferred','caller_stated','imported','manual_edit')
            ),
            CONSTRAINT call_lead_details_type_valid CHECK (
                field_type IN (
                    'text','number','email','phone','datetime',
                    'single_select','multi_select','notes'
                )
            )
        )
        """
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_campaign_lead_fields_campaign "
        "ON campaign_lead_fields (campaign_id, sort_order)",
        "CREATE INDEX IF NOT EXISTS idx_call_lead_details_call " "ON call_lead_details (call_id)",
        "CREATE INDEX IF NOT EXISTS idx_call_lead_details_lead "
        "ON call_lead_details (tenant_id, lead_id) WHERE lead_id IS NOT NULL",
    ):
        _execute(statement)
    _execute(
        "COMMENT ON COLUMN call_lead_details.source IS "
        "'imported | caller_stated | agent_inferred | manual_edit.'"
    )
    _execute(
        "COMMENT ON TABLE call_lead_details IS "
        "'Captured values per call; an absent row means never established.'"
    )


def _create_legacy_policy_audit_table() -> None:
    # The preserved 2026-06-02 schema floor predates this durable telephony
    # audit table even though the maintained complete_schema snapshot has it.
    # Create its storage and any wholly absent canonical audit capabilities.
    # Existing rows and separately deployed function/trigger definitions stay
    # untouched, then the validator fails closed on incompatible signatures or
    # bindings. Forced RLS below adds read/append and deny-mutation policies.
    _execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_policy_audit_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            table_name VARCHAR(80) NOT NULL,
            record_id UUID,
            action VARCHAR(10) NOT NULL
                CHECK (action IN ('INSERT', 'UPDATE', 'DELETE')),
            actor_user_id UUID REFERENCES user_profiles(id) ON DELETE SET NULL,
            actor_type VARCHAR(16) NOT NULL DEFAULT 'system'
                CHECK (actor_type IN ('user', 'system')),
            request_id VARCHAR(128),
            correlation_id VARCHAR(128),
            before_payload JSONB,
            after_payload JSONB,
            changed_fields TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
            source VARCHAR(32) NOT NULL DEFAULT 'db_trigger',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            retention_until TIMESTAMPTZ NOT NULL
                DEFAULT (NOW() + INTERVAL '400 days')
        )
        """
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_tenant_policy_audit_log_tenant_created "
        "ON tenant_policy_audit_log (tenant_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tenant_policy_audit_log_tenant_table_created "
        "ON tenant_policy_audit_log (tenant_id, table_name, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_tenant_policy_audit_log_request_id "
        "ON tenant_policy_audit_log (request_id)",
        "CREATE INDEX IF NOT EXISTS idx_tenant_policy_audit_log_retention_until "
        "ON tenant_policy_audit_log (retention_until)",
    ):
        _execute(statement)
    _execute(
        "COMMENT ON TABLE tenant_policy_audit_log IS "
        "'Append-only tenant telephony policy mutation evidence.'"
    )

    connection = op.get_bind()
    log_function_exists = bool(
        connection.execute(
            text("SELECT to_regprocedure('public.log_tenant_policy_mutation()') " "IS NOT NULL")
        ).scalar()
    )
    if not log_function_exists:
        # Do not CREATE OR REPLACE an operator-owned implementation. The
        # preserved schema lacks the function entirely, so install the
        # canonical append-only trigger body only in that absent state.
        _execute(
            """
            CREATE FUNCTION public.log_tenant_policy_mutation()
            RETURNS TRIGGER AS $$
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
            $$ LANGUAGE plpgsql
            """
        )

    prune_function_exists = bool(
        connection.execute(
            text(
                "SELECT to_regprocedure('public.prune_tenant_policy_audit_log(integer)') "
                "IS NOT NULL"
            )
        ).scalar()
    )
    if not prune_function_exists:
        _execute(
            """
            CREATE FUNCTION public.prune_tenant_policy_audit_log(
                p_limit INTEGER DEFAULT 5000
            )
            RETURNS INTEGER AS $$
            DECLARE
                deleted_count INTEGER;
            BEGIN
                WITH to_delete AS (
                    SELECT id
                    FROM public.tenant_policy_audit_log
                    WHERE retention_until < NOW()
                    ORDER BY retention_until ASC
                    LIMIT GREATEST(COALESCE(p_limit, 0), 0)
                ),
                deleted AS (
                    DELETE FROM public.tenant_policy_audit_log AS audit_row
                    USING to_delete
                    WHERE audit_row.id = to_delete.id
                    RETURNING 1
                )
                SELECT COUNT(*) INTO deleted_count FROM deleted;

                RETURN deleted_count;
            END;
            $$ LANGUAGE plpgsql
            """
        )

    for table, trigger_name in _POLICY_AUDIT_TRIGGER_TARGETS.items():
        trigger_exists = bool(
            connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_trigger
                        WHERE tgrelid=to_regclass(:table_name)
                          AND tgname=:trigger_name
                          AND NOT tgisinternal
                    )
                    """
                ),
                {
                    "table_name": f"public.{table}",
                    "trigger_name": trigger_name,
                },
            ).scalar()
        )
        if not trigger_exists:
            _execute(
                f"CREATE TRIGGER {trigger_name} "
                f"AFTER INSERT OR UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION public.log_tenant_policy_mutation()"
            )


def _create_billing_tables(*, seed_starter_packages: bool) -> None:
    _execute(
        """
        CREATE TABLE IF NOT EXISTS topup_packages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code VARCHAR(64) NOT NULL UNIQUE,
            name VARCHAR(128) NOT NULL,
            minutes INTEGER NOT NULL CHECK (minutes > 0),
            price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
            currency VARCHAR(3) NOT NULL DEFAULT 'GBP',
            expires_days INTEGER CHECK (expires_days IS NULL OR expires_days > 0),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    _execute(
        """
        CREATE TABLE IF NOT EXISTS topup_orders (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            user_id UUID,
            package_code VARCHAR(64) NOT NULL,
            minutes INTEGER NOT NULL CHECK (minutes > 0),
            price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
            currency VARCHAR(3) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'pending',
            provider VARCHAR(32) NOT NULL DEFAULT 'stripe',
            provider_session_id VARCHAR(255),
            provider_payment_id VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            paid_at TIMESTAMPTZ,
            CONSTRAINT topup_orders_status_valid CHECK (
                status IN ('pending','paid','failed','cancelled','refunded','disputed')
            )
        )
        """
    )
    _execute(
        """
        CREATE TABLE IF NOT EXISTS billing_ledger (
            id BIGSERIAL PRIMARY KEY,
            tenant_id UUID NOT NULL,
            order_id UUID REFERENCES topup_orders(id),
            kind VARCHAR(16) NOT NULL,
            minutes_delta INTEGER NOT NULL,
            amount_cents INTEGER NOT NULL DEFAULT 0,
            currency VARCHAR(3),
            provider_event_id VARCHAR(255),
            note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT billing_ledger_kind_valid CHECK (
                kind IN ('topup','refund','adjustment','dispute')
            )
        )
        """
    )
    for statement in (
        "CREATE INDEX IF NOT EXISTS idx_topup_orders_tenant "
        "ON topup_orders (tenant_id, created_at DESC)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_topup_orders_session "
        "ON topup_orders (provider_session_id) "
        "WHERE provider_session_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_billing_ledger_event "
        "ON billing_ledger (provider_event_id)",
        "CREATE INDEX IF NOT EXISTS idx_billing_ledger_tenant "
        "ON billing_ledger (tenant_id, created_at DESC)",
    ):
        _execute(statement)
    _execute(
        "COMMENT ON TABLE billing_ledger IS "
        "'Append-only; corrections are new signed entries and provider_event_id "
        "is unique for webhook idempotency.'"
    )
    _execute(
        """
        CREATE OR REPLACE FUNCTION public.prevent_billing_ledger_mutation()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'billing_ledger is append-only; write a compensating entry'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    _execute("DROP TRIGGER IF EXISTS billing_ledger_immutable ON billing_ledger")
    _execute(
        "CREATE TRIGGER billing_ledger_immutable "
        "BEFORE UPDATE OR DELETE ON billing_ledger "
        "FOR EACH ROW EXECUTE FUNCTION public.prevent_billing_ledger_mutation()"
    )
    # Fire even when a maintenance connection sets session_replication_role to
    # replica. A superuser can still deliberately disable/drop the trigger,
    # but cannot accidentally mutate evidence through an ordinary statement.
    _execute("ALTER TABLE billing_ledger ENABLE ALWAYS TRIGGER billing_ledger_immutable")
    if seed_starter_packages:
        _execute(
            """
            INSERT INTO topup_packages
                (code, name, minutes, price_cents, currency, sort_order)
            VALUES
                ('mins_250',  '250 minutes',   250,  2500, 'GBP', 1),
                ('mins_600',  '600 minutes',   600,  5400, 'GBP', 2),
                ('mins_1500', '1,500 minutes', 1500, 12000, 'GBP', 3)
            ON CONFLICT (code) DO NOTHING
            """
        )


def _validate_contract(*, require_starter_packages: bool) -> None:
    connection = op.get_bind()
    problems: list[str] = []

    for table, required_columns in _REQUIRED_COLUMNS.items():
        actual_columns = set(
            connection.execute(
                text(
                    """
                    SELECT a.attname
                    FROM pg_attribute AS a
                    WHERE a.attrelid = to_regclass(:table_name)
                      AND a.attnum > 0
                      AND NOT a.attisdropped
                    """
                ),
                {"table_name": f"public.{table}"},
            ).scalars()
        )
        missing = sorted(set(required_columns) - actual_columns)
        if missing:
            problems.append(f"{table} missing columns {missing}")

    for index in _REQUIRED_INDEXES:
        exists = connection.execute(
            text("SELECT to_regclass(:name) IS NOT NULL"),
            {"name": f"public.{index}"},
        ).scalar()
        if not exists:
            problems.append(f"missing index {index}")

    actual_constraints = {
        (row["table_name"], row["constraint_name"]): _normalize_sql(row["definition"])
        for row in connection.execute(
            text(
                """
                SELECT relation.relname AS table_name,
                       constraint_row.conname AS constraint_name,
                       pg_get_constraintdef(constraint_row.oid, TRUE) AS definition
                FROM pg_constraint AS constraint_row
                JOIN pg_class AS relation
                  ON relation.oid = constraint_row.conrelid
                WHERE constraint_row.connamespace = 'public'::regnamespace
                """
            )
        ).mappings()
    }
    wrong_constraints = []
    for key, expected_definition in _REQUIRED_CONSTRAINT_DEFINITIONS.items():
        actual_definition = actual_constraints.get(key)
        expected_normalized = _normalize_sql(expected_definition)
        if actual_definition != expected_normalized:
            wrong_constraints.append((key[0], key[1], expected_normalized, actual_definition))
    if wrong_constraints:
        problems.append(f"missing or incompatible constraints {wrong_constraints}")

    audit_function_contracts = {}
    for signature in (
        "public.log_tenant_policy_mutation()",
        "public.prune_tenant_policy_audit_log(integer)",
    ):
        audit_function_contracts[signature] = (
            connection.execute(
                text(
                    """
                    SELECT function_row.prorettype::regtype::text AS return_type,
                           function_row.pronargs,
                           function_row.pronargdefaults
                    FROM pg_proc AS function_row
                    WHERE function_row.oid=to_regprocedure(:signature)
                    """
                ),
                {"signature": signature},
            )
            .mappings()
            .one_or_none()
        )
    log_contract = audit_function_contracts["public.log_tenant_policy_mutation()"]
    if (
        log_contract is None
        or log_contract["return_type"] != "trigger"
        or log_contract["pronargs"] != 0
    ):
        problems.append("log_tenant_policy_mutation() has incompatible signature")
    prune_contract = audit_function_contracts["public.prune_tenant_policy_audit_log(integer)"]
    if (
        prune_contract is None
        or prune_contract["return_type"] != "integer"
        or prune_contract["pronargs"] != 1
        or prune_contract["pronargdefaults"] != 1
    ):
        problems.append("prune_tenant_policy_audit_log(integer) has incompatible signature")

    billing_immutability_function = (
        connection.execute(
            text(
                """
                SELECT function_row.prorettype::regtype::text AS return_type,
                       function_row.pronargs
                FROM pg_proc AS function_row
                WHERE function_row.oid=to_regprocedure(
                    'public.prevent_billing_ledger_mutation()'
                )
                """
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        billing_immutability_function is None
        or billing_immutability_function["return_type"] != "trigger"
        or billing_immutability_function["pronargs"] != 0
    ):
        problems.append("prevent_billing_ledger_mutation() has incompatible signature")

    billing_immutability_trigger = (
        connection.execute(
            text(
                """
                SELECT trigger_row.tgtype,
                       trigger_row.tgenabled::text AS tgenabled,
                       trigger_row.tgfoid = to_regprocedure(
                           'public.prevent_billing_ledger_mutation()'
                       ) AS canonical_function
                FROM pg_trigger AS trigger_row
                WHERE trigger_row.tgrelid='public.billing_ledger'::regclass
                  AND trigger_row.tgname='billing_ledger_immutable'
                  AND NOT trigger_row.tgisinternal
                """
            )
        )
        .mappings()
        .one_or_none()
    )
    # tgtype 27 = ROW + BEFORE + UPDATE + DELETE. ENABLE ALWAYS keeps the
    # trigger active for normal and replica-mode service sessions.
    if (
        billing_immutability_trigger is None
        or billing_immutability_trigger["tgtype"] != 27
        or billing_immutability_trigger["tgenabled"] != "A"
        or not billing_immutability_trigger["canonical_function"]
    ):
        problems.append("billing_ledger_immutable has incompatible trigger binding")

    audit_triggers = {
        (row["table_name"], row["trigger_name"]): row
        for row in connection.execute(
            text(
                """
                SELECT relation.relname AS table_name,
                       trigger_row.tgname AS trigger_name,
                       trigger_row.tgtype,
                       trigger_row.tgenabled::text AS tgenabled,
                       trigger_row.tgfoid = to_regprocedure(
                           'public.log_tenant_policy_mutation()'
                       ) AS canonical_function
                FROM pg_trigger AS trigger_row
                JOIN pg_class AS relation
                  ON relation.oid=trigger_row.tgrelid
                WHERE relation.relnamespace='public'::regnamespace
                  AND NOT trigger_row.tgisinternal
                """
            )
        ).mappings()
    }
    for table, trigger_name in _POLICY_AUDIT_TRIGGER_TARGETS.items():
        trigger_contract = audit_triggers.get((table, trigger_name))
        # tgtype 29 = ROW + AFTER INSERT/DELETE/UPDATE. Checking catalog bits
        # avoids pg_get_triggerdef spelling drift between PostgreSQL releases.
        if (
            trigger_contract is None
            or trigger_contract["tgtype"] != 29
            or trigger_contract["tgenabled"] not in {"O", "A"}
            or not trigger_contract["canonical_function"]
        ):
            problems.append(f"{trigger_name} has incompatible audit binding")

    # Names alone are not enough for the money/idempotency indexes. A wrong
    # pre-existing index with the canonical name makes CREATE IF NOT EXISTS a
    # no-op. Validate exact owner, key, uniqueness, readiness, NULL semantics,
    # and predicate so webhook retries cannot double-credit or block unrelated
    # NULL manual-adjustment rows.
    critical_indexes = {}
    for index in (
        "idx_topup_orders_session",
        "idx_billing_ledger_event",
        "idx_calls_is_test",
    ):
        critical_indexes[index] = (
            connection.execute(
                text(
                    """
                    SELECT relation.relname AS table_name,
                           i.indisunique, i.indisvalid, i.indisready,
                           i.indnullsnotdistinct,
                           i.indnkeyatts, i.indnatts,
                           ARRAY(
                               SELECT attribute.attname
                               FROM unnest(i.indkey) WITH ORDINALITY
                                    AS key_column(attnum, position)
                               JOIN pg_attribute AS attribute
                                 ON attribute.attrelid=i.indrelid
                                AND attribute.attnum=key_column.attnum
                               WHERE key_column.position <= i.indnkeyatts
                               ORDER BY key_column.position
                           ) AS key_columns,
                           pg_get_expr(i.indpred, i.indrelid) AS predicate
                    FROM pg_index AS i
                    JOIN pg_class AS relation ON relation.oid=i.indrelid
                    WHERE i.indexrelid = to_regclass(:index_name)
                    """
                ),
                {"index_name": f"public.{index}"},
            )
            .mappings()
            .one_or_none()
        )
    session_index = critical_indexes["idx_topup_orders_session"]
    session_predicate = _normalize_sql(
        (session_index["predicate"] if session_index else "").replace("(", " ").replace(")", " ")
    )
    if (
        session_index is None
        or session_index["table_name"] != "topup_orders"
        or not session_index["indisunique"]
        or not session_index["indisvalid"]
        or not session_index["indisready"]
        or session_index["indnullsnotdistinct"]
        or session_index["indnkeyatts"] != 1
        or session_index["indnatts"] != 1
        or list(session_index["key_columns"]) != ["provider_session_id"]
        or session_predicate != "provider_session_id is not null"
    ):
        problems.append("idx_topup_orders_session has unsafe definition")
    event_index = critical_indexes["idx_billing_ledger_event"]
    if (
        event_index is None
        or event_index["table_name"] != "billing_ledger"
        or not event_index["indisunique"]
        or not event_index["indisvalid"]
        or not event_index["indisready"]
        or event_index["indnullsnotdistinct"]
        or event_index["indnkeyatts"] != 1
        or event_index["indnatts"] != 1
        or list(event_index["key_columns"]) != ["provider_event_id"]
        or event_index["predicate"] is not None
    ):
        problems.append("idx_billing_ledger_event has unsafe definition")
    test_index = critical_indexes["idx_calls_is_test"]
    if (
        test_index is None
        or test_index["table_name"] != "calls"
        or test_index["indisunique"]
        or not test_index["indisvalid"]
        or not test_index["indisready"]
        or test_index["indnkeyatts"] != 2
        or test_index["indnatts"] != 2
        or list(test_index["key_columns"]) != ["tenant_id", "created_at"]
        or "is_test" not in (test_index["predicate"] or "")
    ):
        problems.append("idx_calls_is_test is not the canonical partial index")

    for table in _RLS_TABLES:
        tenant_column = (
            connection.execute(
                text(
                    """
                    SELECT format_type(a.atttypid, a.atttypmod) AS data_type,
                           a.attnotnull
                    FROM pg_attribute AS a
                    WHERE a.attrelid=to_regclass(:table_name)
                      AND a.attname='tenant_id'
                      AND a.attnum > 0
                      AND NOT a.attisdropped
                    """
                ),
                {"table_name": f"public.{table}"},
            )
            .mappings()
            .one_or_none()
        )
        if (
            tenant_column is None
            or tenant_column["data_type"] != "uuid"
            or not tenant_column["attnotnull"]
        ):
            problems.append(f"{table}.tenant_id is not UUID NOT NULL")

    for table in _RLS_TABLES:
        security_state = (
            connection.execute(
                text(
                    """
                    SELECT relrowsecurity, relforcerowsecurity
                    FROM pg_class
                    WHERE oid=to_regclass(:table_name)
                    """
                ),
                {"table_name": f"public.{table}"},
            )
            .mappings()
            .one_or_none()
        )
        if (
            security_state is None
            or not security_state["relrowsecurity"]
            or not security_state["relforcerowsecurity"]
        ):
            problems.append(f"{table} is not FORCE ROW LEVEL SECURITY")
        if table in _APPEND_ONLY_RLS_TABLES:
            policies = list(
                connection.execute(
                    text(
                        """
                        SELECT cmd, COALESCE(qual, '') AS qual,
                               COALESCE(with_check, '') AS with_check
                        FROM pg_policies
                        WHERE schemaname='public' AND tablename=:table_name
                        ORDER BY cmd
                        """
                    ),
                    {"table_name": table},
                ).mappings()
            )
            by_command = {row["cmd"]: row for row in policies}
            select_policy = by_command.get("SELECT")
            insert_policy = by_command.get("INSERT")
            update_policy = by_command.get("UPDATE")
            delete_policy = by_command.get("DELETE")
            if (
                len(policies) != 4
                or select_policy is None
                or "app.bypass_rls" not in select_policy["qual"]
                or insert_policy is None
                or "app.bypass_rls" not in insert_policy["with_check"]
                or update_policy is None
                or update_policy["qual"] != "false"
                or delete_policy is None
                or delete_policy["qual"] != "false"
            ):
                problems.append(f"{table} lacks canonical read/append and deny-mutation policies")
            continue
        row = (
            connection.execute(
                text(
                    """
                SELECT c.relrowsecurity, c.relforcerowsecurity,
                       count(p.policyname) AS policy_count,
                       bool_and(
                           COALESCE(p.qual, '') LIKE '%app.bypass_rls%'
                           AND COALESCE(p.with_check, '') LIKE '%app.bypass_rls%'
                       ) AS bypass_aware
                FROM pg_class AS c
                LEFT JOIN pg_policies AS p
                  ON p.schemaname = 'public' AND p.tablename = c.relname
                WHERE c.oid = to_regclass(:table_name)
                GROUP BY c.relrowsecurity, c.relforcerowsecurity
                """
                ),
                {"table_name": f"public.{table}"},
            )
            .mappings()
            .one_or_none()
        )
        if (
            row is None
            or not row["relrowsecurity"]
            or not row["relforcerowsecurity"]
            or row["policy_count"] != 1
            or not row["bypass_aware"]
        ):
            problems.append(f"{table} does not have one canonical forced-RLS policy")

    actual_column_contracts = {
        (row["table_name"], row["column_name"]): row
        for row in connection.execute(
            text(
                """
                SELECT relation.relname AS table_name,
                       attribute.attname AS column_name,
                       format_type(attribute.atttypid, attribute.atttypmod)
                           AS data_type,
                       attribute.attnotnull,
                       attribute.attgenerated::text AS attgenerated,
                       pg_get_expr(default_row.adbin, default_row.adrelid)
                           AS default_expression
                FROM pg_attribute AS attribute
                JOIN pg_class AS relation ON relation.oid=attribute.attrelid
                LEFT JOIN pg_attrdef AS default_row
                  ON default_row.adrelid=attribute.attrelid
                 AND default_row.adnum=attribute.attnum
                WHERE relation.relnamespace='public'::regnamespace
                  AND attribute.attnum > 0
                  AND NOT attribute.attisdropped
                """
            )
        ).mappings()
    }
    for key, expected in _COLUMN_CONTRACTS.items():
        row = actual_column_contracts.get(key)
        expected_type, expected_not_null, expected_default, expected_generated = expected
        if row is None:
            problems.append(f"{key[0]}.{key[1]} is absent")
            continue
        if (
            row["data_type"] != expected_type
            or bool(row["attnotnull"]) is not expected_not_null
            or _normalize_sql(row["default_expression"]) != _normalize_sql(expected_default)
            or row["attgenerated"] != expected_generated
        ):
            problems.append(
                f"{key[0]}.{key[1]} has incompatible type/null/default/generated " "contract"
            )

    view_definition = connection.execute(
        text("SELECT pg_get_viewdef(to_regclass('public.billable_calls'), true)")
    ).scalar()
    normalized_view = " ".join((view_definition or "").split()).lower()
    if "not calls.is_test" not in normalized_view and "not is_test" not in normalized_view:
        problems.append("billable_calls does not exclude calls.is_test")

    # Package terms are mutable catalogue data; each paid order snapshots its
    # own terms.  ON CONFLICT deliberately preserves legitimate repricing and
    # reordering.  The schema repair requires the starter identities to exist,
    # never forces their historical prices back onto an existing deployment.
    if require_starter_packages:
        expected_package_codes = {"mins_250", "mins_600", "mins_1500"}
        actual_package_codes = set(
            connection.execute(
                text(
                    """
                    SELECT code
                    FROM topup_packages
                    WHERE code IN ('mins_250', 'mins_600', 'mins_1500')
                    """
                )
            ).scalars()
        )
        if actual_package_codes != expected_package_codes:
            problems.append("starter top-up package insert did not complete")

    if problems:
        raise RuntimeError(
            "0033 bootstrap repair found a malformed partial schema; "
            "transaction rolled back: " + "; ".join(problems)
        )


def upgrade() -> None:
    connection = op.get_bind()
    topup_packages_existed = bool(
        connection.execute(text("SELECT to_regclass('public.topup_packages') IS NOT NULL")).scalar()
    )
    _repair_columns()
    _create_knowledge_and_feedback()
    _create_review_and_prompt_tables()
    _create_ai_audit_and_contact_tables()
    _create_legacy_policy_audit_table()
    _create_billing_tables(seed_starter_packages=not topup_packages_existed)
    for table in _RLS_TABLES:
        _canonical_rls(table)
    _execute("CREATE OR REPLACE VIEW billable_calls AS " "SELECT * FROM calls WHERE NOT is_test")
    _execute(
        "COMMENT ON VIEW billable_calls IS "
        "'calls minus test sessions; use for charging, metering and customer reports.'"
    )
    _validate_contract(require_starter_packages=not topup_packages_existed)


def downgrade() -> None:
    # Hard forward-only boundary.  A no-op downgrade would move the Alembic
    # marker to 0032 while leaving 0033 state behind, falsely claiming a schema
    # that no longer matches its version.  Dropping the state would destroy
    # billing, review, contact or test-call evidence.
    raise RuntimeError(
        "Refusing to downgrade 0033: bootstrap contract repair is a "
        "forward-only data, billing and tenant-isolation boundary"
    )

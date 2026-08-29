"""move the internal Dojo tenant onto the MVP pair, and repair the dead-Llama five

DELIBERATELY NOT A BULK MIGRATION.
Ten tenants have a stored model. This moves SIX and leaves FOUR alone:

    MOVED — the internal validation tenant (1 tenant)
        1845a165  groq/qwen/qwen3.6-27b -> cerebras/gpt-oss-120b
        Owns 5 campaigns, all internal (Dojo-PC AI, INTERNAL-VOICE-VALIDATION,
        Estimation, Construction, dojo), all stopped or draft. This is the
        tenant the frozen live-call batch runs against, so it goes first and
        alone: prove the pair on traffic we own before touching a customer.

    REPAIRED — the dead-Llama five (5 tenants)
        73d2dafd, 5ed4ca7d, ececdad3, f0c29837, 45022490
        groq/llama-3.1-8b-instant -> cerebras/gpt-oss-120b
        That id was removed from the Groq account and returns 404 "does not
        exist or you do not have access to it". Their calls have been silently
        running the Gemini fallback instead of the model their config names.
        This is a repair, not a preference change.

    UNTOUCHED — the remaining qwen three
        5e666d8a, 66f601cd, 4d27c7b5  stay on groq/qwen/qwen3.6-27b
        These are the ones a bulk migration would have swept up. They are
        working, just slower, and moving them is a product decision nobody has
        taken yet.

WHY THERE IS NO PER-CAMPAIGN OPTION
------------------------------------
`campaigns` carries only `knowledge_model` and `tts_provider` — there is no
campaign-level LLM override anywhere in the schema. "Move the Dojo campaign"
therefore necessarily means moving its TENANT, and with it that tenant's other
four campaigns. That is acceptable here only because every one of them is
internal; it would not be acceptable for a customer tenant, and a per-campaign
override is the thing to build before this pattern is repeated.

THE RECORDS ARE THE POINT
--------------------------
`ai_config_migrations` stores the previous provider and model per tenant, so
rollback reads from evidence rather than from someone's memory of what the
value used to be. `downgrade()` restores each tenant to its OWN recorded prior
value — not to a single assumed default, which is what made the previous
draft of this migration wrong: it would have restored the five Llama tenants to
qwen, a model they were never on.

Revision ID: 0019_ai_config_migration_records
Revises: 0018_calls_lead_id_nullable
Create Date: 2026-08-25 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0019_ai_config_migration_records"
down_revision: str | None = "0018_calls_lead_id_nullable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_PROVIDER = "cerebras"
NEW_MODEL = "gpt-oss-120b"
BATCH = "2026-08-25-mvp-pair-phase-1"

# Prefixes, not full UUIDs, so this reads against the same identifiers used in
# the report and the logs. Matched with LIKE and asserted to hit exactly one row.
DOJO_TENANT = "1845a165"
DEAD_LLAMA_TENANTS = ["73d2dafd", "5ed4ca7d", "ececdad3", "f0c29837", "45022490"]


def upgrade() -> None:
    conn = op.get_bind()

    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS ai_config_migrations (
                id             BIGSERIAL PRIMARY KEY,
                tenant_id      UUID        NOT NULL,
                old_provider   VARCHAR(32),
                old_model      VARCHAR(128),
                new_provider   VARCHAR(32) NOT NULL,
                new_model      VARCHAR(128) NOT NULL,
                reason         TEXT        NOT NULL,
                batch          VARCHAR(64) NOT NULL,
                migrated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                rolled_back_at TIMESTAMPTZ
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_ai_config_migrations_batch "
            "ON ai_config_migrations (batch, tenant_id)"
        )
    )
    op.execute(
        text(
            """
            COMMENT ON TABLE ai_config_migrations IS
            'Per-tenant AI model changes with the PREVIOUS value recorded, so a '
            'rollback restores what each tenant actually had rather than a '
            'single assumed default.'
        """
        )
    )

    # Migration 0013 FORCE-enables tenant RLS before this revision.  Count and
    # resolve the historical targets through the canonical transaction-local
    # migration/worker bypass; otherwise an unset tenant context can make a
    # populated database look empty and incorrectly take the bootstrap skip.
    prior_bypass = (
        conn.execute(text("SELECT current_setting('app.bypass_rls', TRUE)")).scalar() or ""
    )
    conn.execute(text("SELECT set_config('app.bypass_rls', 'on', TRUE)"))

    # Both supported bootstrap inputs are schema-only snapshots.  A truly
    # fresh database therefore has no tenant AI rows, and there is no
    # tenant-specific data move to perform.  Keep the exact-one assertions
    # below for every non-empty database: that is the production safety guard
    # against silently moving the wrong tenant or skipping a renamed target.
    tenant_config_count = int(
        conn.execute(text("SELECT count(*) FROM tenant_ai_configs")).scalar() or 0
    )
    if tenant_config_count == 0:
        print(
            "  tenant_ai_configs is empty (schema-only bootstrap); "
            "skipping historical six-tenant AI data move"
        )
        conn.execute(
            text("SELECT set_config('app.bypass_rls', :prior, TRUE)"),
            {"prior": prior_bypass},
        )
        return

    targets = [
        (
            DOJO_TENANT,
            "internal validation tenant — frozen live-call batch "
            "for the MVP model pair (docs/MODEL-SELECTION.md)",
        )
    ]
    targets += [
        (
            t,
            "repair: llama-3.1-8b-instant 404s on this Groq account; calls were "
            "silently running the Gemini fallback",
        )
        for t in DEAD_LLAMA_TENANTS
    ]

    for prefix, reason in targets:
        row = conn.execute(
            text(
                "SELECT tenant_id, llm_provider, llm_model FROM tenant_ai_configs "
                " WHERE tenant_id::text LIKE :p"
            ),
            {"p": f"{prefix}%"},
        ).fetchall()

        if len(row) != 1:
            # Loud and fatal. A prefix that matches zero rows means the tenant
            # moved; matching two means the prefix is ambiguous and we would be
            # editing a tenant nobody authorised.
            raise RuntimeError(
                f"tenant prefix {prefix!r} matched {len(row)} rows, expected 1 — "
                "refusing to guess which tenant was meant"
            )

        tenant_id, old_provider, old_model = row[0]
        if old_provider == NEW_PROVIDER and old_model == NEW_MODEL:
            print(f"  {prefix}: already on {NEW_PROVIDER}/{NEW_MODEL} — skipped")
            continue

        conn.execute(
            text(
                """
                INSERT INTO ai_config_migrations
                    (tenant_id, old_provider, old_model, new_provider, new_model,
                     reason, batch)
                VALUES (:tid, :op_, :om, :np, :nm, :reason, :batch)
                """
            ),
            {
                "tid": tenant_id,
                "op_": old_provider,
                "om": old_model,
                "np": NEW_PROVIDER,
                "nm": NEW_MODEL,
                "reason": reason,
                "batch": BATCH,
            },
        )
        conn.execute(
            text(
                "UPDATE tenant_ai_configs "
                "   SET llm_provider = :np, llm_model = :nm, updated_at = NOW() "
                " WHERE tenant_id = :tid"
            ),
            {"np": NEW_PROVIDER, "nm": NEW_MODEL, "tid": tenant_id},
        )
        print(f"  {prefix}: {old_provider}/{old_model} -> {NEW_PROVIDER}/{NEW_MODEL}")

    remaining = conn.execute(
        text(
            "SELECT llm_provider, llm_model, count(*) FROM tenant_ai_configs "
            " GROUP BY 1,2 ORDER BY 3 DESC"
        )
    ).fetchall()
    print("  after:")
    for prov, model, n in remaining:
        print(f"    {prov}/{model}: {n}")
    conn.execute(
        text("SELECT set_config('app.bypass_rls', :prior, TRUE)"),
        {"prior": prior_bypass},
    )


def downgrade() -> None:
    """Restore each tenant to ITS OWN recorded prior value.

    Not to a single default. The six tenants in this batch came from two
    different models, and a blanket restore would put the Llama five onto qwen —
    a model they were never on.
    """
    conn = op.get_bind()
    rows = conn.execute(
        text(
            "SELECT id, tenant_id, old_provider, old_model FROM ai_config_migrations "
            " WHERE batch = :batch AND rolled_back_at IS NULL"
        ),
        {"batch": BATCH},
    ).fetchall()

    for rec_id, tenant_id, old_provider, old_model in rows:
        if not old_provider or not old_model:
            continue
        conn.execute(
            text(
                "UPDATE tenant_ai_configs "
                "   SET llm_provider = :p, llm_model = :m, updated_at = NOW() "
                " WHERE tenant_id = :tid"
            ),
            {"p": old_provider, "m": old_model, "tid": tenant_id},
        )
        conn.execute(
            text("UPDATE ai_config_migrations SET rolled_back_at = NOW() WHERE id = :i"),
            {"i": rec_id},
        )
        print(f"  restored {str(tenant_id)[:8]} -> {old_provider}/{old_model}")

    # The table itself is kept. Dropping it would destroy the record of what
    # happened, which is the only reason it exists.

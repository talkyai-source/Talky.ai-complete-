"""expanded contact fields (goals.md §11) + structured lead capture (§7)

ONE MIGRATION FOR TWO P0 GOALS, DELIBERATELY
---------------------------------------------
§11 defines what we KNOW about a contact before dialling. §7 defines what the
agent LEARNS about that same contact on the call. They are the same field
vocabulary seen from two ends, and building them separately means designing the
model twice and migrating twice.

WHAT THIS ADDS

  leads.*                    the 8 canonical contact fields §11 lists that the
                             table did not already have
  campaign_lead_fields       per-campaign field DEFINITIONS — what the agent is
                             asked to find out, which of those are required, and
                             who is allowed to see them
  call_lead_details          per-call captured VALUES, one row per field, each
                             carrying where the value came from

PROVENANCE IS THE WHOLE POINT OF §7
------------------------------------
§7: "Record the source of each value: imported contact, caller statement, agent
inference or manual edit" and "Do not treat inferred values as confirmed facts."

So a captured value is never just a string. It carries `source` and `confirmed`,
and the UI is expected to show them differently. A budget the model INFERRED
from "we're a small outfit" is not the same fact as a budget the caller SAID,
and a CRM that cannot tell them apart will eventually act on the wrong one. That
distinction is why this is a row-per-field table rather than a JSONB blob: you
cannot attach provenance to a key inside a blob without inventing a parallel
structure for it anyway.

"UNKNOWN" IS AN ABSENT ROW
--------------------------
§7 asks the agent to use `unknown` when information was not provided. That is
represented by NOT writing a row, not by writing the string "unknown". A missing
row means "never established"; a row with a NULL value means "asked, and the
caller declined" — two different things the dialler and the CRM should treat
differently.

do_not_call IS NOT THE DNC LIST
--------------------------------
`leads.do_not_call` is a per-contact suppression flag, and it is ADDITIVE to the
tenant's DNC list (`/dnc`, blocked_entities), never a replacement. Anything that
dials must keep checking the authoritative list; this flag only lets a specific
contact opt out without needing a list entry. A future reader tempted to treat
this column as "the DNC check" should not.

Revision ID: 0020_contact_and_lead_capture
Revises: 0019_ai_config_migration_records
Create Date: 2026-08-25 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text

from alembic import op

revision: str = "0020_contact_and_lead_capture"
down_revision: str | None = "0019_ai_config_migration_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The four ways a value can arrive, in increasing order of trust.
SOURCES = ("agent_inferred", "caller_stated", "imported", "manual_edit")

FIELD_TYPES = (
    "text", "number", "email", "phone", "datetime",
    "single_select", "multi_select", "notes",
)


def upgrade() -> None:
    # ── §11: the canonical contact model ────────────────────────────────────
    # `phone_number` already exists and stays the DIAL number — the one the
    # dialler uses. mobile/business are additional ways to reach the person,
    # not replacements, so nothing that dials changes behaviour.
    op.execute(
        text(
            """
            ALTER TABLE leads
                ADD COLUMN IF NOT EXISTS business_number           VARCHAR(32),
                ADD COLUMN IF NOT EXISTS company_name              VARCHAR(255),
                ADD COLUMN IF NOT EXISTS job_title                 VARCHAR(255),
                ADD COLUMN IF NOT EXISTS best_time_to_call         VARCHAR(64),
                ADD COLUMN IF NOT EXISTS timezone                  VARCHAR(64),
                ADD COLUMN IF NOT EXISTS calling_notes             TEXT,
                ADD COLUMN IF NOT EXISTS preferred_contact_method  VARCHAR(32),
                ADD COLUMN IF NOT EXISTS do_not_call               BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
    )

    # full_name is DERIVED, never stored independently. Storing it would let it
    # drift out of step with first/last the moment either is edited, and then
    # nobody knows which one is right.
    op.execute(
        text(
            """
            ALTER TABLE leads
                ADD COLUMN IF NOT EXISTS full_name VARCHAR(511)
                GENERATED ALWAYS AS (
                    NULLIF(TRIM(COALESCE(first_name,'') || ' ' || COALESCE(last_name,'')), '')
                ) STORED
            """
        )
    )

    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_leads_do_not_call "
            "ON leads (tenant_id) WHERE do_not_call"
        )
    )
    op.execute(
        text(
            """
            COMMENT ON COLUMN leads.do_not_call IS
            'Per-contact suppression. ADDITIVE to the tenant DNC list, never a '
            'replacement — anything that dials must still check the '
            'authoritative list.'
        """
        )
    )
    op.execute(
        text(
            """
            COMMENT ON COLUMN leads.timezone IS
            'IANA name (e.g. Europe/London). Used to decide whether it is a '
            'civil hour to call this person, which is per-contact and not the '
            'campaign''s timezone.'
        """
        )
    )

    # ── §7: campaign-defined field DEFINITIONS ──────────────────────────────
    op.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS campaign_lead_fields (
                id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id     UUID NOT NULL,
                campaign_id   UUID NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
                field_key     VARCHAR(64)  NOT NULL,
                label         VARCHAR(255) NOT NULL,
                field_type    VARCHAR(32)  NOT NULL DEFAULT 'text',
                is_required   BOOLEAN NOT NULL DEFAULT FALSE,
                -- Two independent visibilities, because they answer different
                -- questions: agent_visible decides whether the field is put in
                -- front of the model at all; user_visible decides whether a
                -- human sees it in the lead panel. A field can be captured for
                -- reporting without the agent ever being told to chase it.
                agent_visible BOOLEAN NOT NULL DEFAULT TRUE,
                user_visible  BOOLEAN NOT NULL DEFAULT TRUE,
                options       JSONB,
                sort_order    INTEGER NOT NULL DEFAULT 0,
                created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT campaign_lead_fields_key_unique UNIQUE (campaign_id, field_key),
                CONSTRAINT campaign_lead_fields_type_valid
                    CHECK (field_type IN {FIELD_TYPES})
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_campaign_lead_fields_campaign "
            "ON campaign_lead_fields (campaign_id, sort_order)"
        )
    )

    # ── §7: per-call captured VALUES, with provenance ───────────────────────
    op.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS call_lead_details (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id    UUID NOT NULL,
                call_id      UUID NOT NULL REFERENCES calls(id) ON DELETE CASCADE,
                campaign_id  UUID,
                lead_id      UUID,
                field_key    VARCHAR(64) NOT NULL,
                field_type   VARCHAR(32) NOT NULL DEFAULT 'text',
                -- One text column rather than one per type. The type is
                -- declared alongside so a reader can coerce, and a select's
                -- multi-value case is a JSON array in the same column. Typed
                -- columns would mean eight mostly-NULL fields per row and a
                -- COALESCE at every read.
                value        TEXT,
                source       VARCHAR(24) NOT NULL,
                -- Did the caller confirm this back? §7: "Confirm important
                -- contact and appointment information." An unconfirmed value
                -- is usable but must not be presented as settled.
                confirmed    BOOLEAN NOT NULL DEFAULT FALSE,
                is_required  BOOLEAN NOT NULL DEFAULT FALSE,
                created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT call_lead_details_unique UNIQUE (call_id, field_key),
                CONSTRAINT call_lead_details_source_valid CHECK (source IN {SOURCES}),
                CONSTRAINT call_lead_details_type_valid CHECK (field_type IN {FIELD_TYPES})
            )
            """
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_call_lead_details_call "
            "ON call_lead_details (call_id)"
        )
    )
    op.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_call_lead_details_lead "
            "ON call_lead_details (tenant_id, lead_id) WHERE lead_id IS NOT NULL"
        )
    )
    op.execute(
        text(
            """
            COMMENT ON COLUMN call_lead_details.source IS
            'imported | caller_stated | agent_inferred | manual_edit. An '
            'inferred value is NOT a confirmed fact (goals.md §7) and the UI '
            'must show it differently.'
        """
        )
    )
    op.execute(
        text(
            """
            COMMENT ON TABLE call_lead_details IS
            'Captured field values per call. ABSENT row = never established; '
            'row with NULL value = asked and declined. Those are different and '
            'the dialler should treat them differently.'
        """
        )
    )

    # ── RLS, matching the canonical shape from migration 0013 ───────────────
    for tbl in ("campaign_lead_fields", "call_lead_details"):
        op.execute(text(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY"))
        op.execute(text(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY"))
        op.execute(text(f"DROP POLICY IF EXISTS {tbl}_tenant_isolation ON {tbl}"))
        op.execute(
            text(
                f"""
                CREATE POLICY {tbl}_tenant_isolation ON {tbl}
                    USING (
                        COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)
                        OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid
                    )
                    WITH CHECK (
                        COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE), '')::boolean, FALSE)
                        OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE), '')::uuid
                    )
                """
            )
        )


def downgrade() -> None:
    op.execute(text("DROP TABLE IF EXISTS call_lead_details"))
    op.execute(text("DROP TABLE IF EXISTS campaign_lead_fields"))
    # The lead columns STAY. Dropping them would destroy contact data a tenant
    # imported — a data loss dressed up as a schema rollback. They are nullable
    # and default-safe, so leaving them costs nothing.

"""Give one assignment table two kinds of address: a public DID or an extension.

Revision ID: 0046_inbound_extension_bindings  (<= 32 chars: alembic_version.version_num is varchar(32))
Revises: 0045_refresh_session_binding

An earlier draft of this revision gave internal PBX extensions their own table.
That was wrong, and it was corrected before the revision was ever applied
anywhere (production is on 0045).

Why a separate table was wrong: three tables carry a foreign key to
``inbound_did_assignments`` -- ``calls.assignment_id`` (composite, tenant-paired),
``inbound_rejections.assignment_id``, and ``inbound_reassignment_requests``. An
extension-addressed call is routed and admitted exactly like a DID call, and then
the ``calls`` INSERT names an assignment id that is not in the referenced table,
so the foreign key rejects it and the call dies AFTER being answered. Keeping the
second table would have meant a parallel nullable column plus a branch on every
one of those integrations, forever.

Folding the two kinds into one table is also closer to the truth: an assignment
is "this ADDRESS routes to this campaign's config on this trunk for this tenant".
Only the address differs.

The two original objections to reuse are both still honoured:

* The E.164 CHECK is not weakened. It becomes NULL-tolerant so an extension row
  can leave ``canonical_did`` empty, and a non-NULL value must still match
  ``^\\+[1-9][0-9]{6,14}$`` exactly as before.
* No ``ext:`` value is written to ``tenant_phone_numbers``, whose every row is
  read by ``trunk_resolver._select_caller_id`` when choosing an outbound
  caller-ID. ``phone_number_id`` is simply NULL for an extension.

``address_kind_exactly_one`` makes the discrimination structural: exactly one of
the two address columns is set, and a DID row must still carry its verified
phone-number row.

Uniqueness mirrors the DID rules and is GLOBAL, not per tenant: a DID list is per
tenant, but a carrier account namespace is not -- ``940005`` is one account on the
carrier and exactly one tenant may answer it.
"""
from alembic import op
from sqlalchemy import text

revision = "0046_inbound_extension_bindings"
down_revision = "0045_refresh_session_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        text(
            "ALTER TABLE public.inbound_did_assignments "
            "ADD COLUMN IF NOT EXISTS extension TEXT"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.inbound_did_assignments "
            "ALTER COLUMN canonical_did DROP NOT NULL"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.inbound_did_assignments "
            "ALTER COLUMN phone_number_id DROP NOT NULL"
        )
    )

    # The E.164 rule itself is unchanged; it only stops applying to a NULL.
    op.execute(
        text(
            "ALTER TABLE public.inbound_did_assignments "
            "DROP CONSTRAINT IF EXISTS inbound_did_assignments_canonical_did_check"
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE public.inbound_did_assignments
            ADD CONSTRAINT inbound_did_assignments_canonical_did_check
            CHECK (canonical_did IS NULL OR canonical_did ~ '^\\+[1-9][0-9]{6,14}$')
            """
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE public.inbound_did_assignments
            ADD CONSTRAINT inbound_assignment_extension_check
            CHECK (extension IS NULL OR extension ~ '^[0-9]{3,8}$')
            """
        )
    )
    # Structural discrimination: one address, and a DID keeps its proof of
    # ownership (the verified tenant_phone_numbers row). An extension proves
    # ownership through the trunk that registers it instead, which the router
    # and the reconciler both check with st.auth_username = extension.
    op.execute(
        text(
            """
            ALTER TABLE public.inbound_did_assignments
            ADD CONSTRAINT inbound_assignment_address_kind_exactly_one
            CHECK (
                (canonical_did IS NOT NULL AND extension IS NULL
                 AND phone_number_id IS NOT NULL)
                OR
                (extension IS NOT NULL AND canonical_did IS NULL
                 AND phone_number_id IS NULL)
            )
            """
        )
    )

    # Mirrors uq_inbound_active_canonical_did / uq_inbound_live_canonical_did.
    op.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_inbound_active_extension "
            "ON public.inbound_did_assignments (extension) "
            "WHERE extension IS NOT NULL AND status = 'active'"
        )
    )
    op.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_inbound_live_extension "
            "ON public.inbound_did_assignments (extension) "
            "WHERE extension IS NOT NULL AND status <> 'archived'"
        )
    )


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS uq_inbound_live_extension"))
    op.execute(text("DROP INDEX IF EXISTS uq_inbound_active_extension"))
    op.execute(
        text(
            "ALTER TABLE public.inbound_did_assignments "
            "DROP CONSTRAINT IF EXISTS inbound_assignment_address_kind_exactly_one"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.inbound_did_assignments "
            "DROP CONSTRAINT IF EXISTS inbound_assignment_extension_check"
        )
    )
    op.execute(
        text("DELETE FROM public.inbound_did_assignments WHERE extension IS NOT NULL")
    )
    op.execute(
        text(
            "ALTER TABLE public.inbound_did_assignments DROP COLUMN IF EXISTS extension"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.inbound_did_assignments "
            "DROP CONSTRAINT IF EXISTS inbound_did_assignments_canonical_did_check"
        )
    )
    op.execute(
        text(
            """
            ALTER TABLE public.inbound_did_assignments
            ADD CONSTRAINT inbound_did_assignments_canonical_did_check
            CHECK (canonical_did ~ '^\\+[1-9][0-9]{6,14}$')
            """
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.inbound_did_assignments "
            "ALTER COLUMN phone_number_id SET NOT NULL"
        )
    )
    op.execute(
        text(
            "ALTER TABLE public.inbound_did_assignments "
            "ALTER COLUMN canonical_did SET NOT NULL"
        )
    )

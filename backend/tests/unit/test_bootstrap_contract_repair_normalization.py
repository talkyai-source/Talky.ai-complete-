"""``_normalize_sql`` must reconcile the two PostgreSQL renderings of the same
``= ANY (ARRAY[...])`` CHECK without weakening real drift detection.

A production database bootstrapped from ``database/complete_schema.sql`` renders
the array-level ``::text[]`` cast distributed onto every element, while a
migration-built database keeps it at the array level.  The two spellings denote
the identical ``text[]`` value, so 0033's catalog validator must treat them as
equal; every other difference (values, column, operator, result type, absence)
must still be reported.
"""

from __future__ import annotations

import importlib

import pytest

MIGRATION = importlib.import_module("Alembic.versions.0033_bootstrap_contract_repair")
_normalize_sql = MIGRATION._normalize_sql
EXPECTED = MIGRATION._REQUIRED_CONSTRAINT_DEFINITIONS


def _production_rendering(column: str, values: tuple[str, ...]) -> str:
    """The spelling ``pg_get_constraintdef`` emits on the restored production
    dump: the ``::text`` cast distributed onto each ``character varying``
    element instead of applied to the array."""
    elements = ", ".join(f"'{value}'::character varying::text" for value in values)
    return f"CHECK ({column}::text = ANY (ARRAY[{elements}]))"


_FIELD_TYPES = (
    "text",
    "number",
    "email",
    "phone",
    "datetime",
    "single_select",
    "multi_select",
    "notes",
)

# The seven pairs that failed `alembic upgrade head` against the restored
# production dump (schema 0021_billing_topup).
PRODUCTION_CONSTRAINTS: dict[tuple[str, str], str] = {
    ("call_feedback", "call_feedback_status_valid"): _production_rendering(
        "transcript_status", ("pending", "done", "failed")
    ),
    ("call_feedback", "call_feedback_storage_provider_valid"): _production_rendering(
        "audio_storage_provider", ("s3", "local")
    ),
    ("campaign_lead_fields", "campaign_lead_fields_type_valid"): _production_rendering(
        "field_type", _FIELD_TYPES
    ),
    ("call_lead_details", "call_lead_details_source_valid"): _production_rendering(
        "source", ("agent_inferred", "caller_stated", "imported", "manual_edit")
    ),
    ("call_lead_details", "call_lead_details_type_valid"): _production_rendering(
        "field_type", _FIELD_TYPES
    ),
    ("topup_orders", "topup_orders_status_valid"): _production_rendering(
        "status",
        ("pending", "paid", "failed", "cancelled", "refunded", "disputed"),
    ),
    ("billing_ledger", "billing_ledger_kind_valid"): _production_rendering(
        "kind", ("topup", "refund", "adjustment", "dispute")
    ),
}


@pytest.mark.parametrize("key", sorted(PRODUCTION_CONSTRAINTS))
def test_production_element_cast_rendering_matches_the_expected_definition(
    key: tuple[str, str],
) -> None:
    assert key in EXPECTED, f"{key} is no longer part of the 0033 contract"
    assert _normalize_sql(EXPECTED[key]) == _normalize_sql(PRODUCTION_CONSTRAINTS[key])


def test_migration_built_rendering_still_matches_itself() -> None:
    # The array-level cast spelling (a database built by running the migration
    # chain) must keep comparing equal - this is the path that already worked.
    for key, definition in EXPECTED.items():
        assert _normalize_sql(definition) == _normalize_sql(definition), key


# --------------------------------------------------------------------------
# Negative cases: these MUST still compare unequal.
# --------------------------------------------------------------------------


def test_a_missing_enum_value_is_still_drift() -> None:
    truncated = _production_rendering(
        "status", ("pending", "paid", "failed", "cancelled", "refunded")
    )
    expected = EXPECTED[("topup_orders", "topup_orders_status_valid")]
    assert _normalize_sql(expected) != _normalize_sql(truncated)


def test_an_extra_enum_value_is_still_drift() -> None:
    widened = _production_rendering(
        "kind", ("topup", "refund", "adjustment", "dispute", "chargeback")
    )
    expected = EXPECTED[("billing_ledger", "billing_ledger_kind_valid")]
    assert _normalize_sql(expected) != _normalize_sql(widened)


def test_a_renamed_enum_value_is_still_drift() -> None:
    renamed = _production_rendering("kind", ("topup", "refund", "adjustment", "disputes"))
    expected = EXPECTED[("billing_ledger", "billing_ledger_kind_valid")]
    assert _normalize_sql(expected) != _normalize_sql(renamed)


def test_a_different_column_is_still_drift() -> None:
    wrong_column = _production_rendering("type", ("topup", "refund", "adjustment", "dispute"))
    expected = EXPECTED[("billing_ledger", "billing_ledger_kind_valid")]
    assert _normalize_sql(expected) != _normalize_sql(wrong_column)


def test_a_different_operator_is_still_drift() -> None:
    inverted = (
        "CHECK (kind::text <> ALL (ARRAY['topup'::character varying::text, "
        "'refund'::character varying::text, 'adjustment'::character varying::text, "
        "'dispute'::character varying::text]))"
    )
    expected = EXPECTED[("billing_ledger", "billing_ledger_kind_valid")]
    assert _normalize_sql(expected) != _normalize_sql(inverted)


def test_an_absent_constraint_is_still_drift() -> None:
    # ``_validate_contract`` compares the normalized expected string against
    # ``None`` when the constraint does not exist.
    for key, definition in EXPECTED.items():
        assert _normalize_sql(definition) != _normalize_sql(None), key
        assert _normalize_sql(definition) is not None


def test_a_dropped_left_hand_cast_is_still_drift() -> None:
    # ``kind = ANY`` and ``kind::text = ANY`` are different expressions; the
    # canonicalisation must not touch the left-hand side.
    no_cast = (
        "CHECK (kind = ANY (ARRAY['topup'::character varying::text, "
        "'refund'::character varying::text, 'adjustment'::character varying::text, "
        "'dispute'::character varying::text]))"
    )
    expected = EXPECTED[("billing_ledger", "billing_ledger_kind_valid")]
    assert _normalize_sql(expected) != _normalize_sql(no_cast)


def test_a_different_array_result_type_is_still_drift() -> None:
    varchar_array = (
        "CHECK (kind::text = ANY (ARRAY['topup'::character varying, "
        "'refund'::character varying, 'adjustment'::character varying, "
        "'dispute'::character varying]::character varying[]))"
    )
    expected = EXPECTED[("billing_ledger", "billing_ledger_kind_valid")]
    assert _normalize_sql(expected) != _normalize_sql(varchar_array)


def test_a_lossy_element_cast_is_not_stripped() -> None:
    # ``'topup'::character(3)`` truncates; it is not the same value list.
    lossy = (
        "CHECK (kind::text = ANY (ARRAY['topup'::character(3), "
        "'refund'::character(3), 'adjustment'::character(3), "
        "'dispute'::character(3)]::text[]))"
    )
    expected = EXPECTED[("billing_ledger", "billing_ledger_kind_valid")]
    assert _normalize_sql(expected) != _normalize_sql(lossy)
    lossy_varchar = (
        "CHECK (kind::text = ANY (ARRAY['topup'::character varying(3), "
        "'refund'::character varying(3), 'adjustment'::character varying(3), "
        "'dispute'::character varying(3)]::text[]))"
    )
    assert _normalize_sql(expected) != _normalize_sql(lossy_varchar)


def test_a_non_literal_array_element_is_left_alone() -> None:
    # Only arrays of plain string literals are canonicalised; anything else is
    # compared verbatim so a functional expression cannot be smuggled in.
    original = "check (kind::text = any (array[lower(kind), 'refund'::text]))"
    assert _normalize_sql(original) == original


# --------------------------------------------------------------------------
# The pre-existing behaviour (case folding + whitespace collapse) is unchanged
# for every input that does not contain an array literal.
# --------------------------------------------------------------------------


def test_none_and_whitespace_behaviour_is_unchanged() -> None:
    assert _normalize_sql(None) == ""
    assert _normalize_sql("") == ""
    assert _normalize_sql("  PRIMARY   KEY (id)\n") == "primary key (id)"
    assert _normalize_sql("FOREIGN KEY (call_id)\tREFERENCES calls(id) ON DELETE CASCADE") == (
        "foreign key (call_id) references calls(id) on delete cascade"
    )
    assert _normalize_sql("'GBP'::character varying") == "'gbp'::character varying"


def test_definitions_without_array_literals_are_only_case_and_space_folded() -> None:
    for key, definition in EXPECTED.items():
        if "ARRAY[" in definition:
            continue
        assert _normalize_sql(definition) == " ".join(definition.lower().split()), key

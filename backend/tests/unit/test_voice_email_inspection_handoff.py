"""A saved voice receipt supplies inspection proof without inferred linkage."""
from copy import deepcopy

import pytest

from app.api.v1.endpoints.admin.actions import _gmail_inspection_bundle
from tests.unit.test_voice_email_receipt_proof import (
    dispatch,
    receipt,
    setup_voice as voice_fixture,
)


@pytest.fixture
async def setup_voice(monkeypatch):
    async for value in voice_fixture.__wrapped__(monkeypatch):
        yield value


@pytest.mark.parametrize("state,fail_outer_save", [
    ("accepted", False), ("unknown", False), ("accepted", True),
])
async def test_actual_saved_voice_email_supplies_one_original_inspection_bundle(
    setup_voice, state, fail_outer_save,
):
    db, _, send = setup_voice
    inner = receipt(db, state, external=False)
    result, public = await dispatch(setup_voice, "send_email", inner, fail_outer_save=fail_outer_save)
    saved_before = deepcopy(db.row)

    proof, message_id = _gmail_inspection_bundle(db.row)

    assert proof == {key: inner[key] for key in (
        "identity_version", "tenant_id", "connector_id", "provider", "account_row_id",
    )}
    assert message_id == inner["message_id"]
    assert db.row == saved_before and send.await_count == 1
    assert public["receipt"]["child_action_id"] == inner["action_id"]
    assert result["success"] is (state == "accepted" and not fail_outer_save)
    assert public["confirmation_allowed"] is (state == "accepted" and not fail_outer_save)


async def test_form_receipt_keeps_evidence_without_silently_expanding_email_inspection(setup_voice):
    db, _, send = setup_voice
    inner = receipt(db, "accepted", external=False)
    _, public = await dispatch(setup_voice, "submit_form", inner)

    assert _gmail_inspection_bundle(db.row) is None
    assert public["receipt"]["account_row_id"] == inner["account_row_id"]
    assert public["receipt"]["message_id"] == inner["message_id"]
    assert public["receipt"]["child_action_id"] == inner["action_id"]
    assert send.await_count == 1

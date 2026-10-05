"""CRM retry/source boundaries with real service logic and no provider I/O."""
from unittest.mock import AsyncMock
import pytest

from tests.unit.test_crm_sync_service import (
    CALL, TENANT, FakeConnector, _run, harness as crm_harness,
)


@pytest.fixture
def harness(monkeypatch):
    return crm_harness.__wrapped__(monkeypatch)


def test_revised_transcript_does_not_publish_obsolete_summary(harness):
    connector = FakeConnector(found={"id": "contact"})
    harness["connectors"]["salesforce"] = connector
    harness["call"].update(
        transcript="User: Correction, I do not want a callback.",
        transcript_json=[{"role": "user", "content": "Correction, I do not want a callback."}],
        summary_json={"headline": "Caller requested a callback", "next_step": "Call tomorrow"},
        summary_transcript_hash="obsolete-source-revision",
    )

    _run(harness["service"].sync_call(TENANT, CALL))

    bodies = [c[2] for c in connector.calls if c[0] in {"log", "update"}]
    assert all("Caller requested a callback" not in body for body in bodies)
    assert all("Call tomorrow" not in body for body in bodies)


def test_unknown_contact_create_never_reconciles_against_changed_recipient(harness):
    connector = FakeConnector()
    connector.create_contact = AsyncMock(side_effect=TimeoutError("synthetic lost create response"))
    harness["connectors"]["salesforce"] = connector
    harness["lead"]["email"] = "original@example.com"

    assert not _run(harness["service"].sync_call(TENANT, CALL)).success
    receipt = harness["deliveries"].rows[(TENANT, CALL, "salesforce")]
    assert receipt["status"] == "unknown" and receipt["phase"] == "creating_contact"
    harness["lead"]["email"] = "corrected@example.com"
    connector.found = {"id": "different-contact"}

    result = _run(harness["service"].sync_call(TENANT, CALL, reason="retry"))

    assert not result.success
    assert not any(c[0] == "search" and c[1] == "corrected@example.com" for c in connector.calls)
    assert not any(c[0] == "log" for c in connector.calls)
    assert connector.create_contact.await_count == 1


@pytest.mark.parametrize("status", ["cancelled", "needs_clarification", "awaiting_confirmation"])
def test_primary_manual_withdrawal_or_unconfirmed_caller_never_falls_back_to_import(harness, status):
    connector = FakeConnector(found={"id": "old-import-contact"})
    harness["connectors"]["salesforce"] = connector
    harness["lead"]["phone_number"] = None
    harness["call"]["phone_number"] = None
    row = {"field_key": "email", "field_type": "email", "value": None,
           "source": "manual_edit" if status == "cancelled" else "caller_stated",
           "validation_status": status, "confirmed": False,
           "evidence": {"provenance_status": "matched"}}
    harness["call_details"] = [row]
    harness["lead_details"] = [row]

    result = _run(harness["service"].sync_call(TENANT, CALL))

    assert not result.success
    assert connector.calls == []


def test_manual_primary_correction_replaces_import_before_first_effect(harness):
    connector = FakeConnector(found={"id": "manual-contact"})
    harness["connectors"]["salesforce"] = connector
    harness["lead_details"] = [{"field_key": "email", "field_type": "email",
        "value": "manual@example.com", "normalized_value": "manual@example.com",
        "source": "manual_edit", "validation_status": "confirmed", "confirmed": True,
        "evidence": {}}]

    result = _run(harness["service"].sync_call(TENANT, CALL))

    assert result.success
    assert ("search", "manual@example.com", harness["lead"]["phone_number"]) in connector.calls


def test_manual_change_after_remote_association_holds_instead_of_updating_wrong_contact(harness):
    connector = FakeConnector(found={"id": "original-contact"})
    harness["connectors"]["salesforce"] = connector
    assert _run(harness["service"].sync_call(TENANT, CALL)).success
    harness["lead_details"] = [{"field_key": "email", "field_type": "email",
        "value": "corrected@example.com", "source": "manual_edit",
        "validation_status": "confirmed", "confirmed": True, "evidence": {}}]
    harness["call"]["summary_json"] = {"headline": "A newer summary"}

    result = _run(harness["service"].sync_call(TENANT, CALL, reason="retry"))

    assert not result.success
    assert not any(c[0] == "update" for c in connector.calls)


@pytest.mark.parametrize("proof", ["matched", "unversioned", "needs_review"])
def test_only_current_confirmed_source_matched_caller_primary_is_used(harness, proof):
    connector = FakeConnector(found={"id": "caller-contact"})
    harness["connectors"]["salesforce"] = connector
    harness["call"]["phone_number"] = harness["lead"]["phone_number"] = None
    harness["call_details"] = [{"field_key": "email", "field_type": "email",
        "value": "current@example.com", "source": "caller_stated", "confirmed": True,
        "validation_status": "confirmed", "evidence": {"provenance_status": proof}}]
    result = _run(harness["service"].sync_call(TENANT, CALL))
    if proof == "matched":
        assert result.success and ("search", "current@example.com", None) in connector.calls
    else:
        assert not result.success and connector.calls == []


def test_numbered_or_older_caller_contact_is_not_promoted(harness):
    connector = FakeConnector(found={"id": "import-contact"})
    harness["connectors"]["salesforce"] = connector
    row = {"field_key": "email_2", "field_type": "email", "value": "other@example.com",
           "source": "caller_stated", "confirmed": True, "validation_status": "confirmed",
           "evidence": {"provenance_status": "matched"}}
    harness["call_details"] = [row]
    harness["lead_details"] = [dict(row, field_key="email")]
    assert _run(harness["service"].sync_call(TENANT, CALL)).success
    assert connector.calls[0] == ("search", harness["lead"]["email"], harness["lead"]["phone_number"])


def test_phone_withdrawal_does_not_fall_back_to_call_number(harness):
    connector = FakeConnector(found={"id": "old-phone-contact"})
    harness["connectors"]["salesforce"] = connector
    harness["lead"]["email"] = None
    harness["call_details"] = [{"field_key": "phone", "field_type": "phone", "value": None,
        "source": "manual_edit", "confirmed": False, "validation_status": "cancelled", "evidence": {}}]
    assert not _run(harness["service"].sync_call(TENANT, CALL)).success
    assert connector.calls == []


def test_lost_create_has_original_arguments_and_account_saved_before_provider_write(harness):
    connector = FakeConnector()
    harness["connectors"]["salesforce"] = connector
    async def create(**arguments):
        row = harness["deliveries"].rows[(TENANT, CALL, "salesforce")]
        assert row["phase"] == "creating_contact"
        assert row["contact_effect"]["arguments"] == arguments
        assert row["contact_effect"]["account_id"] == connector.external_account_id
        assert row["contact_effect"]["connector_id"] == connector.connector_id
        raise TimeoutError("synthetic lost result")
    connector.create_contact = AsyncMock(side_effect=create)
    assert not _run(harness["service"].sync_call(TENANT, CALL)).success
    # Even a later positive broad match is not proof of the original effect.
    connector.found = {"id": "unverified-found-record"}
    assert not _run(harness["service"].sync_call(TENANT, CALL)).success
    assert connector.create_contact.await_count == 1
    assert len([c for c in connector.calls if c[0] == "search"]) == 1


def test_source_change_after_rejected_create_does_not_replay_old_description(harness):
    from app.infrastructure.connectors.base import ConnectorProviderError
    connector = FakeConnector()
    harness["connectors"]["salesforce"] = connector
    connector.create_contact = AsyncMock(side_effect=ConnectorProviderError(
        provider="salesforce", operation="create_contact", category="rate_limit",
        message="synthetic rejected request", status_code=429))
    assert not _run(harness["service"].sync_call(TENANT, CALL)).success
    harness["call"].update(source_revision="2", transcript="User: Correction, not that request.")
    connector.create_contact.side_effect = None
    connector.create_contact.return_value = {"id": "new-contact"}
    assert not _run(harness["service"].sync_call(TENANT, CALL)).success
    assert connector.create_contact.await_count == 1


def test_summary_without_source_hash_is_omitted_but_current_summary_is_kept(harness):
    connector = FakeConnector(found={"id": "contact"})
    harness["connectors"]["salesforce"] = connector
    harness["call"].update(summary_json={"headline": "Current synthetic summary"}, summary_transcript_hash=None)
    assert _run(harness["service"].sync_call(TENANT, CALL)).success
    assert "Current synthetic summary" not in connector.calls[-1][2]
    del harness["call"]["summary_transcript_hash"]  # Fixture now supplies actual matching source proof.
    assert _run(harness["service"].sync_call(TENANT, CALL)).success
    assert "Current synthetic summary" in connector.calls[-1][2]


@pytest.mark.asyncio
async def test_public_crm_receipt_keeps_original_references_but_no_raw_error_or_payload(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from app.core import db_utils
    from app.domain.services.lead_capture_service import LeadCaptureService
    row = {"call_id": CALL, "provider": "salesforce", "status": "unknown",
           "phase": "creating_contact", "attempts": 1, "updated_at": None,
           "destination_connector_id": "original-connection", "destination_account_id": "original-account",
           "remote_contact_id": None, "remote_call_id": None, "contact_effect_available": True,
           "payload_digest": "a" * 64, "last_error": "provider body private@example.com"}
    conn = SimpleNamespace(fetch=AsyncMock(return_value=[row]))
    @asynccontextmanager
    async def acquire(*_args):
        yield conn
    monkeypatch.setattr(db_utils, "acquire_with_tenant", acquire)
    result = (await LeadCaptureService(object()).crm_deliveries(TENANT, call_id=CALL))[0]
    assert result["destination_account_id"] == "original-account"
    assert result["payload_digest"] == "a" * 64
    assert result["last_error"] == "Original CRM delivery needs review."
    assert "private@example.com" not in str(result)
    assert "contact_effect" not in result


def test_withdrawal_during_contact_search_blocks_later_create(harness):
    connector = FakeConnector()
    harness["connectors"]["salesforce"] = connector
    harness["lead"]["phone_number"] = harness["call"]["phone_number"] = None
    row = {"field_key": "email", "field_type": "email", "value": "reviewed@example.com",
           "source": "manual_edit", "confirmed": True, "validation_status": "confirmed", "evidence": {}}
    harness["lead_details"] = [row]
    async def delayed_search(**_kwargs):
        harness["lead_details"] = [dict(row, value=None, confirmed=False, validation_status="cancelled")]
        return None
    connector.search_contact = AsyncMock(side_effect=delayed_search)
    result = _run(harness["service"].sync_call(TENANT, CALL))
    assert not result.success
    assert not any(c[0] in ("create", "log") for c in connector.calls)


def test_account_switch_during_contact_search_blocks_later_create(harness):
    original = FakeConnector()
    replacement = FakeConnector()
    replacement.external_account_id = "replacement-account"
    harness["connectors"]["salesforce"] = original
    async def delayed_search(**_kwargs):
        harness["connectors"]["salesforce"] = replacement
        return None
    original.search_contact = AsyncMock(side_effect=delayed_search)
    result = _run(harness["service"].sync_call(TENANT, CALL))
    assert not result.success
    assert original.calls == [] and replacement.calls == []
    row = harness["deliveries"].rows[(TENANT, CALL, "salesforce")]
    assert row["status"] == "unknown" and row["destination_account_id"] == original.external_account_id


def test_source_revision_during_contact_search_blocks_later_log(harness):
    connector = FakeConnector()
    harness["connectors"]["salesforce"] = connector
    async def delayed_search(**_kwargs):
        harness["call"]["source_revision"] = "newer-source"
        return {"id": "existing-contact"}
    connector.search_contact = AsyncMock(side_effect=delayed_search)
    result = _run(harness["service"].sync_call(TENANT, CALL))
    assert not result.success and connector.calls == []


@pytest.mark.parametrize("refresh", [False, True])
def test_withdrawal_during_connector_resolution_blocks_write(harness, monkeypatch, refresh):
    from app.infrastructure.connectors.base import ConnectorProviderError
    connector = FakeConnector(found={"id": "contact"})
    if refresh:
        connector._fail_first_with = ConnectorProviderError(provider="salesforce", operation="log_call",
            category="authentication", status_code=401, message="synthetic rejection")
    harness["connectors"]["salesforce"] = connector
    row = {"field_key": "email", "field_type": "email", "value": "reviewed@example.com",
           "source": "manual_edit", "confirmed": True, "validation_status": "confirmed", "evidence": {}}
    harness["lead_details"] = [row]
    resolutions = 0
    async def resolve(*_args, force_refresh=False, **_kwargs):
        nonlocal resolutions
        resolutions += 1
        if (refresh and force_refresh) or (not refresh and resolutions == 2):
            harness["lead_details"] = [dict(row, value=None, confirmed=False, validation_status="cancelled")]
        return connector
    monkeypatch.setattr(harness["service"], "_connector", resolve)
    result = _run(harness["service"].sync_call(TENANT, CALL))
    assert not result.success
    assert not any(c[0] == "log" for c in connector.calls)


def test_harmless_call_metadata_update_does_not_strand_identical_rejected_create(harness):
    from app.infrastructure.connectors.base import ConnectorProviderError
    connector = FakeConnector()
    harness["connectors"]["salesforce"] = connector
    connector.create_contact = AsyncMock(side_effect=ConnectorProviderError(
        provider="salesforce", operation="create_contact", category="rate_limit",
        message="synthetic rejected request", status_code=429))
    assert not _run(harness["service"].sync_call(TENANT, CALL)).success
    harness["call"]["source_revision"] = "metadata-only-new-xmin"
    connector.create_contact.side_effect = None
    connector.create_contact.return_value = {"id": "accepted-original-contact"}
    result = _run(harness["service"].sync_call(TENANT, CALL))
    assert result.success
    assert connector.create_contact.await_count == 2


@pytest.mark.parametrize("field,value", [("first_name", "Corrected"), ("company_name", "New company"), ("job_title", "New role")])
def test_current_create_metadata_is_checked_after_connector_await(harness, monkeypatch, field, value):
    connector = FakeConnector()
    harness["connectors"]["salesforce"] = connector
    resolutions = 0
    async def resolve(*_args, **_kwargs):
        nonlocal resolutions
        resolutions += 1
        if resolutions == 2:
            harness["lead"][field] = value
        return connector
    monkeypatch.setattr(harness["service"], "_connector", resolve)
    result = _run(harness["service"].sync_call(TENANT, CALL))
    assert not result.success
    assert not any(c[0] in ("create", "log") for c in connector.calls)


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("change", ["account", "settings", "withdrawal", "source"])
def test_final_snapshot_blocks_authority_changes_during_source_reads(harness, monkeypatch, refresh, change):
    from app.infrastructure.connectors.base import ConnectorProviderError
    connector = FakeConnector(found={"id": "contact"})
    if refresh:
        connector._fail_first_with = ConnectorProviderError(provider="salesforce", operation="log_call",
            category="authentication", status_code=401, message="synthetic rejection")
    harness["connectors"]["salesforce"] = connector
    original = harness["service"]._recipient_lead
    reads = 0

    async def delayed_source(*args):
        nonlocal reads
        current = await original(*args)
        reads += 1
        if reads == (3 if refresh else 2):
            # The just-read source remains unchanged. A separate transaction
            # commits while that awaited read completes, before provider entry.
            if change == "account":
                replacement = FakeConnector()
                replacement.external_account_id = "different-account"
                harness["connectors"]["salesforce"] = replacement
            elif change == "settings":
                connector.config = {"log_calls": False}
            elif change == "withdrawal":
                harness["lead_details"] = [{"field_key": "email", "field_type": "email",
                    "source": "manual_edit", "value": None, "confirmed": False,
                    "validation_status": "cancelled", "evidence": {}}]
            else:
                harness["call"]["source_revision"] = "committed-after-read"
        return current

    monkeypatch.setattr(harness["service"], "_recipient_lead", delayed_source)
    result = _run(harness["service"].sync_call(TENANT, CALL))
    assert not result.success
    assert not any(c[0] == "log" for c in connector.calls)
    assert not any(c[0] == "log" for c in harness["connectors"]["salesforce"].calls)


def test_token_only_refresh_during_source_read_does_not_change_authority(harness, monkeypatch):
    connector = FakeConnector(found={"id": "contact"})
    harness["connectors"]["salesforce"] = connector
    original = harness["service"]._recipient_lead

    async def refresh_token(*args):
        current = await original(*args)
        connector.access_token = "synthetic-rotated-token"
        return current

    monkeypatch.setattr(harness["service"], "_recipient_lead", refresh_token)
    assert _run(harness["service"].sync_call(TENANT, CALL)).success
    assert len([c for c in connector.calls if c[0] == "log"]) == 1


@pytest.mark.parametrize("provider", ["hubspot", "salesforce"])
def test_effective_settings_follow_the_actual_provider_configuration_contract(harness, monkeypatch, provider):
    connector = FakeConnector(provider, found={"id": "contact"})
    harness["providers"] = [provider]
    harness["connectors"][provider] = connector
    original = harness["service"]._write_admission_stamp

    async def stored_settings(*args):
        stamp = await original(*args)
        stamp["connector_config"] = {"log_calls": False}
        return stamp

    monkeypatch.setattr(harness["service"], "_write_admission_stamp", stored_settings)
    result = _run(harness["service"].sync_call(TENANT, CALL))
    # Salesforce consumes these controls. HubSpot's existing apply_config is
    # intentionally a no-op; an ignored legacy field cannot disable that CRM.
    assert result.success is (provider == "hubspot")

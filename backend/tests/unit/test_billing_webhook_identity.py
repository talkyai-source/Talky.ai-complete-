from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.domain.services.billing_webhooks import event_identity
from app.domain.services.billing_webhook_notifications import preflight


def event():
    return {
        "id": "evt_synthetic",
        "type": "invoice.paid",
        "livemode": False,
        "created": 1700000000,
        "pending_webhooks": 2,
        "data": {"object": {"id": "in_synthetic", "status": "paid"}},
    }


def test_same_receipt_has_stable_identity_across_mutable_delivery_count_and_json_order():
    original = event()
    retry = dict(reversed(list(deepcopy(original).items())))
    retry["pending_webhooks"] = 0
    assert event_identity(original) == event_identity(retry)


@pytest.mark.parametrize(
    "key,value",
    [
        ("livemode", True),
        ("type", "invoice.payment_failed"),
        ("data", {"object": {"id": "in_other"}}),
        ("account", "acct_other"),
    ],
)
def test_same_event_id_with_changed_immutable_evidence_has_different_hash(key, value):
    original = event()
    changed = {**original, key: value}
    assert event_identity(original)[3] != event_identity(changed)[3]


@pytest.mark.parametrize(
    "changes",
    [
        {"id": None},
        {"id": ""},
        {"id": "not_a_stripe_event"},
        {"livemode": "false"},
        {"type": ""},
        {"data": {}},
        {"data": {"object": []}},
    ],
)
def test_missing_or_malformed_event_identity_is_rejected(changes):
    with pytest.raises(ValueError):
        event_identity({**event(), **changes})


def test_disabled_delivery_and_missing_credentials_are_known_before_any_send():
    service = SimpleNamespace(email_enabled=False)
    assert preflight(service) == "email_disabled"
    service.email_enabled = True
    service.email_provider = "smtp"
    service.smtp_host = service.smtp_user = service.smtp_password = ""
    assert preflight(service) == "smtp_not_configured"
    service.smtp_host, service.smtp_user, service.smtp_password = (
        "localhost",
        "synthetic",
        "synthetic",
    )
    assert preflight(service) is None


def test_unknown_provider_is_not_reported_as_delivery():
    assert (
        preflight(SimpleNamespace(email_enabled=True, email_provider="absent"))
        == "unknown_email_provider"
    )

"""D2-6: the lenient phone path is gone, not just unused.

The relaxed-account bypass was removed earlier, but the helpers it ran on
stayed importable: normalize_phone_number_lenient (keeps bare digits),
normalize_phone_number_legacy (keeps 3-6 digit "extensions", builds
"+<digits>" for anything else) and the deprecated contacts.normalize_phone_number
that wrapped it. Any new caller would have reopened the hole. They are deleted;
every intake path uses the strict normaliser.
"""
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import campaigns, contacts
from app.domain.services import phone_number_normalizer


@pytest.mark.parametrize("module,name", [
    (phone_number_normalizer, "normalize_phone_number_lenient"),
    (phone_number_normalizer, "normalize_phone_number_legacy"),
    (contacts, "normalize_phone_number"),
    (contacts, "_RELAXED_PHONE_EMAILS"),
    (campaigns, "normalize_phone_number_lenient"),
    (campaigns, "_RELAXED_PHONE_EMAILS"),
])
def test_no_lenient_phone_helper_is_left_to_call(module, name):
    assert not hasattr(module, name)


@pytest.mark.parametrize("raw", ["123456", "12345", "07700 900123", "+07700900123"])
def test_the_intake_normaliser_refuses_what_the_lenient_path_kept(raw):
    with pytest.raises(ValueError):
        contacts._normalize_for_user(raw, SimpleNamespace(email="owner@example.com"), "GB")

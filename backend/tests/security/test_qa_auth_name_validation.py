"""QA-SEC-043: bounded account names cannot contain control characters."""
import pytest
from pydantic import ValidationError

from app.api.v1.endpoints.auth.schemas import SignupStartRequest, UpdateMeRequest


@pytest.mark.parametrize("field", ["name", "business_name"])
@pytest.mark.parametrize("value", ["A" * 101, "A\nB", "A\x00B", "A\x7fB", "A\x85B"])
def test_signup_rejects_oversized_or_control_character_names(field, value):
    values = {"email": "person@example.com", "name": "Owner", "business_name": "Company"}
    values[field] = value
    with pytest.raises(ValidationError):
        SignupStartRequest(**values)


def test_normal_unicode_names_and_profile_updates_are_supported():
    body = SignupStartRequest(email="person@example.com", name="Zoë O'Connor", business_name="Café & Co")
    assert body.name == "Zoë O'Connor"
    assert UpdateMeRequest(name=None, business_name="Café & Co").business_name == "Café & Co"


def test_profile_update_cannot_bypass_name_bounds():
    with pytest.raises(ValidationError):
        UpdateMeRequest(name="A" * 101)


@pytest.mark.parametrize("field", ["name", "business_name"])
@pytest.mark.parametrize("value", ["", "   ", "\u00a0\t "])
def test_signup_rejects_blank_names(field, value):
    values = {"email": "person@example.com", "name": "Owner", "business_name": "Company"}
    values[field] = value
    with pytest.raises(ValidationError):
        SignupStartRequest(**values)


def test_invite_accept_rejects_blank_name():
    from app.api.v1.endpoints.auth.schemas import InviteAcceptRequest

    with pytest.raises(ValidationError):
        InviteAcceptRequest(token="t" * 24, name="   ", password="x", confirm_password="x")


def test_profile_update_still_accepts_blank_so_a_name_can_be_cleared():
    assert UpdateMeRequest(name="").name == ""
    assert UpdateMeRequest(business_name="  ").business_name == "  "

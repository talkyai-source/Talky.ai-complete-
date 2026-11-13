"""D2-4: an empty allowed_days list no longer means "never call".

The schedule validator kept ``[]``, the create/update path stored it, and the
dialer's overlay then allowed no day at all: the campaign could never dial
and nothing said why. ``[]`` is now refused at save, and a ``[]`` already
stored is treated as unset (the account's days apply).
"""
import pytest
from pydantic import ValidationError

from app.api.v1.schemas.campaigns import CampaignCallingSchedule
from app.domain.models.calling_rules import CallingRules
from app.domain.services.dialer.campaign_schedule import effective_rules


def test_an_empty_day_list_is_refused_at_save():
    with pytest.raises(ValidationError) as refused:
        CampaignCallingSchedule(timezone="Europe/London", allowed_days=[])
    assert "at least one" in str(refused.value).lower()


@pytest.mark.parametrize("days,expected", [(None, None), ([4, 0, 0], [0, 4])])
def test_unset_and_real_day_lists_still_save(days, expected):
    assert CampaignCallingSchedule(timezone="Europe/London", allowed_days=days).allowed_days == expected


def test_a_stored_empty_day_list_falls_back_to_the_accounts_days():
    tenant = CallingRules(allowed_days=[0, 1, 2, 3, 4])
    overlaid = effective_rules(tenant, {"allowed_days": [], "timezone": "Europe/London"})
    assert overlaid.allowed_days == [0, 1, 2, 3, 4]
    assert overlaid.timezone == "Europe/London", "the other saved keys still apply"


def test_a_stored_real_day_list_still_overrides_the_account():
    tenant = CallingRules(allowed_days=[0, 1, 2, 3, 4])
    assert effective_rules(tenant, {"allowed_days": [5, 6]}).allowed_days == [5, 6]

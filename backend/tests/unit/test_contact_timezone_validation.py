"""Forms audit B-F9: a contact timezone must be a real IANA zone (empty allowed)."""
import pytest
from pydantic import ValidationError

from app.api.v1.schemas.campaigns import ContactCreate, ContactUpdate


@pytest.mark.parametrize("model,base", [
    (ContactCreate, {"phone_number": "+442079460000"}),
    (ContactUpdate, {}),
])
class TestContactTimezone:
    @pytest.mark.parametrize("bad", ["Eastern", "EST5EDT!", "Mars/Olympus", "not a zone"])
    def test_rejects_non_iana_value(self, model, base, bad):
        with pytest.raises(ValidationError) as exc:
            model(**base, timezone=bad)
        assert "timezone" in str(exc.value)

    @pytest.mark.parametrize("good", ["Europe/London", "America/New_York"])
    def test_accepts_real_zone(self, model, base, good):
        assert model(**base, timezone=good).timezone == good

    @pytest.mark.parametrize("empty", [None, "", "  "])
    def test_empty_stays_allowed(self, model, base, empty):
        model(**base, timezone=empty)

    def test_omitted_stays_allowed(self, model, base):
        assert model(**base).timezone is None

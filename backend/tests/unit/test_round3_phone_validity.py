"""QA F2-14/F14-03/F3-13: a destination is validated, never guessed."""
import pytest

from app.domain.services.phone_number_normalizer import normalize_phone_number


@pytest.mark.parametrize("raw", ["07700 900123", "020 7946 0958", "001 4155552671", "12345"])
def test_us_default_rejects_invalid_national_destinations(raw):
    with pytest.raises(ValueError, match="(?i)invalid|valid"):
        normalize_phone_number(raw, default_country="US")


@pytest.mark.parametrize("raw", ["07700 900123", "12345"])
def test_gb_default_does_not_accept_library_invalid_examples(raw):
    with pytest.raises(ValueError, match="(?i)invalid|valid"):
        normalize_phone_number(raw, default_country="GB")


@pytest.mark.parametrize("raw, country, expected", [
    ("020 7946 0958", "GB", "+442079460958"),
    ("001 4155552671", "GB", "+14155552671"),
    ("4155552671", "US", "+14155552671"),
    ("+1 415 555 2671;ext=12", "GB", "+14155552671"),
])
def test_valid_destinations_keep_the_country_and_exclude_extensions(raw, country, expected):
    assert normalize_phone_number(raw, default_country=country) == expected


def test_unknown_country_cannot_silently_fall_back_to_us():
    with pytest.raises(ValueError, match="(?i)country|region"):
        normalize_phone_number("4155552671", default_country="XX")


def test_campaign_then_account_country_and_no_us_guess():
    from app.domain.services.phone_number_normalizer import campaign_country

    assert campaign_country({}, {"default_country": "gb"}) == "GB"
    assert campaign_country({"script_config": {"default_country_code": "DE"}}, {"default_country": "GB"}) == "DE"
    # D2-5: intake never guesses US; only a caller that names it gets it.
    assert campaign_country({}, {}) is None
    assert campaign_country({}, {}, fallback="US") == "US"


def test_dnc_and_guard_share_extension_free_key():
    from app.domain.services.dnc_service import normalize_e164_for_storage
    from app.domain.services.call_guard import CallGuard

    stored = normalize_e164_for_storage("020 7946 0958;ext=12", default_country="GB")
    assert stored == "+442079460958"
    assert CallGuard._normalize_phone_number(None, "+44 20 7946 0958;ext=12") == stored


def test_no_account_email_bypasses_phone_validation():
    from types import SimpleNamespace
    from app.api.v1.endpoints.contacts import _normalize_for_user

    with pytest.raises(ValueError):
        _normalize_for_user("12345", SimpleNamespace(email="uzairdevelops@gmail.com"))


def test_pasted_extension_stays_with_its_number():
    from app.domain.services.dialer.bulk_ingest import parse_pasted_numbers

    assert parse_pasted_numbers("+14155552671;ext=12;+442079460958") == [
        "+14155552671;ext=12", "+442079460958",
    ]


@pytest.mark.asyncio
async def test_import_invalid_phone_returns_422_before_any_write():
    from io import BytesIO
    from starlette.datastructures import UploadFile
    from fastapi import HTTPException
    from types import SimpleNamespace
    from tests.unit.test_contact_import_validation import FakeDB
    from app.api.v1.endpoints.contacts import upload_campaign_contacts

    db = FakeDB()
    with pytest.raises(HTTPException) as caught:
        await upload_campaign_contacts(
            "c1", file=UploadFile(filename="contacts.csv", file=BytesIO(b"phone_number\n+14155552671\n12345\n")),
            current_user=SimpleNamespace(id="u1", tenant_id="t1", email="test@example.invalid"),
            db_client=db,
        )
    assert caught.value.status_code == 422
    assert caught.value.detail[0]["row"] == 3
    assert caught.value.detail[0]["field"] == "phone_number"
    assert db.tables["leads"] == []
    assert db.tables["contact_lists"] == []


@pytest.mark.asyncio
async def test_manual_contact_uses_account_country_and_explicit_import_override():
    from types import SimpleNamespace
    from tests.unit.test_contact_import_validation import FakeDB
    from app.api.v1.endpoints.campaigns import add_contact_to_campaign
    from app.api.v1.schemas.campaigns import ContactCreate

    db = FakeDB()
    db.tables["tenants"] = [{"id": "t1", "calling_rules": {"default_country": "GB"}}]
    user = SimpleNamespace(id="u1", tenant_id="t1", email="test@example.invalid")
    await add_contact_to_campaign("c1", ContactCreate(phone_number="020 7946 0958"), user, db)
    assert db.tables["leads"][0]["phone_number"] == "+442079460958"
    await add_contact_to_campaign("c1", ContactCreate(phone_number="4155552671", default_country="US"), user, db)
    assert db.tables["leads"][1]["phone_number"] == "+14155552671"


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_country", ["GB", None])
async def test_gb_dnc_write_blocks_canonical_lead_at_guard(monkeypatch, requested_country):
    from tests.unit.test_dnc_service import _FakePool, _FakeConn, TENANT_1
    from app.domain.services.dnc_service import DNCService
    from app.domain.services.call_guard import CallGuard

    pool = _FakePool()
    async def account_country(self, sql, *args):
        assert "WHERE id = $1::uuid" in sql and args == (TENANT_1,)
        return {"default_country": "GB"}

    monkeypatch.setattr(_FakeConn, "fetchval", account_country, raising=False)
    await DNCService(pool).add(tenant_id=TENANT_1, e164="020 7946 0958;ext=12",
                               default_country=requested_country, source="manual_admin")
    original = _FakeConn.fetchrow

    async def read(self, sql, *args):
        if "SELECT id, source, reason" in sql:
            assert "tenant_id = $1" in sql and args == (TENANT_1, "+442079460958")
            return next(iter(self.store["by_id"].values()))
        return await original(self, sql, *args)

    monkeypatch.setattr(_FakeConn, "fetchrow", read)
    guard = CallGuard.__new__(CallGuard)
    guard._db_pool = pool
    result = await guard._check_dnc(TENANT_1, guard._normalize_phone_number("+442079460958"))
    assert result.passed is False
    assert result.reason.startswith("number_on_dnc_list")

"""Unit tests for the Phase-3a shared bulk lead-ingest core."""
import pytest

from app.domain.services.dialer.bulk_ingest import (
    parse_pasted_numbers,
    ingest_lead_records,
    LeadRecord,
)


# ── parse_pasted_numbers ──────────────────────────────────────────
def test_parse_splits_on_newlines_and_commas():
    text = "+14155551234\n4155555678, 4155559999\n;  4155550000"
    assert parse_pasted_numbers(text) == [
        "+14155551234", "4155555678", "4155559999", "4155550000",
    ]


def test_parse_keeps_intra_number_spaces():
    # A spaced number must stay one token (normalizer fixes it later).
    assert parse_pasted_numbers("+1 415 555 1234") == ["+1 415 555 1234"]


def test_parse_empty_returns_empty():
    assert parse_pasted_numbers("") == []
    assert parse_pasted_numbers("\n , ; \t") == []


# ── ingest core: fakes ────────────────────────────────────────────
class _FakeResult:
    def __init__(self, data, error=None):
        self.data = data
        self.error = error


class _SelectChain:
    def __init__(self, rows, error=None):
        self._rows = rows
        self._error = error
    def select(self, *_a, **_k):
        return self
    def eq(self, *_a, **_k):
        return self
    def execute(self):
        return _FakeResult(self._rows, error=self._error)


class _InsertChain:
    def __init__(self, sink):
        self._sink = sink
    def insert(self, chunk):
        self._sink.extend(chunk)
        return self
    def update(self, vals):
        self._sink.append(("update", vals))
        return self
    def eq(self, *_a, **_k):
        return self
    def execute(self):
        return _FakeResult([])


class _FakeDB:
    def __init__(
        self,
        existing_rows,
        *,
        read_error=None,
        write_error=None,
        zero_write=False,
    ):
        self._existing = existing_rows
        self.read_error = read_error
        self.write_error = write_error
        self.zero_write = zero_write
        self.inserted: list = []
        self.updates: list = []
    def table(self, name):
        # The first call in ingest is the existing-phones SELECT; writes
        # come later. Distinguish by returning a chain that supports both.
        return _Chain(self)


class _Chain:
    """Supports both the select-existing read and insert/update writes."""
    def __init__(self, db):
        self._db = db
        self._operation = None
        self._payload = None
    def select(self, *_a, **_k):
        return _SelectChain(self._db._existing, error=self._db.read_error)
    def insert(self, chunk):
        self._operation = "insert"
        self._payload = chunk
        self._db.inserted.extend(chunk)
        return self
    def update(self, vals):
        self._operation = "update"
        self._payload = vals
        self._db.updates.append(vals)
        return self
    def eq(self, *_a, **_k):
        return self
    def execute(self):
        if self._db.write_error:
            return _FakeResult([], error=self._db.write_error)
        if self._db.zero_write:
            return _FakeResult([])
        if self._operation == "insert":
            rows = self._payload if isinstance(self._payload, list) else [self._payload]
            return _FakeResult(rows)
        if self._operation == "update":
            return _FakeResult([self._payload])
        return _FakeResult([])


def _id_normalize(p: str) -> str:
    """Trivial normalizer: strip non-digits, require >= 10 digits."""
    digits = "".join(c for c in p if c.isdigit())
    if len(digits) < 10:
        raise ValueError("too short")
    return "+" + digits


def test_ingest_inserts_new_dedups_and_flags_invalid():
    db = _FakeDB(existing_rows=[])
    records = [
        LeadRecord("+1 415 555 1234", source_row=1),     # -> +14155551234
        LeadRecord("+1 (415) 555-1234", source_row=2),   # dup of #1 after normalize
        LeadRecord("123", source_row=3),                 # invalid (too short)
        LeadRecord("+1 415 555 9999", source_row=4),     # -> +14155559999
    ]
    res = ingest_lead_records(
        db, campaign_id="c1", tenant_id="t1",
        records=records, normalize=_id_normalize,
    )
    assert res.total == 4
    assert res.imported == 2          # #1 and #4
    assert res.duplicates_skipped == 1  # #2
    assert res.invalid == 1           # #3
    assert len(db.inserted) == 2
    phones = {r["phone_number"] for r in db.inserted}
    assert phones == {"+14155551234", "+14155559999"}
    # tenant + campaign stamped, pending status.
    assert all(r["tenant_id"] == "t1" and r["campaign_id"] == "c1" for r in db.inserted)
    assert all(r["status"] == "pending" for r in db.inserted)


def test_ingest_skips_existing_live_phone():
    db = _FakeDB(existing_rows=[
        {"id": "L1", "phone_number": "+14155551234", "status": "pending", "is_lead": False},
    ])
    res = ingest_lead_records(
        db, campaign_id="c1", tenant_id="t1",
        records=[LeadRecord("+1 415 555 1234", source_row=1)],
        normalize=_id_normalize,
    )
    assert res.imported == 0
    assert res.duplicates_skipped == 1
    assert db.inserted == []


def test_ingest_revives_soft_deleted():
    db = _FakeDB(existing_rows=[
        {"id": "DEL1", "phone_number": "+14155551234", "status": "deleted", "is_lead": True},
    ])
    res = ingest_lead_records(
        db, campaign_id="c1", tenant_id="t1",
        records=[LeadRecord("+1 415 555 1234", first_name="Jo", source_row=1)],
        normalize=_id_normalize,
    )
    assert res.revived == 1
    assert res.imported == 1
    assert db.inserted == []           # revived in place, not inserted
    assert any(u.get("status") == "pending" for u in db.updates)


@pytest.mark.parametrize("zero_write", [False, True])
def test_ingest_never_counts_failed_or_zero_row_insert_as_imported(zero_write):
    db = _FakeDB(
        existing_rows=[],
        write_error=None if zero_write else "direction guard rejected insert",
        zero_write=zero_write,
    )

    result = ingest_lead_records(
        db,
        campaign_id="c1",
        tenant_id="t1",
        records=[LeadRecord("+1 415 555 1234", source_row=1)],
        normalize=_id_normalize,
    )

    assert result.imported == 0
    assert len(result.errors) == 1


def test_ingest_never_counts_zero_row_revive_as_imported():
    db = _FakeDB(
        existing_rows=[
            {
                "id": "DEL1",
                "phone_number": "+14155551234",
                "status": "deleted",
                "is_lead": True,
            }
        ],
        zero_write=True,
    )

    result = ingest_lead_records(
        db,
        campaign_id="c1",
        tenant_id="t1",
        records=[LeadRecord("+1 415 555 1234", source_row=1)],
        normalize=_id_normalize,
    )

    assert result.imported == 0
    assert result.revived == 0
    assert len(result.errors) == 1


def test_ingest_fails_closed_when_existing_lead_lookup_failed():
    db = _FakeDB(existing_rows=[], read_error="database unavailable")

    with pytest.raises(RuntimeError, match="existing lead lookup failed"):
        ingest_lead_records(
            db,
            campaign_id="c1",
            tenant_id="t1",
            records=[LeadRecord("+1 415 555 1234", source_row=1)],
            normalize=_id_normalize,
        )

    assert db.inserted == []


# ── company column ────────────────────────────────────────────────
def test_ingest_stores_company_in_custom_fields():
    """A company on the LeadRecord lands in custom_fields.company (no new
    column) and does not disturb existing custom_fields."""
    db = _FakeDB(existing_rows=[])
    res = ingest_lead_records(
        db, campaign_id="c1", tenant_id="t1",
        records=[LeadRecord(
            "+1 415 555 1234", first_name="Jo", company="Acme Roofing",
            custom_fields={"note": "vip"}, source_row=1,
        )],
        normalize=_id_normalize,
    )
    assert res.imported == 1
    assert len(db.inserted) == 1
    cf = db.inserted[0]["custom_fields"]
    assert cf["company"] == "Acme Roofing"
    assert cf["note"] == "vip"          # pre-existing custom field preserved


def test_ingest_company_absent_leaves_custom_fields_clean():
    """No company → no 'company' key injected (byte-for-byte prior behaviour)."""
    db = _FakeDB(existing_rows=[])
    ingest_lead_records(
        db, campaign_id="c1", tenant_id="t1",
        records=[LeadRecord("+1 415 555 1234", first_name="Jo", source_row=1)],
        normalize=_id_normalize,
    )
    assert db.inserted[0]["custom_fields"] == {}


def test_ingest_company_flows_through_revive():
    db = _FakeDB(existing_rows=[
        {"id": "DEL1", "phone_number": "+14155551234", "status": "deleted", "is_lead": True},
    ])
    ingest_lead_records(
        db, campaign_id="c1", tenant_id="t1",
        records=[LeadRecord("+1 415 555 1234", first_name="Jo", company="Beta LLC", source_row=1)],
        normalize=_id_normalize,
    )
    assert any(
        u.get("custom_fields", {}).get("company") == "Beta LLC" for u in db.updates
    )


def test_ingest_tolerates_unknown_extra_column():
    """An unknown 5th (or Nth) column carried in custom_fields must not break
    ingest — the survivor is still inserted with its extra data intact."""
    db = _FakeDB(existing_rows=[])
    res = ingest_lead_records(
        db, campaign_id="c1", tenant_id="t1",
        records=[LeadRecord(
            "+1 415 555 1234", first_name="Jo",
            custom_fields={"favorite_color": "blue"}, source_row=1,
        )],
        normalize=_id_normalize,
    )
    assert res.imported == 1
    assert res.invalid == 0
    assert db.inserted[0]["custom_fields"]["favorite_color"] == "blue"

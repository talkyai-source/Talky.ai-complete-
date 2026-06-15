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
    def __init__(self, data):
        self.data = data


class _SelectChain:
    def __init__(self, rows):
        self._rows = rows
    def select(self, *_a, **_k):
        return self
    def eq(self, *_a, **_k):
        return self
    def execute(self):
        return _FakeResult(self._rows)


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
    def __init__(self, existing_rows):
        self._existing = existing_rows
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
    def select(self, *_a, **_k):
        return _SelectChain(self._db._existing)
    def insert(self, chunk):
        self._db.inserted.extend(chunk)
        return self
    def update(self, vals):
        self._db.updates.append(vals)
        return self
    def eq(self, *_a, **_k):
        return self
    def execute(self):
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

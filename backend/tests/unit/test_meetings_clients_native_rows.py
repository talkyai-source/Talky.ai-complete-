"""QA F2-08: the meetings and clients routes answer 200 for rows as asyncpg returns them.

asyncpg hands back ``uuid.UUID`` for uuid columns, timezone-aware ``datetime`` for
timestamptz columns and decoded lists for jsonb columns. The response models
declared ``id`` / ``start_time`` / ``end_time`` / ``created_at`` as ``str``, so
every real row failed response validation: a 500 from GET /meetings/ and
GET /meetings/{id}, a 400 from POST /meetings/ after the calendar event and row
already existed, and 500s from /clients/ list, get and create (create answered
500 *after* the INSERT had committed).

The web client (Talk-Leee/src/lib/models.ts: ``ListResponseSchema(MeetingResponseSchema)``)
reads ``{"items": [{id, title, participants: string[], start_time, end_time, status}]}``.
The tests drive the real MeetingService and the real endpoints over an in-memory
database whose rows keep the driver's native types.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from asyncpg.pgproto.pgproto import UUID as PgUUID  # the class asyncpg returns for uuid columns
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.dependencies import CurrentUser, get_current_user, get_db_client
from app.api.v1.endpoints import clients, meetings
from app.services.meeting_service import MeetingService

TENANT = "11111111-1111-4111-8111-111111111111"
OTHER_TENANT = "22222222-2222-4222-8222-222222222222"
MEETING_ID = PgUUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OTHER_MEETING_ID = PgUUID("aaaaaaaa-aaaa-4aaa-8aaa-bbbbbbbbbbbb")
LEAD_ID = PgUUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
CLIENT_ID = PgUUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
OTHER_CLIENT_ID = PgUUID("cccccccc-cccc-4ccc-8ccc-dddddddddddd")
START = datetime(2030, 1, 1, 10, 0, tzinfo=timezone.utc)
END = datetime(2030, 1, 1, 10, 30, tzinfo=timezone.utc)
CREATED = datetime(2029, 12, 1, 9, 0, 5, tzinfo=timezone.utc)


def _new_id() -> PgUUID:
    return PgUUID(str(uuid.uuid4()))


class _Query:
    """Chainable stand-in for the adapter's QueryBuilder over in-memory rows.

    Row values keep the types asyncpg returns. Filters compare by text so a str
    parameter matches a UUID column, as the real query's ``::uuid`` cast does.
    """

    def __init__(self, db: "_FakeDB", table: str) -> None:
        self.db, self.table, self.filters = db, table, []
        self.kind, self.payload, self.one = "select", None, False

    def select(self, *_args, **_kwargs):
        self.kind = "select"
        return self

    def insert(self, data):
        self.kind, self.payload = "insert", data
        return self

    def delete(self):
        self.kind = "delete"
        return self

    def eq(self, column, value):
        self.filters.append((column, str(value)))
        return self

    def gte(self, *_args):
        return self

    def lte(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def single(self):
        self.one = True
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.kind == "insert":
            stored = {**self.payload, "id": _new_id(), "tenant_id": PgUUID(self.payload["tenant_id"]),
                      "created_at": CREATED, "updated_at": CREATED}
            rows.append(stored)
            return SimpleNamespace(data=[stored], error=None)
        hit = [row for row in rows if all(str(row.get(col)) == val for col, val in self.filters)]
        if self.kind == "delete":
            for row in hit:
                rows.remove(row)
            return SimpleNamespace(data=hit, error=None)
        return SimpleNamespace(data=(hit[0] if hit else None) if self.one else hit, error=None)


class _FakeDB:
    def __init__(self, **tables) -> None:
        self.tables = {name: list(rows) for name, rows in tables.items()}
        self.queries: list[_Query] = []

    def table(self, name: str) -> _Query:
        query = _Query(self, name)
        self.queries.append(query)
        return query


def _meeting_row(**overrides):
    """A meetings row as the adapter returns it (every column of the table)."""
    row = {
        "id": MEETING_ID, "tenant_id": PgUUID(TENANT), "lead_id": LEAD_ID, "call_id": None,
        "connector_id": _new_id(), "action_id": _new_id(), "external_event_id": "evt_1",
        "title": "Demo call", "description": "Walk through the product",
        "start_time": START, "end_time": END, "timezone": "Europe/London", "location": None,
        "join_link": "https://meet.example.com/abc",
        "status": "scheduled",
        "attendees": [{"email": "buyer@example.com", "status": "pending"},
                      {"email": "ops@example.com", "status": "pending"}],
        "metadata": {"provider": "google_calendar"},
        "created_at": CREATED, "updated_at": CREATED,
    }
    row.update(overrides)
    return row


def _client_row(**overrides):
    row = {
        "id": CLIENT_ID, "tenant_id": PgUUID(TENANT), "name": "Acme Ltd", "company": "Acme",
        "phone": "+14155550100", "email": "buyer@example.com", "tags": ["vip", "uk"],
        "notes": None, "created_at": CREATED, "updated_at": CREATED,
    }
    row.update(overrides)
    return row


def _app(router, db: _FakeDB, role: str = "tenant_admin") -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="u-1", email="u@example.com", tenant_id=TENANT, role=role)
    app.dependency_overrides[get_db_client] = lambda: db
    return app


def _meetings_client(monkeypatch, db: _FakeDB) -> TestClient:
    service = object.__new__(MeetingService)  # skips the connector-encryption bootstrap
    service.db_client = db
    monkeypatch.setattr(meetings, "get_meeting_service", lambda _db: service)
    return TestClient(_app(meetings.router, db))


def _clients_client(db: _FakeDB) -> TestClient:
    return TestClient(_app(clients.router, db), raise_server_exceptions=False)


def _web_accepts_meeting(item: dict) -> None:
    """Mirror of models.ts MeetingResponseSchema (the snake_case branch the backend feeds)."""
    for key in ("id", "title", "start_time", "end_time", "status"):
        assert isinstance(item[key], str), f"web schema needs {key} as a string, got {item.get(key)!r}"
    assert isinstance(item["participants"], list)
    assert all(isinstance(p, str) for p in item["participants"]), item["participants"]


# --------------------------------------------------------------------------- meetings

def test_list_meetings_returns_the_items_envelope_the_web_client_parses(monkeypatch):
    db = _FakeDB(meetings=[_meeting_row()])
    response = _meetings_client(monkeypatch, db).get("/meetings/")
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == ["items"]
    (item,) = body["items"]
    _web_accepts_meeting(item)
    assert item["id"] == str(MEETING_ID)
    assert item["title"] == "Demo call"
    assert item["start_time"] == "2030-01-01T10:00:00+00:00"
    assert item["end_time"] == "2030-01-01T10:30:00+00:00"
    assert item["created_at"] == "2029-12-01T09:00:05+00:00"
    assert item["lead_id"] == str(LEAD_ID)
    assert item["timezone"] == "Europe/London"
    assert item["join_link"] == "https://meet.example.com/abc"
    assert item["status"] == "scheduled"
    assert item["participants"] == ["buyer@example.com", "ops@example.com"]
    assert item["attendees"] == [{"email": "buyer@example.com", "status": "pending"},
                                 {"email": "ops@example.com", "status": "pending"}]


def test_list_meetings_with_no_rows_is_an_empty_items_envelope(monkeypatch):
    response = _meetings_client(monkeypatch, _FakeDB(meetings=[])).get("/meetings/")
    assert response.status_code == 200, response.text
    assert response.json() == {"items": []}


def test_list_meetings_only_returns_the_callers_tenant(monkeypatch):
    theirs = _meeting_row(id=OTHER_MEETING_ID, tenant_id=PgUUID(OTHER_TENANT), title="Other tenant")
    db = _FakeDB(meetings=[theirs, _meeting_row()])
    response = _meetings_client(monkeypatch, db).get("/meetings/")
    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [str(MEETING_ID)]
    assert ("tenant_id", TENANT) in db.queries[-1].filters


def test_get_meeting_returns_one_row_with_text_ids_and_times(monkeypatch):
    db = _FakeDB(meetings=[_meeting_row()])
    response = _meetings_client(monkeypatch, db).get(f"/meetings/{MEETING_ID}")
    assert response.status_code == 200, response.text
    item = response.json()
    _web_accepts_meeting(item)
    assert item["id"] == str(MEETING_ID)
    assert item["start_time"] == "2030-01-01T10:00:00+00:00"
    assert item["end_time"] == "2030-01-01T10:30:00+00:00"
    assert item["created_at"] == "2029-12-01T09:00:05+00:00"
    assert item["lead_id"] == str(LEAD_ID)
    assert item["participants"] == ["buyer@example.com", "ops@example.com"]
    assert ("tenant_id", TENANT) in db.queries[-1].filters


def test_get_meeting_of_another_tenant_is_404(monkeypatch):
    theirs = _meeting_row(tenant_id=PgUUID(OTHER_TENANT))
    response = _meetings_client(monkeypatch, _FakeDB(meetings=[theirs])).get(f"/meetings/{MEETING_ID}")
    assert response.status_code == 404, response.text


def test_meeting_with_null_jsonb_and_optional_columns_still_serialises(monkeypatch):
    row = _meeting_row(attendees=None, timezone=None, description=None, join_link=None, lead_id=None)
    response = _meetings_client(monkeypatch, _FakeDB(meetings=[row])).get("/meetings/")
    assert response.status_code == 200, response.text
    (item,) = response.json()["items"]
    _web_accepts_meeting(item)
    assert item["attendees"] == []
    assert item["participants"] == []
    assert item["timezone"] == "UTC"
    assert item["description"] is None
    assert item["join_link"] is None
    assert item["lead_id"] is None


def test_participants_are_the_attendee_emails_and_skip_entries_without_one(monkeypatch):
    stored = [{"email": "a@example.com", "status": "pending"}, {"email": ""}, {"status": "accepted"},
              {"email": "b@example.com", "status": "accepted"}]
    row = _meeting_row(attendees=stored)
    response = _meetings_client(monkeypatch, _FakeDB(meetings=[row])).get(f"/meetings/{MEETING_ID}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["participants"] == ["a@example.com", "b@example.com"]
    assert body["attendees"] == stored


def test_create_meeting_answers_200_when_the_service_receipt_carries_a_uuid(monkeypatch):
    # MeetingService.create_meeting returns row["id"] from INSERT ... RETURNING: a UUID.
    service = MagicMock()
    service.create_meeting = AsyncMock(return_value={
        "success": True, "status": "created", "meeting_id": MEETING_ID, "action_id": _new_id(),
        "title": "Demo", "start_time": "2030-01-01T10:00:00+00:00", "end_time": "2030-01-01T10:30:00+00:00",
        "join_link": None, "calendar_link": None, "attendees": [], "reminders_created": 0,
    })
    monkeypatch.setattr(meetings, "get_meeting_service", lambda _db: service)
    client = TestClient(_app(meetings.router, _FakeDB()))
    response = client.post("/meetings/", json={"title": "Demo", "start_time": "2030-01-01T10:00:00Z",
                                               "attendees": []})
    assert response.status_code == 200, response.text
    assert response.json()["meeting_id"] == str(MEETING_ID)
    assert response.json()["success"] is True


# --------------------------------------------------------------------------- clients

def test_list_clients_answers_200_for_uuid_rows():
    db = _FakeDB(clients=[_client_row()])
    response = _clients_client(db).get("/clients/")
    assert response.status_code == 200, response.text
    assert response.json() == [{"id": str(CLIENT_ID), "name": "Acme Ltd", "company": "Acme",
                                "phone": "+14155550100", "email": "buyer@example.com", "tags": ["vip", "uk"]}]
    assert ("tenant_id", TENANT) in db.queries[-1].filters


def test_list_clients_only_returns_the_callers_tenant():
    theirs = _client_row(id=OTHER_CLIENT_ID, tenant_id=PgUUID(OTHER_TENANT), name="Other tenant")
    response = _clients_client(_FakeDB(clients=[theirs, _client_row()])).get("/clients/")
    assert response.status_code == 200, response.text
    assert [c["id"] for c in response.json()] == [str(CLIENT_ID)]


def test_get_client_answers_200_for_a_uuid_row():
    db = _FakeDB(clients=[_client_row()])
    response = _clients_client(db).get(f"/clients/{CLIENT_ID}")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == str(CLIENT_ID)
    assert response.json()["tags"] == ["vip", "uk"]
    assert ("tenant_id", TENANT) in db.queries[-1].filters


def test_get_client_of_another_tenant_is_404():
    theirs = _client_row(tenant_id=PgUUID(OTHER_TENANT))
    response = _clients_client(_FakeDB(clients=[theirs])).get(f"/clients/{CLIENT_ID}")
    assert response.status_code == 404, response.text


def test_client_with_null_tags_and_optional_columns_serialises():
    row = _client_row(tags=None, company=None, phone=None, email=None)
    response = _clients_client(_FakeDB(clients=[row])).get(f"/clients/{CLIENT_ID}")
    assert response.status_code == 200, response.text
    assert response.json() == {"id": str(CLIENT_ID), "name": "Acme Ltd", "company": None,
                               "phone": None, "email": None, "tags": []}


def test_create_client_answers_200_with_the_generated_uuid_as_text():
    db = _FakeDB(clients=[])
    response = _clients_client(db).post("/clients/", json={"name": "New Co", "email": "new@example.com",
                                                          "tags": ["lead"]})
    assert response.status_code == 200, response.text
    (stored,) = db.tables["clients"]
    assert response.json() == {"id": str(stored["id"]), "name": "New Co", "company": None, "phone": None,
                               "email": "new@example.com", "tags": ["lead"]}
    assert str(stored["tenant_id"]) == TENANT


def test_delete_client_removes_only_the_callers_row():
    db = _FakeDB(clients=[_client_row(), _client_row(id=OTHER_CLIENT_ID, tenant_id=PgUUID(OTHER_TENANT))])
    response = _clients_client(db).delete(f"/clients/{CLIENT_ID}")
    assert response.status_code == 200, response.text
    assert [row["id"] for row in db.tables["clients"]] == [OTHER_CLIENT_ID]
    assert _clients_client(db).delete(f"/clients/{OTHER_CLIENT_ID}").status_code == 404


@pytest.mark.parametrize("model,kwargs", [
    (meetings.MeetingResponse, dict(id="meeting-123", title="t", description=None, start_time="2026-01-08T10:00:00",
                                    end_time="2026-01-08T10:30:00", timezone="UTC", join_link=None,
                                    status="scheduled", attendees=[], lead_id=None, created_at="2026-01-07T12:00:00")),
    (clients.ClientResponse, dict(id="client-1", name="Acme")),
])
def test_response_models_still_accept_plain_strings(model, kwargs):
    assert model(**kwargs).id in {"meeting-123", "client-1"}

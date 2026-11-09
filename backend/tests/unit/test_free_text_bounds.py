"""QA F2-13 (rest): the free-text fields Codex left open are bounded and storable.

Client name/company/notes/tags, meeting title/description/timezone/attendees/
cancellation reason and the blocked-entity fields had no length limit, so a
long value reached PostgreSQL (VARCHAR(255) columns error with a 500) or was
stored without bound, and a NUL or lone surrogate cannot be stored in a text
column at all. They now fail validation with a 422 before any write.
"""
import pytest
from pydantic import ValidationError

from app.api.v1.endpoints.blocked_entities import CreateBlockedEntityRequest
from app.api.v1.endpoints.clients import ClientCreate
from app.api.v1.endpoints.meetings import (
    CancelMeetingRequest,
    CreateMeetingRequest,
    UpdateMeetingRequest,
)

MEETING = {"title": "Intro", "start_time": "2026-11-10T10:00:00Z"}
BLOCK = {"entity_type": "phone_number", "value": "+442079460958"}

TOO_LONG = [
    (ClientCreate, {}, "name", "x" * 256),
    (ClientCreate, {"name": "Acme"}, "company", "x" * 256),
    (ClientCreate, {"name": "Acme"}, "notes", "x" * 10_001),
    (ClientCreate, {"name": "Acme"}, "phone", "1" * 33),
    (ClientCreate, {"name": "Acme"}, "tags", ["t"] * 51),
    (ClientCreate, {"name": "Acme"}, "tags", ["t" * 65]),
    (CreateMeetingRequest, {"start_time": MEETING["start_time"]}, "title", "x" * 256),
    (CreateMeetingRequest, MEETING, "description", "x" * 10_001),
    (CreateMeetingRequest, MEETING, "timezone", "x" * 51),
    (CreateMeetingRequest, MEETING, "attendees", [f"p{i}@example.com" for i in range(51)]),
    (UpdateMeetingRequest, {}, "title", "x" * 256),
    (UpdateMeetingRequest, {}, "description", "x" * 10_001),
    (CancelMeetingRequest, {}, "reason", "x" * 1_001),
    (CreateBlockedEntityRequest, {"value": BLOCK["value"]}, "entity_type", "x" * 33),
    (CreateBlockedEntityRequest, {"entity_type": "phone_number"}, "value", "1" * 65),
    (CreateBlockedEntityRequest, BLOCK, "reason", "x" * 1_001),
]


@pytest.mark.parametrize("model,base,field,value", TOO_LONG,
                         ids=[f"{m.__name__}.{f}-{len(v)}" for m, _, f, v in TOO_LONG])
def test_over_long_free_text_is_refused(model, base, field, value):
    with pytest.raises(ValidationError):
        model(**{**base, field: value})


@pytest.mark.parametrize("model,base,field", [
    (ClientCreate, {}, "name"),
    (ClientCreate, {"name": "Acme"}, "notes"),
    (CreateMeetingRequest, {"start_time": MEETING["start_time"]}, "title"),
    (CancelMeetingRequest, {}, "reason"),
    (CreateBlockedEntityRequest, BLOCK, "reason"),
])
@pytest.mark.parametrize("bad", ["a\x00b", "a\ud800b", "a\x07b"])
def test_nul_lone_surrogate_and_control_characters_are_refused(model, base, field, bad):
    with pytest.raises(ValidationError):
        model(**{**base, field: bad})


def test_values_at_the_limits_and_multiline_notes_still_pass():
    client = ClientCreate(name="x" * 255, company="y" * 255, notes="line one\nline two\ttab",
                          tags=["t" * 64] * 50, phone="1" * 32)
    assert client.name == "x" * 255 and client.notes.endswith("tab")
    meeting = CreateMeetingRequest(title="x" * 255, start_time=MEETING["start_time"],
                                   description="d" * 10_000, timezone="Europe/London",
                                   attendees=[f"p{i}@example.com" for i in range(50)])
    assert len(meeting.attendees) == 50
    assert CancelMeetingRequest(reason="r" * 1_000).reason
    assert CreateBlockedEntityRequest(**BLOCK, reason="r" * 1_000).reason


def test_empty_names_and_titles_are_refused():
    with pytest.raises(ValidationError):
        ClientCreate(name="")
    with pytest.raises(ValidationError):
        CreateMeetingRequest(title="", start_time=MEETING["start_time"])

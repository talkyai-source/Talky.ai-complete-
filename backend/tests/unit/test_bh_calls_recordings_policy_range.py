"""Behavioural tests: PUT /recordings/policy audit call and suffix byte ranges."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import Request

from app.api.v1.dependencies import CurrentUser
from app.api.v1.endpoints import recordings
from app.domain.services.audit_logger import AuditEvent


class _Audit:
    def __init__(self):
        self.events: list[dict] = []

    async def log(self, **kwargs):
        # Mirror the real signature: event_type and action are required.
        assert "event_type" in kwargs and "action" in kwargs
        self.events.append(kwargs)


def _request(range_header: str | None = None) -> Request:
    headers = [] if range_header is None else [(b"range", range_header.encode())]
    return Request(
        {"type": "http", "method": "GET", "path": "/x", "query_string": b"", "headers": headers}
    )


@pytest.mark.asyncio
async def test_put_recording_policy_saves_and_audits(monkeypatch):
    tenant_id = str(uuid4())
    user = CurrentUser(id=str(uuid4()), email="a@example.com", tenant_id=tenant_id, role="tenant_admin")
    saved = {
        "default_consent_mode": "one_party",
        "announcement_text": "",
        "opt_out_dtmf_digit": None,
        "two_party_country_codes": [],
        "retention_days": 30,
        "updated_at": datetime.now(timezone.utc),
    }
    conn = SimpleNamespace()

    async def fetchrow(query, *args):
        return saved

    conn.fetchrow = fetchrow

    @asynccontextmanager
    async def acquire(_pool, tenant):
        assert tenant == tenant_id
        yield conn

    monkeypatch.setattr(recordings, "acquire_with_tenant", acquire)
    audit = _Audit()
    body = recordings.RecordingPolicyBody(default_consent_mode="one_party", retention_days=30)

    response = await recordings.put_recording_policy(
        body=body, user=user, db_pool=object(), audit_logger=audit
    )

    assert response.configured is True and response.retention_days == 30
    assert len(audit.events) == 1
    event = audit.events[0]
    assert event["event_type"] == AuditEvent.CONFIG_CHANGED
    assert event["action"] == "recording_policy_updated"
    assert event["tenant_id"] == tenant_id
    assert event["resource_type"] == "tenant_recording_policy"
    assert event["metadata"]["default_consent_mode"] == "one_party"
    assert event["metadata"]["retention_days"] == 30


def _ranged(tmp_path, header):
    path = tmp_path / "r.mp3"
    path.write_bytes(bytes(range(100)))
    return recordings._ranged_file_response(str(path), _request(header), media_type="audio/mpeg")


async def _body(response) -> bytes:
    out = b""
    async for chunk in response.body_iterator:
        out += chunk
    return out


@pytest.mark.asyncio
async def test_suffix_range_returns_last_n_bytes(tmp_path):
    response = _ranged(tmp_path, "bytes=-10")
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 90-99/100"
    assert response.headers["content-length"] == "10"
    assert await _body(response) == bytes(range(90, 100))


@pytest.mark.asyncio
async def test_suffix_range_larger_than_file_returns_whole_file(tmp_path):
    response = _ranged(tmp_path, "bytes=-500")
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-99/100"
    assert response.headers["content-length"] == "100"


def test_zero_length_suffix_range_is_416(tmp_path):
    response = _ranged(tmp_path, "bytes=-0")
    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */100"


@pytest.mark.asyncio
async def test_open_and_closed_ranges_unchanged(tmp_path):
    response = _ranged(tmp_path, "bytes=10-19")
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 10-19/100"
    assert await _body(response) == bytes(range(10, 20))
    response = _ranged(tmp_path, "bytes=95-")
    assert response.headers["content-range"] == "bytes 95-99/100"
    assert _ranged(tmp_path, "bytes=100-").status_code == 416

"""Authenticated Alertmanager delivery into the existing durable incident store.

This adapter does not evaluate thresholds, fetch payload URLs or notify people.
Its acknowledgement proves storage of the complete received batch, not external
receiver delivery or historical coverage across an Alertmanager outage.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.api.v1.dependencies import get_db_client
from app.core.config import get_settings
from app.core.db_utils import acquire_with_tenant

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["monitoring"])
MAX_BODY_BYTES = 256 * 1024
MAX_ALERTS = 100
SOURCE_LABELS = frozenset({"alertname", "severity", "job", "instance", "component", "cluster", "environment"})


def configured_source():
    settings = get_settings()
    secret = settings.alertmanager_ingest_token
    token = secret.get_secret_value() if secret is not None else ""
    source = settings.alertmanager_source_id
    if (not 32 <= len(token) <= 256 or any(not 33 <= ord(char) <= 126 for char in token)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", source)):
        return None
    return source, token


def _authenticate(request: Request) -> str:
    configured = configured_source()
    if configured is None:
        raise HTTPException(status_code=503, detail="Alert ingestion is not configured.")
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        raise HTTPException(status_code=401, detail="Alert ingestion authentication required.")
    scheme, separator, presented = values[0].partition(" ")
    if scheme.lower() != "bearer" or not separator or not presented or len(presented) > 1024:
        raise HTTPException(status_code=401, detail="Alert ingestion authentication required.")
    source, token = configured
    if not hmac.compare_digest(hashlib.sha256(presented.encode()).digest(), hashlib.sha256(token.encode()).digest()):
        raise HTTPException(status_code=401, detail="Alert ingestion authentication required.")
    return source


def _bounded_map(value, *, count=64, value_length=1024):
    if not isinstance(value, dict) or len(value) > count:
        raise ValueError("Invalid alert map")
    for key, item in value.items():
        if (not isinstance(key, str) or not 1 <= len(key) <= 128 or not isinstance(item, str)
                or len(item) > value_length or any(ord(char) < 32 and char not in "\n\r\t" for char in key + item)):
            raise ValueError("Invalid alert text")
    return value


class Alert(BaseModel):
    status: Literal["firing", "resolved"]
    fingerprint: str = Field(pattern=r"^[0-9a-f]{16,64}$")
    startsAt: datetime
    endsAt: datetime | None = None
    labels: dict[str, str]
    annotations: dict[str, str] = Field(default_factory=dict)
    generatorURL: str | None = Field(None, max_length=2048)

    @field_validator("labels", mode="before")
    @classmethod
    def labels_bound(cls, value):
        return _bounded_map(value)

    @field_validator("annotations", mode="before")
    @classmethod
    def annotations_bound(cls, value):
        return _bounded_map(value, count=32, value_length=4096)

    @field_validator("startsAt", "endsAt", mode="before")
    @classmethod
    def timestamp_text(cls, value):
        if value is not None and not isinstance(value, str):
            raise ValueError("RFC3339 timestamp required")
        return value

    @model_validator(mode="after")
    def validate_event(self):
        now = datetime.now(timezone.utc) + timedelta(minutes=5)
        if self.startsAt.tzinfo is None or self.startsAt.year < 2000 or self.startsAt > now:
            raise ValueError("Invalid alert start timestamp")
        if self.endsAt is not None and self.endsAt.tzinfo is None:
            raise ValueError("Timezone required")
        if self.status == "resolved" and (self.endsAt is None or not self.startsAt <= self.endsAt <= now):
            raise ValueError("Resolved alert requires a valid end timestamp")
        name = self.labels.get("alertname", "").strip()
        severity = self.labels.get("severity", "warning")
        summary = self.annotations.get("summary", name).strip()
        if not name or len(name) > 128 or not summary or len(summary) > 512:
            raise ValueError("Alert name and bounded summary required")
        if severity not in {"critical", "warning", "info"}:
            raise ValueError("Unsupported alert severity")
        return self


class Notification(BaseModel):
    version: Literal["4"]
    status: Literal["firing", "resolved"]
    # Native AM0.27 includes this field. Missing/positive values cannot attest
    # a complete batch and are rejected before a transaction begins.
    truncatedAlerts: int = Field(..., strict=True, ge=0, le=0)
    alerts: list[Alert] = Field(min_length=1, max_length=MAX_ALERTS)
    receiver: str = Field(max_length=128)
    groupKey: str = Field(max_length=1024)
    groupLabels: dict[str, str] = Field(default_factory=dict)
    commonLabels: dict[str, str] = Field(default_factory=dict)
    commonAnnotations: dict[str, str] = Field(default_factory=dict)
    externalURL: str | None = Field(None, max_length=2048)

    @field_validator("groupLabels", "commonLabels", mode="before")
    @classmethod
    def labels_bound(cls, value):
        return _bounded_map(value)

    @field_validator("commonAnnotations", mode="before")
    @classmethod
    def annotations_bound(cls, value):
        return _bounded_map(value, count=32, value_length=4096)


# Document the manually bounded body without introducing FastAPI body parsing
# before authentication. Alert is the model's sole child; inline it so its local
# JSON-schema reference does not become an unresolved OpenAPI component pointer.
_notification_schema = Notification.model_json_schema()
_notification_schema["properties"]["alerts"]["items"] = _notification_schema.pop("$defs")["Alert"]
_documented_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="AlertmanagerIngestBearer",
    bearerFormat="Dedicated ingestion secret",
    description="Dedicated ALERTMANAGER_INGEST_TOKEN, 32-256 printable ASCII nonwhitespace characters. "
        "User JWTs, browser cookies and internal-service tokens do not authorize this route. "
        "This credential grants ingestion only, never Admin read or operator privileges.",
)
_error_schema = {"type": "object", "required": ["error"], "properties": {
    "error": {"type": "object", "required": ["code", "message", "details", "request_id"],
        "properties": {"code": {"type": "string"}, "message": {"type": "string"},
            "details": {"type": "null"}, "request_id": {"anyOf": [{"type": "string"}, {"type": "null"}]}}},
}}
_documented_responses = {
    status: {"description": description, "content": {"application/json": {"schema": _error_schema}}}
    for status, description in {
        400: "Invalid Content-Length. No alerts accepted.",
        401: "Missing or invalid dedicated ingestion credential. No alerts accepted.",
        403: "Existing CSRF controls reject cookie-only or untrusted browser requests. No alerts accepted.",
        413: "Body exceeds 256 KiB. Entire batch rejected; correct sender grouping.",
        415: "application/json is required. No alerts accepted.",
        422: "Invalid, unsupported or truncated v4 batch. No alerts accepted; correct the sender payload.",
        503: "Configuration, body timeout or storage confirmation unavailable. Retry the complete batch; "
             "durable episode identity makes a lost acknowledgement safe to replay.",
    }.items()
}
# CSRF middleware returns its own direct response before the exception handler.
_documented_responses[403]["content"]["application/json"]["schema"] = {
    "type": "object", "required": ["detail"], "properties": {"detail": {"type": "string"}},
}
_documented_responses[200] = {
    "description": "The complete received batch committed to durable incidents; this is not notification-delivery proof.",
    "content": {"application/json": {"schema": {
        "type": "object", "required": ["accepted_alerts", "episode_count", "incident_ids"],
        "properties": {
            "accepted_alerts": {"type": "integer", "minimum": 1, "maximum": MAX_ALERTS},
            "episode_count": {"type": "integer", "minimum": 1, "maximum": MAX_ALERTS},
            "incident_ids": {"type": "array", "minItems": 1, "maxItems": MAX_ALERTS,
                "uniqueItems": True, "items": {"type": "string", "format": "uuid"}},
        },
    }}},
}


async def _read_body(request):
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) < 0 or int(length) > MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail="Alert notification exceeds the body limit.")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid body length.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Alert notification exceeds the body limit.")
        body.extend(chunk)
    return bytes(body)


async def persist_notification(pool, source: str, notification: Notification):
    identifiers = set()
    # Stable order prevents opposite-order deliveries locking overlapping
    # episodes cyclically. Human transitions only lock a single incident row.
    alerts = sorted(notification.alerts, key=lambda item: (item.fingerprint, item.startsAt))
    async with acquire_with_tenant(pool, None, timeout=3.0) as conn:
        await conn.execute("SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='3s'")
        for alert in alerts:
            recovered = alert.endsAt if alert.status == "resolved" else None
            incident_id = await conn.fetchval("""
                INSERT INTO incidents(title,severity,description,triggered_at,
                    source_system,source_fingerprint,source_started_at,source_status,
                    source_recovered_at,source_last_received_at,source_labels)
                VALUES($1,$2,$3,$4,$5,$6,$4,$7,$8,clock_timestamp(),$9::text::jsonb)
                ON CONFLICT(source_system,source_fingerprint,source_started_at)
                    WHERE source_system IS NOT NULL
                DO UPDATE SET
                    source_status=CASE WHEN incidents.source_status='recovered'
                        OR EXCLUDED.source_status='recovered' THEN 'recovered' ELSE 'firing' END,
                    source_recovered_at=CASE WHEN incidents.source_recovered_at IS NULL
                        THEN EXCLUDED.source_recovered_at WHEN EXCLUDED.source_recovered_at IS NULL
                        THEN incidents.source_recovered_at
                        ELSE LEAST(incidents.source_recovered_at,EXCLUDED.source_recovered_at) END,
                    source_last_received_at=clock_timestamp()
                RETURNING id
            """, alert.annotations.get("summary", alert.labels["alertname"]).strip(),
                alert.labels.get("severity", "warning"), alert.annotations.get("description"),
                alert.startsAt, source, alert.fingerprint, "recovered" if recovered else "firing", recovered,
                json.dumps({key: value for key, value in alert.labels.items() if key in SOURCE_LABELS}))
            if incident_id is None:
                raise RuntimeError("Incident ingestion receipt missing")
            identifiers.add(str(incident_id))
    return {"accepted_alerts": len(alerts), "episode_count": len(identifiers), "incident_ids": sorted(identifiers)}


@router.post(
    "/alertmanager",
    dependencies=[Depends(_documented_bearer)],
    responses=_documented_responses,
    openapi_extra={"requestBody": {"required": True,
        "description": "Native Alertmanager v4 JSON, at most 256 KiB and 100 alerts. truncatedAlerts must be present and zero. "
            "Label maps allow at most 64 entries (keys 1-128 characters, values at most 1024); annotation maps allow 32 entries "
            "with values at most 4096 characters. Alert names are at most 128 and summaries at most 512 characters. "
            "Severity is critical, warning or info (missing defaults to warning). Starts must be timezone-aware, year 2000 or later "
            "and at most five minutes ahead of the server clock. Resolved ends must be between start and that same clock bound. "
            "The complete batch is rejected on any invalid alert.",
        "content": {"application/json": {"schema": _notification_schema}}}},
    description="Ingest a complete Alertmanager v4 batch into existing durable incidents. "
        "Authenticate using the dedicated write-only Bearer secret. Source identity comes from server configuration; "
        "payload URLs are never fetched. Episodes deduplicate by source, fingerprint and startsAt. "
        "Source recovery is recorded separately from human acknowledgement/resolution. "
        "Alertmanager 0.27 treats 4xx as terminal delivery failures requiring operator correction; 503 is retryable. "
        "A 200 receipt proves received-batch storage, not external notification delivery or historical alert coverage.",
)
async def ingest_alertmanager(request: Request):
    source = _authenticate(request)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="Alert notification must use application/json.")
    try:
        body = await asyncio.wait_for(_read_body(request), timeout=5.0)
        notification = Notification.model_validate_json(body)
    except asyncio.TimeoutError:
        # AM0.27's webhook retrier retries 5xx, not HTTP408. A transient body
        # timeout must remain retryable rather than dropping this delivery.
        raise HTTPException(status_code=503, detail="Alert notification body timed out; retry the complete notification.")
    except ValidationError:
        # Do not echo caller payload/annotations/URLs through validation errors.
        raise HTTPException(status_code=422, detail="Invalid or truncated Alertmanager v4 notification; no alerts accepted.")
    try:
        # Resolve the pool only after authentication and bounded validation;
        # an unavailable container must return retryable 503, not an auth leak.
        db_client = get_db_client()
        return await asyncio.wait_for(persist_notification(db_client.pool, source, notification), timeout=10.0)
    except Exception as exc:
        # Type distinguishes timeout/schema/storage faults without leaking
        # labels, annotations, SQL parameters or credentials into logs.
        logger.warning("Alertmanager incident batch was not confirmed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Alert incident batch was not confirmed; retry the complete notification.")

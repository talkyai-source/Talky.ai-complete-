"""Admin-panel crash reports (telemetry intake).

The report is untrusted text typed by a browser. It is scrubbed again here,
stripped of control characters, capped, and written as ONE error log line.
No database write and no audit row: this is telemetry, not an operator action.
ERROR log lines already become error-tracking events (LoggingIntegration
event_level=ERROR in app/core/sentry_init.py), so nothing is sent separately.
"""
import json
import logging
import re
import unicodedata
from datetime import datetime
from typing import Annotated, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, field_validator

from app.api.v1.dependencies import CurrentUser, require_admin
from app.api.v1.endpoints.auth import limiter
from app.core.sentry_scrub import scrub_string

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_BODY_BYTES = 8 * 1024
# The panel itself sends at most 5 a minute; this leaves room for a second tab
# and keeps one account from filling the error log.
REPORTS_PER_MINUTE = "10/minute"
_QUERY = re.compile(r"\?[\w.\-%\[\]]+=[^\s'\")>]*")


def _user_key(request: Request) -> str:
    uid = getattr(request.state, "authenticated_user_id", None)
    if uid:
        return f"client-errors-user:{uid}"
    return f"client-errors-ip:{request.client.host if request.client else 'unknown'}"


class ClientErrorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["render", "error", "unhandledrejection"]
    name: Annotated[str, StringConstraints(max_length=100)]
    message: Annotated[str, StringConstraints(max_length=500)]
    stack: List[Annotated[str, StringConstraints(max_length=300)]]
    route: Annotated[str, StringConstraints(max_length=200)]
    app_version: Optional[Annotated[str, StringConstraints(max_length=64)]] = None
    occurred_at: Annotated[str, StringConstraints(max_length=40)]

    @field_validator("stack")
    @classmethod
    def _stack_len(cls, value):
        if len(value) > 10:
            raise ValueError("at most 10 stack lines")
        return value

    @field_validator("occurred_at")
    @classmethod
    def _iso(cls, value):
        # A browser sends "...Z"; older Pythons only read "+00:00".
        datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        return value


def _clean(text: str, limit: int) -> str:
    text = "".join(
        " " if unicodedata.category(c) in ("Cc", "Zl", "Zp") else c for c in text
    )
    text = scrub_string(_QUERY.sub("?[query]", text))
    text = "".join(" " if unicodedata.category(c) in ("Cc", "Zl", "Zp") else c for c in text)
    return text[:limit]


def _clean_route(route: str) -> str:
    route = re.split(r"[?#]", route, maxsplit=1)[0]
    return _clean(route, 200)


async def _read_limited(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail="Report too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Report too large")
    return bytes(body)


@router.post("/client-errors", status_code=204, response_class=Response)
@limiter.limit(REPORTS_PER_MINUTE, key_func=_user_key)
async def report_client_error(
    request: Request,
    current_user: CurrentUser = Depends(require_admin),
):
    raw = await _read_limited(request)
    try:
        report = ClientErrorReport.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        raise HTTPException(status_code=422, detail="Invalid report")

    fields = {
        "kind": report.kind,
        "name": _clean(report.name, 100),
        "message": _clean(report.message, 500),
        "route": _clean_route(report.route),
        "app_version": _clean(report.app_version, 64) if report.app_version else None,
        "occurred_at": _clean(report.occurred_at, 40),
        "stack": [_clean(line, 300) for line in report.stack],
    }
    logger.error(
        "admin_client_error user=%s role=%s fields=%s",
        _clean(str(current_user.id), 8), _clean(str(current_user.role), 32),
        json.dumps(fields, ensure_ascii=True),
    )
    return Response(status_code=204)

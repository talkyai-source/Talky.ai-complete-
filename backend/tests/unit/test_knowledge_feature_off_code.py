"""D3-11: "knowledge is switched off" has its own code; the wording is unchanged.

With the knowledge feature off, the knowledge read answered 404 with the
generic envelope code "not_found" and the message "Campaign knowledge is not
enabled". The frontend recognised "feature off" by that exact message, so the
message must stay word for word; the envelope now also carries the dedicated
code "knowledge_disabled".
"""
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.endpoints import campaign_knowledge
from app.core.error_handlers import http_exception_handler


async def test_feature_off_has_a_dedicated_code_and_the_same_message(monkeypatch):
    monkeypatch.setattr(campaign_knowledge, "knowledge_enabled", lambda: False)
    with pytest.raises(HTTPException) as refused:
        campaign_knowledge._require_enabled()
    assert refused.value.status_code == 404

    request = Request({"type": "http", "method": "GET", "path": "/api/v1/campaigns/x/knowledge", "headers": []})
    response = await http_exception_handler(request, refused.value)
    body = json.loads(response.body)["error"]
    assert body["code"] == "knowledge_disabled"
    assert body["message"] == "Campaign knowledge is not enabled", "the frontend matches this exact text"


def test_feature_on_raises_nothing(monkeypatch):
    monkeypatch.setattr(campaign_knowledge, "knowledge_enabled", lambda: True)
    campaign_knowledge._require_enabled()

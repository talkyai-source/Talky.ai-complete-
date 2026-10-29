import json
import uuid

from app.domain.services.support.bot.tools import TOOL_NAMES, TOOL_SCHEMAS, validate_tool_call


def test_schemas_match_names():
    assert {t["function"]["name"] for t in TOOL_SCHEMAS} == TOOL_NAMES and len(TOOL_NAMES) == 8


def test_extra_fields_rejected():
    assert validate_tool_call("get_account_overview", {"tenant_id": "x"}) == (None, "invalid_arguments")
    assert validate_tool_call("search_help_articles", {"query": "abc", "x": 1})[1] == "invalid_arguments"


def test_bad_uuid_and_bounds():
    assert validate_tool_call("get_call_details", {"call_id": "nope"})[1] == "invalid_arguments"
    assert validate_tool_call("get_call_details", {"call_id": str(uuid.uuid4())})[1] is None
    assert validate_tool_call("search_help_articles", {"query": "a"})[1] == "invalid_arguments"
    assert validate_tool_call("search_help_articles", {"query": "a" * 201})[1] == "invalid_arguments"
    assert validate_tool_call("get_campaign_status", {"campaign": "x" * 101})[1] == "invalid_arguments"
    assert validate_tool_call("get_campaign_status", {"campaign": ""})[1] == "invalid_arguments"


def test_unknown_and_malformed():
    assert validate_tool_call("create_ticket", {}) == (None, "unknown_tool")
    assert validate_tool_call("get_campaign_status", "{bad")[1] == "invalid_arguments"
    assert validate_tool_call("get_account_overview", "")[1] is None
    assert validate_tool_call("get_campaign_status", json.dumps({"campaign": "A"}))[1] is None

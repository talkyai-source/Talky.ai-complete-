"""Guards for the inbound config content editor."""
from __future__ import annotations

import inspect
from pathlib import Path

from scripts import update_inbound_config as editor

BACKEND = Path(__file__).resolve().parents[2]


def test_it_goes_through_the_service_not_the_database():
    source = inspect.getsource(editor)
    assert "update_campaign(" in source
    assert "UPDATE inbound_campaign_configs" not in source


def test_after_hours_message_is_merged_into_business_hours_not_replacing_it():
    """after_hours_message lives inside the business_hours object. Replacing
    that object wholesale would silently wipe the configured opening times."""
    source = inspect.getsource(editor._run)
    assert 'payload.get("business_hours", before.get("business_hours") or {})' in source
    assert 'hours["after_hours_message"] = args.after_hours_message' in source


def test_only_the_flags_passed_are_sent():
    source = inspect.getsource(editor._run)
    assert "if not payload:" in source
    assert "nothing to change" in source
    for field in ("greeting", "after_hours_action", "after_hours_message", "transfer_number"):
        assert f"args.{field} is not None" in source, field


def test_it_does_not_offer_to_change_the_address():
    """Moving a config to a different number is the assignment workflow's job."""
    source = inspect.getsource(editor)
    assert "--did-number" not in source
    assert "--extension" not in source


def test_remaining_blockers_are_named_after_the_edit():
    source = inspect.getsource(editor._run)
    assert "BLOCKED" in source
    assert "ready to activate" in source


def test_script_is_executable_by_path_from_backend_directory():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "update_inbound_config.py"), "--help"],
        capture_output=True, text=True, cwd=str(BACKEND),
    )
    assert result.returncode == 0, result.stderr
    assert "--after-hours-message" in result.stdout


def test_the_expected_version_is_always_sent():
    """update_campaign is optimistically concurrent and refuses without it, so
    omitting it made every edit fail with expected_version_required."""
    source = inspect.getsource(editor._run)
    assert 'payload["expected_version"]' in source
    assert 'before.get("version")' in source


def test_the_version_can_be_pinned_explicitly():
    source = inspect.getsource(editor.main)
    assert "--expected-version" in source


def test_business_hours_can_be_replaced_wholesale_to_recover_a_malformed_one():
    """An EMPTY business_hours object is explicitly 24/7 and valid. A non-empty
    one without weekly_schedule is malformed -- which is what writing only an
    after_hours_message into an empty object produced, blocking activation with
    business_hours_valid. Replacement is how that is recovered."""
    source = inspect.getsource(editor._run)
    assert 'payload["business_hours"] = json.loads(args.business_hours_json)' in source
    # and the message merge must build on the replacement, not the stale value
    assert 'payload.get("business_hours", before.get("business_hours") or {})' in source


def test_the_business_hours_contract_is_what_the_validator_actually_enforces():
    from app.domain.services.telephony.business_hours import evaluate_business_hours

    assert evaluate_business_hours("Europe/London", {}).valid is True
    assert evaluate_business_hours("Europe/London", {"after_hours_message": "x"}).valid is False
    full = {
        "weekly_schedule": [
            {"day": d, "enabled": True, "windows": [{"start": "00:00", "end": "23:59"}]}
            for d in range(7)
        ],
        "after_hours_message": "x",
    }
    assert evaluate_business_hours("Europe/London", full).valid is True

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
    assert 'hours = dict(before.get("business_hours") or {})' in source
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

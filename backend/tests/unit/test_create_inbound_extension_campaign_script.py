"""Guards for the extension-campaign provisioning entry point.

It must go through InboundCampaignService rather than writing rows, so it
inherits idempotency, the direction lock, versioning, audit, the ownership
check and the global uniqueness rule. And it must never invent a base campaign:
a campaign carries the agent's prompt, voice and goal, which are product
decisions, not provisioning defaults.
"""
from __future__ import annotations

import argparse
import inspect
from pathlib import Path

import pytest

from scripts import create_inbound_extension_campaign as maker

BACKEND = Path(__file__).resolve().parents[2]


def _args(**over):
    base = dict(
        tenant_id="t", actor_user_id="a", actor_role="tenant_admin",
        campaign_id="c", sip_trunk_id="s", extension="940003",
        name="Reception", greeting="Hello", timezone="UTC",
        opening_mode="agent_first", business_hours=None,
        after_hours_action="voicemail", transfer_number=None, activate=False,
    )
    base.update(over)
    return argparse.Namespace(**base)


def test_the_address_is_sent_in_canonical_tagged_form():
    payload = maker.build_payload(_args())
    assert payload["did_number"] == "ext:940003", (
        "the tag is what tells the service to demand trunk ownership instead of "
        "a verified phone-number row"
    )


@pytest.mark.parametrize("bad", ["94", "9400031234", "94000a", ""])
def test_a_malformed_extension_is_refused_before_anything_is_built(bad):
    with pytest.raises(SystemExit, match="not a valid internal extension"):
        maker.build_payload(_args(extension=bad))


def test_recording_is_off_by_default():
    """Recording carries consent obligations; provisioning must not enable it."""
    assert maker.build_payload(_args())["recording_enabled"] is False


def test_it_requires_an_existing_base_campaign():
    source = inspect.getsource(maker)
    assert "INSERT INTO campaigns" not in source
    assert "never invents one" in source
    assert '"--campaign-id",' in source
    assert "an EXISTING inbound campaign" in source


def test_it_goes_through_the_service_not_the_database():
    source = inspect.getsource(maker)
    assert "InboundCampaignService" in source
    assert "create_campaign(" in source
    for forbidden in ("INSERT INTO inbound_did_assignments", "INSERT INTO inbound_campaign_configs"):
        assert forbidden not in source, forbidden


def test_it_reads_the_config_id_under_the_key_the_service_actually_returns():
    """_serialize_bundle keys the config as "id". Reading "config_id" raised
    KeyError AFTER create_campaign had already written the config and its
    assignment, leaving both stranded unactivated on production."""
    source = inspect.getsource(maker._run)
    assert 'created["id"]' in source
    assert 'created["config_id"]' not in source


def test_readiness_blockers_are_read_from_the_blockers_list():
    readiness_keys = inspect.getsource(maker._run)
    assert 'readiness.get("blockers")' in readiness_keys


def test_an_idempotent_replay_is_reported_rather_than_looking_like_a_fresh_create():
    assert "idempotent_replay" in inspect.getsource(maker._run)


def test_the_idempotency_key_is_deterministic_per_tenant_and_extension():
    """Re-running provisioning must return the same config, not create a second."""
    source = inspect.getsource(maker._run)
    assert 'f"ext-campaign-{args.tenant_id}-{args.extension}"' in source


def test_script_is_executable_by_path_from_backend_directory():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "create_inbound_extension_campaign.py"), "--help"],
        capture_output=True, text=True, cwd=str(BACKEND),
    )
    assert result.returncode == 0, result.stderr
    assert "--extension" in result.stdout


def test_the_after_hours_default_needs_no_extra_content():
    """Defaulting to "voicemail" produced configs that could never activate: it
    requires a non-empty business_hours.after_hours_message, and provisioning
    supplied empty business hours. "hangup" is the API schema's own default and
    requires nothing."""
    source = inspect.getsource(maker)
    assert 'default="hangup"' in source
    assert 'default="voicemail"' not in source

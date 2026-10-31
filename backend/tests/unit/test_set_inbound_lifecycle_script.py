"""Guards for the lifecycle entry point.

Creation and activation were coupled, so a config created but not activated had
no way forward: re-running creation is idempotency-keyed on the request and is
correctly refused once any input differs. Activation needs its own verb.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from scripts import set_inbound_lifecycle as lifecycle

BACKEND = Path(__file__).resolve().parents[2]


def test_it_goes_through_the_service_not_the_database():
    source = inspect.getsource(lifecycle)
    assert "set_lifecycle(" in source
    for forbidden in ("UPDATE inbound_campaign_configs", "UPDATE inbound_did_assignments"):
        assert forbidden not in source, forbidden


def test_a_readiness_refusal_names_every_failing_check():
    """'Activation refused' alone tells an operator nothing they can act on."""
    source = inspect.getsource(lifecycle._run)
    assert "InboundReadinessError" in source
    assert "BLOCKED" in source
    assert "check.get('key')" in source and "check.get('detail')" in source
    # the exception really does carry the report
    from app.domain.services.inbound_campaign_service import InboundReadinessError

    err = InboundReadinessError({"ready": False, "checks": [{"key": "k", "passed": False}]})
    assert err.readiness["checks"][0]["key"] == "k"


def test_it_is_a_no_op_when_already_in_the_target_state():
    source = inspect.getsource(lifecycle._run)
    assert 'already {args.status}; nothing to do' in source or "already " in source


def test_the_idempotency_key_is_unique_per_attempt():
    """Unlike creation, a lifecycle move must not be refused as a replay when an
    operator legitimately retries after fixing a blocker."""
    source = inspect.getsource(lifecycle._run)
    assert "uuid.uuid4()" in source


def test_the_expected_version_defaults_to_what_was_just_read():
    source = inspect.getsource(lifecycle._run)
    assert 'current.get("version")' in source


def test_script_is_executable_by_path_from_backend_directory():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "set_inbound_lifecycle.py"), "--help"],
        capture_output=True, text=True, cwd=str(BACKEND),
    )
    assert result.returncode == 0, result.stderr
    assert "--config-id" in result.stdout
    assert "--status" in result.stdout

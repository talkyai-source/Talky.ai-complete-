"""Every worker process installs the PII log redaction first (QA F2-10, QA-BE-110).

``app_bootstrap.configure_logging`` installs it for the API, but the workers
set up logging themselves and never did, so the dialer wrote lead phone
numbers to the journal in clear. The voice worker gets it from the voice
pipeline modules it imports.
"""
from __future__ import annotations

import importlib
import logging

import pytest


class _Installed(BaseException):
    """Stops ``main()`` at the install, before any worker work starts."""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module",
    [
        "app.workers.dialer_worker",
        "app.workers.reminder_worker",
        "app.workers.cleanup_worker",
        "app.workers.support_jobs",
    ],
)
async def test_the_worker_installs_redaction_before_anything_else(monkeypatch, module):
    worker = importlib.import_module(module)
    calls = []

    def install():
        calls.append(module)
        raise _Installed

    monkeypatch.setattr("app.core.log_redact.install_pii_log_redaction", install)
    with pytest.raises(_Installed):
        await worker.main()
    assert calls == [module]


def test_the_installed_redaction_hides_a_phone_number(caplog):
    from app.core.log_redact import install_pii_log_redaction

    install_pii_log_redaction()
    with caplog.at_level(logging.INFO, logger="redaction_probe"):
        logging.getLogger("redaction_probe").info("dialing +14155552671 now")
    assert "+14155552671" not in caplog.text

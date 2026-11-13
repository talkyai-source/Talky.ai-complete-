"""Execute real entry-point bodies with infrastructure stopped at its boundary."""
import ast
import logging
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.core import sentry_init


class ReachedWorkerBoundary(Exception):
    pass


@pytest.mark.parametrize("worker,boundary", [
    ("dialer_worker", "DialerWorker"),
    ("voice_worker", "VoicePipelineWorker"),
    ("reminder_worker", "ReminderWorker"),
    ("cleanup_worker", "_dry_run_enabled"),
    ("support_jobs", "load_settings"),
])
async def test_worker_initializes_sentry_before_work(worker, boundary, monkeypatch):
    source = Path(__file__).resolve().parents[2] / "app/workers" / f"{worker}.py"
    main = next(node for node in ast.parse(source.read_text(encoding="utf-8")).body
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "main")
    events = []
    monkeypatch.setattr(sentry_init, "init_sentry", lambda: events.append("sentry"))
    # Use the exact source function, excluding module-level service imports.
    # Neither worker construction, provider access nor settings-file reads run.
    def boundary_reached(*args, **kwargs):
        events.append("work")
        raise ReachedWorkerBoundary

    voice_config = ModuleType("app.core.voice_config")
    voice_config.get_voice_config = lambda: SimpleNamespace(worker_log_level="INFO")
    monkeypatch.setitem(sys.modules, "app.core.voice_config", voice_config)
    redact = ModuleType("app.core.log_redact")
    redact.install_pii_log_redaction = lambda: None
    monkeypatch.setitem(sys.modules, "app.core.log_redact", redact)
    import argparse
    namespace = {"logging": logging, "argparse": argparse, boundary: boundary_reached}
    module = ast.fix_missing_locations(ast.Module(body=[main], type_ignores=[]))
    exec(compile(module, str(source), "exec"), namespace)
    with pytest.raises(ReachedWorkerBoundary):
        if worker == "support_jobs":
            await namespace["main"]([])
        else:
            await namespace["main"]()
    assert events == ["sentry", "work"], f"{worker} started work without Sentry initialization"

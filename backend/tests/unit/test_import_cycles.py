"""Import-order independence for the auth/tenant-isolation cycle.

WHY THIS EXISTS (2026-07-29)
----------------------------
`app.core.security.tenant_isolation` imports `CurrentUser` / `get_current_user`
from `app.api.v1.dependencies`, and `dependencies` re-exported five names back
from `tenant_isolation`. That is a genuine cycle, and Python only tolerates it
in ONE direction: import `dependencies` first and the re-export line runs after
both names it needs already exist. Import `tenant_isolation` first and it fails:

    ImportError: cannot import name 'TenantContext' from partially initialized
    module 'app.core.security.tenant_isolation'

It was invisible because the whole suite is collected after conftest has already
imported the app, so `dependencies` always won the race. It surfaced the moment
two test modules were run standalone — `tests/unit/test_unavailable_disposition.py`
and `tests/unit/test_disposition_retry_bounds.py` import `call_service`, which
imports `tenant_isolation` directly, and neither could be collected on its own.

The fix made the re-export lazy (PEP 562 module `__getattr__`). These tests pin
BOTH halves of that: the ordering must not matter, and the re-exported public
surface must still be reachable.

Each import order runs in a FRESH interpreter — once a module is in
`sys.modules`, the cycle cannot reproduce, so an in-process test would pass
vacuously no matter what the code did.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[2]

# The two orders. The first is the one that used to raise.
_ORDERS = [
    pytest.param(
        ("app.core.security.tenant_isolation", "app.api.v1.dependencies"),
        id="tenant_isolation-first",
    ),
    pytest.param(
        ("app.api.v1.dependencies", "app.core.security.tenant_isolation"),
        id="dependencies-first",
    ),
]

# Every name `dependencies` re-exports from `tenant_isolation`. Kept explicit:
# these are imported by name elsewhere in the codebase, so losing one silently
# would be an API break, not a refactor.
_REEXPORTS = (
    "TenantContext",
    "require_tenant_access",
    "get_tenant_context_dependency",
    "get_current_tenant_id",
    "validate_tenant_access",
)


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_BACKEND),
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.mark.parametrize("order", _ORDERS)
def test_import_order_does_not_matter(order):
    """Neither module may depend on the other having been imported first."""
    first, second = order
    result = _run(f"import {first}\nimport {second}\nprint('OK')")
    assert result.returncode == 0 and "OK" in result.stdout, (
        f"importing {first} before {second} failed — the dependencies <-> "
        f"tenant_isolation cycle is back:\n{result.stderr[-2000:]}"
    )


def test_reexports_still_resolve_after_the_hostile_order():
    """The lazy re-export must return the real objects, not just avoid raising.

    Uses the order that used to fail, so this covers the case where
    `tenant_isolation` is only partially initialised when `dependencies` is
    first touched.
    """
    names = ", ".join(f"'{n}'" for n in _REEXPORTS)
    result = _run(
        "import app.core.security.tenant_isolation as ti\n"
        "import app.api.v1.dependencies as deps\n"
        f"for name in ({names},):\n"
        "    assert getattr(deps, name) is getattr(ti, name), name\n"
        "print('OK')"
    )
    assert result.returncode == 0 and "OK" in result.stdout, (
        "a re-exported tenant-isolation name is missing or is not the same "
        f"object as the one on tenant_isolation:\n{result.stderr[-2000:]}"
    )


def test_unknown_attribute_still_raises_attribute_error():
    """The module `__getattr__` must not swallow genuine typos into an
    ImportError from somewhere unrelated."""
    result = _run(
        "import app.api.v1.dependencies as deps\n"
        "try:\n"
        "    deps.definitely_not_a_real_dependency\n"
        "except AttributeError:\n"
        "    print('OK')\n"
    )
    assert result.returncode == 0 and "OK" in result.stdout, result.stderr[-2000:]


def test_the_two_modules_that_exposed_this_collect_standalone():
    """Regression guard on the actual symptom.

    These two import `call_service` (-> `tenant_isolation`) at module scope and
    could not be collected outside a full-suite run.
    """
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "--collect-only", "-q",
            "tests/unit/test_unavailable_disposition.py",
            "tests/unit/test_disposition_retry_bounds.py",
        ],
        cwd=str(_BACKEND),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        "standalone collection failed — the import cycle (or another "
        f"collection error) is back:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}"
    )

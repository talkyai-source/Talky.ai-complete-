"""Architecture lock: the domain layer must never import the API layer.

Regression guard for the domain→API dependency inversion fixed in
``app/domain/services/telephony/lifecycle.py`` (the old ``_bridge()``
helper lazily imported ``app.api.v1.endpoints.telephony_bridge`` — a
domain module reaching *up* into the API layer, backwards from the
correct dependency direction). The fix replaced it with:

  * ``app.domain.services.telephony.state_backend.get_state_backend()``
    for per-call state (already in place before this fix).
  * ``app.domain.services.telephony.adapter_registry.get_adapter()`` for
    the live PBX adapter — the API layer registers a getter closure at
    import time instead of the domain layer importing it directly.
  * ``app.domain.services.telephony.config`` for the
    ``_MAX_TELEPHONY_SESSIONS`` / ``_RINGING_MAX_AGE_S`` constants
    (moved out of ``telephony_bridge.py``, which now imports them).

This test statically walks every ``.py`` file under ``app/domain`` and
asserts none of them import from ``app.api`` — at the AST level (not a
regex on source text) so a comment or a docstring mentioning the API
module never causes a false failure, and so the check can't be defeated
by reformatting an import statement.

Known pre-existing exceptions (NOT touched by this fix, out of scope for
the lifecycle.py refactor — each is its own separate, larger migration):

  * ``app/domain/services/campaign_service.py`` — imports
    ``telephony_bridge`` for campaign-triggered call origination.
  * ``app/domain/services/telephony/state_backend.py`` — its
    ``LocalOnlyStateBackend`` still holds a lazy reference to
    ``telephony_bridge``'s legacy module-level dicts (voice sessions,
    gateway-session map, etc.); the file's own docstring documents this
    as "step 1" of a staged migration that lifts dict ownership into the
    backend class in a later step.

New violations elsewhere in the domain layer are NOT allowed — this
test only tolerates the two named legacy files above so it can start
locking in the fix now rather than waiting for those larger migrations.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DOMAIN_ROOT = _BACKEND_ROOT / "app" / "domain"

# Pre-existing, out-of-scope violations (see module docstring). Paths are
# relative to app/domain/, forward-slash form.
_ALLOWED_VIOLATIONS = {
    "services/campaign_service.py",
    "services/telephony/state_backend.py",
}


def _imports_app_api(tree: ast.Module) -> list[str]:
    """Return the dotted module names of any `import app.api...` /
    `from app.api... import ...` statement found anywhere in the file
    (module level or nested inside a function, matching the lazy-import
    pattern the old ``_bridge()`` helper used)."""
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.api" or alias.name.startswith("app.api."):
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module == "app.api" or node.module.startswith("app.api.")
            ):
                hits.append(node.module)
    return hits


def _domain_py_files() -> list[pathlib.Path]:
    assert _DOMAIN_ROOT.is_dir(), f"expected {_DOMAIN_ROOT} to exist"
    return sorted(_DOMAIN_ROOT.rglob("*.py"))


@pytest.mark.parametrize(
    "path", _domain_py_files(), ids=lambda p: str(p.relative_to(_DOMAIN_ROOT)).replace("\\", "/")
)
def test_domain_module_does_not_import_app_api(path: pathlib.Path) -> None:
    rel = str(path.relative_to(_DOMAIN_ROOT)).replace("\\", "/")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    hits = _imports_app_api(tree)

    if rel in _ALLOWED_VIOLATIONS:
        # Still exercised so this test immediately flags it if someone
        # cleans up the legacy file and forgets to shrink the allowlist.
        assert hits, (
            f"{rel} is listed in _ALLOWED_VIOLATIONS but no longer imports "
            "app.api — remove it from the allowlist."
        )
        return

    assert not hits, (
        f"{rel} imports from the API layer ({hits}) — the domain layer must "
        "never import app.api.* (dependency direction inversion). See "
        "app/domain/services/telephony/adapter_registry.py for the pattern "
        "to use instead (API layer registers a callback/getter at import "
        "time; domain layer calls it)."
    )


def test_allowlist_has_no_stale_entries() -> None:
    """Every allowlisted path must actually exist under app/domain —
    catches a typo or a since-deleted file silently weakening the lock."""
    existing = {
        str(p.relative_to(_DOMAIN_ROOT)).replace("\\", "/") for p in _domain_py_files()
    }
    stale = _ALLOWED_VIOLATIONS - existing
    assert not stale, f"allowlisted paths no longer exist: {stale}"

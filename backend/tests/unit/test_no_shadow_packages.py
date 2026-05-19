"""Fail if a Python package (directory) is shadowed by a sibling .py module.

Python silently prefers a package over a module with the same name when both
exist in the same parent directory. This has bitten us hard:

  - commit 976c8ca: backend/app/api/v1/endpoints/mfa.py shadowed the
    mfa/ package. Three phases of security work (MFA brute-force gate,
    session revoke on disable, transactional recovery) were edited into
    the dead .py for weeks and never actually ran.

  - the audit that caught that mistake found six more pairs that had
    been hiding in the codebase for weeks, two of them with feature work
    from the previous 24h sitting silent in the dead file.

Once a pair like that exists, callers, IDE jump-to-definition, and code
search all return two answers — and humans pick the wrong one. This
test prevents the pattern from ever re-entering the repo unnoticed.

If you legitimately need a name to refer to both a module and a package
(rare), put them in different parent directories or rename one of them.
"""
from __future__ import annotations

import os
from pathlib import Path

# backend/tests/unit/test_no_shadow_packages.py -> backend/app
APP_ROOT = Path(__file__).resolve().parents[2] / "app"


def test_no_shadow_package_pairs() -> None:
    offenders: list[str] = []
    for dirpath, dirnames, _ in os.walk(APP_ROOT):
        # Skip dunder / hidden / cache directories — they're never importable
        # as Python packages, so they can't shadow anything.
        dirnames[:] = [d for d in dirnames if not d.startswith(("__", "."))]
        for d in dirnames:
            sibling = Path(dirpath) / f"{d}.py"
            if sibling.exists():
                offenders.append(str(sibling.relative_to(APP_ROOT.parent)))

    assert not offenders, (
        "Shadow .py file(s) found next to a package of the same name. "
        "Python silently picks the package and ignores the .py — see "
        "commit 976c8ca for what that costs in production. Delete the "
        f".py file or rename one of the pair. Offenders: {offenders}"
    )

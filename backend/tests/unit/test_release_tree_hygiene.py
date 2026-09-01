"""Repository hygiene required by the only supported production deploy path.

The deploy refuses a dirty ``/opt/talky`` before it can freeze a SHA.  A build
artefact that is already tracked, or a runtime-secret directory that is not
ignored, therefore makes the standard deploy impossible even on an otherwise
correct checkout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=check,
        capture_output=True,
    )


def test_gateway_build_output_is_not_tracked() -> None:
    tracked = set(_git("ls-files").stdout.decode().splitlines())

    build_directories = (
        "services/voice-gateway-cpp/build/",
        "services/voice-gateway-cpp/build-asan/",
    )
    leaked = sorted(
        path for path in tracked if path.startswith(build_directories)
    )

    assert leaked == []


def test_runtime_secret_and_local_worktree_directories_are_ignored() -> None:
    for candidate in (
        "secrets/runtime.env",
        ".codex-worktrees/probe/HEAD",
        "runtime/bin/voice_gateway",
    ):
        result = _git("check-ignore", "--no-index", "--quiet", candidate, check=False)
        assert result.returncode == 0, f"{candidate} must never dirty a checkout"


def test_linux_health_probe_is_lf_in_the_git_index() -> None:
    blob = _git("show", ":telephony/scripts/check_sbc_health.sh").stdout

    assert b"\r\n" not in blob

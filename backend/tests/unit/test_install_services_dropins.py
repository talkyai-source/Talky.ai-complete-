"""install-services.sh must install the boot-order and restart drop-ins.

The last test runs the real installer with `install`, `ln` and `systemctl`
replaced by local shims (the same harness as test_journald_installer), so
nothing outside the test directory is written. The first three are source-text
guards.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.unit.test_bh_ops_scripts_deploy import _launch, _real_bash, _write

BACKEND = Path(__file__).resolve().parents[2]
INSTALLER = (BACKEND / "systemd" / "install-services.sh").read_text(encoding="utf-8")
SRC = BACKEND / "deploy" / "infra" / "systemd"
PYTHON_UNITS = ("talky-api", "talky-api-web", "talky-dialer-worker",
                "talky-voice-worker", "talky-reminder-worker")


def test_sources_exist_and_installer_references_them():
    for name in ("talky-deps.conf", "talky-restart.conf", "nginx-restart.conf"):
        assert (SRC / name).is_file()
        assert f"deploy/infra/systemd/{name}" in INSTALLER


def test_every_python_unit_gets_the_deps_dropin_and_helper():
    for unit in PYTHON_UNITS:
        assert unit in INSTALLER.split("talky-deps.conf")[0].rsplit("for unit in", 1)[1]
    assert "/20-talky-deps.conf" in INSTALLER
    # the drop-in's ExecStartPre target must be installed before the drop-in
    assert INSTALLER.index("/opt/talky-infra/bin/wait-for-deps") < INSTALLER.index("20-talky-deps.conf")


def test_gateway_and_nginx_restart_dropins_target_the_right_units():
    assert "/etc/systemd/system/talky-voice-gateway.service.d/20-talky-restart.conf" in INSTALLER
    assert "/etc/systemd/system/nginx.service.d/20-talky-restart.conf" in INSTALLER
    assert INSTALLER.index("20-talky-restart.conf") < INSTALLER.index("systemctl daemon-reload")


@pytest.mark.skipif(_real_bash() is None, reason="no POSIX bash available")
def test_running_the_installer_installs_the_helper_then_the_dropins_before_reload(tmp_path):
    shim = tmp_path / "shim"
    shim.mkdir()
    log = tmp_path / "operations.log"
    for name in ("ln", "systemctl"):
        _write(shim / name, '#!/usr/bin/env bash\nprintf "%s\\n" "' + name + ' $*" >> "$OPERATIONS"\n')
    # Records "install <mode> <destination>" and only checks that the source
    # exists: no destination is ever written.
    _write(shim / "install", '''#!/usr/bin/env bash
set -euo pipefail
[ "$#" = 5 ] && [ "$1" = -D ] && [ "$2" = -m ]
test -f "$4"
printf '%s\n' "install $3 $5" >> "$OPERATIONS"
''')
    env = dict(os.environ, OPERATIONS=str(log),
               TALKY_PROD_HOST="nobody@example.invalid", TALKY_PROD_KEY="fake-key")
    result = _launch("exec bash systemd/install-services.sh", env, BACKEND, shim)
    assert result.returncode == 0, result.stdout + result.stderr

    operations = log.read_text().splitlines()
    helper = operations.index("install 0755 /opt/talky-infra/bin/wait-for-deps")
    deps = [operations.index(f"install 0644 /etc/systemd/system/{unit}.service.d/20-talky-deps.conf")
            for unit in PYTHON_UNITS]
    # The drop-in runs the helper before each start, so the helper goes in first.
    assert all(helper < position for position in deps)
    gateway = operations.index(
        "install 0644 /etc/systemd/system/talky-voice-gateway.service.d/20-talky-restart.conf")
    nginx = operations.index("install 0644 /etc/systemd/system/nginx.service.d/20-talky-restart.conf")
    # systemd only sees them after the reload that follows.
    assert max([*deps, gateway, nginx]) < operations.index("systemctl daemon-reload")

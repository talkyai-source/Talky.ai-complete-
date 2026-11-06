"""F9-03: run the real installer with system mutations stubbed locally."""
import configparser
import os
from pathlib import Path

import pytest

from tests.unit.test_bh_ops_scripts_deploy import _launch, _real_bash, _write


@pytest.mark.skipif(_real_bash() is None, reason="no POSIX bash available")
def test_installer_copies_retention_policy_and_activates_it(tmp_path):
    backend = Path(__file__).resolve().parents[2]
    shim = tmp_path / "shim"
    shim.mkdir()
    log = tmp_path / "operations.log"
    copied = tmp_path / "journald-copy.conf"
    for name in ("ln", "systemctl"):
        _write(shim / name, '#!/usr/bin/env bash\nprintf "%s\\n" "' + name + ' $*" >> "$OPERATIONS"\n')
    # No /etc path can be written: the install shim only copies the named
    # source into this test's private directory, after checking every argument.
    _write(shim / "install", '''#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "install $*" >> "$OPERATIONS"
[ "$#" = 5 ] && [ "$1" = -D ] && [ "$2" = -m ] && [ "$3" = 0644 ]
case "$5" in
  /etc/systemd/journald.conf.d/60-talky.conf) cp -- "$4" "$COPIED_POLICY" ;;
  /etc/systemd/system/docker.service.d/60-talky-oom.conf) test -f "$4" ;;
  *) exit 92 ;;
esac
''')
    env = dict(os.environ, OPERATIONS=str(log), COPIED_POLICY=str(copied),
               TALKY_PROD_HOST="nobody@example.invalid", TALKY_PROD_KEY="fake-key")
    result = _launch("exec bash systemd/install-services.sh", env, backend, shim)
    assert result.returncode == 0, result.stdout + result.stderr
    assert copied.is_file(), "Installer never installed the codified journald retention policy"
    config = configparser.ConfigParser()
    config.read(copied, encoding="utf-8")
    assert config["Journal"]["SystemMaxUse"] == "1G"
    assert config["Journal"]["SystemKeepFree"] == "2G"
    assert config["Journal"]["MaxRetentionSec"] == "30day"
    operations = log.read_text().splitlines()
    copy_index = next(i for i, row in enumerate(operations) if row.startswith("install "))
    activation = operations.index("systemctl restart systemd-journald.service")
    assert copy_index < activation < operations.index("systemctl daemon-reload")

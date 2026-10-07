"""Exercise systemctl file operations only in disposable synthetic roots."""
import json
from pathlib import Path
import subprocess
import tempfile

results = []
for retained in (False, True):
    with tempfile.TemporaryDirectory(prefix='talky-unit-link-proof-') as directory:
        root = Path(directory)
        target = root / 'opt/talky/backend/systemd/talky-trunk-status.timer'
        if retained:
            target.parent.mkdir(parents=True)
            target.write_text('[Unit]\nDescription=Retired timer\n[Timer]\nOnBootSec=30\nOnUnitActiveSec=10s\n[Install]\nWantedBy=timers.target\n')
        unit = root / 'etc/systemd/system/talky-trunk-status.timer'
        unit.parent.mkdir(parents=True)
        unit.symlink_to('/opt/talky/backend/systemd/talky-trunk-status.timer')
        wants = root / 'etc/systemd/system/timers.target.wants/talky-trunk-status.timer'
        wants.parent.mkdir()
        wants.symlink_to('/etc/systemd/system/talky-trunk-status.timer')
        result = subprocess.run(['systemctl', '--root=' + directory, 'disable', 'talky-trunk-status.timer'], capture_output=True, text=True)
        results.append({'retained_definition': retained, 'exit_code': result.returncode, 'enabled_link_remains': wants.is_symlink()})
assert results == [
    {'retained_definition': False, 'exit_code': 1, 'enabled_link_remains': True},
    {'retained_definition': True, 'exit_code': 0, 'enabled_link_remains': False}]
print(json.dumps({'scope': 'isolated temporary roots; no live systemd service operation', 'cases': results}))

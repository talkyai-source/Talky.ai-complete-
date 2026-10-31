"""Read-only, value-limited release inventory; do not read environment secrets."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

def run(*args):
    return subprocess.run(args, cwd='/opt/talky', capture_output=True, text=True, timeout=15)

report = {'observed_at': datetime.now(timezone.utc).isoformat(), 'server_sha': run('git', 'rev-parse', 'HEAD').stdout.strip(),
          'git_status': run('git', 'status', '--porcelain').stdout.splitlines(), 'services': {}, 'health': {}}
for service in ('talky-api', 'talky-dialer-worker', 'talky-reminder-worker', 'talky-voice-worker', 'talky-voice-gateway', 'talky-trunk-status', 'talky-trunk-status.timer'):
    report['services'][service] = run('systemctl', 'is-active', service).stdout.strip()
sudo = run('sudo', '-n', 'true')
report['sudo_noninteractive_exit'] = sudo.returncode
report['sudo_requires_password'] = 'password is required' in sudo.stderr
backup = run('systemctl', 'show', 'talky-db-backup.service', '-p', 'Result', '-p', 'ExecMainStatus', '-p', 'InactiveEnterTimestamp')
report['backup_state'] = dict(line.split('=', 1) for line in backup.stdout.splitlines() if '=' in line)
journal = run('journalctl', '-u', 'talky-db-backup.service', '--since', '2 days ago', '--no-pager', '-n', '45')
matches = re.findall(r'dump is (\d+) bytes, previous good was (\d+)', journal.stdout)
report['backup_size_rejections'] = [{'candidate_bytes': int(a), 'previous_bytes': int(b)} for a, b in matches]
for route in ('/health', '/api/v1/healthz/ready', '/api/v1/healthz/deep'):
    try:
        with urlopen('http://127.0.0.1:8000' + route, timeout=10) as response:
            report['health'][route] = response.status
    except HTTPError as exc:
        report['health'][route] = exc.code
    except (URLError, TimeoutError):
        report['health'][route] = 'unavailable'
print(json.dumps(report, indent=2))

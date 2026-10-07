"""Fetch the release Git objects without changing checkout, services or FETCH_HEAD.

Run over the existing SSH connection, passing the pushed full SHA as argv[1].
Only object/ref staging is performed; this is not production activation.
"""
from datetime import datetime, timezone
import json
import re
import subprocess
import sys

candidate = sys.argv[1]
assert re.fullmatch(r'[0-9a-f]{40}', candidate), 'Expected full immutable SHA'
branch = 'codex/production-release-20261008'
remote_ref = 'refs/remotes/origin/' + branch

def git(*args):
    result = subprocess.run(['git', *args], cwd='/opt/talky', capture_output=True,
                            text=True, timeout=90)
    if result.returncode:
        raise RuntimeError('Git operation failed: ' + args[0])
    return result.stdout.strip()

before = git('rev-parse', 'HEAD')
assert not git('status', '--porcelain'), 'Preserve existing server changes'
git('fetch', '--quiet', '--no-write-fetch-head', 'origin',
    'refs/heads/' + branch + ':' + remote_ref)
fetched = git('rev-parse', remote_ref)
assert fetched == candidate, 'Remote candidate changed; do not activate'
git('cat-file', '-e', candidate + '^{commit}')
after = git('rev-parse', 'HEAD')
status = git('status', '--porcelain')
assert before == after and not status, 'Concurrent server change observed'
print(json.dumps({'observed_at': datetime.now(timezone.utc).isoformat(),
    'candidate_sha': candidate, 'fetched_ref': remote_ref,
    'server_head_before': before, 'server_head_after': after,
    'working_tree_clean': not status, 'checkout_changed': False,
    'activation_performed': False, 'services_restarted': False,
    'production_migration_run': False}, indent=2))

"""Run the affected cleanup regression and preserve its exact source inputs."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root = next(p for p in Path(__file__).resolve().parents if (p / 'backend/app').is_dir())
artifact = root / 'docs/sessions/artifacts/agent-cleanup'
artifact.mkdir(parents=True, exist_ok=True)
prior = json.loads((root / 'docs/sessions/artifacts/model-conversation-integration/verification.json').read_text())
changed = subprocess.check_output(['git', 'diff', '--name-only', '458c6798', '--', 'backend'], cwd=root, text=True).splitlines()
tests = {p for p in prior['command'] if p.startswith('tests/') and p.endswith('.py')}
tests.update(p.removeprefix('backend/') for p in changed if p.startswith(('backend/tests/unit/', 'backend/tests/security/')) and p.endswith('.py'))
tests.update(['tests/unit/assistant/test_agent_import.py', 'tests/unit/test_assistant_agent_service.py', 'tests/unit/test_realtime_session_type_0930.py',
              'tests/unit/test_model_contact_recording.py', 'tests/unit/test_ag05_transcript_evidence.py',
              'tests/unit/test_caller_authorized_end_call.py', 'tests/unit/test_realtime_end_call_ownership.py',
              'tests/unit/test_op07_native_dnc_speech.py'])
tests = sorted(p for p in tests if (root / 'backend' / p).is_file())
paths = sorted({p for p in [*changed, *prior['input_sha256_lf'], *['backend/' + p for p in tests]]
                if p.endswith('.py') and (root / p).is_file()})
def hashes():
    return {p: hashlib.sha256((root / p).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in paths}
guard = root / 'docs/sessions/artifacts/agent-dead-code-cleanup/check.py'
before = hashes()
env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', ENVIRONMENT='test',
           DATABASE_URL='postgresql://test:test@127.0.0.1:1/unavailable_test', PYTHONPATH='.')
command = [sys.executable, '-B', str(guard), *tests]
started = time.perf_counter()
result = subprocess.run(command, cwd=root / 'backend', env=env, text=True, encoding='utf-8',
                        errors='replace', stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
elapsed = time.perf_counter() - started
(artifact / 'affected.txt').write_text(result.stdout, encoding='utf-8')
after = hashes()
manifest = {'base': '458c6798', 'tested_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    'command': command, 'module_count': len(tests), 'exit_code': result.returncode,
    'summary': next((line for line in reversed(result.stdout.splitlines()) if ' passed' in line), result.stdout[-1000:]),
    'offline_transport_counts': next((line for line in result.stdout.splitlines() if line.startswith('OFFLINE_TRANSPORT_COUNTS')), None),
    'outer_process_seconds': round(elapsed, 2),
    'guard_sha256_lf': hashlib.sha256(guard.read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
    'source_stable': before == after, 'input_sha256_lf': after,
    'deleted_paths': [p for p in changed if not (root / p).exists()],
    'limits': ['Focused offline regression in existing Python environment; not whole-suite or exact-lock qualification.',
               'No live model, voice, customer, production database, deployment or release acceptance.']}
(artifact / 'verification.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: manifest[k] for k in ('module_count', 'exit_code', 'summary', 'source_stable')}))
if result.returncode:
    print(result.stdout[-16000:])
sys.exit(result.returncode or (0 if before == after else 1))

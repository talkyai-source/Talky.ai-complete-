"""Run the combined residual-repair regression with source and transport evidence."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root = next(p for p in Path(__file__).resolve().parents if (p / 'backend/app').is_dir())
artifact = Path(__file__).resolve().parent
base = '1750341b9425a994da2f43e27b2ecfb2fa7e5f04'
prior = json.loads((root / 'docs/sessions/artifacts/agent-cleanup/verification.json').read_text())
changed = subprocess.check_output(['git', 'diff', '--name-only', base, '--', 'backend'], cwd=root, text=True).splitlines()
tests = {p for p in prior['command'] if p.startswith('tests/') and p.endswith('.py')}
tests.update(p.removeprefix('backend/') for p in changed if p.startswith(('backend/tests/unit/', 'backend/tests/security/')) and p.endswith('.py'))
input_manifest = json.loads((root / 'docs/sessions/artifacts/agent-input-repairs/affected.json').read_text())
tests.update(p.removeprefix('backend/') for p in input_manifest['pytest_argv'] if p.startswith('backend/tests/') and p.endswith('.py'))
tests.update('tests/unit/' + name + '.py' for name in (
    'test_model_contact_recording', 'test_live_call_lead_capture', 'test_lead_capture_service',
    'test_ag05_native_contact_revision', 'test_ag05_contact_projection', 'test_contact_capture_state_machine',
    'test_contact_capture_best_practice', 'test_prompt_builder', 'test_email_confirmation',
    'test_phone_confirmation', 'test_voice_action_contract', 'test_knowledge_model_sections',
    'test_gemini_tools', 'test_model_driven_voice_turn', 'test_assistant_knowledge_authorization',
    'test_live_structured_state', 'test_ask_ai_prompt', 'test_telephony_session_config'))
tests = sorted(p for p in tests if (root / 'backend' / p).is_file())
paths = sorted({p for p in [*changed, *prior['input_sha256_lf'], *['backend/' + p for p in tests]]
                if p.endswith('.py') and (root / p).is_file()})

def hashes():
    return {p: hashlib.sha256((root / p).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in paths}

guard = root / 'docs/sessions/artifacts/agent-dead-code-cleanup/check.py'
before = hashes()
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', ENVIRONMENT='test',
           DATABASE_URL='postgresql://test:test@127.0.0.1:1/unavailable_test', PYTHONPATH='.')
command = [sys.executable, '-B', str(guard), *tests]
started = time.perf_counter()
result = subprocess.run(command, cwd=root / 'backend', env=env, text=True, encoding='utf-8',
                        errors='replace', stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
elapsed = time.perf_counter() - started
(artifact / 'affected.txt').write_text(result.stdout, encoding='utf-8')
after = hashes()
manifest = {'base': base, 'tested_head': head,
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
print(json.dumps({k: manifest[k] for k in ('module_count', 'exit_code', 'summary', 'source_stable', 'offline_transport_counts')}))
if result.returncode:
    print(result.stdout[-16000:])
sys.exit(result.returncode or (0 if before == after else 1))

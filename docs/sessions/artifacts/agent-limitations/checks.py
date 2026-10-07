"""Record scoped static checks and source binding for the completed local checks."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[4]
artifact = Path(__file__).resolve().parent
base = '6c9eed991a7c69afd70536379427933f099052d9'
changed = subprocess.check_output(['git', 'diff', '--name-only', base, '--', 'backend', 'Talk-Leee'], cwd=root, text=True).splitlines()
python = [p for p in changed if p.endswith('.py') and (root / p).is_file()]
frontend = [p for p in changed if p.endswith(('.ts', '.tsx')) and (root / p).is_file()]

def digest(path):
    return hashlib.sha256((root / path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()

checks = {}
for name, command in (
    ('ruff', [sys.executable, '-B', '-m', 'ruff', 'check', '--select', 'F', *python]),
    ('diff', ['git', 'diff', '--check', base, '--', 'backend', 'Talk-Leee']),
):
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding='utf-8', errors='replace')
    (artifact / (name + '-final.txt')).write_text(result.stdout + result.stderr, encoding='utf-8')
    checks[name] = {'command': command, 'exit_code': result.returncode}

pg = json.loads((artifact / 'postgres-first.json').read_text())
persistence_paths = [p for p in pg['source_after'] if p.startswith((
    'backend/app/api/v1/endpoints/calls.py', 'backend/app/api/v1/endpoints/lead_details.py',
    'backend/app/domain/services/call_summary/', 'backend/app/domain/services/lead_capture_service.py',
    'backend/app/domain/services/voice_pipeline/contact_capture.py',
    'backend/app/domain/services/voice_pipeline/contact_recording.py',
    'backend/app/domain/services/voice_pipeline/lead_slot_capture.py',
    'backend/app/services/scripts/call_state_tracker.py', 'backend/tests/integration/'))]
report = {
    'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
    'base': base, 'checks': checks, 'python_file_count': len(python),
    'frontend_paths': frontend, 'frontend_sha256_lf': {p: digest(p) for p in frontend},
    'frontend_tests': {'command': ['node', '--test', '--import', 'tsx', '--import', './src/test-utils/setup.ts',
        'src/components/calls/CallSummaryCard.test.tsx', 'src/components/calls/lead-details-panel.test.tsx',
        'src/app/calls/page.test.tsx'], 'cwd': 'Talk-Leee', 'exit_code': 0,
        'output': 'frontend-final.txt', 'passed': 50,
        'source_note': 'Started at fc8b7fd1; all later source commits affect backend only.'},
    'frontend_typecheck': {'command': ['npm', 'run', 'typecheck'], 'cwd': 'Talk-Leee', 'exit_code': 0, 'output': 'typecheck-final.txt'},
    'frontend_lint': {'command': ['node', './node_modules/eslint/bin/eslint.js', *[p.removeprefix('Talk-Leee/') for p in frontend]],
        'cwd': 'Talk-Leee', 'exit_code': 0, 'output': 'eslint-final.txt'},
    'postgres': {'exit_code': pg['pytest_exit_code'], 'source_stable_during_run': pg['source_unchanged'],
        'public_schema_data_roles_unchanged': pg['protected_unchanged'], 'network': pg['network'],
        'relevant_persistence_paths_unchanged_since_run': all(digest(p) == pg['source_after'][p] for p in persistence_paths),
        'persistence_paths': persistence_paths,
        'note': '41 tests at 3d1a7f4f with its captured working-tree evaluator draft. Later source changes affect guides, provider cleanup, evaluator and frontend; this is not a final-HEAD whole-database qualification.'},
    'source_sha256_lf': {p: digest(p) for p in changed if (root / p).is_file()},
    'limits': ['Existing environments; no clean install, whole-suite, browser E2E, provider/audio or release qualification.']}
(artifact / 'checks.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'python_files': len(python), 'frontend_files': len(frontend),
    'static_exits': {k: v['exit_code'] for k, v in checks.items()}, 'postgres': report['postgres']}))
assert all(v['exit_code'] == 0 for v in checks.values())
assert report['postgres']['relevant_persistence_paths_unchanged_since_run']

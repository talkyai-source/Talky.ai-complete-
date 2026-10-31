"""Exact bounded integration: archived location assumes tmp/ when executing."""
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading

root = Path(__file__).resolve().parents[1]
folder = root / 'docs/sessions/artifacts/mail-voice-integration'
folder.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(root / 'backend'))
local = threading.local()
original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex
original_socketpair = socket.socketpair
connections = {'internal_socketpairs': 0, 'prohibited_attempts': 0}


def guarded_connect(sock, address, *, original):
    if getattr(local, 'socketpair', False) and address[0] in {'127.0.0.1', '::1'}:
        return original(sock, address)
    connections['prohibited_attempts'] += 1
    raise AssertionError('No database or provider network connection is authorized for this unit run')


def guarded_socketpair(*args, **kwargs):
    local.socketpair = True
    try:
        pair = original_socketpair(*args, **kwargs)
        connections['internal_socketpairs'] += 1
        return pair
    finally:
        local.socketpair = False


socket.socket.connect = lambda sock, addr: guarded_connect(sock, addr, original=original_connect)
socket.socket.connect_ex = lambda sock, addr: guarded_connect(sock, addr, original=original_connect_ex)
socket.socketpair = guarded_socketpair

import pytest  # noqa: E402

modules = [
    'tests/unit/test_crm_sync_service.py', 'tests/unit/test_ag06_crm_evidence.py',
    'tests/unit/test_crm_sync_hooks.py', 'tests/unit/test_admin_crm_inspection.py',
    'tests/unit/test_reviewed_selector_identity_types.py', 'tests/unit/test_reviewed_account_identity.py',
    'tests/unit/test_inbox_original_account.py', 'tests/unit/test_inbox_authorization_health.py',
    'tests/unit/test_admin_gmail_inspection.py', 'tests/unit/test_voice_email_receipt_proof.py',
    'tests/unit/test_voice_email_inspection_handoff.py',
]
paths = [
    'backend/app/services/crm_sync_service.py', 'backend/app/services/connector_resolver.py',
    'backend/app/domain/services/voice_pipeline/action_execution.py',
    'backend/app/api/v1/endpoints/admin/actions.py', 'backend/app/services/action_execution.py',
    'backend/app/services/email_service.py', 'backend/app/services/meeting_service.py',
    'backend/app/api/v1/endpoints/admin/calls.py',
    'backend/app/infrastructure/connectors/crm/base.py',
    'backend/app/infrastructure/connectors/crm/hubspot.py',
    'backend/app/infrastructure/connectors/crm/salesforce.py',
    *['backend/' + name for name in modules],
]


def hashes():
    return {path: hashlib.sha256((root / path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for path in paths}


before = hashes()
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
argv = [*modules, '-q', '--tb=short', '--disable-warnings']
result = int(pytest.main(argv))
after = hashes()
record = {
    'source_base_head': head, 'source_status': 'Frozen application source plus new uncommitted four-control handoff test; hashes identify executed files', 'cwd': str(Path.cwd()), 'python': sys.executable,
    'pytest_argv': argv, 'source_hashes_before': before, 'source_hashes_after': after,
    'source_unchanged': before == after, 'exit_code': result, **connections,
    'limits': 'Eleven affected synthetic-port unit modules, no live provider, database or browser acceptance.',
}
(folder / 'result.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
assert before == after and connections['prohibited_attempts'] == 0
raise SystemExit(result)

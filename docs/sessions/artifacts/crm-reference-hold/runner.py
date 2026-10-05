import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'docs/sessions/artifacts/crm-reference-hold'
ARTIFACTS.mkdir(parents=True, exist_ok=True)
phase, *pytest_args = sys.argv[1:]
sys.path.insert(0, str(ROOT / 'backend'))
network_attempts = []
internal_socketpairs = []
local = threading.local()
original_connect = socket.socket.connect
original_connect_ex = socket.socket.connect_ex
original_socketpair = socket.socketpair


def guarded_connect(sock, address, *, original):
    # Windows asyncio uses TCP only to implement its private socketpair.
    if getattr(local, 'socketpair', False) and address[0] in {'127.0.0.1', '::1'}:
        return original(sock, address)
    network_attempts.append('blocked socket connection')
    raise AssertionError('External/network socket connections are prohibited in this scoped test')


def guarded_socketpair(*args, **kwargs):
    local.socketpair = True
    try:
        pair = original_socketpair(*args, **kwargs)
        internal_socketpairs.append('Windows event-loop internal socketpair')
        return pair
    finally:
        local.socketpair = False


socket.socket.connect = lambda sock, addr: guarded_connect(sock, addr, original=original_connect)
socket.socket.connect_ex = lambda sock, addr: guarded_connect(sock, addr, original=original_connect_ex)
socket.socketpair = guarded_socketpair
import pytest  # noqa: E402

paths = [
    'backend/app/services/crm_sync_service.py',
    'backend/tests/unit/test_crm_sync_service.py',
    'backend/tests/unit/test_ag06_crm_evidence.py',
    'backend/tests/unit/test_crm_sync_hooks.py',
]


def hashes():
    return {path: hashlib.sha256((ROOT / path).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for path in paths}


before = hashes()
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
result = int(pytest.main(pytest_args))
after = hashes()
record = {
    'phase': phase, 'source_head': head, 'cwd': str(Path.cwd()),
    'python': sys.executable, 'pytest_argv': pytest_args,
    'source_hashes_before': before, 'source_hashes_after': after,
    'source_unchanged': before == after, 'exit_code': result,
    'network_attempts': len(network_attempts),
    'allowed_internal_socketpairs': len(internal_socketpairs),
    'limits': 'Actual CRM service with synthetic resolver, persistence and provider seams; no PostgreSQL, HTTP provider, browser or deployment acceptance.',
}
(ARTIFACTS / (phase + '.json')).write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
if before != after or network_attempts:
    raise AssertionError('Source changed or network attempted during verification')
raise SystemExit(result)

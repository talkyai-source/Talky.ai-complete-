"""Bounded offline passage verification; invoke from backend with a new phase."""
import asyncio
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'docs/sessions/artifacts/ag02-required-conditions'
phase = sys.argv[1]
assert phase in {'baseline', 'final'}
OUTPUT.mkdir(parents=True, exist_ok=True)
destination = OUTPUT / (phase + '.json')
assert not destination.exists()
sys.path.insert(0, str(ROOT / 'backend'))
modules = ['test_knowledge_required_conditions.py']
if phase == 'final':
    modules += ['test_kb_evidence_contract.py', 'test_knowledge_evidence_budget_order.py',
                'test_agent_knowledge_repairs.py', 'test_knowledge_budget.py',
                'test_kb_source_first_all_paths.py', 'test_realtime_knowledge_evidence.py',
                'test_knowledge_uncertainty_contract.py', 'test_knowledge_relevance.py',
                'test_knowledge_render.py']
paths = ['backend/app/services/scripts/knowledge/passages.py',
         'backend/app/services/scripts/knowledge/retrieval.py',
         'backend/app/domain/services/voice_pipeline/kb_budget.py',
         'backend/app/domain/services/voice_pipeline/knowledge_tool.py',
         'backend/app/domain/services/voice_pipeline/turn_streamer.py',
         'backend/app/realtime/bridge.py',
         *['backend/tests/unit/' + name for name in modules]]
def hashes():
    return {p: hashlib.sha256((ROOT / p).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in paths}
before = hashes()
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
local = threading.local()
connect, connect_ex, pair = socket.socket.connect, socket.socket.connect_ex, socket.socketpair
network = {'internal_socketpairs': 0, 'prohibited_socket_attempts': 0, 'prohibited_transport_attempts': 0}
def guarded_connect(sock, address, original):
    if getattr(local, 'pair', False) and address[0] in {'127.0.0.1', '::1'}:
        return original(sock, address)
    network['prohibited_socket_attempts'] += 1
    raise AssertionError('External connections prohibited during passage unit checks')
def guarded_pair(*args, **kwargs):
    local.pair = True
    try:
        result = pair(*args, **kwargs)
        network['internal_socketpairs'] += 1
        return result
    finally:
        local.pair = False
socket.socket.connect = lambda sock, address: guarded_connect(sock, address, connect)
socket.socket.connect_ex = lambda sock, address: guarded_connect(sock, address, connect_ex)
socket.socketpair = guarded_pair
async def denied_transport(*args, **kwargs):
    network['prohibited_transport_attempts'] += 1
    raise AssertionError('External async transports prohibited during passage unit checks')
asyncio.BaseEventLoop.create_connection = denied_transport
import pytest
argv = ['tests/unit/' + name for name in modules] + ['-q', '--tb=short', '--disable-warnings', '-o', 'addopts=']
code = int(pytest.main(argv))
after = hashes()
destination.write_text(json.dumps({'phase': phase, 'base_head': head, 'python': sys.executable,
    'cwd': str(Path.cwd()), 'pytest_argv': argv, 'source_before': before, 'source_after': after,
    'source_unchanged': before == after, 'exit_code': code, 'network': network,
    'limits': 'Offline actual application methods with synthetic ports; no provider, database or customer acceptance.'}, indent=2) + '\n', encoding='utf-8')
assert before == after and network['prohibited_socket_attempts'] == network['prohibited_transport_attempts'] == 0
raise SystemExit(code)

"""Offline integrated query-tool checks; preserve each labelled run."""
import asyncio
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading

ROOT = next(parent for parent in Path(__file__).resolve().parents
            if (parent / 'backend/app').is_dir() and (parent / '.git').exists())
OUT = ROOT / 'docs/sessions/artifacts/ag02-query-integration'
phase = sys.argv[1]
assert phase in {'prompt', 'combined', 'final'}
OUT.mkdir(parents=True, exist_ok=True)
destination = OUT / (phase + '.json')
assert not destination.exists(), 'Preserve prior evidence'
sys.path.insert(0, str(ROOT / 'backend'))
modules = ['test_knowledge_tool.py', 'test_knowledge_uncertainty_contract.py',
           'test_llm_tool_continuation.py', 'test_gemini_tools.py']
if phase != 'prompt':
    modules += sys.argv[2:]
paths = ['backend/app/services/scripts/knowledge/retrieval.py',
         'backend/app/services/scripts/knowledge/passages.py',
         'backend/app/domain/services/voice_pipeline/kb_budget.py',
         'backend/app/domain/services/voice_pipeline/knowledge_tool.py',
         'backend/app/domain/services/voice_pipeline/turn_streamer.py',
         'backend/app/infrastructure/llm/streaming.py',
         'backend/app/infrastructure/llm/groq.py',
         'backend/app/infrastructure/llm/gemini.py',
         'backend/app/realtime/prompts.py', 'backend/app/realtime/openai.py',
         'backend/app/realtime/bridge.py',
         'backend/tests/fixtures/knowledge/ag02_gold.json',
         'backend/scripts/evaluate_ag02_knowledge.py',
         *['backend/tests/unit/' + m for m in modules]]
if phase != 'prompt':
    paths += [line for line in subprocess.check_output(
        ['git', 'diff', '--name-only', '76ba91ea', 'HEAD', '--', 'backend/scripts',
         'backend/tests/fixtures/knowledge'], cwd=ROOT, text=True).splitlines()
        if line not in paths]

def hashes():
    return {p: hashlib.sha256((ROOT / p).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for p in paths}

head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
before = hashes()
for path, expected in before.items():
    value = subprocess.check_output(['git', 'show', head + ':' + path], cwd=ROOT)
    assert hashlib.sha256(value.replace(b'\r\n', b'\n')).hexdigest() == expected, path
original_connect, original_ex, original_pair = socket.socket.connect, socket.socket.connect_ex, socket.socketpair
local = threading.local()
network = {'internal_socketpairs': 0, 'prohibited_socket_attempts': 0, 'prohibited_transport_attempts': 0}

def connect(sock, address, original):
    if getattr(local, 'pair', False) and address[0] in {'127.0.0.1', '::1'}:
        return original(sock, address)
    network['prohibited_socket_attempts'] += 1
    raise AssertionError('External connections prohibited in offline query verification')

def pair(*args, **kwargs):
    local.pair = True
    try:
        result = original_pair(*args, **kwargs)
        network['internal_socketpairs'] += 1
        return result
    finally:
        local.pair = False

socket.socket.connect = lambda sock, address: connect(sock, address, original_connect)
socket.socket.connect_ex = lambda sock, address: connect(sock, address, original_ex)
socket.socketpair = pair

async def deny_transport(*args, **kwargs):
    network['prohibited_transport_attempts'] += 1
    raise AssertionError('External async transports prohibited in offline query verification')

asyncio.BaseEventLoop.create_connection = deny_transport
import pytest
argv = ['tests/unit/' + m for m in modules] + ['-q', '--tb=short', '--disable-warnings', '-o', 'addopts=']
code = int(pytest.main(argv))
from scripts.evaluate_ag02_knowledge import load_matrix, effective_query, evaluate_case, summarize
from app.services.scripts.knowledge.retrieval import retrieve_pinned_knowledge
matrix = load_matrix()
cases = [evaluate_case(case, retrieve_pinned_knowledge(matrix['nodes'], effective_query(case), k=3)) for case in matrix['cases']]
gold = summarize(matrix, cases, path='production raw pinned retrieval; independent of model-authored tool queries')
(OUT / (phase + '-gold.json')).write_text(json.dumps(gold, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
historical = json.loads((ROOT / 'docs/sessions/artifacts/ag02/gold-pinned-current.json').read_text(encoding='utf-8'))
after = hashes()
report = {'head': head, 'python': sys.executable, 'cwd': str(Path.cwd()), 'pytest_argv': argv,
    'source_before': before, 'source_after': after, 'source_unchanged': before == after,
    'all_inputs_committed_before_run': True, 'pytest_exit_code': code, 'network': network,
    'gold_gate_exit_code': 0 if gold['retrieval_targets_met'] else 1,
    'gold_cases_unchanged_from_preserved_baseline': cases == historical['cases'],
    'gold_recall_at_3': gold['recall_at_3'], 'gold_sufficient_passage_rate': gold['sufficient_passage_rate'],
    'limits': 'Offline checks in the existing venv, not exact-requirements verification. No provider answer, DB, audio or customer acceptance.'}
destination.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: report[k] for k in ['pytest_exit_code', 'gold_gate_exit_code',
    'gold_cases_unchanged_from_preserved_baseline', 'gold_recall_at_3', 'gold_sufficient_passage_rate']}))
assert before == after and network['prohibited_socket_attempts'] == network['prohibited_transport_attempts'] == 0
raise SystemExit(code)

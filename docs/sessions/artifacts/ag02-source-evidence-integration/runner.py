"""Combined offline AG02 regression and unchanged raw-source quality gate."""
import asyncio
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/sessions/artifacts/ag02-source-evidence-integration'
assert not OUT.exists(), 'Preserve prior evidence'
OUT.mkdir(parents=True)
sys.path.insert(0, str(ROOT / 'backend'))
modules = ['test_knowledge_required_conditions.py', 'test_knowledge_authored_coverage.py',
    'test_campaign_knowledge_staged_http.py', 'test_kb_evidence_contract.py',
    'test_knowledge_evidence_budget_order.py', 'test_agent_knowledge_repairs.py',
    'test_knowledge_uncertainty_contract.py', 'test_realtime_knowledge_evidence.py',
    'test_knowledge_relevance.py', 'test_knowledge_render.py',
    'test_knowledge_enrichment_routing.py', 'test_knowledge_write_invalidation.py',
    'test_kb_source_first_all_paths.py']
paths = ['backend/app/services/scripts/knowledge/' + n for n in
    ['retrieval.py', 'passages.py', 'enricher.py', 'ingest_service.py', 'node_updates.py']]
paths += ['backend/app/api/v1/endpoints/campaign_knowledge.py',
    'backend/app/domain/services/voice_pipeline/kb_budget.py',
    'backend/app/domain/services/voice_pipeline/knowledge_tool.py',
    'backend/app/domain/services/voice_pipeline/turn_streamer.py', 'backend/app/realtime/bridge.py',
    'backend/scripts/evaluate_ag02_knowledge.py', 'backend/tests/fixtures/knowledge/ag02_gold.json',
    *['backend/tests/unit/' + m for m in modules]]
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
    raise AssertionError('No DB/provider connection is authorized for this combined offline run')
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
    raise AssertionError('No DB/provider async transport authorized')
asyncio.BaseEventLoop.create_connection = deny_transport
import pytest
argv = ['tests/unit/' + m for m in modules] + ['-q', '--tb=short', '--disable-warnings', '-o', 'addopts=']
code = int(pytest.main(argv))
from scripts.evaluate_ag02_knowledge import load_matrix, effective_query, evaluate_case, summarize
from app.services.scripts.knowledge.retrieval import retrieve_pinned_knowledge
matrix = load_matrix()
cases = [evaluate_case(case, retrieve_pinned_knowledge(matrix['nodes'], effective_query(case), k=3)) for case in matrix['cases']]
gold = summarize(matrix, cases, path='integrated production pinned retrieval + shared evidence')
(OUT / 'gold.json').write_text(json.dumps(gold, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
historical = json.loads((ROOT / 'docs/sessions/artifacts/ag02/gold-pinned-current.json').read_text(encoding='utf-8'))
after = hashes()
report = {'head': head, 'python': sys.executable, 'cwd': str(Path.cwd()), 'pytest_argv': argv,
    'source_before': before, 'source_after': after, 'source_unchanged': before == after,
    'all_inputs_committed_before_run': True, 'pytest_exit_code': code, 'network': network,
    'gold_gate_exit_code': 0 if gold['retrieval_targets_met'] else 1,
    'gold_cases_unchanged_from_preserved_baseline': cases == historical['cases'],
    'gold_recall_at_3': gold['recall_at_3'], 'gold_sufficient_passage_rate': gold['sufficient_passage_rate'],
    'limits': 'Offline integration only; quality gate is reported separately from unit results. No DB/provider/browser/customer acceptance.'}
(OUT / 'result.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: report[k] for k in ['pytest_exit_code', 'gold_gate_exit_code',
    'gold_cases_unchanged_from_preserved_baseline', 'gold_recall_at_3', 'gold_sufficient_passage_rate']}))
assert before == after and network['prohibited_socket_attempts'] == network['prohibited_transport_attempts'] == 0
raise SystemExit(code)

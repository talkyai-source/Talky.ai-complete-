#!/usr/bin/env python3
"""Exercise releases ONLY inside the explicitly marked disposable installer VM.

Uses the actual fresh_release.py CLI and synthetic database supplied by the
owning outer harness. Synthetic approval records are never owner attestation.
The outer harness owns reboots, VM/database cleanup and final acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from urllib.parse import parse_qsl, quote, unquote, urlsplit
import uuid

SOURCE = Path('/root/update-source')
CURRENT = Path('/opt/talky')
INPUTS = Path('/root/talklee-rehearsal-inputs')
MARKER = Path('/etc/talklee-disposable-installer-vm')
STATE = Path('/var/lib/talklee-release-rehearsal')
RECEIPT = STATE / 'receipt.json'
SERVICE_FILE = INPUTS / 'release.pg_service.conf'
PASSFILE = INPUTS / 'release.pgpass'
DOMAINS = ('api.installer.test', 'app.installer.test', 'admin.installer.test')
APP_UNITS = ('talky-api', 'talky-voice-gateway', 'talky-voice-worker',
             'talky-dialer-worker', 'talky-reminder-worker', 'talky-dashboard', 'talky-trunk-status')
VERSION_UNITS = ('talky-api', 'talky-voice-worker', 'talky-dialer-worker', 'talky-reminder-worker')
SHA = re.compile(r'[a-f0-9]{40}\Z')
ENV = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
       'HOME': '/root', 'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1',
       'PYTHON_DOTENV_DISABLED': '1', 'GIT_TERMINAL_PROMPT': '0'}
DIAGNOSTICS_ALLOWED = False
LAST_CHILD_DIAGNOSTIC = None


class RehearsalError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise RehearsalError(message)


def protected(path, *, directory=False):
    value = path.lstat()
    require((stat.S_ISDIR(value.st_mode) if directory else stat.S_ISREG(value.st_mode))
            and value.st_uid == 0 and stat.S_IMODE(value.st_mode) & 0o077 == 0,
            'Fixture inputs/state must be private root-owned paths without symlinks')


def child_diagnostic(binary, return_code, expected, stdout=b'', stderr=b'', *, timed_out=False):
    global LAST_CHILD_DIAGNOSTIC
    name = Path(binary).name
    if name not in {'git', 'python', 'python3', 'python3.12', 'systemctl', 'systemd-detect-virt', 'curl', 'ufw'}:
        name = 'child'
    LAST_CHILD_DIAGNOSTIC = {'binary': name, 'return_code': return_code,
                             'expected_return_code': expected, 'timed_out': timed_out}
    if DIAGNOSTICS_ALLOWED:
        protected(STATE, directory=True)
        path = STATE / ('child-failure-' + uuid.uuid4().hex + '.json')
        private_new(path, json.dumps({**LAST_CHILD_DIAGNOSTIC,
            'stdout_tail': (stdout or b'')[-8192:].decode('utf-8', errors='replace'),
            'stderr_tail': (stderr or b'')[-8192:].decode('utf-8', errors='replace')}) + '\n')
        LAST_CHILD_DIAGNOSTIC['private_diagnostic'] = path.name


def command_result(arguments, *, cwd=None, input=None, timeout=180, expected=0):
    try:
        result = subprocess.run([str(value) for value in arguments], cwd=cwd, env=ENV,
                                input=input, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        child_diagnostic(arguments[0], None, expected, error.stdout, error.stderr, timed_out=True)
        raise RehearsalError('Rehearsal child exceeded its time limit') from None
    if result.returncode != 0:
        child_diagnostic(arguments[0], result.returncode, expected, result.stdout, result.stderr)
    require(result.returncode == expected, 'Rehearsal child returned an unexpected exit code')
    return result


def command(arguments, **options):
    result = command_result(arguments, **options)
    return result.stdout


def git(path, *arguments):
    return command(['git', '-C', path, *arguments]).decode().strip()


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private_new(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def write_receipt(receipt):
    temporary = STATE / ('.receipt-' + uuid.uuid4().hex)
    private_new(temporary, json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    os.replace(temporary, RECEIPT)
    descriptor = os.open(STATE, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def guard(candidate):
    require(sys.platform == 'linux' and os.geteuid() == 0, 'Disposable Linux root VM required')
    require(SHA.fullmatch(candidate), 'Full candidate SHA required')
    protected(MARKER)
    protected(INPUTS, directory=True)
    protected(INPUTS / 'fixture.json')
    payload = json.loads((INPUTS / 'fixture.json').read_text())
    token = payload.get('token', '')
    require(re.fullmatch(r'[a-f0-9]{32}', token) and MARKER.read_text().strip() == token,
            'Disposable fixture marker does not match')
    require(command(['systemd-detect-virt', '--vm']).decode().strip() in ('qemu', 'kvm'),
            'Only the owned QEMU/KVM guest is supported')
    for path in (SOURCE, SOURCE / '.git'):
        metadata = path.lstat()
        require(stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == 0
                and not stat.S_IMODE(metadata.st_mode) & 0o022,
                'Reviewed source must have protected root-owned directories')
    require(SOURCE.resolve() == SOURCE and git(SOURCE, 'rev-parse', 'HEAD') == candidate
            and not git(SOURCE, 'status', '--porcelain', '--untracked-files=all'),
            'Update source must be the clean exact supplied candidate')
    for path in (SERVICE_FILE, PASSFILE):
        protected(path)
    endpoints = []
    for key, role in (('runtime_url', 'fixture_runtime'), ('owner_url', 'fixture_migration')):
        parsed = urlsplit(payload[key])
        options = parse_qsl(parsed.query, keep_blank_values=True)
        require(parsed.scheme == 'postgresql' and parsed.hostname and parsed.port == 5432
                and unquote(parsed.username or '') == role and parsed.password
                and parsed.path == '/talky_synthetic_source' and not parsed.fragment
                and len(options) == 2 and dict(options) == {
                    'sslmode': 'verify-full', 'sslrootcert': '/etc/ssl/certs/talky-db-ca.pem'},
                'Only explicit verified-TLS synthetic fixture database URLs are accepted')
        endpoints.append((parsed.hostname, parsed.port, parsed.path))
    require(endpoints[0] == endpoints[1], 'Fixture runtime and owner endpoints differ')
    release = module('guest_fresh_release', SOURCE / 'scripts/installation/fresh_release.py')
    profile = release.install.read_env(CURRENT / 'backend/.env')
    require(profile.get('DATABASE_URL') == payload['runtime_url']
            and profile.get('TELEPHONY_ENABLED') == 'false'
            and profile.get('STRIPE_BILLING_DISABLED') == '1'
            and profile.get('SMTP_HOST') == '127.0.0.1' and profile.get('SMTP_PORT') == '9'
            and not profile.get('STRIPE_SECRET_KEY') and not profile.get('STRIPE_WEBHOOK_SECRET')
            and all(profile.get(key, '').startswith('synthetic-disabled-')
                    for key in ('GROQ_API_KEY', 'DEEPGRAM_API_KEY', 'CARTESIA_API_KEY')),
            'Rehearsal requires the original disabled synthetic provider profile')
    recovery = module('guest_release_recovery', SOURCE / 'scripts/database/recovery.py')
    import configparser
    config = configparser.ConfigParser(interpolation=None)
    config.read(SERVICE_FILE)
    require(not config.defaults() and set(config.sections()) == {
        'source_runtime', 'source_migration', 'source_backup'}, 'Unexpected fixture service sections')
    for name, role in (('source_runtime', 'fixture_runtime'), ('source_migration', 'fixture_migration'),
                       ('source_backup', 'fixture_backup')):
        service = recovery.Service(SERVICE_FILE, name)
        require(service.values['user'] == role and service.values['dbname'] == 'talky_synthetic_source'
                and service.values['host'] == endpoints[0][0] and service.values['port'] == '5432'
                and service.passfile == PASSFILE, 'Fixture service escaped the owned database profile')
        require(service.values.get('sslrootcert') == '/etc/ssl/certs/talky-db-ca.pem', 'Fixture CA differs')
        require('hostaddr' not in service.values, 'Fixture service cannot override the selected host')
    return payload, release, recovery


DATABASE_SCRIPT = r'''
import hashlib,json,sys,psycopg2
p=json.load(sys.stdin)
with psycopg2.connect(p['owner_url'],connect_timeout=10) as connection:
 with connection.cursor() as cursor:
  cursor.execute("SELECT current_database(),session_user,current_user,(SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication FROM pg_roles WHERE rolname=current_user")
  row=cursor.fetchone()
  assert row==('talky_synthetic_source','fixture_migration','fixture_migration',True,False,False,False,False,False)
  cursor.execute("SET LOCAL statement_timeout='10000'; SET LOCAL lock_timeout='5000'")
  cursor.execute("SELECT set_config('app.bypass_rls','true',true)")
  if p['freeze']:
   cursor.execute("UPDATE public.platform_runtime_controls SET outbound_calls_paused=TRUE,inbound_enabled=FALSE,pause_reason='synthetic disposable VM release rehearsal',updated_at=now() WHERE id=1")
   assert cursor.rowcount==1
  cursor.execute("SELECT outbound_calls_paused,inbound_enabled FROM public.platform_runtime_controls WHERE id=1")
  assert cursor.fetchone()==(True,False)
  cursor.execute("SELECT version_num FROM public.alembic_version ORDER BY version_num")
  heads=[r[0] for r in cursor.fetchall()]
  business={}
  for table in ('tenants','campaigns','leads','calls'):
   cursor.execute("SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY id),'[]'::jsonb)::text FROM public."+table+" t")
   value=cursor.fetchone()[0]
   rows=len(json.loads(value))
   assert rows>=2
   business[table]={'rows':rows,'sha256':hashlib.sha256(value.encode()).hexdigest()}
  print(json.dumps({'database':'talky_synthetic_source','tls':True,'owner_role':'fixture_migration','outbound_paused':True,'inbound_enabled':False,'heads':heads,'synthetic_business_records':business}))
'''


def fixture_database(payload, *, freeze=False):
    return json.loads(command([CURRENT / 'backend/venv/bin/python', '-B', '-c', DATABASE_SCRIPT],
        input=json.dumps({'owner_url': payload['owner_url'], 'freeze': freeze}).encode()))


def freeze_ingress():
    config = json.loads((INPUTS / 'prepared/install.json').read_text())
    require(tuple(config[key] for key in ('api_domain', 'dashboard_domain', 'admin_domain')) == DOMAINS,
            'Only the synthetic installer virtual hosts are supported')
    sip, start, end = (config[key] for key in ('sip_port', 'rtp_port_start', 'rtp_port_end'))
    require(all(type(port) is int and 1024 <= port <= 65535 for port in (sip, start, end))
            and start <= end and sip not in (22, 80, 443), 'Unexpected fixture media port range')
    for protocol, port in (('udp', str(sip)), ('tcp', str(sip)), ('udp', f'{start}:{end}')):
        command(['ufw', 'insert', '1', 'deny', 'in', 'proto', protocol, 'to', 'any', 'port', port])
    require(b'Status: active' in command(['ufw', 'status']), 'Fixture firewall is not active')
    return {'sip_rtp_ingress_denied': True, 'ufw_sha256': digest(Path('/etc/ufw/user.rules')),
            'ufw6_sha256': digest(Path('/etc/ufw/user6.rules'))}


def manifest(release, sha, label):
    observation = release.observe(CURRENT)
    stamp = datetime.now(timezone.utc)
    value = {'schema_version': 2, 'manifest_id': 'synthetic-vm-' + uuid.uuid4().hex,
        'candidate_sha': sha, 'environment': 'production',
        'issued_at': stamp.isoformat().replace('+00:00', 'Z'),
        'expires_at': (stamp + timedelta(minutes=20)).isoformat().replace('+00:00', 'Z'),
        'approval_mode': 'single-owner', 'owner_authorization_ref': 'synthetic-ci-fixture:not-owner-attestation',
        'traffic': {'ingress_disabled': True, 'outbound_origination_disabled': True,
                    'active_counts': {key: item['count'] for key, item in observation['observations'].items()}},
        'evidence': {'topology_ref': 'synthetic-ci-fixture:isolated-qemu-no-carrier',
                     'change_ref': 'synthetic-ci-fixture:' + label + ':' + sha},
        'approvers': [{'principal': 'synthetic-ci-fixture@installer.test', 'role': 'platform-owner',
                       'approval_ref': 'synthetic-ci-fixture:no-human-approval:' + label}]}
    path = STATE / (label + '-manifest.json')
    private_new(path, json.dumps(value, sort_keys=True) + '\n')
    return path, digest(path), observation


def cli(source, *arguments, expected=0, timeout=3600):
    return command(['/usr/bin/python3', '-B', SOURCE / 'scripts/installation/fresh_release.py', *arguments],
                   cwd=source, expected=expected, timeout=timeout)


def prepare(source, sha, previous):
    cli(source, 'prepare', '--source', source, '--sha', sha, '--expected-current-sha', previous,
        '--domains', *DOMAINS)


def activate(release, recovery, sha, label, *, expected=0, migration_failure=False, forward_migration=False):
    before = set(Path('/var/lib/talky-release/transactions').glob('*/journal.json'))
    path, checksum, observation = manifest(release, sha, label)
    schema_change = migration_failure or forward_migration
    extra = ['--allow-forward-migration'] if schema_change else []
    cli(SOURCE, 'activate', '--sha', sha, '--manifest', path, '--manifest-sha256', checksum,
        '--service-file', SERVICE_FILE, '--runtime-service', 'source_runtime',
        '--owner-service', 'source_migration', '--backup-service', 'source_backup', *extra, expected=expected)
    created = set(Path('/var/lib/talky-release/transactions').glob('*/journal.json')) - before
    require(len(created) == 1, 'Expected exactly one real release transaction')
    journal_path = created.pop()
    protected(journal_path)
    journal = json.loads(journal_path.read_text())
    require(journal['candidate_sha'] == sha and journal['migration_started'] is migration_failure
            and (journal['old_heads'] != journal['new_heads']) is schema_change and not journal['traffic_reopened'],
            'Release transaction did not preserve the expected schema/freeze boundary')
    wanted_status = ('failed_migration_started' if migration_failure else
                     'activated_health_checked' if expected == 0 else 'rolled_back_health_checked')
    require(journal['status'] == wanted_status,
            'Release did not produce the required successful activation/automatic rollback')
    backup = recovery.verify_bundle(journal_path.parent / 'database')
    require(backup['status'] == 'archive_verified' and backup['source_database'] == 'talky_synthetic_source'
            and backup['inventory']['heads'] == journal['old_heads']
            and backup['sha256'] == journal['backup']['sha256'], 'Actual verified backup does not match journal')
    return {'candidate_sha': sha, 'previous_sha': journal['previous_sha'], 'status': journal['status'],
            'journal': str(journal_path), 'manifest_id': journal['manifest_id'], 'manifest_sha256': checksum,
            'observation': observation, 'migration_started': journal['migration_started'],
            'heads': journal['old_heads'], 'candidate_heads': journal['new_heads'],
            'timers': journal['timers'], 'backup': {'status': backup['status'], 'bytes': backup['bytes'],
                'sha256': backup['sha256'], 'off_host_verified': False, 'restored': False}}


def fingerprint_state(release):
    return {'configuration': release.configuration_invariants(CURRENT), 'keys': release.key_fingerprints(CURRENT),
            'data': release.data_identity(CURRENT), 'host': release.host_configuration(release.Layout()),
            'markers': {name: digest(CURRENT / name / '.talklee-release-fixture') for name in release.MUTABLE}}


def application_pids():
    result = {}
    for unit in APP_UNITS:
        require(command(['systemctl', 'is-active', unit]).strip() == b'active', 'Expected original active application')
        pid = command(['systemctl', 'show', '--property=MainPID', '--value', unit]).decode().strip()
        require(re.fullmatch(r'[1-9][0-9]*', pid), 'Application process identity is missing')
        result[unit] = pid
    return result


def preactivation_rejections(release, candidate):
    def rejected(label):
        path, checksum, _ = manifest(release, candidate, label)
        before = set(Path('/var/lib/talky-release/transactions').glob('*/journal.json'))
        pids = application_pids()
        cli(SOURCE, 'activate', '--sha', candidate, '--manifest', path, '--manifest-sha256', checksum,
            '--service-file', SERVICE_FILE, '--runtime-service', 'source_runtime',
            '--owner-service', 'source_migration', '--backup-service', 'source_backup', expected=2)
        require(application_pids() == pids, 'Rejected activation restarted or stopped the current application')
        return path, set(Path('/var/lib/talky-release/transactions').glob('*/journal.json')) - before

    artifact = Path('/opt/talky-releases') / candidate / 'Admin/frontend/dist/index.html'
    original = artifact.read_bytes()
    try:
        artifact.write_bytes(original + b'\n<!-- synthetic unpublished artifact drift -->\n')
        path, created = rejected('artifact-drift')
        identifier = json.loads(path.read_text())['manifest_id']
        require(not created and not (Path('/var/lib/talky-release/used-manifests') / (identifier + '.used')).exists(),
                'Artifact drift was not refused before transaction/manifest consumption')
    finally:
        artifact.write_bytes(original)
    override = Path('/etc/systemd/system/talky-migrate.service.d/90-disposable-rehearsal.conf')
    require(not override.exists(), 'Owned maintenance fixture override already exists')
    private_new(override, '[Service]\nExecStart=\nExecStart=/bin/sleep 120\n')
    try:
        command(['systemctl', 'daemon-reload'])
        command(['systemctl', 'start', '--no-block', 'talky-migrate'])
        deadline = time.monotonic() + 20
        while command(['systemctl', 'show', '--property=ActiveState', '--value', 'talky-migrate']).strip() != b'activating':
            require(time.monotonic() < deadline, 'Fixture oneshot did not enter activating state')
            time.sleep(0.2)
        _, created = rejected('activating-maintenance')
        require(len(created) == 1, 'Expected one refused-maintenance transaction')
        journal = json.loads(created.pop().read_text())
        require(journal['status'] == 'blocked_before_runtime_stop' and not journal['runtime_stop_started']
                and not journal['migration_started'] and 'backup' not in journal,
                'Activating maintenance did not block before application stop/backup')
    finally:
        command(['systemctl', 'stop', 'talky-migrate'])
        override.unlink()
        command(['systemctl', 'daemon-reload'])
    return {'unpublished_artifact_drift_refused_before_transaction': True,
            'activating_oneshot_refused_before_runtime_stop': True, 'original_application_pids_preserved': True}


def dashboard_node_runtime():
    expected = json.loads((CURRENT / 'scripts/installation/install.example.json').read_text(
        encoding='utf-8-sig'))['node_version']
    require(isinstance(expected, str) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', expected),
            'Candidate Node version pin is invalid')
    pid = command(['systemctl', 'show', '--property=MainPID', '--value', 'talky-dashboard']).decode().strip()
    require(re.fullmatch(r'[1-9][0-9]*', pid), 'Expected a running dashboard process')
    executable = Path('/proc', pid, 'exe')
    installed = Path('/usr/local/bin/node').resolve(strict=True)
    require(executable.resolve(strict=True) == installed,
            'Dashboard process is not using the installed Node executable')
    version = command([executable, '--version'], timeout=10).decode().strip()
    require(version == 'v' + expected, 'Running dashboard Node version differs from the candidate pin')
    require(command(['systemctl', 'show', '--property=MainPID', '--value', 'talky-dashboard']).decode().strip() == pid,
            'Dashboard process changed during Node verification')
    return {'dashboard_node_version': version, 'dashboard_node_executable': str(installed),
            'dashboard_running_node_verified': True}


def health(release, sha, baseline, transaction):
    require(CURRENT.is_symlink() and CURRENT.resolve() == Path('/opt/talky-releases') / sha,
            'Published release link does not select the expected candidate')
    require(git(CURRENT, 'rev-parse', 'HEAD') == sha, 'Running source SHA differs')
    require(release.tracked_source_clean(CURRENT), 'Published tracked source changed')
    proof = json.loads(release.Layout().prepared(sha).read_text())
    release.verify_artifacts(release.Layout(), sha, proof)
    require(fingerprint_state(release) == baseline, 'Configuration, keys, host or data-directory identity changed')
    deadline = time.monotonic() + 180
    while True:
        running = all(subprocess.run(['systemctl', 'is-active', '--quiet', unit], env=ENV,
                      capture_output=True, timeout=10).returncode == 0 for unit in APP_UNITS)
        if running:
            break
        require(time.monotonic() < deadline, 'Services did not become active after real reboot')
        time.sleep(3)
    states = {}
    for unit in APP_UNITS:
        require(command(['systemctl', 'is-active', unit]).strip() == b'active', 'Application service is not active')
        require(command(['systemctl', 'show', '--property=User', '--value', unit]).strip() == b'talky'
                and command(['systemctl', 'show', '--property=Group', '--value', unit]).strip() == b'talky'
                and command(['systemctl', 'show', '--property=NoNewPrivileges', '--value', unit]).strip() == b'yes',
                'Application service protection changed')
        states[unit] = 'active'
    for unit in VERSION_UNITS:
        pid = command(['systemctl', 'show', '--property=MainPID', '--value', unit]).decode().strip()
        require(re.fullmatch(r'[1-9][0-9]*', pid), 'Expected a running application process')
        values = [entry.split(b'=', 1)[1] for entry in Path('/proc', pid, 'environ').read_bytes().split(b'\0')
                  if entry.startswith(b'APP_VERSION=')]
        require(values == [sha.encode()], 'Running backend process has the wrong release version')
    for timer in transaction['timers']:
        require(command(['systemctl', 'show', '--property=UnitFileState', '--value', timer]).strip() == b'disabled',
                'Frozen maintenance schedule was reenabled')
        require(command(['systemctl', 'show', '--property=ActiveState', '--value', timer]).strip() == b'inactive',
                'Frozen maintenance schedule restarted')
    release.wait_health('http://127.0.0.1:18080/ready', sha=sha)
    for route in ('/health', '/api/v1/healthz/ready', '/api/v1/healthz/deep'):
        release.wait_health('http://127.0.0.1:8000' + route)
    def fetch(domain, path):
        return command(['curl', '--fail', '--silent', '--show-error', '--max-time', '30', '--noproxy', '*',
                        '--resolve', domain + ':443:127.0.0.1', 'https://' + domain + path])
    fetch(DOMAINS[0], '/health')
    fetch(DOMAINS[1], '/')
    admin = CURRENT / 'Admin/frontend/dist/index.html'
    require(hashlib.sha256(fetch(DOMAINS[2], '/')).hexdigest() == digest(admin), 'Admin HTTPS serves another artifact')
    assets = sorted((CURRENT / 'Talk-Leee/.next/static').glob('*/_buildManifest.js'))
    require(assets, 'Dashboard build manifest missing')
    asset = assets[0]
    asset_url = '/_next/static/' + quote(asset.relative_to(CURRENT / 'Talk-Leee/.next/static').as_posix(), safe='/')
    require(hashlib.sha256(fetch(DOMAINS[1], asset_url)).hexdigest() == digest(asset), 'Dashboard HTTPS serves another build')
    current = json.loads(Path('/var/lib/talky-release/current.json').read_text())
    require(current['candidate_sha'] == sha and current['journal'] == transaction['journal'], 'Current release receipt differs')
    provider_dns = module('release_fixture_provider_dns', SOURCE / 'scripts/installation/guest_rehearsal.py').provider_dns_check()
    return {**dashboard_node_runtime(), **provider_dns, 'services': states,
            'service_users_and_protections': True, 'backend_process_versions': sha,
            'gateway_source_protocol_ready': True, 'three_https_hosts_checked': True,
            'admin_served_sha256': digest(admin), 'dashboard_served_manifest_sha256': digest(asset),
            'configuration_keys_host_and_data_identity_preserved': True, 'schedules_still_frozen': True}


def fault_source(candidate):
    fault = STATE / 'fault-source'
    require(not fault.exists(), 'Fault fixture source already exists')
    command(['git', 'clone', '--no-hardlinks', '--no-checkout', SOURCE, fault])
    git(fault, 'remote', 'set-url', 'origin', git(SOURCE, 'remote', 'get-url', 'origin'))
    git(fault, 'checkout', '--detach', candidate)
    path = fault / 'backend/systemd/talky-voice-worker.service'
    original = path.read_text()
    changed, count = re.subn(r'^ExecStart=.*$', 'ExecStart=/bin/false', original, flags=re.M)
    require(count == 1 and changed != original, 'Fault fixture must replace exactly one worker start command')
    path.write_text(changed)
    require(git(fault, 'diff', '--name-only') == 'backend/systemd/talky-voice-worker.service', 'Unexpected fault source change')
    git(fault, 'add', '--', 'backend/systemd/talky-voice-worker.service')
    git(fault, '-c', 'user.name=Synthetic CI Fixture', '-c', 'user.email=synthetic-ci@installer.test',
        'commit', '-m', 'Disposable VM worker-start fault only; never publish')
    sha = git(fault, 'rev-parse', 'HEAD')
    require(git(fault, 'rev-parse', 'HEAD^') == candidate and not git(fault, 'status', '--porcelain'),
            'Fault commit must be a clean direct descendant')
    return fault, sha


def migration_fault_source(candidate, old_head):
    fault = STATE / 'migration-fault-source'
    require(not fault.exists() and re.fullmatch(r'[A-Za-z0-9_]+', old_head), 'Invalid migration fixture state')
    command(['git', 'clone', '--no-hardlinks', '--no-checkout', SOURCE, fault])
    git(fault, 'remote', 'set-url', 'origin', git(SOURCE, 'remote', 'get-url', 'origin'))
    git(fault, 'checkout', '--detach', candidate)
    revision = 'fixture_fail_' + uuid.uuid4().hex[:8]
    relative = 'backend/Alembic/versions/9999_disposable_release_failure.py'
    path = fault / relative
    require(not path.exists(), 'Fixture revision already exists')
    path.write_text('"""Guest-only intentional migration failure. Never publish."""\n'
        'from alembic import op\n'
        f'revision = {revision!r}\ndown_revision = {old_head!r}\nbranch_labels = None\ndepends_on = None\n'
        'def upgrade():\n    op.execute("SELECT 1")\n'
        '    raise RuntimeError("Synthetic disposable release migration failure")\n'
        'def downgrade():\n    raise RuntimeError("Fixture has no downgrade")\n')
    policy_relative = 'scripts/database/runtime_grants_profile.json'
    policy_path = fault / policy_relative
    policy = json.loads(policy_path.read_text())
    require(policy['heads'] == [old_head], 'Guest migration fixture requires the healthy grants-profile head')
    policy['heads'] = [revision]
    policy_path.write_text(json.dumps(policy, indent=2) + '\n')
    git(fault, 'add', '--', relative, policy_relative)
    git(fault, '-c', 'user.name=Synthetic CI Fixture', '-c', 'user.email=synthetic-ci@installer.test',
        'commit', '-m', 'Disposable VM migration-failure boundary only; never publish')
    sha = git(fault, 'rev-parse', 'HEAD')
    require(git(fault, 'rev-parse', 'HEAD^') == candidate and not git(fault, 'status', '--porcelain')
            and set(git(fault, 'diff', '--name-only', 'HEAD^', 'HEAD').splitlines()) == {relative, policy_relative},
            'Unexpected migration fault commit')
    before = json.loads(git(fault, 'show', 'HEAD^:' + policy_relative))
    after = json.loads(policy_path.read_text())
    before.pop('heads')
    after.pop('heads')
    require(before == after, 'Guest migration fixture must not change any grant policy')
    return fault, sha


def migration_refusal(payload, release, recovery, receipt):
    require(receipt['status'] == 'rollback_reboot_verified', 'Migration fault requires both verified reboots')
    candidate = receipt['candidate_sha']
    health(release, candidate, receipt['baseline'], receipt['rollback'])
    old = fixture_database(payload)
    require(len(old['heads']) == 1, 'Migration failure fixture needs one existing head')
    source, sha = migration_fault_source(candidate, old['heads'][0])
    receipt.update(stage='migration_fault_prepare', migration_fault_sha=sha)
    write_receipt(receipt)
    prepare(source, sha, candidate)
    migration_env = Path('/etc/talky/migration.env')
    original_profile = migration_env.read_bytes()
    try:
        migration_env.write_text('MIGRATION_DATABASE_URL=' + payload['owner_url'] + '&host=203.0.113.7\n')
        receipt['stage'] = 'migration_dsn_override_rejection'
        write_receipt(receipt)
        receipt['migration_dsn_override_rejection'] = activate(release, recovery, sha,
            'migration-dsn-override', expected=2, forward_migration=True)
    finally:
        migration_env.write_bytes(original_profile)
    receipt['after_migration_dsn_rejection'] = health(release, candidate, receipt['baseline'],
                                                    receipt['migration_dsn_override_rejection'])
    recovery_path, recovery_digest, _ = manifest(release, candidate, 'migration-refusal-recovery')
    receipt['stage'] = 'migration_fault_activate'
    write_receipt(receipt)
    transaction = activate(release, recovery, sha, 'migration-refusal', expected=2, migration_failure=True)
    after = fixture_database(payload)
    require(after == old, 'Intentional transaction failure changed the database head or synthetic records')
    journal_path = Path(transaction['journal'])
    before_journal = digest(journal_path)
    result = command_result(['/usr/bin/python3', '-B', str(SOURCE / 'scripts/installation/fresh_release.py'),
        'recover', '--journal', str(journal_path), '--manifest', str(recovery_path),
        '--manifest-sha256', recovery_digest], expected=2, timeout=180)
    require(result.returncode == 2 and b'Migration began; code rollback is forbidden' in result.stderr,
            'Recovery did not refuse the started-migration uncertainty boundary')
    require(digest(journal_path) == before_journal, 'Rejected recovery mutated the release journal')
    recovery_manifest = json.loads(recovery_path.read_text())
    require(not (Path('/var/lib/talky-release/used-manifests') /
                 (recovery_manifest['manifest_id'] + '.used')).exists(), 'Recovery consumed approval before refusing migration')
    require(git(CURRENT, 'rev-parse', 'HEAD') == candidate and fingerprint_state(release) == receipt['baseline'],
            'Failed migration changed the healthy source/configuration/data identity')
    stopped = {}
    for unit in transaction['timers'] | {name: {} for name in APP_UNITS}:
        state = command(['systemctl', 'show', '--property=ActiveState', '--value', unit]).decode().strip()
        require(state in ('inactive', 'failed'), 'Migration failure left an application/schedule running')
        stopped[unit] = state
    receipt.update(migration_refusal=transaction, migration_refusal_verified=True,
        migration_failure_heads_unchanged=True, recovery_refused_after_migration_started=True,
        stopped_after_migration_failure=stopped, successful_forward_migration=False,
        status='passed', stage='guest_checks_complete', finished_utc=now())


def main(argv=None):
    global DIAGNOSTICS_ALLOWED
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('activate', 'verify-reboot', 'fault-rollback', 'verify-rollback-reboot',
                                         'migration-refusal'))
    parser.add_argument('--candidate-sha', required=True)
    parser.add_argument('--base-sha')
    args = parser.parse_args(argv)
    receipt = None
    guarded = False
    try:
        payload, release, recovery = guard(args.candidate_sha)
        guarded = True
        os.umask(0o022)
        if args.phase == 'activate':
            require(args.base_sha and SHA.fullmatch(args.base_sha), 'Full installed base SHA required')
            require(not STATE.exists(), 'Rehearsal state exists; refuse repeated activation')
            require(git(CURRENT, 'rev-parse', 'HEAD') == args.base_sha, 'Installed base does not match')
            install = json.loads(Path('/var/lib/talky-install/receipt.json').read_text())
            require(install.get('status') == 'installed_health_checked' and install.get('source_sha') == args.base_sha
                    and install.get('frontend_mode') == 'linux', 'Expected completed Linux-frontend base installation')
            STATE.mkdir(mode=0o700)
            DIAGNOSTICS_ALLOWED = True
            receipt = {'status': 'running', 'stage': 'freeze', 'started_utc': now(),
                'base_sha': args.base_sha, 'candidate_sha': args.candidate_sha,
                'helper_sha256': digest(Path(__file__)), 'release_cli_sha256': digest(SOURCE / 'scripts/installation/fresh_release.py'),
                'production_acceptance': False, 'customer_call_acceptance': False, 'off_host_recovery_acceptance': False,
                'production_accessed': False, 'outer_cleanup_verified': False,
                'update_reboot_verified': False, 'rollback_reboot_verified': False, 'migration_refusal_verified': False,
                'human_owner_attestation': False, 'approval_identity': 'synthetic-ci-fixture@installer.test',
                'boot_before_activation': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
            write_receipt(receipt)
            receipt['fixture_database'] = fixture_database(payload, freeze=True)
            receipt['firewall_freeze'] = freeze_ingress()
            migration_env = Path('/etc/talky/migration.env')
            require(not migration_env.exists(), 'Disposable migration profile already exists')
            private_new(migration_env, 'MIGRATION_DATABASE_URL=' + payload['owner_url'] + '\n')
            for name in release.MUTABLE:
                private_new(CURRENT / name / '.talklee-release-fixture', 'synthetic fixture state ' + uuid.uuid4().hex + '\n')
            receipt['baseline'] = fingerprint_state(release)
            receipt['stage'] = 'prepare'
            write_receipt(receipt)
            prepare(SOURCE, args.candidate_sha, args.base_sha)
            receipt['stage'] = 'preactivation_guard_probes'
            write_receipt(receipt)
            receipt['preactivation_rejections'] = preactivation_rejections(release, args.candidate_sha)
            receipt['stage'] = 'activate'
            write_receipt(receipt)
            receipt['activation'] = activate(release, recovery, args.candidate_sha, 'update')
            receipt['after_activation'] = health(release, args.candidate_sha, receipt['baseline'], receipt['activation'])
            receipt.update(status='awaiting_update_reboot', stage='awaiting_update_reboot')
        else:
            protected(STATE, directory=True)
            protected(RECEIPT)
            DIAGNOSTICS_ALLOWED = True
            receipt = json.loads(RECEIPT.read_text())
            require(receipt['candidate_sha'] == args.candidate_sha and receipt['helper_sha256'] == digest(Path(__file__)),
                    'Receipt source binding changed')
            require(receipt['release_cli_sha256'] == digest(SOURCE / 'scripts/installation/fresh_release.py'),
                    'Release CLI changed between phases')
            database = fixture_database(payload)
            require(database == receipt['fixture_database'], 'Synthetic database records/schema/freeze changed')
            require(digest(Path('/etc/ufw/user.rules')) == receipt['firewall_freeze']['ufw_sha256']
                    and digest(Path('/etc/ufw/user6.rules')) == receipt['firewall_freeze']['ufw6_sha256'],
                    'Persistent fixture ingress freeze changed')
            if args.phase == 'verify-reboot':
                require(receipt['status'] == 'awaiting_update_reboot', 'Update reboot phase is out of order')
                boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                require(boot != receipt['boot_before_activation'], 'Actual VM reboot was not observed')
                receipt['after_update_reboot'] = health(release, args.candidate_sha, receipt['baseline'], receipt['activation'])
                receipt.update(status='update_reboot_verified', stage='update_reboot_verified', boot_after_update=boot,
                               update_reboot_verified=True)
            elif args.phase == 'fault-rollback':
                require(receipt['status'] == 'update_reboot_verified', 'Fault injection requires verified update reboot')
                health(release, args.candidate_sha, receipt['baseline'], receipt['activation'])
                source, sha = fault_source(args.candidate_sha)
                receipt.update(stage='fault_prepare', fault_sha=sha)
                write_receipt(receipt)
                prepare(source, sha, args.candidate_sha)
                receipt['stage'] = 'fault_activate'
                write_receipt(receipt)
                receipt['rollback'] = activate(release, recovery, sha, 'fault', expected=2)
                receipt['after_rollback'] = health(release, args.candidate_sha, receipt['baseline'], receipt['rollback'])
                require('ExecStart=/bin/false' not in Path('/etc/systemd/system/talky-voice-worker.service').read_text(),
                        'Faulted unit was not restored')
                receipt.update(status='awaiting_rollback_reboot', stage='awaiting_rollback_reboot',
                    boot_before_rollback_reboot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
            elif args.phase == 'verify-rollback-reboot':
                require(receipt['status'] == 'awaiting_rollback_reboot', 'Rollback reboot phase is out of order')
                boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
                require(boot != receipt['boot_before_rollback_reboot'], 'Rollback VM reboot was not observed')
                receipt['after_rollback_reboot'] = health(release, args.candidate_sha, receipt['baseline'], receipt['rollback'])
                receipt.update(status='rollback_reboot_verified', stage='rollback_reboot_verified',
                               boot_after_rollback=boot, rollback_reboot_verified=True)
            else:
                migration_refusal(payload, release, recovery, receipt)
        write_receipt(receipt)
        print(json.dumps({'status': receipt['status'], 'stage': receipt['stage'], 'candidate_sha': args.candidate_sha,
                          'receipt': str(RECEIPT), 'production_acceptance': False}))
        return 0
    except Exception as error:
        if guarded and receipt is not None:
            receipt.update(status='failed', error_type=type(error).__name__)
            if isinstance(error, RehearsalError):
                receipt['error_detail'] = str(error)
            if LAST_CHILD_DIAGNOSTIC:
                receipt['last_child_diagnostic'] = LAST_CHILD_DIAGNOSTIC
            write_receipt(receipt)
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__, 'production_acceptance': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Actual installer/reboot acceptance in a newly owned Ubuntu cloud-image VM.

Runs only on an ephemeral GitHub-hosted Linux runner with KVM. No existing VM,
host, database endpoint, account or provider credentials can be selected.
The database containers and VM share the runner: this is NOT off-host recovery.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
IMAGE_BASE = 'https://cloud-images.ubuntu.com/noble/current/'
IMAGE_NAME = 'noble-server-cloudimg-amd64.img'
REPOSITORY = 'https://github.com/talkyai-source/Talky.ai-complete-.git'
PINNED_BASE_SHA = 'b216774648537e2e25afa550f9b227fc3283e089'
SPEC = importlib.util.spec_from_file_location('tls_fixture', ROOT / 'scripts/database/tests/tls_canonical_rehearsal.py')
tls = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tls)
UNITS = ('talky-api', 'talky-voice-gateway', 'talky-voice-worker', 'talky-dialer-worker',
         'talky-reminder-worker', 'talky-dashboard', 'talky-redis', 'asterisk', 'nginx',
         'talky-trunk-status', 'talky-asterisk-reconcile.timer', 'talky-healthwatch.timer')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(command, *, timeout=180, input=None):
    result = subprocess.run(command, input=input, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        # Installer messages intentionally exclude subprocess output and secret
        # values. Keep its fixed diagnostic without publishing arbitrary logs.
        installer = [line for line in (result.stdout + result.stderr).splitlines()
                     if line.startswith(('ERROR: ', 'Installing pinned OS packages',
                                         'Installing locked Python dependencies', 'Starting services and checking readiness'))]
        diagnostic = '; '.join(installer[-4:]) if 'install_talklee.sh apply' in str(command[-1]) else 'output withheld'
        raise RuntimeError(f'{Path(command[0]).name} failed (exit {result.returncode}); {diagnostic}')
    return result.stdout.strip()


def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def baseline_source(folder, sha):
    """Own a separate clean checkout of the sole permitted installation base."""
    require(sha == PINNED_BASE_SHA, 'Unreviewed installation baseline')
    source = folder / 'baseline-source'
    require(not source.exists(), 'Baseline fixture already exists')
    run(['git', 'init', '--quiet', str(source)])
    run(['git', '-C', str(source), 'remote', 'add', 'origin', REPOSITORY])
    run(['git', '-C', str(source), 'fetch', '--quiet', '--depth=1', 'origin', sha], timeout=300)
    run(['git', '-C', str(source), 'checkout', '--quiet', '--detach', 'FETCH_HEAD'])
    require(run(['git', '-C', str(source), 'rev-parse', 'HEAD']) == sha
            and not run(['git', '-C', str(source), 'status', '--porcelain']),
            'Database baseline checkout differs from installed source')
    return source


def download_image(folder):
    with urlopen(IMAGE_BASE + 'SHA256SUMS', timeout=45) as response:
        manifest = response.read(1024 * 1024).decode()
    matches = re.findall(r'^([a-f0-9]{64})\s+\*?' + re.escape(IMAGE_NAME) + r'$', manifest, re.M)
    require(len(matches) == 1, 'Official Ubuntu checksum must identify exactly one amd64 image')
    target = folder / 'ubuntu-base.img'
    digest = hashlib.sha256()
    with urlopen(IMAGE_BASE + IMAGE_NAME, timeout=120) as response, target.open('xb') as output:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
    require(digest.hexdigest() == matches[0], 'Ubuntu cloud-image checksum mismatch')
    return target, digest.hexdigest()


class VM:
    def __init__(self, folder, token):
        self.folder, self.token = folder, token
        self.ssh_port, self.https_port = port(), port()
        require(self.ssh_port != self.https_port, 'Loopback forwarding ports collided')
        self.process = None
        self.serial = None

    def ssh_options(self):
        return ['-i', str(self.folder / 'client-key'), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'UserKnownHostsFile=' + str(self.folder / 'known-hosts')]

    def ssh(self, command, timeout=180):
        return run(['ssh', *self.ssh_options(), '-p', str(self.ssh_port), 'root@127.0.0.1', command], timeout=timeout)

    def copy(self, source, target):
        run(['scp', '-p', *self.ssh_options(), '-P', str(self.ssh_port), str(source),
             'root@127.0.0.1:' + target])

    def start(self, image):
        for name in ('client-key', 'host-key'):
            run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(self.folder / name)])
        host_key = (self.folder / 'host-key').read_text()
        host_public = (self.folder / 'host-key.pub').read_text().strip()
        client_public = (self.folder / 'client-key.pub').read_text().strip()
        tls.write_private(self.folder / 'known-hosts', f'[127.0.0.1]:{self.ssh_port} {host_public}\n')
        cloud = {
            'disable_root': False, 'ssh_pwauth': False,
            'ssh_authorized_keys': [client_public],
            'ssh_keys': {'ed25519_private': host_key, 'ed25519_public': host_public},
            'write_files': [{'path': '/etc/talklee-disposable-installer-vm', 'permissions': '0600',
                             'content': self.token + '\n'}],
            'runcmd': [['sh', '-c', 'printf "PermitRootLogin prohibit-password\\n" > '
                        '/etc/ssh/sshd_config.d/01-talklee-rehearsal.conf && systemctl restart ssh']],
        }
        # JSON is valid YAML; cloud-init's marker selects the cloud-config handler.
        tls.write_private(self.folder / 'user-data', '#cloud-config\n' + json.dumps(cloud))
        tls.write_private(self.folder / 'meta-data', json.dumps({'instance-id': self.token,
                                                               'local-hostname': 'talklee-installer-test'}))
        run(['cloud-localds', str(self.folder / 'seed.img'), str(self.folder / 'user-data'), str(self.folder / 'meta-data')])
        overlay = self.folder / 'vm.qcow2'
        run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(image), str(overlay), '30G'])
        self.serial = (self.folder / 'serial.log').open('xb')
        self.process = subprocess.Popen([
            'qemu-system-x86_64', '-machine', 'q35,accel=kvm', '-cpu', 'host', '-m', '8192', '-smp', '2',
            '-display', 'none', '-monitor', 'none', '-serial', 'stdio',
            '-drive', f'if=virtio,format=qcow2,file={overlay}',
            '-drive', f'if=virtio,format=raw,file={self.folder / "seed.img"}',
            '-netdev', 'user,id=network,hostfwd=tcp:127.0.0.1:' + str(self.ssh_port) + '-:22,'
                       'hostfwd=tcp:127.0.0.1:' + str(self.https_port) + '-:443',
            '-device', 'virtio-net-pci,netdev=network'],
            stdin=subprocess.DEVNULL, stdout=self.serial, stderr=self.serial)
        self.wait_ssh()
        self.ssh('cloud-init status --wait', timeout=300)
        require(self.ssh('cat /etc/talklee-disposable-installer-vm') == self.token, 'Guest identity mismatch')

    def wait_ssh(self):
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            require(self.process.poll() is None, 'Owned QEMU process exited unexpectedly')
            try:
                self.ssh('true', timeout=15)
                return
            except (RuntimeError, subprocess.TimeoutExpired):
                time.sleep(3)
        raise RuntimeError('Owned VM did not become SSH-ready')

    def cleanup(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        if self.serial:
            self.serial.close()


def web_certificate(folder):
    run(['openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-subj', '/CN=api.installer.test',
         '-keyout', str(folder / 'web.key'), '-out', str(folder / 'web.csr')])
    (folder / 'web.key').chmod(0o600)
    tls.write_private(folder / 'web.ext',
                      'subjectAltName=DNS:api.installer.test,DNS:app.installer.test,DNS:admin.installer.test\n'
                      'extendedKeyUsage=serverAuth\n')
    run(['openssl', 'x509', '-req', '-in', str(folder / 'web.csr'), '-CA', str(folder / 'ca.crt'),
         '-CAkey', str(folder / 'ca.key'), '-CAcreateserial', '-days', '2',
         '-extfile', str(folder / 'web.ext'), '-out', str(folder / 'web.crt')])


def verify(vm, ca, database_host, database_route, sha):
    states = {unit: vm.ssh('systemctl is-active ' + shlex.quote(unit)) for unit in UNITS}
    require(all(value == 'active' for value in states.values()), 'Not all installed services are active')
    identities = {unit: vm.ssh('systemctl show --property=User --value ' + shlex.quote(unit))
                  for unit in UNITS if unit.startswith('talky-') and unit != 'talky-redis' and not unit.endswith('.timer')}
    require(set(identities.values()) == {'talky'}, 'Installed application services must use talky')
    for unit in ('talky-migrate', 'talky-asterisk-reconcile'):
        require(vm.ssh('systemctl show --property=User --value ' + unit) == 'root', 'Operator identity mismatch')
        require(vm.ssh('systemctl show --property=Group --value ' + unit) == 'root', 'Operator group mismatch')
    for unit in ('talky-trunk-status.service', 'talky-asterisk-reconcile.timer', 'talky-healthwatch.timer'):
        require(vm.ssh('systemctl is-enabled ' + unit) == 'enabled', 'Required scheduling is not enabled')
    for unit in ('talky-migrate.service', 'talky-trunk-status.timer', 'talky-db-backup.timer',
                 'talky-inbound-synthetic.timer'):
        state = vm.ssh('systemctl show --property=UnitFileState --value ' + unit)
        require(state in ('static', 'disabled', ''), 'Unapproved schedule was enabled')
    route = json.loads(vm.ssh('ip -json route get ' + str(ipaddress.ip_address(database_host))))[0]
    require(route.get('dev') == database_route, 'Docker changed the external database route')
    container = vm.ssh('docker compose -f /etc/talky/compose.yml ps --quiet redis')
    require(re.fullmatch('[a-f0-9]{64}', container), 'Expected exactly one installed Redis container')
    require(vm.ssh("docker inspect --format '{{.HostConfig.NetworkMode}}' " + container) == 'host',
            'Redis must use the supported bridge-free host network')
    require(vm.ssh("sed -n '/^bind /p' /etc/talky/redis.conf") == 'bind 127.0.0.1',
            'Redis must bind only to host loopback')
    endpoint = vm.ssh("asterisk -rx 'pjsip show endpoint blazedigitel-endpoint'")
    require('blazedigitel-endpoint' in endpoint
            and re.search(r'^\s*context\s*:\s*from-talky-inbound\s*$', endpoint, re.MULTILINE),
            'Synthetic shared endpoint is not loaded in the managed inbound context')
    for endpoint in ('http://127.0.0.1:8000/health', 'http://127.0.0.1:8000/api/v1/healthz/ready',
                     'http://127.0.0.1:8000/api/v1/healthz/deep', 'http://127.0.0.1:18080/ready'):
        response = vm.ssh('curl --fail --silent --show-error --max-time 30 ' + shlex.quote(endpoint))
        if endpoint.endswith(':18080/ready'):
            gateway = json.loads(response)
            require(gateway.get('build_sha') == sha and gateway.get('ready') is True
                    and gateway.get('protocol_version') == 2, 'Running gateway source/protocol mismatch')
    pid = vm.ssh('systemctl show talky-api.service --property=MainPID --value')
    require(re.fullmatch(r'[1-9][0-9]{0,9}', pid), 'API has no running process')
    vm.ssh('/opt/talky/backend/venv/bin/python /opt/talky/backend/scripts/set_release_version.py '
           '--candidate-sha ' + sha + ' --check-pid ' + pid)
    expected_node = vm.ssh('python3 -c ' + shlex.quote(
        "import json; print(json.load(open('/root/talklee-rehearsal-inputs/prepared/install.json'))['node_version'])"))
    dashboard_pid = vm.ssh('systemctl show talky-dashboard.service --property=MainPID --value')
    require(re.fullmatch(r'[1-9][0-9]{0,9}', dashboard_pid), 'Dashboard has no running process')
    dashboard_node = vm.ssh('readlink -e /proc/' + dashboard_pid + '/exe')
    installed_node = vm.ssh('readlink -e /usr/local/bin/node')
    require(dashboard_node == installed_node, 'Dashboard is not using the installed Node executable')
    node_version = vm.ssh('/proc/' + dashboard_pid + '/exe --version')
    require(node_version == 'v' + expected_node, 'Running dashboard Node version differs from the reviewed pin')
    require(vm.ssh('systemctl show talky-dashboard.service --property=MainPID --value') == dashboard_pid,
            'Dashboard process changed during Node verification')
    provider_dns = json.loads(vm.ssh('PYTHONDONTWRITEBYTECODE=1 python3 -c ' + shlex.quote(
        "import json,sys; sys.path.insert(0, '/root/source/scripts/installation'); "
        "from guest_rehearsal import provider_dns_check; print(json.dumps(provider_dns_check()))")))
    for domain, path in (('api.installer.test', '/health'), ('app.installer.test', '/'), ('admin.installer.test', '/')):
        run(['curl', '--fail', '--silent', '--show-error', '--max-time', '60', '--noproxy', '*',
             '--cacert', str(ca), '--resolve', f'{domain}:{vm.https_port}:127.0.0.1',
             f'https://{domain}:{vm.https_port}{path}'])
    return {**provider_dns, 'services': states, 'application_identities': identities, 'operator_identity_and_scheduling_verified': True,
            'local_and_host_https_probes': True, 'database_route_device': route['dev'], 'redis_host_loopback_verified': True,
            'api_process_and_gateway_source_sha': sha, 'asterisk_shared_endpoint_context_verified': True,
            'dashboard_node_version': node_version,
            'dashboard_node_executable': dashboard_node, 'dashboard_running_node_verified': True}


def release_fixture_inputs(folder, fixture, runtime, query):
    """Only the three owned source roles; never copy fixture administrator access."""
    guest_inputs = '/root/talklee-rehearsal-inputs'
    service_rows, password_rows = [], []
    for name in ('source_runtime', 'source_migration', 'source_backup'):
        values = fixture.service(name).values
        service_rows.append(f"[{name}]\nhost={values['host']}\nport={values['port']}\n"
                            f"dbname={values['dbname']}\nuser={values['user']}\nsslmode=verify-full\n"
                            f"sslrootcert=/etc/ssl/certs/talky-db-ca.pem\npassfile={guest_inputs}/release.pgpass\n")
        password_rows.append(f"{values['host']}:{values['port']}:{values['dbname']}:{values['user']}:"
                             + fixture.passwords[values['user']])
    tls.write_private(folder / 'release.pg_service.conf', '\n'.join(service_rows))
    tls.write_private(folder / 'release.pgpass', '\n'.join(password_rows) + '\n')
    return {'owner_url': f"postgresql://fixture_migration:{fixture.passwords['fixture_migration']}@"
                         f"{runtime['host']}:5432/{tls.SOURCE_DB}?{query}",
            'operator_passwords': {name: fixture.passwords[name] for name in ('fixture_migration', 'fixture_backup')}}


def reboot_guest(vm):
    before = vm.ssh('cat /proc/sys/kernel/random/boot_id')
    vm.ssh('systemctl reboot --no-block')
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        time.sleep(5)
        try:
            after = vm.ssh('cat /proc/sys/kernel/random/boot_id', timeout=15)
            if after != before:
                return
        except (RuntimeError, subprocess.TimeoutExpired):
            pass
    raise RuntimeError('Guest boot identity did not change')


def release_receipt(vm):
    path = '/var/lib/talklee-release-rehearsal/receipt.json'
    value = json.loads(vm.ssh('cat ' + path))
    checksum = vm.ssh('sha256sum ' + path).split()[0]
    require(re.fullmatch('[a-f0-9]{64}', checksum), 'Guest release receipt digest is invalid')
    # Internal key/configuration fingerprints are used inside the guest for
    # equality checks; published CI evidence does not need those values.
    value.pop('baseline', None)
    value.update(private_fingerprints_omitted=True, guest_receipt_sha256=checksum)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--update-from-sha', choices=[PINNED_BASE_SHA],
                        help='Install and reboot this exact baseline before exercising update/recovery')
    args = parser.parse_args()
    require(sys.platform == 'linux' and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted',
            'Only ephemeral GitHub-hosted Linux runners are accepted')
    require(Path('/dev/kvm').is_char_device() and os.access('/dev/kvm', os.R_OK | os.W_OK), 'Usable KVM is required')
    require(not args.output.exists(), 'Refuse to overwrite a rehearsal receipt')
    sha = run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'])
    require(re.fullmatch('[a-f0-9]{40}', sha) and sha == os.environ['GITHUB_SHA'], 'Source identity mismatch')
    install_sha = args.update_from_sha or sha
    require(not run(['git', '-C', str(ROOT), 'status', '--porcelain', '--untracked-files=all']), 'Source must be clean')
    for key in list(os.environ):
        if key.startswith('PG') or key in {'DATABASE_URL', 'MIGRATION_DATABASE_URL', 'READ_DATABASE_URL'}:
            os.environ.pop(key)
    os.environ['PYTHON_DOTENV_DISABLED'] = '1'
    receipt = {'status': 'failed', 'source_sha': sha, 'started_utc': datetime.now(timezone.utc).isoformat(),
               'controlled_call_verified': False, 'live_provider_verified': False,
               'paid_billing_verified': False, 'offhost_recovery_verified': False,
               'public_dns_verified': False, 'independent_engineer_verified': False}
    if args.update_from_sha:
        receipt['installed_base_sha'] = install_sha
    try:
        with tempfile.TemporaryDirectory(prefix='talklee-blank-vm-') as temporary:
            folder = Path(temporary)
            fixture = tls.Fixture(folder, 'postgres:16-bookworm')
            vm = VM(folder, fixture.token)
            try:
                receipt['stage'] = 'cloud_image'
                print('Verifying official Ubuntu image...', flush=True)
                image, digest = download_image(folder)
                receipt.update(image_url=IMAGE_BASE + IMAGE_NAME, image_sha256=digest,
                               checksum_url=IMAGE_BASE + 'SHA256SUMS')
                receipt['stage'] = 'external_synthetic_database'
                print('Preparing isolated TLS PostgreSQL...', flush=True)
                fixture.prepare()
                receipt.update(postgres_image_id=fixture.image_id, postgres_image_digests=fixture.image_digests)
                pg = tls.recovery.Postgres(Path('/usr/lib/postgresql/16/bin'), timeout=180)
                schema_source = baseline_source(folder, install_sha) if args.update_from_sha else ROOT
                tls.bootstrap(pg, fixture, source_root=schema_source)
                receipt['database_bootstrap_sha'] = install_sha
                if args.update_from_sha:
                    # Synthetic completed calls/draft campaigns also prove the
                    # release backup sees data. No runnable customer work exists.
                    tls.seed(pg, fixture)
                    # Subsequent fault drills back up the upgraded schema too.
                    # Only this fixture's separate backup login inherits SELECT
                    # on objects created by its restricted migration owner.
                    pg.sql(fixture.service('source_migration'),
                           'ALTER DEFAULT PRIVILEGES FOR ROLE fixture_migration IN SCHEMA public '
                           'GRANT SELECT ON TABLES TO fixture_backup; '
                           'ALTER DEFAULT PRIVILEGES FOR ROLE fixture_migration IN SCHEMA public '
                           'GRANT SELECT ON SEQUENCES TO fixture_backup;', readonly=False)
                grants_spec = importlib.util.spec_from_file_location('vm_runtime_grants', schema_source / 'scripts/database/runtime_grants.py')
                grants = importlib.util.module_from_spec(grants_spec)
                grants_spec.loader.exec_module(grants)
                receipt['runtime_grants'] = grants.execute(fixture.service('source_migration'), fixture.service('source_runtime'), 'apply')
                web_certificate(folder)
                receipt['stage'] = 'blank_vm'
                print('Starting fresh Ubuntu VM...', flush=True)
                vm.start(image)
                receipt['guest_os'] = vm.ssh('cat /etc/os-release')
                vm.ssh('apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y git python3 ca-certificates', timeout=300)
                vm.ssh('git init -q /root/source && git -C /root/source remote add origin ' + shlex.quote(REPOSITORY)
                       + ' && git -C /root/source fetch --quiet --depth=1 origin ' + install_sha
                       + ' && git -C /root/source checkout --quiet --detach FETCH_HEAD', timeout=300)
                require(vm.ssh('git -C /root/source rev-parse HEAD') == install_sha, 'Guest source mismatch')
                if args.update_from_sha:
                    vm.ssh('git init -q /root/update-source && git -C /root/update-source remote add origin '
                           + shlex.quote(REPOSITORY)
                           + ' && git -C /root/update-source fetch --quiet --depth=64 origin ' + sha
                           + ' && git -C /root/update-source checkout --quiet --detach FETCH_HEAD', timeout=300)
                    require(vm.ssh('git -C /root/update-source rev-parse HEAD') == sha, 'Update source mismatch')
                    vm.ssh('git -C /root/update-source merge-base --is-ancestor ' + install_sha + ' ' + sha)
                vm.ssh('install -d -m 0700 /root/talklee-rehearsal-inputs')
                runtime = fixture.service('source_runtime').values
                database_route = json.loads(vm.ssh('ip -json route get ' + str(ipaddress.ip_address(runtime['host']))))[0]['dev']
                require(not database_route.startswith(('docker', 'br-')), 'Fixture database must initially route outside the guest')
                query = urlencode({'sslmode': 'verify-full', 'sslrootcert': '/etc/ssl/certs/talky-db-ca.pem'})
                payload = {'token': fixture.token,
                    'runtime_url': f"postgresql://fixture_runtime:{fixture.passwords['fixture_runtime']}@"
                                   f"{runtime['host']}:5432/{tls.SOURCE_DB}?{query}"}
                copies = ['fixture.json', 'ca.crt', 'web.crt', 'web.key']
                if args.update_from_sha:
                    payload.update(release_fixture_inputs(folder, fixture, runtime, query))
                    copies.extend(('release.pg_service.conf', 'release.pgpass'))
                tls.write_private(folder / 'fixture.json', json.dumps(payload))
                for name in copies:
                    vm.copy(folder / name, '/root/talklee-rehearsal-inputs/' + name)
                receipt['stage'] = 'prepare'
                vm.ssh('PYTHONDONTWRITEBYTECODE=1 python3 /root/source/scripts/installation/guest_rehearsal.py', timeout=300)
                receipt['stage'] = 'apply'
                print('Running actual installer apply with Linux frontends...', flush=True)
                vm.ssh('umask 0022; cd /root/source && PYTHONDONTWRITEBYTECODE=1 bash install_talklee.sh apply '
                       '--config /root/talklee-rehearsal-inputs/prepared/install.json --source /root/source --confirm-new-host', timeout=3600)
                receipt['install'] = json.loads(vm.ssh('cat /var/lib/talky-install/receipt.json'))
                require(receipt['install']['source_sha'] == install_sha and receipt['install']['status'] == 'installed_health_checked',
                        'Installer receipt did not prove the selected source')
                receipt['before_reboot'] = verify(vm, folder / 'ca.crt', runtime['host'], database_route, install_sha)
                receipt['stage'] = 'reboot'
                reboot_guest(vm)
                # systemd may still be starting API/dashboard after SSH is up.
                deadline = time.monotonic() + 180
                while True:
                    try:
                        receipt['after_reboot'] = verify(vm, folder / 'ca.crt', runtime['host'], database_route, install_sha)
                        break
                    except RuntimeError:
                        require(time.monotonic() < deadline, 'Installed services did not recover after reboot')
                        time.sleep(5)
                receipt.update(reboot_verified=True, stage='cleanup')
                if args.update_from_sha:
                    receipt['stage'] = 'update_rollback'
                    helper = ('umask 0022; PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 '
                              '/opt/talky/backend/venv/bin/python '
                              '/root/update-source/scripts/installation/guest_release_rehearsal.py ')
                    print('Patched baseline installed and rebooted; exercising actual staged update...', flush=True)
                    vm.ssh(helper + 'activate --candidate-sha ' + sha + ' --base-sha ' + install_sha, timeout=3600)
                    reboot_guest(vm)
                    vm.ssh(helper + 'verify-reboot --candidate-sha ' + sha, timeout=300)
                    print('Updated release survived reboot; exercising worker-failure recovery...', flush=True)
                    vm.ssh(helper + 'fault-rollback --candidate-sha ' + sha, timeout=3600)
                    reboot_guest(vm)
                    vm.ssh(helper + 'verify-rollback-reboot --candidate-sha ' + sha, timeout=300)
                    print('Recovered release survived reboot; exercising migration-failure refusal...', flush=True)
                    vm.ssh(helper + 'migration-refusal --candidate-sha ' + sha, timeout=3600)
                    release = release_receipt(vm)
                    require(release.get('status') == 'passed' and release.get('candidate_sha') == sha
                            and release.get('base_sha') == install_sha and release.get('production_accessed') is False
                            and all(release.get(key) is True for key in ('update_reboot_verified',
                                    'rollback_reboot_verified', 'migration_refusal_verified',
                                    'successful_forward_migration')),
                            'Guest release receipt did not prove every required phase')
                    receipt['release_rehearsal'] = release
            except Exception:
                if receipt.get('stage') == 'update_rollback':
                    try:
                        receipt['release_rehearsal'] = release_receipt(vm)
                    except Exception:
                        receipt['release_receipt_unavailable'] = True
                if receipt.get('stage') in {'apply', 'reboot', 'update_rollback'}:
                    try:
                        diagnosis_source = '/root/update-source' if args.update_from_sha else '/root/source'
                        receipt['diagnostics'] = json.loads(vm.ssh(
                            'PYTHONDONTWRITEBYTECODE=1 python3 ' + diagnosis_source
                            + '/scripts/installation/guest_rehearsal.py --diagnose'))
                    except Exception:
                        receipt['diagnostics_unavailable'] = True
                raise
            finally:
                try:
                    vm.cleanup()
                finally:
                    fixture.cleanup()
                receipt['owned_resources_cleaned'] = True
            receipt.update(status='passed', stage='complete')
    except Exception as error:
        receipt['status'] = 'failed'
        receipt['error_type'] = type(error).__name__
        # Exception messages are from our fixed assertions; subprocess output
        # and generated credentials are deliberately not retained in artifacts.
        receipt['error'] = str(error) if isinstance(error, RuntimeError) else 'Rehearsal infrastructure operation failed'
        print('Blank-host rehearsal failed at ' + receipt.get('stage', 'preflight'), flush=True)
    finally:
        receipt['finished_utc'] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        tls.write_private(args.output, json.dumps(receipt, indent=2) + '\n')
    return 0 if receipt['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())

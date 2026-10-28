#!/usr/bin/env python3
"""Configure ONLY the disposable VM created by blank_host_rehearsal.py.

Generated credentials and disabled calls/billing qualify installation mechanics,
never provider, payment, production DNS, customer or disaster-recovery outcomes.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
from pathlib import Path
import secrets
import re
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
INPUTS = Path('/root/talklee-rehearsal-inputs')
MARKER = Path('/etc/talklee-disposable-installer-vm')
UNITS = ('talky-api', 'talky-voice-gateway', 'talky-voice-worker', 'talky-dialer-worker',
         'talky-reminder-worker', 'talky-dashboard', 'talky-redis', 'asterisk', 'nginx',
         'talky-trunk-status', 'talky-asterisk-reconcile', 'talky-healthwatch')


def run(*args):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def private(path, content):
    with path.open('x', encoding='utf-8') as handle:
        path.chmod(0o600)
        handle.write(content)


def diagnose():
    """Sanitized synthetic-VM service failures, never an arbitrary host reader."""
    if os.geteuid() != 0 or not MARKER.is_file():
        raise RuntimeError('Only the marked disposable VM can be diagnosed')
    payload = json.loads((INPUTS / 'fixture.json').read_text())
    if MARKER.read_text().strip() != payload['token']:
        raise RuntimeError('Disposable VM identity mismatch')
    from urllib.parse import unquote, urlsplit
    secret_values = [payload['runtime_url'], unquote(urlsplit(payload['runtime_url']).password or '')]
    if payload.get('owner_url'):
        secret_values.extend((payload['owner_url'], unquote(urlsplit(payload['owner_url']).password or '')))
    secret_values.extend(payload.get('operator_passwords', {}).values())
    for line in (INPUTS / 'prepared/backend.env').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            if any(part in key for part in ('KEY', 'TOKEN', 'PASSWORD', 'SECRET', '_URL', 'EMAIL_PASS')):
                value = value.strip('"\'')
                secret_values.append(value)
                if key.endswith('_URL'):
                    try:
                        password = urlsplit(value).password
                        if password:
                            secret_values.extend((password, unquote(password)))
                    except ValueError:
                        pass
    def sanitize(text):
        for value in sorted(set(secret_values), key=len, reverse=True):
            if len(value) >= 8:
                text = text.replace(value, '[redacted synthetic credential]')
        return re.sub(r'(?:postgres(?:ql)?|redis)://\S+', '[redacted connection]', text)

    result = {}
    for unit in UNITS:
        state = subprocess.run(['systemctl', 'show', unit, '--property=ActiveState,SubState,Result,ExecMainStatus,User,Group'],
                               capture_output=True, text=True, timeout=10)
        journal = subprocess.run(['journalctl', '-u', unit, '-n', '35', '--no-pager', '-o', 'cat'],
                                 capture_output=True, text=True, timeout=10)
        text = sanitize(journal.stdout[-12000:])
        result[unit] = {'state': state.stdout.strip(), 'sanitized_journal': text}
    failures = []
    for path in sorted(Path('/var/lib/talky-install').glob('command-failure-*.json'))[:3]:
        metadata = path.lstat()
        if (stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0
                and stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_size < 1024 * 1024):
            record = json.loads(path.read_text())
            failures.append({key: sanitize(str(record.get(key, ''))[-16000:])
                             for key in ('binary', 'exit_code', 'stdout_tail', 'stderr_tail')})
    result['sanitized_install_failures'] = failures
    release_failures = []
    for path in sorted(Path('/var/lib/talky-release/private-command-logs').glob('failed-*.log'),
                       key=lambda item: item.lstat().st_mtime_ns)[-5:]:
        metadata = path.lstat()
        if (stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0
                and stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_size < 1024 * 1024):
            release_failures.append(sanitize(path.read_text(errors='replace')[-16000:]))
    result['sanitized_release_failures'] = release_failures
    helper_failures = []
    for path in sorted(Path('/var/lib/talklee-release-rehearsal').glob('child-failure-*.json'),
                       key=lambda item: item.lstat().st_mtime_ns)[-3:]:
        metadata = path.lstat()
        if (stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0
                and stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_size < 1024 * 1024):
            record = json.loads(path.read_text())
            helper_failures.append({key: sanitize(str(record.get(key, ''))[-8192:]) for key in (
                'binary', 'return_code', 'expected_return_code', 'timed_out', 'stdout_tail', 'stderr_tail')})
    result['sanitized_rehearsal_failures'] = helper_failures
    print(json.dumps(result))


def main():
    if os.geteuid() != 0 or not MARKER.is_file():
        raise RuntimeError('Only the explicitly marked disposable installer VM is accepted')
    payload = json.loads((INPUTS / 'fixture.json').read_text())
    if MARKER.read_text().strip() != payload['token']:
        raise RuntimeError('Disposable VM identity mismatch')
    config_path = INPUTS / 'prepared/install.json'
    if config_path.exists():
        raise RuntimeError('Rehearsal inputs already exist; refuse reuse')
    run('apt-get', 'update', '-qq')
    run(sys.executable, str(ROOT / 'scripts/installation/prepare.py'), '--source', str(ROOT),
        '--output', str(config_path.parent))
    config = json.loads(config_path.read_text())
    config.update(api_domain='api.installer.test', dashboard_domain='app.installer.test',
                  admin_domain='admin.installer.test', ssh_source_cidrs=['10.0.2.2/32'],
                  sip_source_cidrs=['10.0.2.2/32'], media_source_cidrs=['10.0.2.2/32'],
                  database_prepared=True, database_ca_file=str(INPUTS / 'ca.crt'),
                  tls_certificate_file=str(INPUTS / 'web.crt'), tls_private_key_file=str(INPUTS / 'web.key'))
    spec = importlib.util.spec_from_file_location('installer', ROOT / 'scripts/installation/install.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    backend = Path(config['backend_env_file'])
    env = installer.read_env(backend)
    for key, value in env.items():
        if value.startswith('REPLACE_'):
            env[key] = secrets.token_urlsafe(40)
    env.update(DATABASE_URL=payload['runtime_url'],
               REDIS_URL='redis://:' + secrets.token_urlsafe(40) + '@127.0.0.1:6379/0',
               SECRETS_MASTER_KEY=secrets.token_hex(32),
               CONNECTOR_ENCRYPTION_KEY=base64.urlsafe_b64encode(os.urandom(32)).decode(),
               TOTP_ENCRYPTION_KEY=base64.urlsafe_b64encode(os.urandom(32)).decode(),
               FRONTEND_URL='https://app.installer.test', API_BASE_URL='https://api.installer.test',
               CORS_ORIGINS='https://app.installer.test,https://admin.installer.test',
               STRIPE_BILLING_DISABLED='1', TELEPHONY_ENABLED='false',
               SMTP_HOST='127.0.0.1', SMTP_PORT='9', SMTP_USE_TLS='false',
               SMTP_FROM_EMAIL='no-mail@installer.test',
               DEEPGRAM_API_KEY='synthetic-disabled-' + secrets.token_hex(24),
               GROQ_API_KEY='synthetic-disabled-' + secrets.token_hex(24),
               CARTESIA_API_KEY='synthetic-disabled-' + secrets.token_hex(24))
    env.pop('STRIPE_SECRET_KEY', None)
    env.pop('STRIPE_WEBHOOK_SECRET', None)
    backend.write_text(''.join(f'{key}={value}\n' for key, value in env.items()))
    for name in ('dashboard_env_file', 'admin_env_file'):
        path = Path(config[name])
        path.write_text(path.read_text().replace('api.example.com', 'api.installer.test'))
    asterisk = Path(config['asterisk_config_directory'])
    configs = {
        'asterisk.conf': '[directories]\nastetcdir=/etc/asterisk\nastmoddir=/usr/lib/x86_64-linux-gnu/asterisk/modules\n'
                         'astvarlibdir=/var/lib/asterisk\nastdbdir=/var/lib/asterisk\nastkeydir=/var/lib/asterisk\n'
                         'astdatadir=/usr/share/asterisk\nastagidir=/usr/share/asterisk/agi-bin\n'
                         'astspooldir=/var/spool/asterisk\nastrundir=/var/run/asterisk\nastlogdir=/var/log/asterisk\n'
                         '[options]\nrunuser=asterisk\nrungroup=asterisk\n'
                         '[files]\nastctlpermissions=0660\nastctlowner=asterisk\nastctlgroup=asterisk\n',
        # An inert endpoint satisfies the existing managed-context contract.
        # No transport, contact, registration or identify mapping can reach a carrier.
        'pjsip.conf': '[global]\ntype=global\n#include pjsip.d/*.conf\n'
                      '[blazedigitel-endpoint]\ntype=endpoint\ncontext=from-talky-inbound\n'
                      'disallow=all\nallow=ulaw\naors=installer-inert-aor\n'
                      '[installer-inert-aor]\ntype=aor\nmax_contacts=0\n',
        'extensions.conf': '[general]\nstatic=yes\nwriteprotect=yes\n'
                           '#tryinclude extensions.d/talky-inbound.conf\n[default]\n',
        'http.conf': '[general]\nenabled=yes\nbindaddr=127.0.0.1\nbindport=8088\n',
        'ari.conf': '[general]\nenabled=yes\npretty=no\n[talky]\ntype=user\nread_only=no\n'
                    'password=' + env['ASTERISK_ARI_PASSWORD'] + '\n',
        'rtp.conf': f"[general]\nrtpstart={config['rtp_port_start']}\nrtpend={config['rtp_port_end']}\n",
        'modules.conf': '[modules]\nautoload=yes\n',
    }
    for name, content in configs.items():
        private(asterisk / name, content)
    config_path.write_text(json.dumps(config, indent=2) + '\n')
    run('cp', str(INPUTS / 'ca.crt'), '/usr/local/share/ca-certificates/talklee-rehearsal.crt')
    run('update-ca-certificates')
    installer.validate_config(config, inputs=True)
    print('Disposable synthetic inputs prepared; calls, payments and email delivery disabled.')


if __name__ == '__main__':
    if sys.argv[1:] == ['--diagnose']:
        diagnose()
    else:
        main()

#!/usr/bin/env python3
"""Fresh-host installer; plan and validate are read-only and work on Windows.

Apply is intentionally not a production update/migration path. It refuses an
existing /opt/talky, consumes a clean immutable local checkout, and expects the
external DB to have been prepared and checked separately. Secrets are never
shell-evaluated or passed as command-line arguments.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import parse_qs, parse_qsl, unquote, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
TARGET = Path('/opt/talky')
STATE = Path('/var/lib/talky-install')
PACKAGES = ('asterisk', 'build-essential', 'ca-certificates', 'cmake', 'curl',
            'docker.io', 'docker-compose-v2', 'git', 'libffi-dev', 'libpq-dev',
            'libssl-dev', 'nginx', 'postgresql-client-16', 'python3.12',
            'python3.12-dev', 'python3.12-venv', 'ufw', 'xz-utils')
SERVICES = ('talky-api', 'talky-voice-worker', 'talky-dialer-worker',
            'talky-reminder-worker', 'talky-voice-gateway')
AST_FILES = ('asterisk.conf', 'pjsip.conf', 'extensions.conf', 'http.conf',
             'ari.conf', 'rtp.conf', 'modules.conf')
SECRET_FIELDS = ('backend_env_file', 'tls_private_key_file')
KEYS = {'schema_version', 'source_sha', 'frontend_mode', 'api_domain',
        'dashboard_domain', 'admin_domain', 'ssh_port', 'ssh_source_cidrs',
        'sip_source_cidrs', 'media_source_cidrs', 'sip_port', 'rtp_port_start',
        'rtp_port_end', 'backend_env_file', 'dashboard_env_file', 'admin_env_file',
        'asterisk_config_directory', 'tls_certificate_file', 'tls_private_key_file',
        'python_requirements_lock', 'apt_versions_file', 'redis_image',
        'node_version', 'node_archive_sha256', 'database_prepared', 'database_ca_file'}


class InstallError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InstallError(message)


def sha256(path: Path) -> str:
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read_env(path: Path) -> dict[str, str]:
    """Restricted shared dotenv/systemd format, without shell expansion."""
    values = {}
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Z][A-Z0-9_]*)=(.*)', line)
        require(match is not None, f'{path.name}:{number}: expected KEY=value')
        key, value = match.groups()
        require(key not in values, f'{path.name}:{number}: duplicate key {key}')
        if value.startswith(('"', "'")):
            require(value.endswith(value[0]) and len(value) >= 2,
                    f'{path.name}:{number}: mismatched quotes')
            value = value[1:-1]
        require(not any(char in value for char in ('\n', '\r', '\x00', '\\')),
                f'{path.name}:{number}: unsupported escape')
        values[key] = value
    return values


def secure_input(path: Path, secret: bool = False) -> None:
    require(path.is_absolute() and path.is_file() and not path.is_symlink(),
            f'Required regular absolute input file missing: {path}')
    if secret and os.name == 'posix':
        require(stat.S_IMODE(path.stat().st_mode) & 0o077 == 0,
                f'Secret input must be mode 0600 or stricter: {path}')


def validate_config(config: dict, *, inputs: bool = False) -> dict:
    require(isinstance(config, dict), 'Configuration must be a JSON object')
    require(set(config) == KEYS, 'Configuration keys differ from install.example.json')
    require(config['schema_version'] == 1, 'Unsupported configuration schema')
    require(config['frontend_mode'] in ('external', 'linux'),
            'frontend_mode must be external or linux')
    for name in ('api_domain', 'dashboard_domain', 'admin_domain'):
        value = config[name]
        require(isinstance(value, str) and len(value) <= 253 and
                re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', value) and
                '.' in value and '..' not in value, f'Invalid {name}')
    require(len({config[k] for k in ('api_domain', 'dashboard_domain', 'admin_domain')}) == 3,
            'API, dashboard and admin need distinct hostnames')
    for name in ('ssh_port', 'sip_port', 'rtp_port_start', 'rtp_port_end'):
        require(type(config[name]) is int and 1 <= config[name] <= 65535,
                f'Invalid {name}')
    require(config['rtp_port_start'] <= config['rtp_port_end'], 'Invalid RTP range')
    for name in ('ssh_source_cidrs', 'sip_source_cidrs', 'media_source_cidrs'):
        require(isinstance(config[name], list) and config[name], f'{name} is required')
        for value in config[name]:
            try:
                network = ipaddress.ip_network(value, strict=True)
            except ValueError:
                raise InstallError(f'Invalid network in {name}') from None
            require(network.prefixlen != 0, f'Unrestricted network is not accepted in {name}')
    require(type(config['database_prepared']) is bool, 'database_prepared must be boolean')
    if not inputs:
        return config
    require(re.fullmatch(r'[a-f0-9]{40}', config['source_sha']) is not None and
            set(config['source_sha']) != {'0'}, 'source_sha must be a reviewed full Git SHA')
    require(config['database_prepared'],
            'Prepare the external database and verify its schema before installation')
    require(re.fullmatch(r'redis:[0-9][a-zA-Z0-9_.-]*@sha256:[a-f0-9]{64}',
                         config['redis_image']) is not None, 'Pin Redis to a reviewed image digest')
    paths = ['backend_env_file', 'tls_certificate_file', 'tls_private_key_file', 'database_ca_file',
             'python_requirements_lock', 'apt_versions_file']
    if config['frontend_mode'] == 'linux':
        paths += ['dashboard_env_file', 'admin_env_file']
        require(re.fullmatch(r'22\.(?:1[89]|[2-9][0-9])\.\d+', config['node_version']) is not None,
                'Linux frontends require a pinned Node 22 version >=22.18')
        require(re.fullmatch(r'[a-f0-9]{64}', config['node_archive_sha256']) is not None,
                'Pin the official Node Linux x64 archive SHA256')
    for name in paths:
        secure_input(Path(config[name]), name in SECRET_FIELDS or name.endswith('_env_file'))
    if config['frontend_mode'] == 'linux':
        dashboard = read_env(Path(config['dashboard_env_file']))
        admin = read_env(Path(config['admin_env_file']))
        api_url = f"https://{config['api_domain']}/api/v1"
        require(dashboard.get('NEXT_PUBLIC_API_BASE_URL', '').rstrip('/') == api_url and
                dashboard.get('NEXT_PUBLIC_APP_ENV') == 'production' and
                dashboard.get('NODE_ENV', 'production') == 'production',
                'Dashboard environment must use the configured API URL and production mode')
        require(admin.get('VITE_API_BASE_URL', '').rstrip('/') == api_url,
                'Admin environment must use the configured API URL')
        for name in ('VITE_ADMIN_DEV_MODE', 'VITE_USE_DUMMY_AUTH'):
            require(admin.get(name, 'false').lower() == 'false', f'Admin {name} must be false')
    versions = json.loads(Path(config['apt_versions_file']).read_text())
    require(isinstance(versions, dict) and set(PACKAGES).issubset(versions) and
            all(re.fullmatch(r'[a-z0-9][a-z0-9+.-]*(?::amd64)?', k) for k in versions),
            'apt_versions_file must pin each package reported by packages command')
    require(all(isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9.+:~_-]+', v)
                for v in versions.values()), 'Invalid APT package version')
    lock = Path(config['python_requirements_lock']).read_text()
    logical = lock.replace('\\\n', ' ').replace('\\\r\n', ' ').splitlines()
    rows = [line.strip() for line in logical if line.strip() and not line.lstrip().startswith('#')]
    require(rows and all(re.fullmatch(
        r'[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.+!-]+(?:\s+--hash=sha256:[a-f0-9]{64})+',
        line) for line in rows), 'Python lock must contain exact versions with SHA256 hashes only')
    asterisk = Path(config['asterisk_config_directory'])
    require(asterisk.is_absolute() and asterisk.is_dir() and not asterisk.is_symlink(),
            'Supply an absolute directory of reviewed production Asterisk configuration')
    for filename in AST_FILES:
        secure_input(asterisk / filename, filename in ('ari.conf', 'pjsip.conf'))
    for path in asterisk.iterdir():
        require(path.is_file() and path.suffix == '.conf' and not path.is_symlink(),
                'Asterisk directory must contain regular .conf files only')
        text = path.read_text()
        require('local_only_change_me' not in text and 'password=1002' not in text,
                'Lab Asterisk credentials are not accepted')
        require(not re.search(r'^\s*#(?:exec|include|tryinclude)\s+[/~]', text, re.M),
                'Asterisk includes must be relative to the supplied config directory')
        require(not re.search(r'^\s*#exec\b', text, re.M), 'Asterisk #exec is not accepted')
        for include in re.findall(r'^\s*#(?:include|tryinclude)\s+(.+)', text, re.M):
            include = include.strip().strip('"')
            require(include == 'pjsip.d/*.conf' or
                    (re.fullmatch(r'[A-Za-z0-9_-]+\.conf', include) and (asterisk / include).is_file()),
                    'Asterisk include must resolve inside the supplied config or generated pjsip.d')
    http = (asterisk / 'http.conf').read_text()
    require(re.search(r'^\s*bindaddr\s*=\s*127\.0\.0\.1\s*(?:;.*)?$', http, re.M) is not None,
            'Asterisk HTTP/ARI must bind to 127.0.0.1')
    ast_main = (asterisk / 'asterisk.conf').read_text()
    for key, value in (('astctlpermissions', '0660'), ('astctlowner', 'asterisk'),
                       ('astctlgroup', 'asterisk'), ('runuser', 'asterisk'), ('rungroup', 'asterisk')):
        require(re.search(r'^\s*' + key + r'\s*=\s*' + value + r'\s*(?:;.*)?$', ast_main, re.M) is not None,
                f'Asterisk config must set {key}={value} for the service account')
    require('#include pjsip.d/*.conf' in (asterisk / 'pjsip.conf').read_text(),
            'pjsip.conf must include generated pjsip.d/*.conf')
    env = read_env(Path(config['backend_env_file']))
    require('MIGRATION_DATABASE_URL' not in env, 'Keep migration credentials out of the runtime environment')
    require(not env.get('READ_DATABASE_URL'),
            'READ_DATABASE_URL needs separate qualification; leave it unset for this fresh-host profile')
    for name in ('DATABASE_URL', 'REDIS_URL', 'JWT_SECRET', 'SECRET_KEY',
                 'CONNECTOR_ENCRYPTION_KEY', 'SECRETS_MASTER_KEY', 'TELEPHONY_METRICS_TOKEN', 'INTERNAL_SERVICE_TOKEN',
                 'VOICE_GATEWAY_AUTH_TOKEN', 'BACKEND_INTERNAL_URL',
                 'VOICE_GATEWAY_CALLBACK_HOST', 'ASTERISK_ARI_USER',
                 'ASTERISK_ARI_PASSWORD', 'FRONTEND_URL', 'API_BASE_URL'):
        require(bool(env.get(name, '').strip()) and 'REPLACE' not in env[name] and 'change_me' not in env[name],
                f'Configure backend {name}')
    require(env.get('ENVIRONMENT') == 'production', 'ENVIRONMENT must be production')
    validate_production_gate_inputs(env, rows)
    require(env['FRONTEND_URL'] == f"https://{config['dashboard_domain']}" and
            env['API_BASE_URL'] == f"https://{config['api_domain']}", 'Backend domain mismatch')
    cors = {item.strip().rstrip('/') for item in env.get('CORS_ORIGINS', '').split(',') if item.strip()}
    require({f"https://{config['dashboard_domain']}", f"https://{config['admin_domain']}"}.issubset(cors)
            and '*' not in cors, 'CORS_ORIGINS must explicitly allow dashboard and Admin')
    require(env['BACKEND_INTERNAL_URL'] == 'http://127.0.0.1:8000' and
            env['VOICE_GATEWAY_CALLBACK_HOST'] == '127.0.0.1', 'Gateway callback must be local')
    require(env.get('ASTERISK_ARI_HOST', '127.0.0.1') == '127.0.0.1', 'ARI must stay local')
    ari = configparser.ConfigParser(interpolation=None, strict=False,
                                   inline_comment_prefixes=(';',), comment_prefixes=('#', ';'))
    ari.read(asterisk / 'ari.conf')
    user = env['ASTERISK_ARI_USER']
    require(re.fullmatch(r'[A-Za-z0-9_-]+', user) is not None and
            re.fullmatch(r'[A-Za-z0-9_-]{32,}', env['ASTERISK_ARI_PASSWORD']) is not None,
            'Use a simple ARI user and 32+ URL-safe password')
    require(ari.has_section(user) and ari.get(user, 'password', fallback='') == env['ASTERISK_ARI_PASSWORD']
            and ari.get(user, 'read_only', fallback='').lower() in ('no', 'false', '0')
            and ari.get('general', 'enabled', fallback='').lower() in ('yes', 'true', '1'),
            'Reviewed ARI user/password/permissions must match the runtime environment')
    http_settings = configparser.ConfigParser(interpolation=None, strict=False,
                                              inline_comment_prefixes=(';',), comment_prefixes=('#', ';'))
    http_settings.read(asterisk / 'http.conf')
    require(http_settings.get('general', 'bindport', fallback='') == env.get('ASTERISK_ARI_PORT', '8088')
            and http_settings.get('general', 'enabled', fallback='').lower() in ('yes', 'true', '1'),
            'Reviewed Asterisk HTTP port/enabled settings must match the runtime environment')
    require(env.get('TELEPHONY_PJSIP_AUTO_RELOAD', '').lower() in ('true', 'yes', 'on', '1'),
            'Enable TELEPHONY_PJSIP_AUTO_RELOAD for working tenant trunk lifecycle')
    require(env.get('CLEANUP_DRY_RUN', 'true').lower() == 'true',
            'Keep cleanup dry-run until retention acceptance is separately recorded')
    for key, allowed in {
        'LOCAL_RECORDINGS_DIR': ('./recordings', '/opt/talky/backend/recordings'),
        'RECORDINGS_STORAGE_DIR': ('audio_files/storage', '/opt/talky/backend/audio_files/storage'),
        'LOCAL_FEEDBACK_DIR': ('./feedback_audio', '/opt/talky/backend/feedback_audio'),
        'TELEPHONY_PJSIP_CONF_DIR': ('/etc/asterisk/pjsip.d',),
    }.items():
        require(key not in env or env[key] in allowed,
                f'{key} must use the documented fresh-host writable directory')
    for key in ('JWT_SECRET', 'SECRET_KEY', 'INTERNAL_SERVICE_TOKEN', 'VOICE_GATEWAY_AUTH_TOKEN'):
        require(env[key] == env[key].strip() and len(env[key]) >= 32,
                f'{key} must have at least 32 characters without surrounding whitespace')
    require(env['INTERNAL_SERVICE_TOKEN'] != env['VOICE_GATEWAY_AUTH_TOKEN'],
            'Gateway and internal service tokens must be distinct')
    try:
        db = urlsplit(env['DATABASE_URL'])
        redis = urlsplit(env['REDIS_URL'])
        require(db.scheme in ('postgres', 'postgresql') and db.hostname and db.username
                and bool(unquote(db.path).lstrip('/').strip()),
                'DATABASE_URL must be a PostgreSQL URL')
        query_pairs = parse_qsl(db.query, keep_blank_values=True)
        require(len({key for key, _ in query_pairs}) == len(query_pairs),
                'DATABASE_URL query options must not be duplicated')
        require(not {'host', 'hostaddr', 'port', 'user', 'password', 'dbname', 'database',
                     'service', 'passfile', 'ssl'} & {key.lower() for key, _ in query_pairs},
                'DATABASE_URL query must not override target, identity or SSL policy')
        try:
            address = ipaddress.ip_address(db.hostname)
        except ValueError:
            address = None
        require(db.hostname.lower().rstrip('.') not in ('localhost', 'postgres') and
                not (address and (address.is_loopback or address.is_unspecified)),
                'Database must be separate from the application host')
        require(parse_qs(db.query).get('sslmode') == ['verify-full'],
                'DATABASE_URL must verify TLS using sslmode=verify-full')
        require(parse_qs(db.query).get('sslrootcert') == ['/etc/ssl/certs/talky-db-ca.pem'],
                'DATABASE_URL must use sslrootcert=/etc/ssl/certs/talky-db-ca.pem')
        require(unquote(db.username) not in ('postgres', 'root'), 'Use a restricted application DB role')
        require(redis.scheme == 'redis' and redis.hostname == '127.0.0.1' and
                redis.port == 6379 and redis.password and re.fullmatch(r'[A-Za-z0-9_-]{32,}', redis.password),
                'REDIS_URL must use loopback port 6379 and a 32+ character URL-safe password')
    except ValueError:
        raise InstallError('Malformed database or Redis connection URL') from None
    return config


def validate_production_gate_inputs(env: dict[str, str], lock_rows: list[str]) -> None:
    """Reject the production gate's configuration failures before OS mutation.

    SDK availability is checked against the qualified lock at this stage and
    against the actual installed environment by enforce_production_gate later.
    This profile installs Asterisk only, so auto/FreeSWITCH is not a valid mode.
    """
    require(env.get('TELEPHONY_ADAPTER', '').lower() == 'asterisk',
            'TELEPHONY_ADAPTER must explicitly be asterisk for this installation profile')
    for key in ('TELEPHONY_DEV_BYPASS_GUARD_ERRORS', 'TELEPHONY_LOCAL_DEV'):
        require(env.get(key, '').strip().lower() in ('', '0', 'false', 'no'),
                f'{key} is prohibited by the production startup gate')
    require(env.get('INBOUND_TRANSFER_STAGING_PROOF_ENABLED', '').strip().lower()
            in ('', '0', 'false', 'no', 'off', 'disabled'),
            'INBOUND_TRANSFER_STAGING_PROOF_ENABLED is prohibited in production')
    for key in ('INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID', 'INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID'):
        require(not env.get(key, '').strip(), f'{key} must be unset in production')
    require(env.get('CALLER_ID_ENFORCEMENT_MODE', '').strip().lower() in ('', 'enforce'),
            'CALLER_ID_ENFORCEMENT_MODE must enforce caller ID in production')
    for key in ('TELEPHONY_INBOUND_REQUIRE_TENANT', 'INBOUND_STRICT_ROUTING', 'TELEPHONY_STRICT_INBOUND_ROUTING'):
        require(env.get(key, '').strip().lower() not in ('0', 'false', 'no', 'off', 'disabled'),
                f'{key} cannot disable production tenant routing')
    stripe_key = env.get('STRIPE_SECRET_KEY', '').strip()
    billing_disabled = env.get('STRIPE_BILLING_DISABLED', '').strip().lower() in ('1', 'true', 'yes')
    require(stripe_key or billing_disabled,
            'STRIPE_SECRET_KEY is required unless STRIPE_BILLING_DISABLED is explicitly acknowledged')
    require(not stripe_key or stripe_key.startswith('sk_live_'),
            'Production STRIPE_SECRET_KEY must be a live key; test keys are refused')
    if stripe_key:
        packages = {line.split('==', 1)[0].split('[', 1)[0].lower().replace('_', '-') for line in lock_rows}
        require('stripe' in packages, 'The qualified Python lock must include Stripe when billing is configured')


def steps(config: dict) -> list[str]:
    return [
        'Refuse existing installation, require Ubuntu 24.04 amd64 and a clean reviewed Git SHA',
        'Install exact APT package versions with package auto-start temporarily inhibited',
        'Create talky service account; clone immutable source into /opt/talky',
        'Install hash-locked Python packages and build/test matching C++ gateway',
        'Verify external database TLS, restricted runtime role, and exact Alembic head',
        'Install reviewed Asterisk config and loopback-only digest-pinned Redis',
        'Install existing systemd units with least-privilege account drop-ins',
        ('Build dashboard/Admin from npm lockfiles and serve on Linux' if config['frontend_mode'] == 'linux'
         else 'Keep dashboard/Admin at the externally managed URLs'),
        'Install TLS nginx site and source-restricted SSH/SIP/RTP firewall rules',
        'Start services, verify local health/readiness, record exact package versions and input hashes',
        'Record real controlled-call and clean-server acceptance separately (never automatic)'
    ]


def run(args: list[str], *, cwd: Path | None = None, env: dict | None = None,
        capture: bool = False) -> str:
    # Capture rather than echo commands/environment: package tools may reveal URLs.
    result = subprocess.run(args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise InstallError(f'{Path(args[0]).name} failed (exit {result.returncode}); '
                           'inspect the service/package locally; command output was suppressed to protect secrets')
    return result.stdout if capture else ''


def write(path: Path, content: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8', newline='\n')
    path.chmod(mode)


def copy_secret(source: str | Path, target: Path, mode: int = 0o600) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(mode)


def unit_dropin(*, asterisk: bool = False) -> str:
    return ('[Unit]\nAfter=network-online.target talky-redis.service asterisk.service\n'
            'Wants=network-online.target talky-redis.service\n[Service]\n'
            'User=talky\nGroup=talky\n' +
            ('SupplementaryGroups=asterisk\n' if asterisk else '') +
            'UMask=0027\nNoNewPrivileges=true\nPrivateTmp=true\n')


def nginx_site(config: dict) -> str:
    domains = [config['api_domain']]
    if config['frontend_mode'] == 'linux':
        domains += [config['dashboard_domain'], config['admin_domain']]
    body = ('map $http_upgrade $talky_connection { default upgrade; "" close; }\n'
            'server { listen 80; server_name ' + ' '.join(domains) +
            '; return 308 https://$host$request_uri; }\n')
    for domain in domains:
        body += ('server {\n listen 443 ssl;\n server_name ' + domain + ';\n'
                 ' ssl_certificate /etc/talky/tls/fullchain.pem;\n'
                 ' ssl_certificate_key /etc/talky/tls/privkey.pem;\n'
                 ' ssl_protocols TLSv1.2 TLSv1.3;\n client_max_body_size 25m;\n')
        if domain == config['admin_domain']:
            body += (' root /opt/talky/Admin/frontend/dist;\n'
                     ' location / { try_files $uri $uri/ /index.html; }\n')
        else:
            port = 8000 if domain == config['api_domain'] else 3000
            body += (' location / {\n proxy_pass http://127.0.0.1:' + str(port) + ';\n'
                     ' proxy_http_version 1.1;\n proxy_set_header Host $host;\n'
                     ' proxy_set_header X-Real-IP $remote_addr;\n'
                     ' proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n'
                     ' proxy_set_header X-Forwarded-Proto $scheme;\n'
                     ' proxy_set_header Upgrade $http_upgrade;\n'
                     ' proxy_set_header Connection $talky_connection;\n'
                     ' proxy_buffering off;\n proxy_read_timeout 3600s;\n }\n')
        body += '}\n'
    return body


def checked_origin(source: Path) -> str:
    origin = run(['git', 'remote', 'get-url', 'origin'], cwd=source, capture=True).strip()
    if re.fullmatch(r'git@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+', origin):
        return origin
    parsed = urlsplit(origin)
    require(parsed.scheme in ('https', 'ssh') and bool(parsed.hostname) and
            not parsed.password and (parsed.scheme == 'ssh' or not parsed.username) and
            not parsed.query and not parsed.fragment,
            'Source origin must be a credential-free HTTPS or SSH repository URL')
    return origin


def ensure_fresh_host(config: dict, source: Path) -> None:
    require(sys.platform == 'linux' and platform.machine() == 'x86_64',
            'Apply supports Ubuntu 24.04 amd64 only')
    require(os.geteuid() == 0, 'Apply must run as root on the new host')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines()
                   if '=' in line)
    require(release.get('ID', '').strip('"') == 'ubuntu' and
            release.get('VERSION_ID', '').strip('"') == '24.04', 'Ubuntu 24.04 is required')
    require(Path('/run/systemd/system').is_dir(), 'A systemd VM/server is required')
    for path in (TARGET, STATE, Path('/etc/talky'), Path('/etc/systemd/system/talky-api.service'),
                 Path('/etc/nginx'), Path('/etc/asterisk'), Path('/var/lib/docker'),
                 Path('/usr/sbin/policy-rc.d')):
        require(not path.exists() and not path.is_symlink(), f'Refusing existing path: {path}')
    require(not run(['git', 'status', '--porcelain'], cwd=source, capture=True).strip(),
            'Installer source checkout must be completely clean')
    require(run(['git', 'rev-parse', 'HEAD'], cwd=source, capture=True).strip() == config['source_sha'],
            'Source checkout must equal the reviewed source_sha')
    checked_origin(source)
    # Existing runtime services are evidence that this is not an empty host.
    for unit in ('asterisk', 'nginx', 'docker', *SERVICES):
        result = subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=False,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        require(result.returncode != 0, f'Refusing active pre-existing service: {unit}')
    ssh = os.environ.get('SSH_CONNECTION', '').split()
    if ssh:
        require(len(ssh) == 4 and int(ssh[3]) == config['ssh_port'],
                'Configured SSH port differs from current connection')
        require(any(ipaddress.ip_address(ssh[0]) in ipaddress.ip_network(cidr)
                    for cidr in config['ssh_source_cidrs']),
                'SSH source allow-list would exclude the current operator')


DATABASE_CHECK_SCRIPT = '''import os, psycopg2
from alembic.config import Config
from alembic.script import ScriptDirectory
def check(condition, message):
    if not condition:
        raise RuntimeError(message)
with psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10) as c:
    with c.cursor() as q:
        q.execute("SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user")
        role=q.fetchone()
        check(role and not any(role), "runtime role cannot be superuser/BYPASSRLS/CREATEDB/CREATEROLE")
        q.execute("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()")
        check(q.fetchone()==(True,), "TLS required")
        q.execute("SELECT version_num FROM alembic_version")
        actual={r[0] for r in q.fetchall()}
        expected=set(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
        check(expected and actual==expected, "prepare and verify schema before install")
'''


def runtime_environment(env: dict[str, str]) -> dict[str, str]:
    # Match the file-based service environment instead of inheriting operator
    # development flags, PGOPTIONS, PYTHONPATH or alternate database identities.
    base = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT') if key in os.environ}
    return {**base, 'HOME': '/var/lib/talky', 'PYTHONDONTWRITEBYTECODE': '1', **env}


def database_check(env: dict, backend: Path) -> None:
    run([str(backend / 'venv/bin/python'), '-c', DATABASE_CHECK_SCRIPT], cwd=backend,
        env=runtime_environment(env))


def apply(config: dict, source: Path) -> None:
    validate_config(config, inputs=True)
    ensure_fresh_host(config, source)
    versions = json.loads(Path(config['apt_versions_file']).read_text())
    env = read_env(Path(config['backend_env_file']))
    STATE.mkdir(mode=0o700)
    write(STATE / 'state.json', json.dumps({'status': 'installing', 'source_sha': config['source_sha']}) + '\n', 0o600)
    # Prevent package installation from exposing default Asterisk/nginx configs.
    policy = Path('/usr/sbin/policy-rc.d')
    write(policy, '#!/bin/sh\nexit 101\n', 0o755)
    try:
        print('Installing pinned OS packages...', flush=True)
        run(['apt-get', 'update'])
        run(['apt-get', 'install', '--yes', '--no-install-recommends',
             *(f'{name}={versions[name]}' for name in sorted(versions))],
            env={**os.environ, 'DEBIAN_FRONTEND': 'noninteractive'})
    finally:
        policy.unlink()
    run(['useradd', '--system', '--create-home', '--home-dir', '/var/lib/talky',
         '--shell', '/usr/sbin/nologin', '--user-group', 'talky'])
    run(['usermod', '--append', '--groups', 'asterisk', 'talky'])
    run(['git', 'clone', '--no-hardlinks', '--no-checkout', str(source), str(TARGET)])
    run(['git', 'remote', 'set-url', 'origin', checked_origin(source)], cwd=TARGET)
    run(['git', 'checkout', '--detach', config['source_sha']], cwd=TARGET)
    require(run(['git', 'rev-parse', 'HEAD'], cwd=TARGET, capture=True).strip() == config['source_sha'],
            'Installed checkout SHA mismatch')
    backend = TARGET / 'backend'
    copy_secret(config['backend_env_file'], backend / '.env', 0o640)
    run(['chown', 'root:talky', str(backend / '.env')])
    runtime = TARGET / 'runtime'
    (runtime / 'bin').mkdir(parents=True)
    for directory in (backend / 'logs', backend / 'recordings', backend / 'tmp',
                      backend / 'uploads', backend / 'audio_files/storage',
                      backend / 'feedback_audio', runtime / 'state'):
        directory.mkdir(parents=True, exist_ok=True)
        run(['chown', '-R', 'talky:talky', str(directory)])
    print('Installing locked Python dependencies and building the gateway...', flush=True)
    run(['python3.12', '-m', 'venv', str(backend / 'venv')])
    # pip uses only hashes in this qualified lock; requirements.txt is not a lock.
    run([str(backend / 'venv/bin/python'), '-m', 'pip', 'install', '--require-hashes',
         '-r', config['python_requirements_lock']])
    run([str(backend / 'venv/bin/python'), '-m', 'pip', 'check'])
    run([str(backend / 'venv/bin/python'), '-c',
         'from app.core.prod_gate import enforce_production_gate; enforce_production_gate()'],
        cwd=backend, env=runtime_environment(env))
    run(['bash', str(backend / 'scripts/build_voice_gateway_release.sh'),
         str(runtime / 'bin/voice_gateway')])
    run(['chown', 'root:talky', str(runtime / 'bin')])
    (runtime / 'bin').chmod(0o750)
    run(['chown', 'root:talky', str(runtime / 'bin/voice_gateway')])
    copy_secret(config['database_ca_file'], Path('/etc/ssl/certs/talky-db-ca.pem'), 0o644)
    database_check(env, backend)
    # This directory is explicitly supplied; repository lab SIP routes are never copied.
    for path in Path(config['asterisk_config_directory']).glob('*.conf'):
        copy_secret(path, Path('/etc/asterisk') / path.name, 0o640)
        run(['chown', 'root:asterisk', str(Path('/etc/asterisk') / path.name)])
    generated_sip = Path('/etc/asterisk/pjsip.d')
    generated_sip.mkdir(exist_ok=True)
    run(['chown', 'asterisk:asterisk', str(generated_sip)])
    generated_sip.chmod(0o2770)
    redis_password = urlsplit(env['REDIS_URL']).password
    Path('/etc/talky').mkdir(mode=0o700, exist_ok=True)
    Path('/etc/talky').chmod(0o700)
    write(Path('/etc/talky/redis.conf'), 'bind 0.0.0.0\nprotected-mode yes\nport 6379\n'
          'appendonly yes\nappendfsync everysec\nmaxmemory-policy noeviction\n'
          f'requirepass {redis_password}\n', 0o444)
    # The root-only host directory protects this file. The container's redis
    # account must be able to read its read-only bind mount after dropping root.
    write(Path('/etc/talky/compose.yml'),
          'name: talky-rebuild\nservices:\n  redis:\n    image: ' + config['redis_image'] + '\n'
          '    restart: unless-stopped\n    command: ["redis-server", "/usr/local/etc/redis/redis.conf"]\n'
          '    ports: ["127.0.0.1:6379:6379"]\n'
          '    volumes:\n      - /etc/talky/redis.conf:/usr/local/etc/redis/redis.conf:ro\n'
          '      - talky_redis:/data\n'
          '    healthcheck:\n      test: ["CMD-SHELL", "redis-cli ping | grep -q NOAUTH"]\n'
          '      interval: 10s\n      timeout: 3s\n      retries: 5\n'
          'volumes:\n  talky_redis:\n', 0o600)
    write(Path('/etc/systemd/system/talky-redis.service'),
          '[Unit]\nDescription=Talky Redis\nRequires=docker.service\nAfter=docker.service\n'
          '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
          'ExecStart=/usr/bin/docker compose -f /etc/talky/compose.yml up -d --wait\n'
          'ExecStop=/usr/bin/docker compose -f /etc/talky/compose.yml stop\n'
          '[Install]\nWantedBy=multi-user.target\n')
    for path in (backend / 'systemd').iterdir():
        if path.suffix in ('.service', '.timer', '.target'):
            shutil.copyfile(path, Path('/etc/systemd/system') / path.name)
            if path.suffix == '.service':
                write(Path('/etc/systemd/system') / (path.name + '.d') / '10-fresh-host.conf',
                      unit_dropin(asterisk=path.stem == 'talky-trunk-status'))
    write(Path('/etc/systemd/system/talky-migrate.service.d/20-migration-identity.conf'),
          '[Service]\nEnvironmentFile=/etc/talky/migration.env\n'
          'ExecStartPre=/opt/talky/backend/venv/bin/python -c "import os,sys; '
          "sys.exit(0 if os.environ.get('MIGRATION_DATABASE_URL') else 'MIGRATION_DATABASE_URL is required')" + '"\n')
    # Loopback bind; nginx is the only public HTTP listener.
    write(Path('/etc/systemd/system/talky-api.service.d/20-loopback.conf'),
          '[Service]\nExecStart=\nExecStart=/opt/talky/backend/venv/bin/uvicorn app.main:app '
          '--host 127.0.0.1 --port 8000 --workers 1 --ws websockets '
          '--ws-ping-interval 30 --ws-ping-timeout 5\n')
    if config['frontend_mode'] == 'linux':
        install_frontends(config)
    copy_secret(config['tls_certificate_file'], Path('/etc/talky/tls/fullchain.pem'), 0o644)
    copy_secret(config['tls_private_key_file'], Path('/etc/talky/tls/privkey.pem'))
    write(Path('/etc/nginx/sites-available/talky'), nginx_site(config))
    Path('/etc/nginx/sites-enabled/talky').symlink_to('/etc/nginx/sites-available/talky')
    default = Path('/etc/nginx/sites-enabled/default')
    if default.is_symlink():
        default.unlink()
    run(['nginx', '-t'])
    configure_firewall(config)
    print('Starting services and checking readiness...', flush=True)
    run(['systemctl', 'daemon-reload'])
    run(['systemctl', 'enable', '--now', 'docker', 'talky-redis', 'asterisk'])
    run(['systemctl', 'enable', '--now', 'talky-voice-gateway'])
    run(['systemctl', 'enable', '--now', *SERVICES[:-1], 'talky-healthwatch.timer',
         'talky-trunk-status.timer', 'nginx'])
    if config['frontend_mode'] == 'linux':
        run(['systemctl', 'enable', '--now', 'talky-dashboard'])
    for unit in ('asterisk', 'nginx', 'talky-redis', *SERVICES):
        run(['systemctl', 'is-active', '--quiet', unit])
    for url in ('http://127.0.0.1:18080/ready', 'http://127.0.0.1:8000/health',
                'http://127.0.0.1:8000/api/v1/healthz/ready',
                'http://127.0.0.1:8000/api/v1/healthz/deep'):
        healthy = False
        for _ in range(12):
            try:
                with urlopen(url, timeout=10) as response:
                    healthy = response.status == 200
                    if url.endswith(':18080/ready'):
                        payload = json.load(response)
                        healthy = healthy and payload.get('ready') is True and payload.get('protocol_version') == 2 \
                            and 'pcmu' in payload.get('codecs', [])
                if healthy:
                    break
            except OSError:
                pass
            time.sleep(5)
        require(healthy, f'Health check failed: {url}')
    # Resolve to this new host while validating the supplied certificate name.
    run(['curl', '--fail', '--silent', '--show-error', '--max-time', '15', '--resolve',
         f"{config['api_domain']}:443:127.0.0.1", f"https://{config['api_domain']}/health"])
    if config['frontend_mode'] == 'linux':
        run(['systemctl', 'is-active', '--quiet', 'talky-dashboard'])
        for key in ('dashboard_domain', 'admin_domain'):
            run(['curl', '--fail', '--silent', '--show-error', '--max-time', '15', '--resolve',
                 f"{config[key]}:443:127.0.0.1", f"https://{config[key]}/"])
    receipt = {'status': 'installed_health_checked', 'source_sha': config['source_sha'],
               'frontend_mode': config['frontend_mode'], 'controlled_call_verified': False,
               'production_recovery_verified': False, 'created_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               'python_lock_sha256': sha256(Path(config['python_requirements_lock'])),
               'apt_versions_sha256': sha256(Path(config['apt_versions_file'])),
               'redis_image': config['redis_image']}
    write(STATE / 'receipt.json', json.dumps(receipt, indent=2) + '\n', 0o600)
    write(STATE / 'apt-installed.tsv', run(['dpkg-query', '-W', '-f=${Package}\t${Version}\n'], capture=True), 0o600)
    write(STATE / 'python-installed.txt', run([str(backend / 'venv/bin/python'), '-m', 'pip', 'freeze'], capture=True), 0o600)
    write(STATE / 'state.json', json.dumps({'status': 'installed_health_checked'}) + '\n', 0o600)
    print('Host installed and health checked. Receipt: /var/lib/talky-install/receipt.json')
    print('Controlled call, backup restore and independent rebuild acceptance remain required.')


def configure_firewall(config: dict) -> None:
    run(['ufw', 'default', 'deny', 'incoming'])
    run(['ufw', 'default', 'allow', 'outgoing'])
    for source in config['ssh_source_cidrs']:
        run(['ufw', 'allow', 'from', source, 'to', 'any', 'port', str(config['ssh_port']), 'proto', 'tcp'])
    for port in ('80', '443'):
        run(['ufw', 'allow', port + '/tcp'])
    for source in config['sip_source_cidrs']:
        for protocol in ('udp', 'tcp'):
            run(['ufw', 'allow', 'from', source, 'to', 'any', 'port', str(config['sip_port']), 'proto', protocol])
    for source in config['media_source_cidrs']:
        run(['ufw', 'allow', 'from', source, 'to', 'any', 'port',
             f"{config['rtp_port_start']}:{config['rtp_port_end']}", 'proto', 'udp'])
    run(['ufw', '--force', 'enable'])


def install_frontends(config: dict) -> None:
    version = config['node_version']
    name = f'node-v{version}-linux-x64'
    with tempfile.TemporaryDirectory(prefix='talky-node-') as tmp:
        archive = Path(tmp) / 'node.tar.xz'
        with urlopen(f'https://nodejs.org/dist/v{version}/{name}.tar.xz', timeout=60) as response:
            with archive.open('wb') as output:
                shutil.copyfileobj(response, output)
        require(sha256(archive) == config['node_archive_sha256'], 'Node archive checksum mismatch')
        with tarfile.open(archive) as bundle:
            bundle.extractall('/opt', filter='data')
    for binary in ('node', 'npm', 'npx'):
        link = Path('/usr/local/bin') / binary
        require(not link.exists() and not link.is_symlink(), f'Refusing existing {link}')
        link.symlink_to(Path('/opt') / name / 'bin' / binary)
    for relative, key in (('Talk-Leee', 'dashboard_env_file'), ('Admin/frontend', 'admin_env_file')):
        directory = TARGET / relative
        require((directory / 'package-lock.json').is_file(), f'Missing {relative} lockfile')
        copy_secret(config[key], directory / '.env.production', 0o600)
        run(['chown', '-R', 'talky:talky', str(directory)])
        build_env = {**os.environ, 'NODE_ENV': 'production'}
        run(['runuser', '-u', 'talky', '--', '/usr/local/bin/npm', 'ci', '--include=dev'], cwd=directory, env=build_env)
        run(['runuser', '-u', 'talky', '--', '/usr/local/bin/npm', 'run', 'build'], cwd=directory, env=build_env)
        run(['chown', '-R', 'root:talky', str(directory)])
        (directory / '.env.production').chmod(0o640)
    run(['chmod', '-R', 'a+rX', str(TARGET / 'Admin/frontend/dist')])
    # Next needs its data cache to remain writable after ownership is restored.
    cache = TARGET / 'Talk-Leee/.next/cache'
    cache.mkdir(parents=True, exist_ok=True)
    run(['chown', '-R', 'talky:talky', str(cache)])
    write(Path('/etc/systemd/system/talky-dashboard.service'),
          '[Unit]\nDescription=Talky dashboard\nAfter=network.target talky-api.service\n'
          '[Service]\nUser=talky\nGroup=talky\nWorkingDirectory=/opt/talky/Talk-Leee\n'
          'Environment=NODE_ENV=production\nEnvironmentFile=/opt/talky/Talk-Leee/.env.production\n'
          'ExecStart=/usr/local/bin/node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3000\n'
          'Restart=on-failure\nRestartSec=5\nNoNewPrivileges=true\nPrivateTmp=true\n'
          '[Install]\nWantedBy=multi-user.target\n')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('plan', 'validate', 'packages', 'apply'), default='plan')
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('install.example.json'))
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--confirm-new-host', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.command == 'packages':
            print(json.dumps(list(PACKAGES), indent=2))
            return 0
        config = json.loads(args.config.read_text(encoding='utf-8-sig'))
        validate_config(config, inputs=args.command in ('validate', 'apply'))
        if args.command == 'plan':
            print(json.dumps({'mode': 'read_only_plan', 'target': '/opt/talky',
                              'supported_os': 'Ubuntu 24.04 amd64', 'steps': steps(config),
                              'external_inputs_checked': False}, indent=2))
        elif args.command == 'validate':
            print('Local inputs are structurally valid. Network, host, DB and call acceptance are unverified.')
        else:
            require(args.confirm_new_host, 'Apply requires --confirm-new-host; use plan first')
            apply(config, args.source.resolve())
        return 0
    except (InstallError, OSError, ValueError, TypeError, configparser.Error) as error:
        # Validation messages contain only field/file identifiers, never secret values.
        if isinstance(error, InstallError):
            print(f'ERROR: {error}', file=sys.stderr)
        else:
            print(f'ERROR: invalid or unreadable installer input ({type(error).__name__})', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

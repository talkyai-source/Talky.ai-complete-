#!/usr/bin/env python3
"""Prepare and activate an exact release on an installed fresh Ubuntu profile.

No OS, Node, Redis, TLS, firewall or carrier-topology changes. Traffic stays
under the operator's real freeze until separate reopening. See FRESH-RELEASE.md.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlsplit
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import install

MUTABLE = ('backend/logs', 'backend/recordings', 'backend/tmp', 'backend/uploads',
           'backend/audio_files/storage', 'backend/feedback_audio', 'runtime/state')
CONFIGS = ('backend/.env', 'Talk-Leee/.env.production', 'Admin/frontend/.env.production')
RUNTIME = ('talky-api', 'talky-api-web', 'talky-voice-worker', 'talky-dialer-worker',
           'talky-reminder-worker', 'talky-trunk-status', 'talky-dashboard', 'talky-voice-gateway')
TIMERS = ('talky-healthwatch.timer', 'talky-asterisk-reconcile.timer', 'talky-cleanup.timer',
          'talky-db-backup.timer', 'talky-inbound-synthetic.timer', 'talky-trunk-status.timer',
          'talklee-db-backup.timer', 'talklee-db-backup-health.timer')
OPERATOR_ONESHOTS = ('talky-migrate', 'talky-asterisk-reconcile', 'talky-cleanup',
                    'talky-db-backup', 'talky-inbound-synthetic', 'talklee-db-backup', 'talklee-db-backup-health')
KEYS = ('JWT_SECRET', 'SECRET_KEY', 'TOTP_ENCRYPTION_KEY', 'CONNECTOR_ENCRYPTION_KEY', 'SECRETS_MASTER_KEY')
SHA = re.compile(r'[a-f0-9]{40}\Z')
HOST_CONFIGS = ('etc/talky/redis.conf', 'etc/talky/compose.yml', 'etc/talky/tls/fullchain.pem',
                'etc/talky/tls/privkey.pem', 'etc/docker/daemon.json',
                'etc/nginx/sites-available/talky', 'etc/ssl/certs/talky-db-ca.pem')
DIAGNOSTIC_ROOT = None
ARTIFACT_ROOTS = ('backend/venv', 'runtime/bin', 'Talk-Leee/node_modules', 'Talk-Leee/.next',
                  'Admin/frontend/dist')


class ReleaseError(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise ReleaseError(message)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def command(args, *, cwd=None, env=None, timeout=1800):
    result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env,
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        diagnostic = ''
        if DIAGNOSTIC_ROOT is not None:
            DIAGNOSTIC_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
            protected(DIAGNOSTIC_ROOT, directory=True, private=True)
            target = DIAGNOSTIC_ROOT / ('failed-' + uuid.uuid4().hex + '.log')
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                # Failed build output can contain secrets and can be very large.
                # Retain only bounded private tails, never arguments/environment.
                stream.write(b'STDOUT (last 64 KiB)\n' + result.stdout.encode('utf-8', errors='replace')[-65536:]
                             + b'\nSTDERR (last 64 KiB)\n' + result.stderr.encode('utf-8', errors='replace')[-65536:])
            diagnostic = '; root-only diagnostic: ' + str(target)
        raise ReleaseError(f'{Path(args[0]).name} failed (output withheld)' + diagnostic)
    return result.stdout.strip()


def git(path, *args):
    return command(['git', '-c', 'safe.directory=' + str(path), '-C', path, *args])


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def protected(path, *, directory=False, private=False):
    metadata = path.lstat()
    require(stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode),
            'Protected path has the wrong type or is a symlink')
    require(metadata.st_uid == 0 and not stat.S_IMODE(metadata.st_mode) & (0o077 if private else 0o022),
            'Protected path must be root-owned with no unauthorized write/read permissions')


def fingerprint(path):
    metadata = path.stat()
    return dict(sha256=install.sha256(path), mode=stat.S_IMODE(metadata.st_mode),
                uid=metadata.st_uid, gid=metadata.st_gid)


def configuration_fingerprints(root):
    return {name: fingerprint((root / name).resolve(strict=True)) for name in CONFIGS}


def configuration_invariants(root):
    result = configuration_fingerprints(root)
    values = install.read_env(root / 'backend/.env')
    values.pop('APP_VERSION', None)
    result['backend/.env']['sha256'] = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    return result


def data_identity(root):
    return {name: [value.st_dev, value.st_ino] for name in MUTABLE
            for value in [(root / name).stat()]}


def host_configuration(layout):
    for name in HOST_CONFIGS:
        protected(layout.root / name)
    return {name: fingerprint(layout.root / name) for name in HOST_CONFIGS}


def artifact_manifest(root):
    result = {}
    for name in ARTIFACT_ROOTS:
        base = root / name
        require(base.is_dir() and not base.is_symlink(), 'Prepared build output directory is missing or redirected')
        for directory, names, files in os.walk(base, followlinks=False):
            parent = Path(directory)
            if parent == root / 'Talk-Leee/.next':
                names[:] = [name for name in names if name != 'cache']
            for entry in [parent, *(parent / name for name in names + files)]:
                key = entry.relative_to(root).as_posix()
                if key == 'Talk-Leee/.next/cache': continue
                metadata = entry.lstat()
                value = dict(mode=stat.S_IMODE(metadata.st_mode), uid=metadata.st_uid, gid=metadata.st_gid)
                if entry.is_symlink():
                    value['link'] = os.readlink(entry)
                    allowed_interpreter = entry.parent == root / 'backend/venv/bin' and entry.name in ('python', 'python3', 'python3.12')
                    require(entry.resolve().is_relative_to(root) or
                            (allowed_interpreter and entry.resolve() == Path('/usr/bin/python3.12')),
                            'Prepared artifact symlink escapes its release')
                elif entry.is_file():
                    value.update(sha256=install.sha256(entry), size=metadata.st_size)
                else:
                    require(entry.is_dir(), 'Prepared artifact has unsupported type')
                    value['directory'] = True
                require(metadata.st_uid == 0 and (entry.is_symlink() or not value['mode'] & 0o022),
                        'Prepared artifact is writable outside the root operator')
                result[key] = value
    return result


def verify_artifacts(layout, sha, proof):
    path = layout.prepared(sha).with_suffix('.artifacts.json')
    protected(path, private=True)
    require(install.sha256(path) == proof['artifact_manifest_sha256'], 'Prepared artifact receipt changed')
    require(artifact_manifest(layout.release(sha)) == json.loads(path.read_text()),
            'Prepared binary/dependency/frontend artifacts changed')


def tracked_source_clean(root):
    changes = git(root, 'diff', '--name-only', 'HEAD').splitlines()
    # Historical source contains recording fixtures; the installed storage
    # directory is an explicit writable overlay, never immutable application code.
    return all(any(name == allowed or name.startswith(allowed + '/') for allowed in MUTABLE)
               for name in changes)


def protected_source(root):
    protected(root, directory=True)
    visited = set()
    for name in git(root, 'ls-files', '-z').split('\0'):
        if not name or any(name == allowed or name.startswith(allowed + '/') for allowed in MUTABLE):
            continue
        path = root / name
        require(path.is_relative_to(root), 'Tracked source path leaves the release')
        current = path
        while current != root and current not in visited:
            visited.add(current)
            if current.is_symlink():
                require(current.lstat().st_uid == 0 and current.resolve().is_relative_to(root),
                        'Tracked source link is not protected inside the release')
                protected(current.resolve(), directory=current.resolve().is_dir())
            else:
                protected(current, directory=current.is_dir())
            current = current.parent


def verify_previous_artifacts(layout, sha, proof, previous):
    path = layout.prepared(sha).with_suffix('.previous-artifacts.json')
    protected(path, private=True)
    require(install.sha256(path) == proof['previous_artifact_manifest_sha256'], 'Prior artifact receipt changed')
    protected_source(previous)
    require(artifact_manifest(previous) == json.loads(path.read_text()), 'Prior release artifacts changed; rollback is unqualified')


def key_fingerprints(root):
    values = install.read_env(root / 'backend/.env')
    require(all(values.get(key) for key in KEYS), 'Protected application keys are incomplete')
    return {key: hashlib.sha256(values[key].encode()).hexdigest() for key in KEYS}


@dataclass
class Layout:
    root: Path = Path('/')

    @property
    def current(self): return self.root / 'opt/talky'
    @property
    def releases(self): return self.root / 'opt/talky-releases'
    @property
    def state(self): return self.root / 'var/lib/talky-release'
    @property
    def systemd(self): return self.root / 'etc/systemd/system'

    def release(self, sha):
        require(isinstance(sha, str) and SHA.fullmatch(sha), 'Full reviewed SHA required')
        return self.releases / sha

    def prepared(self, sha):
        self.release(sha)
        return self.state / 'prepared' / (sha + '.json')


@contextmanager
def release_lock(layout):
    import fcntl
    layout.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    protected(layout.state, directory=True, private=True)
    fd = os.open(layout.state / 'release.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReleaseError('Another local release operation is active') from None
        yield
    finally:
        os.close(fd)


def clean_source(source, sha):
    protected(source, directory=True)
    require(git(source, 'rev-parse', '--show-toplevel') == str(source), 'Source must be its repository root')
    require(git(source, 'rev-parse', 'HEAD') == sha, 'Checkout differs from reviewed SHA')
    require(not git(source, 'status', '--porcelain', '--untracked-files=all'), 'Source has tracked or untracked drift')
    install.checked_origin(source)


def installed(layout, expected_sha):
    require(layout.current.exists(), 'Fresh-host installation is missing')
    actual = layout.current.resolve(strict=True)
    if layout.current.is_symlink():
        require(actual == layout.release(expected_sha), 'Installed release link does not match expected SHA')
    else:
        protected(layout.current, directory=True)
    protected(actual, directory=True)
    protected(actual / '.git', directory=True)
    require(git(actual, 'rev-parse', 'HEAD') == expected_sha, 'Installed SHA changed')
    require(tracked_source_clean(actual), 'Installed tracked source has drift outside known runtime overlays')
    protected_source(actual)
    receipt_path = layout.root / 'var/lib/talky-install/receipt.json'
    protected(receipt_path, private=True)
    receipt = json.loads(receipt_path.read_text())
    require(receipt.get('status') == 'installed_health_checked' and receipt.get('frontend_mode') == 'linux',
            'Only the qualified Linux-frontend fresh-host profile is supported')
    for name in CONFIGS:
        protected((actual / name).resolve(strict=True))
    return actual


def seal_tree(path):
    """Remove build-user write access, without following dependency symlinks."""
    import grp
    group = grp.getgrnam('talky').gr_gid
    for directory, names, files in os.walk(path, followlinks=False):
        for entry in [Path(directory), *(Path(directory) / name for name in names + files)]:
            if entry.is_symlink():
                interpreter = entry.parent == path / 'backend/venv/bin' and entry.name in ('python', 'python3', 'python3.12')
                require(entry.resolve().is_relative_to(path) or
                        (interpreter and entry.resolve() == Path('/usr/bin/python3.12')),
                        'Build produced a symlink outside the release')
                os.lchown(entry, 0, group)
                continue
            mode = entry.stat().st_mode
            require(stat.S_ISDIR(mode) or stat.S_ISREG(mode), 'Build produced an unsupported filesystem object')
            os.chown(entry, 0, group)
            entry.chmod(0o640 if str(entry.relative_to(path)) in CONFIGS else
                        (0o755 if entry.is_dir() or mode & 0o111 else 0o644))
    (path / '.git').chmod(0o700)
    for name in CONFIGS:
        (path / name).chmod(0o640)
    (path / 'runtime/bin').chmod(0o750)
    (path / 'runtime/bin/voice_gateway').chmod(0o750)
    cache = path / 'Talk-Leee/.next/cache'
    cache.mkdir(parents=True, exist_ok=True)
    command(['chown', '-R', 'talky:talky', cache])


def operator_environment(values):
    # Privileged release tools must not discover configuration or executable
    # hooks in the running application's writable home/cache directories.
    environment = install.runtime_environment(values)
    for name in list(environment):
        if name.startswith(('PIP_', 'LD_')) or name in {
                'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONINSPECT', 'BASH_ENV', 'ENV'}:
            environment.pop(name)
    environment.update(HOME='/root', PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                       XDG_CONFIG_HOME='/root/.config', XDG_CACHE_HOME='/root/.cache',
                       PYTHONNOUSERSITE='1', PYTHON_DOTENV_DISABLED='1',
                       PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK='1')
    return environment


def backend_command(release, args, env=None):
    return command([release / 'backend/venv/bin/python', *args], cwd=release / 'backend',
                   env=operator_environment(env or install.read_env(release / 'backend/.env')))


def schema_heads(release):
    return json.loads(backend_command(release, ['-c',
        'import json; from alembic.config import Config; from alembic.script import ScriptDirectory; '
        'print(json.dumps(sorted(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())))']))


def database_state(release):
    return json.loads(backend_command(release, ['-c', '''
import hashlib,json,os,psycopg2
with psycopg2.connect(os.environ['DATABASE_URL'],connect_timeout=10) as c:
 c.set_session(readonly=True)
 with c.cursor() as q:
  q.execute('SELECT current_database(),inet_server_addr()::text,inet_server_port(),(SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid())')
  database,address,port,tls=q.fetchone(); assert tls is True
  q.execute('SELECT version_num FROM public.alembic_version ORDER BY version_num'); heads=[row[0] for row in q.fetchall()]
  endpoint=hashlib.sha256(json.dumps(dict(address=address,port=port),sort_keys=True).encode()).hexdigest()
  print(json.dumps(dict(database=database,endpoint=endpoint,heads=heads)))
''']))


def build_candidate(candidate, previous):
    import pwd
    # A running application must not be able to alter the candidate build.
    try:
        builder = pwd.getpwnam('talky-build')
    except KeyError:
        command(['useradd', '--system', '--create-home', '--home-dir', '/var/lib/talky-build',
                 '--shell', '/usr/sbin/nologin', '--user-group', 'talky-build'])
        builder = pwd.getpwnam('talky-build')
    require(0 < builder.pw_uid < 1000 and builder.pw_uid != pwd.getpwnam('talky').pw_uid
            and builder.pw_shell == '/usr/sbin/nologin' and builder.pw_dir == '/var/lib/talky-build'
            and set(os.getgrouplist('talky-build', builder.pw_gid)) == {builder.pw_gid},
            'Dedicated build identity is invalid')
    copy_candidate_configuration(previous, candidate)
    env = install.read_env(candidate / 'backend/.env')
    command(['python3.12', '-m', 'venv', candidate / 'backend/venv'], env=operator_environment(env))
    lock = candidate / 'scripts/installation/requirements-linux-py312.lock'
    backend_command(candidate, ['-m', 'pip', 'install', '--require-hashes', '-r', str(lock)])
    backend_command(candidate, ['-m', 'pip', 'check'])
    backend_command(candidate, ['-c', 'from app.core.prod_gate import enforce_production_gate; enforce_production_gate(); import app.main'])
    (candidate / 'runtime/bin').mkdir(parents=True)
    command(['bash', candidate / 'backend/scripts/build_voice_gateway_release.sh', candidate / 'runtime/bin/voice_gateway'],
            env={**operator_environment(env), 'VOICE_GATEWAY_BUILD_SHA': git(candidate, 'rev-parse', 'HEAD')})
    expected_node = json.loads((candidate / 'scripts/installation/install.example.json').read_text())['node_version']
    require(command(['/usr/local/bin/node', '--version']) == 'v' + expected_node,
            'Node version change requires a separately qualified host rebuild')
    for name in ('Talk-Leee', 'Admin/frontend'):
        directory = candidate / name
        command(['chown', '-R', 'talky-build:talky-build', directory])
        build_env = install.frontend_build_environment(directory / '.env.production')
        build_env['HOME'] = '/var/lib/talky-build'
        command(['runuser', '-u', 'talky-build', '--', '/usr/local/bin/npm', 'ci', '--include=dev'], cwd=directory, env=build_env)
        command(['runuser', '-u', 'talky-build', '--', '/usr/local/bin/npm', 'run', 'build'], cwd=directory, env=build_env)
    seal_tree(candidate)
    require(not git(candidate, 'diff', '--name-only', 'HEAD'), 'Build changed tracked source')


def copy_candidate_configuration(previous, candidate):
    for name in CONFIGS:
        # Atomic 0600 creation prevents even a transient umask-based 0644 copy.
        # Frontend ownership changes only to the isolated build account later.
        install.copy_secret(previous / name, candidate / name)


def prepare(layout, source, sha, previous_sha, domains):
    layout.release(sha)
    layout.release(previous_sha)
    require(sha != previous_sha, 'Candidate must differ from the current release')
    clean_source(source, sha)
    previous = installed(layout, previous_sha)
    # No downgrade disguised as an update; rollback has a separate journal path.
    command(['git', '-C', source, 'merge-base', '--is-ancestor', previous_sha, sha])
    candidate = layout.release(sha)
    require(not candidate.exists() and not candidate.is_symlink() and not layout.prepared(sha).exists(),
            'Candidate path/receipt already exists; retain and inspect it')
    require(len(domains) == 3 and all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', value) for value in domains),
            'Supply API, dashboard and Admin certificate hostnames')
    layout.releases.mkdir(parents=True, exist_ok=True, mode=0o755)
    protected(layout.releases, directory=True)
    before = configuration_fingerprints(previous)
    previous_artifacts = artifact_manifest(previous)
    install.clone_source(source, candidate)
    git(candidate, 'remote', 'set-url', 'origin', install.checked_origin(source))
    git(candidate, 'checkout', '--detach', sha)
    # Changed OS dependencies or Redis topology require a new qualified host.
    for name in ('scripts/installation/install.example.json',):
        old, new = (json.loads((root / name).read_text()) for root in (previous, candidate))
        for key in ('node_version', 'frontend_mode'):
            require(old[key] == new[key], 'Host profile change requires separate qualification')
    build_candidate(candidate, previous)
    old_heads, new_heads = schema_heads(previous), schema_heads(candidate)
    require(len(old_heads) == len(new_heads) == 1, 'Both releases must have one explicit migration head')
    grant_profile = json.loads((candidate / 'scripts/database/runtime_grants_profile.json').read_text())
    require(grant_profile.get('heads') == new_heads, 'Candidate runtime-grants profile does not match its migration head')
    ancestors = json.loads(backend_command(candidate, ['-c',
        'import json; from alembic.config import Config; from alembic.script import ScriptDirectory; '
        'print(json.dumps([r.revision for r in ScriptDirectory.from_config(Config("alembic.ini")).walk_revisions()]))']))
    require(set(old_heads).issubset(ancestors), 'Candidate graph does not descend from installed schema')
    # Every old revision must retain its exact published bytes; append-only
    # migrations are this release path's supported compatibility contract.
    for old in (previous / 'backend/Alembic/versions').glob('*.py'):
        require((candidate / 'backend/Alembic/versions' / old.name).read_bytes() == old.read_bytes(),
                'Published migration changed; separately review and qualify the history')
    require(configuration_fingerprints(previous) == before, 'Installed configuration changed while building')
    require(artifact_manifest(previous) == previous_artifacts, 'Prior artifacts changed while building')
    artifact_path = layout.prepared(sha).with_suffix('.artifacts.json')
    private_json(artifact_path, artifact_manifest(candidate))
    previous_artifact_path = layout.prepared(sha).with_suffix('.previous-artifacts.json')
    private_json(previous_artifact_path, previous_artifacts)
    proof = dict(status='prepared', candidate_sha=sha, previous_sha=previous_sha,
                 old_heads=old_heads, new_heads=new_heads, domains=domains,
                 configuration=before, configuration_invariants=configuration_invariants(previous),
                 keys=key_fingerprints(previous), data_identity=data_identity(previous),
                 host_configuration=host_configuration(layout),
                 artifact_manifest_sha256=install.sha256(artifact_path),
                 previous_artifact_manifest_sha256=install.sha256(previous_artifact_path),
                 python_lock_sha256=install.sha256(candidate / 'scripts/installation/requirements-linux-py312.lock'),
                 created_utc=datetime.now(timezone.utc).isoformat())
    private_json(layout.prepared(sha), proof)
    return proof


def switch_current(layout, old_sha, new_sha):
    """First directory-to-link conversion, then atomic source-link publication."""
    old, new = layout.release(old_sha), layout.release(new_sha)
    if old != new or layout.current.is_symlink():
        protected(new, directory=True)
    if not layout.current.is_symlink():
        protected(layout.current, directory=True)
        require(not old.exists() and not old.is_symlink(), 'Prior release destination exists')
        require(layout.current.stat().st_dev == layout.releases.stat().st_dev, 'Initial transition must stay on one filesystem')
        layout.current.rename(old)
        sync_directory(layout.current.parent)
        sync_directory(layout.releases)
        # Journal already identifies old. If publication fails, restore its
        # original directory immediately; an interrupted process uses recover.
        try:
            layout.current.symlink_to(old, target_is_directory=True)
            sync_directory(layout.current.parent)
        except BaseException:
            old.rename(layout.current)
            raise
    require(layout.current.resolve() == old, 'Current release changed before publication')
    temporary = layout.current.with_name('.talky-release-' + uuid.uuid4().hex)
    try:
        temporary.symlink_to(new, target_is_directory=True)
        os.replace(temporary, layout.current)
        sync_directory(layout.current.parent)
    finally:
        temporary.unlink(missing_ok=True)


def share_state(layout, previous_sha, candidate_sha):
    previous, candidate = layout.release(previous_sha), layout.release(candidate_sha)
    # Called only after the initial rename, with applications and schedulers stopped.
    for name in (*MUTABLE, *CONFIGS):
        source = (previous / name).resolve(strict=True)
        require(source.is_relative_to(layout.releases), 'Shared application path leaves the release store')
        target = candidate / name
        if target.exists() or target.is_symlink():
            require(not target.is_symlink(), 'Unexpected pre-existing candidate runtime link')
            if name in CONFIGS:
                require(target.is_file(), 'Candidate configuration has wrong type')
                target.unlink()
            else:
                require(target.is_dir(), 'Candidate runtime overlay has wrong type')
                # Retain seeded source/build data privately; never delete or
                # merge it over the actual installed customer's shared data.
                retained = candidate / '.git/talky-seeded-state' / name
                retained.parent.mkdir(parents=True, exist_ok=True)
                target.rename(retained)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source, target_is_directory=source.is_dir())


def observe(release):
    result = json.loads(backend_command(release, ['scripts/observe_release_quiescence.py', '--env-file', str(release / 'backend/.env')]))
    require(result.get('complete') is True and result.get('all_zero_observed') is True,
            'Fresh gateway/Asterisk/Redis/database observations are incomplete or not zero')
    return result


def active(unit):
    return subprocess.run(['systemctl', 'is-active', '--quiet', unit], capture_output=True).returncode == 0


def stop_runtime(journal):
    command(['systemctl', 'stop', 'talky-asterisk-reconcile', *journal['active_services']])


def timer_states():
    result = {}
    listed = command(['systemctl', 'list-unit-files', '--type=timer', '--no-legend', '--no-pager'])
    for line in listed.splitlines():
        name = line.split()[0] if line.split() else ''
        require(not name.startswith(('talky-', 'talklee-')) or name in TIMERS,
                'Unknown Talky maintenance timer requires an explicit release-profile review')
    for timer in TIMERS:
        loaded = command(['systemctl', 'show', '--property=LoadState', '--value', timer])
        if loaded == 'not-found': continue
        require(loaded == 'loaded', 'Maintenance timer is not in a supported loaded state')
        enabled = command(['systemctl', 'show', '--property=UnitFileState', '--value', timer])
        require(enabled in ('enabled', 'disabled'), 'Maintenance timer has unsupported enablement/override state')
        result[timer] = dict(enabled=enabled, active=active(timer))
    return result


def freeze_timers(journal):
    for timer, state in journal['timers'].items():
        command(['systemctl', 'disable', '--now', timer])
    for unit in OPERATOR_ONESHOTS:
        state = command(['systemctl', 'show', '--property=ActiveState', '--value', unit])
        require(state in ('inactive', 'failed'),
                'Maintenance operation is not fully stopped after timers were frozen; inspect before retrying')


def start_runtime(journal, release, sha):
    command(['systemctl', 'daemon-reload'])
    command(['systemctl', 'start', 'talky-voice-gateway'])
    wait_health('http://127.0.0.1:18080/ready', sha=sha)
    command(['systemctl', 'start', *(unit for unit in journal['active_services'] if unit != 'talky-voice-gateway')])
    for unit in journal['active_services']:
        require(active(unit), 'Required application service did not start')
        require(command(['systemctl', 'show', '--property=User', '--value', unit]) == 'talky', 'Application service identity changed')
        require(command(['systemctl', 'show', '--property=Group', '--value', unit]) == 'talky'
                and command(['systemctl', 'show', '--property=NoNewPrivileges', '--value', unit]) == 'yes',
                'Application group or NoNewPrivileges protection changed')
    for unit in ('talky-api', 'talky-api-web'):
        if unit in journal['active_services']:
            pid = command(['systemctl', 'show', '--property=MainPID', '--value', unit])
            backend_command(release, ['scripts/set_release_version.py', '--candidate-sha', sha, '--check-pid', pid])
            require((Path('/proc') / pid / 'cwd').resolve() == release / 'backend', 'API process uses another release directory')
    dashboard_pid = command(['systemctl', 'show', '--property=MainPID', '--value', 'talky-dashboard'])
    require((Path('/proc') / dashboard_pid / 'cwd').resolve() == release / 'Talk-Leee', 'Dashboard process uses another release directory')
    for path in ('/health', '/api/v1/healthz/ready', '/api/v1/healthz/deep'):
        wait_health('http://127.0.0.1:8000' + path)
    command(['nginx', '-t'])
    for index, domain in enumerate(journal['domains']):
        command(['curl', '--fail', '--silent', '--show-error', '--max-time', '30', '--resolve',
                 f'{domain}:443:127.0.0.1', f'https://{domain}' + ('/health' if index == 0 else '/')])
    verify_frontend_artifacts(release, journal['domains'])
    # Producer/maintenance timers remain disabled across reboot until the
    # separate, explicit reopening operation restores their recorded state.


def verify_frontend_artifacts(release, domains):
    static = release / 'Talk-Leee/.next/static'
    build_id = (release / 'Talk-Leee/.next/BUILD_ID').read_text().strip()
    require(re.fullmatch(r'[A-Za-z0-9_-]{1,128}', build_id), 'Dashboard build identifier is invalid')
    asset = static / build_id / '_buildManifest.js'
    if not asset.is_file():
        assets = sorted(static.rglob('*.js'))
        require(assets, 'Dashboard produced no verifiable JavaScript artifact')
        asset = assets[0]
    for domain, url_path, expected in (
            (domains[1], '/_next/static/' + quote(asset.relative_to(static).as_posix(), safe='/._-'), asset),
            (domains[2], '/', release / 'Admin/frontend/dist/index.html')):
        body = command(['curl', '--fail', '--silent', '--show-error', '--max-time', '30', '--resolve',
                        f'{domain}:443:127.0.0.1', f'https://{domain}{url_path}'])
        require(body == expected.read_text().strip(), 'HTTPS frontend artifact differs from the prepared release')


def wait_health(url, sha=None):
    for _ in range(30):
        try:
            with urlopen(url, timeout=5) as response:
                if sha:
                    payload = json.load(response)
                    require(payload.get('ready') is True and payload.get('build_sha') == sha
                            and payload.get('protocol_version') == 2 and 'pcmu' in payload.get('codecs', []),
                            'Gateway answered with wrong build/protocol/readiness')
                require(response.status == 200, 'Readiness response was unsuccessful')
                return
        except OSError:
            time.sleep(2)
    raise ReleaseError('Required readiness endpoint did not become available')


def publish_version(release, sha):
    backend_command(release, ['scripts/set_release_version.py', '--candidate-sha', sha,
                             '--env-file', str((release / 'backend/.env').resolve(strict=True))])


def unit_snapshot(layout, destination):
    destination.mkdir(mode=0o700)
    for path in layout.systemd.glob('talky-*'):
        require(re.fullmatch(r'talky-[a-z0-9-]+\.(?:service|timer|target)(?:\.d)?', path.name),
                'Unrecognized Talky unit path')
        command(['cp', '-a', '--', path, destination / path.name])


def restore_units(layout, snapshot):
    # Only the known Talky unit namespace is replaced; other host units remain untouched.
    for path in layout.systemd.glob('talky-*'):
        require(re.fullmatch(r'talky-[a-z0-9-]+\.(?:service|timer|target)(?:\.d)?', path.name), 'Unexpected unit path')
        if path.is_dir() and not path.is_symlink(): shutil.rmtree(path)
        else: path.unlink()
    for path in snapshot.iterdir():
        command(['cp', '-a', '--', path, layout.systemd / path.name])


def service_matches_url(service, url):
    parsed, values = urlsplit(url), service.values
    pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    require(not parsed.fragment and len({key for key, _ in pairs}) == len(pairs)
            and all(key in {'sslmode', 'sslrootcert', 'connect_timeout'} for key, _ in pairs),
            'Database URL query may contain only unique supported TLS/timeout settings')
    query = parse_qs(parsed.query, strict_parsing=True)
    require('connect_timeout' not in query or
            (re.fullmatch(r'[1-9][0-9]?', query['connect_timeout'][0]) and int(query['connect_timeout'][0]) <= 60),
            'Database connection timeout is invalid')
    require(parsed.scheme in ('postgresql', 'postgres') and parsed.hostname == values['host']
            and str(parsed.port or 5432) == values['port'] and unquote(parsed.path.lstrip('/')) == values['dbname']
            and unquote(parsed.username or '') == values['user'] and query.get('sslmode') == ['verify-full']
            and query.get('sslrootcert') == [values.get('sslrootcert')], 'Protected database identities do not select the same endpoint/TLS policy')


def database_backup(release, service_file, runtime_name, owner_name, backup_name, destination):
    recovery = load_module('fresh_recovery', release / 'scripts/database/recovery.py')
    protected(service_file, private=True)
    runtime, owner, backup = (recovery.Service(service_file, name) for name in (runtime_name, owner_name, backup_name))
    for service in (runtime, owner, backup):
        require(service.passfile is not None, 'Explicit protected database password file is required')
        protected(service.passfile, private=True)
    service_matches_url(runtime, install.read_env(release / 'backend/.env')['DATABASE_URL'])
    for key in ('host', 'hostaddr', 'port', 'dbname'):
        require(runtime.values.get(key) == backup.values.get(key) == owner.values.get(key),
                'Backup, runtime and migration services must share one explicit database endpoint')
    state = database_state(release)
    recovery.backup(recovery.Postgres(), backup, destination, 1024, None, 0.5)
    # Independently reopen the published archive and receipt, not the returned object.
    verified = recovery.verify_bundle(destination)
    require(verified['source_database'] == state['database'] and verified['source_endpoint_fingerprint'] == state['endpoint']
            and verified['inventory']['heads'] == state['heads'], 'Verified backup does not match the actual runtime database/schema')
    return verified


def grants(release, services, journal_dir, mode):
    backend_command(release, [str(release / 'scripts/database/runtime_grants.py'), mode,
        '--service-file', str(services.file), '--owner-service', services.owner, '--runtime-service', services.runtime,
        '--receipt', str(journal_dir / ('grants-' + mode + '.json'))])


def migration_preflight(candidate, values, owner, expected):
    # Validate the *actual migration DSN*, not merely another login from the
    # service file. The old release's grants verifier separately proves complete
    # table ownership, ACLs and policy/profile identity before migration begins.
    result = json.loads(backend_command(candidate, ['-c', '''
import hashlib,json,os,psycopg2
with psycopg2.connect(os.environ['MIGRATION_DATABASE_URL'],connect_timeout=10) as c:
 c.set_session(readonly=True)
 with c.cursor() as q:
  q.execute('SELECT current_user,session_user,current_database(),inet_server_addr()::text,inet_server_port(),(SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid())')
  user,session,database,address,port,tls=q.fetchone()
  q.execute('SELECT rolcanlogin AND NOT (rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication) AND NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid) FROM pg_roles r WHERE rolname=current_user')
  restricted=q.fetchone()==(True,)
  q.execute('SELECT version_num FROM public.alembic_version ORDER BY version_num'); heads=[row[0] for row in q.fetchall()]
  endpoint=hashlib.sha256(json.dumps(dict(address=address,port=port),sort_keys=True).encode()).hexdigest()
  print(json.dumps(dict(user=user,session=session,database=database,endpoint=endpoint,tls=tls,restricted=restricted,heads=heads)))
'''], values))
    require(result['user'] == result['session'] == owner and result['restricted'] is True and result['tls'] is True
            and all(result[key] == expected[key] for key in ('database', 'endpoint', 'heads')),
            'Actual migration login role, endpoint, TLS or schema differs from the verified database')


def rollback_journal(layout, journal, journal_path):
    require(not journal['migration_started'], 'Migration began: automatic/code rollback is forbidden; use reviewed code-forward or isolated restore')
    freeze_timers(journal)
    previous, candidate = layout.release(journal['previous_sha']), layout.release(journal['candidate_sha'])
    current = layout.current.resolve(strict=True) if layout.current.exists() else previous
    old_source = previous if previous.exists() else layout.current
    require(git(old_source, 'rev-parse', 'HEAD') == journal['previous_sha'] and tracked_source_clean(old_source),
            'Prior tracked source changed; rollback is unqualified')
    verify_previous_artifacts(layout, journal['candidate_sha'], journal, old_source)
    require(database_state(current)['heads'] == journal['old_heads'], 'Schema changed; code rollback is forbidden')
    stop_runtime(journal)
    if not layout.current.exists():
        protected(previous, directory=True)
        layout.current.symlink_to(previous, target_is_directory=True)
    elif layout.current.is_symlink() and layout.current.resolve() == candidate:
        switch_current(layout, journal['candidate_sha'], journal['previous_sha'])
    else:
        require(git(layout.current, 'rev-parse', 'HEAD') == journal['previous_sha'], 'Unexpected current release during recovery')
    restore_units(layout, journal_path.parent / 'units')
    old = layout.current.resolve()
    publish_version(old, journal['previous_sha'])
    command(['bash', old / 'backend/scripts/reconcile_asterisk_release.sh'], env={**operator_environment(install.read_env(old / 'backend/.env')),
            'TALKY_BACKEND_ENV_FILE': str(old / 'backend/.env'), 'TALKY_BACKEND_PYTHON': str(old / 'backend/venv/bin/python')})
    start_runtime(journal, old, journal['previous_sha'])
    require(key_fingerprints(old) == journal['keys'], 'Application keys changed during recovery')
    require(configuration_invariants(old) == journal['configuration_invariants']
            and data_identity(old) == journal['data_identity']
            and host_configuration(layout) == journal['host_configuration'],
            'Configuration/data identity changed during recovery')
    journal.update(status='rolled_back_health_checked', traffic_reopened=False)
    private_json(journal_path, journal)
    private_json(layout.state / 'current.json', {'candidate_sha': journal['previous_sha'], 'journal': str(journal_path)})


def activate(layout, sha, manifest, manifest_digest, services, allow_migration=False):
    prepared_path = layout.prepared(sha)
    protected(prepared_path, private=True)
    proof = json.loads(prepared_path.read_text())
    require(proof['candidate_sha'] == sha and proof['status'] == 'prepared', 'Prepared release identity is invalid')
    previous = installed(layout, proof['previous_sha'])
    candidate = layout.release(sha)
    protected(layout.releases, directory=True)
    protected(candidate, directory=True)
    protected_source(candidate)
    require(git(candidate, 'rev-parse', 'HEAD') == sha and tracked_source_clean(candidate), 'Prepared source changed')
    verify_artifacts(layout, sha, proof)
    verify_previous_artifacts(layout, sha, proof, previous)
    require(configuration_fingerprints(previous) == proof['configuration'] and key_fingerprints(previous) == proof['keys'],
            'Installed configuration or keys changed after prepare')
    require(host_configuration(layout) == proof['host_configuration'], 'Protected host configuration changed after prepare')
    require(schema_heads(candidate) == proof['new_heads'] and database_state(previous)['heads'] == proof['old_heads'],
            'Database or candidate migration graph changed')
    migrating = proof['old_heads'] != proof['new_heads']
    require(not migrating or allow_migration, 'New schema requires explicit --allow-forward-migration')
    verifier = load_module('fresh_drain_verifier', candidate / 'backend/scripts/verify_deploy_drain_manifest.py')
    # Reuse the existing v1/v2 authority, freshness, four counts and replay contract.
    manifest_id = verifier.verify_and_consume_manifest(manifest_path=manifest, candidate_sha=sha,
        expected_sha256=manifest_digest, replay_dir=layout.state / 'used-manifests')
    folder = layout.state / 'transactions' / uuid.uuid4().hex
    folder.mkdir(parents=True, mode=0o700)
    journal_path = folder / 'journal.json'
    journal = {**proof, 'status': 'activating', 'manifest_id': manifest_id,
               'migration_started': False, 'runtime_stop_started': False, 'traffic_reopened': False,
               'active_services': [unit for unit in RUNTIME if active(unit)],
               'timers': timer_states()}
    require(set(install.SERVICES).issubset(journal['active_services']) and 'talky-dashboard' in journal['active_services'],
            'Required original application services are not healthy')
    unit_snapshot(layout, folder / 'units')
    for name in HOST_CONFIGS:
        target = folder / 'protected-config' / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        command(['cp', '-a', '--', layout.root / name, target])
    private_json(journal_path, journal)
    try:
        freeze_timers(journal)
        journal['observation'] = observe(previous)
        journal['runtime_stop_started'] = True
        private_json(journal_path, journal)
        stop_runtime(journal)
        journal['backup'] = database_backup(previous, services.file, services.runtime, services.owner, services.backup, folder / 'database')
        private_json(journal_path, journal)
        if migrating:
            migration_env = layout.root / 'etc/talky/migration.env'
            protected(migration_env, private=True)
            values = install.read_env(migration_env)
            require(set(values) == {'MIGRATION_DATABASE_URL'}, 'Migration file must contain only the owner DSN')
            recovery = load_module('fresh_migration_recovery', candidate / 'scripts/database/recovery.py')
            service_matches_url(recovery.Service(services.file, services.owner), values['MIGRATION_DATABASE_URL'])
            migration_values = {**install.read_env(candidate / 'backend/.env'), **values}
            backend_command(candidate, [str(candidate / 'scripts/installation/install.py'), 'check-migration-identity'], migration_values)
            grants(previous, services, folder, 'verify')
            migration_preflight(candidate, migration_values, recovery.Service(services.file, services.owner).values['user'], database_state(previous))
            journal['migration_started'] = True
            private_json(journal_path, journal)
            backend_command(candidate, ['-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'heads'], migration_values)
            grants(candidate, services, folder, 'apply')
        else:
            grants(candidate, services, folder, 'verify')
        require(database_state(candidate)['heads'] == proof['new_heads'], 'Schema did not reach the exact candidate heads')
        # First conversion makes the original state stable before linking it
        # into the candidate. Services remain stopped throughout.
        if not layout.current.is_symlink():
            switch_current(layout, proof['previous_sha'], proof['previous_sha'])
        share_state(layout, proof['previous_sha'], sha)
        switch_current(layout, proof['previous_sha'], sha)
        install.install_service_units(candidate / 'backend', layout.systemd)
        publish_version(candidate, sha)
        command(['bash', candidate / 'backend/scripts/reconcile_asterisk_release.sh'], env={**operator_environment(install.read_env(candidate / 'backend/.env')),
                'TALKY_BACKEND_ENV_FILE': str(candidate / 'backend/.env'), 'TALKY_BACKEND_PYTHON': str(candidate / 'backend/venv/bin/python')})
        start_runtime(journal, candidate, sha)
        verify_artifacts(layout, sha, proof)
        require(key_fingerprints(candidate) == proof['keys'], 'Protected keys changed during activation')
        require(configuration_invariants(candidate) == proof['configuration_invariants']
                and data_identity(candidate) == proof['data_identity']
                and host_configuration(layout) == proof['host_configuration'],
                'Configuration/data identity changed during activation')
        journal.update(status='activated_health_checked', finished_utc=datetime.now(timezone.utc).isoformat())
        private_json(journal_path, journal)
        private_json(layout.state / 'current.json', {'candidate_sha': sha, 'journal': str(journal_path)})
        return journal_path
    except Exception:
        journal['status'] = ('failed_migration_started' if journal['migration_started'] else
                             ('failed_before_migration' if journal['runtime_stop_started'] else 'blocked_before_runtime_stop'))
        private_json(journal_path, journal)
        # An active call or activating maintenance job is a preflight refusal,
        # never permission to stop the old runtime or interrupt its operation.
        if journal['runtime_stop_started']:
            try:
                stop_runtime(journal)
                if not journal['migration_started']:
                    rollback_journal(layout, journal, journal_path)
            except Exception:
                journal['status'] = 'failed_rollback_requires_operator'
                private_json(journal_path, journal)
        raise ReleaseError('Release failed; traffic remains frozen. Inspect the private transaction journal before recovery') from None


def main(argv=None):
    global DIAGNOSTIC_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    build = sub.add_parser('prepare')
    build.add_argument('--source', type=Path, required=True)
    build.add_argument('--sha', required=True)
    build.add_argument('--expected-current-sha', required=True)
    build.add_argument('--domains', nargs=3, metavar=('API', 'DASHBOARD', 'ADMIN'), required=True)
    apply = sub.add_parser('activate')
    apply.add_argument('--sha', required=True)
    apply.add_argument('--manifest', type=Path, required=True)
    apply.add_argument('--manifest-sha256', required=True)
    apply.add_argument('--service-file', type=Path, required=True)
    apply.add_argument('--runtime-service', default='talklee_runtime')
    apply.add_argument('--owner-service', default='talklee_owner')
    apply.add_argument('--backup-service', default='talklee_backup')
    apply.add_argument('--allow-forward-migration', action='store_true')
    recover = sub.add_parser('recover')
    recover.add_argument('--journal', type=Path, required=True)
    recover.add_argument('--manifest', type=Path, required=True)
    recover.add_argument('--manifest-sha256', required=True)
    reopen = sub.add_parser('resume-schedules')
    reopen.add_argument('--journal', type=Path, required=True)
    reopen.add_argument('--acknowledge-traffic-window', action='store_true')
    args = parser.parse_args(argv)
    try:
        require(sys.platform == 'linux' and os.geteuid() == 0, 'Run on the installed Ubuntu host as root')
        operating_system = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        require(operating_system.get('ID', '').strip('"') == 'ubuntu'
                and operating_system.get('VERSION_ID', '').strip('"') == '24.04'
                and install.platform.machine() == 'x86_64' and Path('/run/systemd/system').is_dir(),
                'Only the qualified Ubuntu 24.04 amd64 systemd profile is supported')
        install.require_install_umask()
        layout = Layout()
        DIAGNOSTIC_ROOT = layout.state / 'private-command-logs'
        with release_lock(layout):
            if args.command == 'prepare':
                prepare(layout, args.source.resolve(), args.sha, args.expected_current_sha, args.domains)
                print('Exact candidate prepared. Traffic, backup and activation gates remain required.')
            elif args.command == 'activate':
                services = argparse.Namespace(file=args.service_file, runtime=args.runtime_service,
                                              owner=args.owner_service, backup=args.backup_service)
                path = activate(layout, args.sha, args.manifest, args.manifest_sha256, services, args.allow_forward_migration)
                print('Release health checked; traffic was not reopened. Private journal: ' + str(path))
            elif args.command == 'recover':
                protected(args.journal, private=True)
                require(args.journal.resolve().is_relative_to(layout.state / 'transactions'), 'Recovery journal is outside the release store')
                journal = json.loads(args.journal.read_text())
                require(not journal['migration_started'], 'Migration began; code rollback is forbidden')
                verifier = load_module('fresh_recovery_verifier', HERE.parents[1] / 'backend/scripts/verify_deploy_drain_manifest.py')
                verifier.verify_and_consume_manifest(manifest_path=args.manifest, candidate_sha=journal['previous_sha'],
                    expected_sha256=args.manifest_sha256, replay_dir=layout.state / 'used-manifests')
                if any(active(unit) for unit in RUNTIME):
                    observe(layout.current.resolve(strict=True))
                # A crashed/stopped runtime cannot provide live counters. The
                # operator's new candidate-bound manifest records the actual
                # independent drain observation and continuing external freeze.
                rollback_journal(layout, journal, args.journal)
                print('Previous release health checked; traffic was not reopened.')
            else:
                require(args.acknowledge_traffic_window, 'Restoring schedules requires explicit operator reopening acknowledgment')
                protected(args.journal, private=True)
                require(args.journal.resolve().is_relative_to(layout.state / 'transactions'), 'Journal is outside the release store')
                journal = json.loads(args.journal.read_text())
                require(journal['status'] in ('activated_health_checked', 'rolled_back_health_checked'),
                        'Schedules cannot resume for an unsuccessful release/recovery')
                sha = journal['candidate_sha'] if journal['status'] == 'activated_health_checked' else journal['previous_sha']
                current = installed(layout, sha)
                require(database_state(current)['heads'] == (journal['new_heads'] if sha == journal['candidate_sha'] else journal['old_heads']),
                        'Current database schema changed before reopening')
                start_runtime(journal, current, sha)
                for timer, state in journal['timers'].items():
                    if state['enabled'] == 'enabled': command(['systemctl', 'enable', timer])
                    if state['active']: command(['systemctl', 'start', timer])
                journal['schedules_restored_utc'] = datetime.now(timezone.utc).isoformat()
                private_json(args.journal, journal)
                print('Prior maintenance schedules restored; external traffic controls remain operator-owned.')
        return 0
    except Exception as error:
        detail = str(error) if isinstance(error, (ReleaseError, install.InstallError)) else type(error).__name__
        print('RELEASE REFUSED: ' + detail, file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

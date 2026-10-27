#!/usr/bin/env python3
"""Prepare protected, incomplete installer inputs; never install or start services."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
NODE_VERSION = '22.22.0'
REDIS_TAG = 'redis:7.4-alpine'
LOCK_NAME = 'requirements-linux-py312.lock'
PROVENANCE_NAME = LOCK_NAME + '.provenance.json'
MAX_METADATA_BYTES = 2 * 1024 * 1024


class PreparationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PreparationError(message)


def run(args: list[str], source: Path | None = None) -> str:
    return subprocess.run(args, cwd=source, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env={**os.environ, 'LC_ALL': 'C'}).stdout


def check_host() -> None:
    require(platform.system() == 'Linux' and platform.machine() in ('x86_64', 'amd64'),
            'Prepare on Ubuntu 24.04 amd64 using its current APT metadata')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines()
                   if '=' in line)
    require(release.get('ID', '').strip('"') == 'ubuntu' and
            release.get('VERSION_ID', '').strip('"') == '24.04', 'Ubuntu 24.04 is required')


def checked_source(source: Path) -> str:
    require(source == ROOT, 'Run the preparation helper from the selected source checkout')
    # A root shell may inspect an operator-owned clone without SUDO_UID. Trust
    # only this explicitly selected checkout for these reads, never globally.
    git = ['git', '-c', 'safe.directory=', '-c', f'safe.directory={source}']
    require(Path(run([*git, 'rev-parse', '--show-toplevel'], source).strip()).resolve() == source,
            'Source must be the Git checkout root')
    require(not run([*git, 'status', '--porcelain', '--untracked-files=all'], source).strip(),
            'Source checkout must be completely clean; commit or use a clean release checkout')
    sha = run([*git, 'rev-parse', 'HEAD'], source).strip()
    require(re.fullmatch(r'[a-f0-9]{40}', sha) is not None, 'Source HEAD must be a full Git SHA')
    return sha


def installer_packages(source: Path) -> tuple[str, ...]:
    spec = importlib.util.spec_from_file_location('prepared_installer', source / 'scripts/installation/install.py')
    module = importlib.util.module_from_spec(spec)
    # Avoid ignored bytecode files when reading the clean selected installer.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return tuple(module.PACKAGES)


def apt_candidates(packages: tuple[str, ...]) -> dict[str, str]:
    output = run(['apt-cache', 'policy', *packages])
    versions: dict[str, str] = {}
    current = None
    for line in output.splitlines():
        if line and not line[0].isspace() and line.endswith(':'):
            current = line[:-1].split(':')[0]
        elif current in packages:
            match = re.fullmatch(r'\s+Candidate:\s+(\S+)\s*', line)
            if match and re.fullmatch(r'[A-Za-z0-9.+:~_-]+', match[1]):
                versions[current] = match[1]
    missing = sorted(set(packages) - versions.keys())
    require(not missing, 'No APT candidate for: ' + ', '.join(missing) +
            '. Refresh the approved Ubuntu package metadata and retry.')
    return versions


def fetch(url: str, headers: dict[str, str] | None = None) -> tuple[bytes, object]:
    with urlopen(Request(url, headers=headers or {}), timeout=30) as response:
        data = response.read(MAX_METADATA_BYTES + 1)
        require(len(data) <= MAX_METADATA_BYTES, 'Public dependency metadata exceeded its size limit')
        return data, response.headers


def node_checksum() -> str:
    body, _ = fetch(f'https://nodejs.org/dist/v{NODE_VERSION}/SHASUMS256.txt')
    filename = f'node-v{NODE_VERSION}-linux-x64.tar.xz'
    matches = re.findall(r'^([a-f0-9]{64})\s+\*?' + re.escape(filename) + r'\s*$',
                         body.decode('utf-8'), re.M)
    require(len(matches) == 1, 'Official Node metadata must contain exactly one Linux x64 archive checksum')
    return matches[0]


def redis_image() -> str:
    token_body, _ = fetch('https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/redis:pull')
    token_metadata = json.loads(token_body)
    require(isinstance(token_metadata, dict), 'Invalid public Docker Hub token metadata')
    token = token_metadata.get('token')
    require(isinstance(token, str) and token and '\n' not in token and '\r' not in token,
            'Docker Hub did not supply a valid public pull token')
    body, headers = fetch('https://registry-1.docker.io/v2/library/redis/manifests/7.4-alpine', {
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json',
    })
    digest = headers.get('Docker-Content-Digest', '')
    require(re.fullmatch(r'sha256:[a-f0-9]{64}', digest) is not None and
            digest == 'sha256:' + hashlib.sha256(body).hexdigest(),
            'Docker Hub manifest digest did not match the downloaded public manifest')
    manifest = json.loads(body)
    require(isinstance(manifest, dict) and isinstance(manifest.get('manifests'), list),
            'Docker Hub must supply a multi-platform Redis manifest')
    require(any(isinstance(item, dict) and isinstance(item.get('platform'), dict) and
                item['platform'].get('os') == 'linux' and
                item['platform'].get('architecture') == 'amd64'
                for item in manifest.get('manifests', [])),
            'Redis manifest does not list a Linux amd64 image')
    return REDIS_TAG + '@' + digest


def bundled_lock(source: Path) -> tuple[bytes, bytes]:
    directory = source / 'scripts/installation'
    lock_path, provenance_path = directory / LOCK_NAME, directory / PROVENANCE_NAME
    require(lock_path.is_file() and not lock_path.is_symlink() and
            provenance_path.is_file() and not provenance_path.is_symlink(),
            'Selected release must include the Python hash lock and its provenance sidecar')
    lock, provenance = normalized_bytes(lock_path), provenance_path.read_bytes()
    metadata = json.loads(provenance)
    require(isinstance(metadata, dict), 'Python lock provenance must be a JSON object')
    require(metadata.get('hash_normalization') == 'UTF-8 text with LF newlines',
            'Unsupported Python lock provenance hash normalization')
    require(metadata.get('lock_sha256') == hashlib.sha256(lock).hexdigest(),
            'Bundled Python lock does not match its provenance checksum')
    requirements = normalized_bytes(source / 'backend/requirements.txt')
    require(metadata.get('requirements_sha256') == hashlib.sha256(requirements).hexdigest(),
            'Python lock provenance does not match the selected backend requirements')
    return lock, provenance


def normalized_bytes(path: Path) -> bytes:
    return path.read_text(encoding='utf-8').replace('\r\n', '\n').replace('\r', '\n').encode('utf-8')


def protected_write(path: Path, content: bytes) -> None:
    with path.open('xb') as output:
        path.chmod(0o600)
        output.write(content)


def prepare(source: Path, output: Path, *, node_sha256: str | None = None,
            redis: str | None = None) -> dict:
    check_host()
    require(not output.exists() and not output.is_symlink(), 'Output already exists; nothing will be overwritten')
    source, output = source.resolve(), output.resolve()
    require(not output.is_relative_to(source), 'Protected inputs must be outside the source checkout')
    require(not output.is_relative_to(Path('/opt/talky')), 'Protected inputs must be outside /opt/talky')
    source_sha = checked_source(source)
    lock, provenance = bundled_lock(source)
    template_dir = source / 'scripts/installation'
    config = json.loads((template_dir / 'install.example.json').read_text())
    config.update(source_sha=source_sha, frontend_mode='linux', database_prepared=False,
                  node_version=NODE_VERSION)
    file_names = {
        'backend_env_file': 'backend.env', 'dashboard_env_file': 'dashboard.env',
        'admin_env_file': 'admin.env', 'asterisk_config_directory': 'asterisk',
        'tls_certificate_file': 'fullchain.pem', 'tls_private_key_file': 'privkey.pem',
        'database_ca_file': 'database-ca.pem', 'python_requirements_lock': LOCK_NAME,
        'apt_versions_file': 'apt-versions.json',
    }
    config.update({key: str(output / name) for key, name in file_names.items()})
    envs = {name: (template_dir / (name + '.example')).read_bytes()
            for name in ('backend.env', 'dashboard.env', 'admin.env')}
    if node_sha256 is not None:
        require(re.fullmatch(r'[a-f0-9]{64}', node_sha256) is not None, 'Invalid --node-sha256')
    if redis is not None:
        require(re.fullmatch(r'redis:7\.4-alpine@sha256:[a-f0-9]{64}', redis) is not None,
                '--redis-image must pin redis:7.4-alpine with a SHA256 digest')
    versions = apt_candidates(installer_packages(source))
    config['node_archive_sha256'] = node_sha256 or node_checksum()
    config['redis_image'] = redis or redis_image()
    # Recheck after network reads: a concurrently edited source must not be labelled clean.
    require(checked_source(source) == source_sha, 'Source changed while inputs were being prepared')
    receipt = {
        'status': 'templates_prepared_operator_inputs_required',
        'created_utc': datetime.now(timezone.utc).isoformat(), 'source_sha': source_sha,
        'source': str(source), 'python_lock_sha256': hashlib.sha256(lock).hexdigest(),
        'python_lock_provenance': PROVENANCE_NAME,
        'apt_metadata': 'Existing local apt-cache policy candidates; no apt update or installation performed',
        'node_checksum_source': 'operator flag' if node_sha256 else 'https://nodejs.org/dist/v22.22.0/SHASUMS256.txt',
        'redis_digest_source': 'operator flag' if redis else 'Docker Hub library/redis:7.4-alpine manifest',
        'remaining_inputs': ['real domains and network allow-lists', 'prepared external database and runtime credentials',
                             'retained encryption and authentication keys', 'approved provider and SMTP credentials',
                             'reviewed carrier/Asterisk configuration', 'public TLS certificate/key and database CA'],
        'installation_performed': False, 'database_accessed': False,
    }
    # Only create output after all source, lock and dependency metadata checks succeeded.
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.mkdir(mode=0o700)
    for name, data in {**envs, LOCK_NAME: lock, PROVENANCE_NAME: provenance,
                       'install.json': (json.dumps(config, indent=2) + '\n').encode(),
                       'apt-versions.json': (json.dumps(versions, indent=2) + '\n').encode(),
                       'preparation.json': (json.dumps(receipt, indent=2) + '\n').encode()}.items():
        protected_write(output / name, data)
    (output / 'asterisk').mkdir(mode=0o700)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--node-sha256', help='Reviewed Node 22.22.0 Linux x64 tar.xz checksum; skips public lookup')
    parser.add_argument('--redis-image', help='Reviewed redis:7.4-alpine@sha256:...; skips public lookup')
    args = parser.parse_args(argv)
    try:
        prepare(args.source, args.output, node_sha256=args.node_sha256, redis=args.redis_image)
        print(f'Protected templates prepared: {args.output.resolve()}')
        print('NOT ready to apply: supply real domains, database, credentials, certificates and carrier configuration, then validate.')
        return 0
    except PreparationError as error:
        print(f'ERROR: {error}', file=sys.stderr)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print('ERROR: input preparation failed; check source, bundled provenance, APT metadata and public network access.', file=sys.stderr)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())

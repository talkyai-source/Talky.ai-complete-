"""Real child/shell boundaries for literal operator environment loading."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'


def test_selected_env_preserves_literals_and_does_not_print_or_execute_them(tmp_path):
    marker = tmp_path / 'must-not-execute'
    profile = tmp_path / 'profile.env'
    url = 'postgresql://fixture:private@db.invalid/db?sslmode=verify-full&sslrootcert=/protected/ca.pem'
    literal = '$(touch ' + str(marker) + ')'
    profile.write_text(f'DATABASE_URL={url}\nLITERAL_VALUE={literal}\nEXPANSION=${{DATABASE_URL}}\n')
    child = tmp_path / 'child.py'
    child.write_text("import os; assert os.environ['DATABASE_URL'] == os.environ['EXPECTED_URL']; "
                     "assert os.environ['LITERAL_VALUE'] == os.environ['EXPECTED_LITERAL']; "
                     "assert os.environ['EXPANSION'] == '${DATABASE_URL}'; print('literal child verified')")
    env = dict(os.environ, DATABASE_URL='wrong ambient value', EXPECTED_URL=url, EXPECTED_LITERAL=literal)
    result = subprocess.run([sys.executable, str(SCRIPTS / 'run_with_backend_env.py'), str(profile),
                             sys.executable, str(child)], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and result.stdout.strip() == 'literal child verified'
    assert not marker.exists() and url not in result.stdout + result.stderr


@pytest.mark.parametrize('contents', ['MISSING_VALUE\n', 'BAD-KEY=private-value\n', "KEY='private-value\n"])
def test_invalid_profile_never_starts_the_child(tmp_path, contents):
    profile = tmp_path / 'profile.env'
    profile.write_text(contents)
    marker = tmp_path / 'child-started'
    result = subprocess.run([sys.executable, str(SCRIPTS / 'run_with_backend_env.py'), str(profile),
                             sys.executable, '-c', f'from pathlib import Path; Path({str(marker)!r}).touch()'],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and not marker.exists()
    assert 'private-value' not in result.stdout + result.stderr


@pytest.mark.skipif(os.name != 'posix', reason='The release wrapper requires a real Unix root operator')
def test_release_wrapper_keeps_digest_and_apply_boundaries_with_literal_tls_env(tmp_path):
    bash = shutil.which('bash')
    assert bash
    fixture = tmp_path / 'backend' / 'scripts'
    fixture.mkdir(parents=True)
    for name in ('reconcile_asterisk_release.sh', 'run_with_backend_env.py'):
        shutil.copyfile(SCRIPTS / name, fixture / name)
    marker = tmp_path / 'must-not-execute'
    profile = tmp_path / 'profile.env'
    profile.write_text('DATABASE_URL=postgresql://fixture:private@db.invalid/db?sslmode=verify-full&sslrootcert=/protected/ca.pem\n'
                       f'LITERAL_VALUE=$(touch {marker})\n')
    log = tmp_path / 'calls'
    (fixture / 'reconcile_pjsip_configs.py').write_text(
        "import json,os,sys\nfrom pathlib import Path\n"
        "assert os.environ['DATABASE_URL'].endswith('&sslrootcert=/protected/ca.pem')\n"
        "assert os.environ['LITERAL_VALUE'].startswith('$(touch ')\n"
        f"with Path({str(log)!r}).open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "print(json.dumps({'digest':'a'*64,'mode':'check'}))\n")
    env = dict(os.environ, TALKY_BACKEND_ENV_FILE=str(profile), TALKY_BACKEND_PYTHON=sys.executable)
    command = [bash, str(fixture / 'reconcile_asterisk_release.sh')]
    if os.geteuid() != 0:
        sudo = shutil.which('sudo')
        if not sudo:
            pytest.skip('Passwordless sudo is needed for the isolated root-wrapper fixture')
        command = [sudo, '-n', 'env', f'TALKY_BACKEND_ENV_FILE={profile}',
                   f'TALKY_BACKEND_PYTHON={sys.executable}', *command]
    result = subprocess.run([*command, '--check-only'], env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and log.read_text().splitlines() == ['[]']
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert log.read_text().splitlines() == ['[]', '[]', '["--apply", "--expected-digest", "' + 'a' * 64 + '"]']
    assert not marker.exists() and 'postgresql://' not in result.stdout + result.stderr

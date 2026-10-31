"""Offline tests for destructive boundaries and generated installation contracts."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import sys
import types
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('talky_install', Path(__file__).with_name('install.py'))
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
# prod_gate imports app.* (billing mode); make the backend package importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
GATE_SPEC = importlib.util.spec_from_file_location('installer_actual_prod_gate',
    Path(__file__).resolve().parents[2] / 'backend/app/core/prod_gate.py')
production_gate = importlib.util.module_from_spec(GATE_SPEC)
sys.modules[GATE_SPEC.name] = production_gate
GATE_SPEC.loader.exec_module(production_gate)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.config = json.loads(Path(__file__).with_name('install.example.json').read_text())
        self.config.update(source_sha='a' * 40, database_prepared=True,
                           redis_image='redis:7.4-alpine@sha256:' + 'b' * 64,
                           node_archive_sha256='c' * 64)
        for key in ('backend_env_file', 'tls_certificate_file', 'tls_private_key_file',
                    'python_requirements_lock', 'apt_versions_file',
                    'dashboard_env_file', 'admin_env_file', 'database_ca_file'):
            self.config[key] = str(self.path / key)
            Path(self.config[key]).write_text('test\n')
            Path(self.config[key]).chmod(0o600)
        self.env = {
            'ENVIRONMENT': 'production',
            'DATABASE_URL': 'postgresql://talky_runtime:test@db.example.net/talky?sslmode=verify-full&sslrootcert=/etc/ssl/certs/talky-db-ca.pem',
            'REDIS_URL': 'redis://:' + 'r' * 32 + '@127.0.0.1:6379/0',
            'JWT_SECRET': 'j' * 32, 'SECRET_KEY': 's' * 32,
            'CONNECTOR_ENCRYPTION_KEY': 'e' * 44,
            'SECRETS_MASTER_KEY': 'c' * 64, 'TELEPHONY_METRICS_TOKEN': 'm' * 32,
            'TELEPHONY_ADAPTER': 'asterisk', 'STRIPE_BILLING_DISABLED': '1',
            'INTERNAL_SERVICE_TOKEN': 'i' * 32, 'VOICE_GATEWAY_AUTH_TOKEN': 'g' * 32,
            'BACKEND_INTERNAL_URL': 'http://127.0.0.1:8000',
            'VOICE_GATEWAY_CALLBACK_HOST': '127.0.0.1',
            'ASTERISK_ARI_USER': 'talky', 'ASTERISK_ARI_PASSWORD': 'a' * 32,
            'FRONTEND_URL': 'https://app.example.com', 'API_BASE_URL': 'https://api.example.com',
            'CORS_ORIGINS': 'https://app.example.com,https://admin.example.com',
            'TELEPHONY_PJSIP_AUTO_RELOAD': 'on',
        }
        self.write_env()
        Path(self.config['dashboard_env_file']).write_text('NEXT_PUBLIC_APP_ENV=production\nNEXT_PUBLIC_API_BASE_URL=https://api.example.com/api/v1\n')
        Path(self.config['admin_env_file']).write_text('VITE_API_BASE_URL=https://api.example.com/api/v1\nVITE_USE_DUMMY_AUTH=false\nVITE_ADMIN_DEV_MODE=false\n')
        Path(self.config['python_requirements_lock']).write_text('example==1.0 --hash=sha256:' + 'd' * 64 + '\n')
        Path(self.config['apt_versions_file']).write_text(json.dumps({k: '1.0-1' for k in installer.PACKAGES}))
        ast = self.path / 'asterisk'
        ast.mkdir()
        self.config['asterisk_config_directory'] = str(ast)
        for name in installer.AST_FILES:
            (ast / name).write_text('; reviewed test fixture\n')
            (ast / name).chmod(0o600)
        (ast / 'asterisk.conf').write_text('astctlpermissions=0660\nastctlowner=asterisk\nastctlgroup=asterisk\nrunuser=asterisk\nrungroup=asterisk\n')
        (ast / 'http.conf').write_text('[general]\nenabled=yes\nbindaddr=127.0.0.1\nbindport=8088\n')
        (ast / 'ari.conf').write_text('[general]\nenabled=yes\n[talky]\nread_only=no\npassword=' + 'a' * 32 + '\n')
        (ast / 'pjsip.conf').write_text('#include pjsip.d/*.conf\n')

    def write_env(self):
        Path(self.config['backend_env_file']).write_text(''.join(f'{k}={v}\n' for k, v in self.env.items()))

    def test_valid_inputs(self):
        installer.validate_config(self.config, inputs=True)
        self.config['frontend_mode'] = 'linux'
        installer.validate_config(self.config, inputs=True)

    def test_valid_fixture_also_passes_actual_production_gate(self):
        with patch.dict(os.environ, self.env, clear=True):
            production_gate.enforce_production_gate()

    def test_missing_production_secrets_fail_before_apply(self):
        for key in ('SECRETS_MASTER_KEY', 'TELEPHONY_METRICS_TOKEN'):
            original = self.env.pop(key)
            self.write_env()
            config_path = self.path / 'config.json'
            config_path.write_text(json.dumps(self.config))
            with patch.object(installer, 'apply') as apply:
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(installer.main(['apply', '--config', str(config_path), '--confirm-new-host']), 2)
            apply.assert_not_called()
            self.env[key] = original

    def test_production_guard_flags_rejected_before_install(self):
        bad = {
            'TELEPHONY_DEV_BYPASS_GUARD_ERRORS': 'true', 'TELEPHONY_LOCAL_DEV': 'yes',
            'INBOUND_TRANSFER_STAGING_PROOF_ENABLED': 'true',
            'INBOUND_TRANSFER_STAGING_PROOF_TENANT_ID': 'tenant',
            'INBOUND_TRANSFER_STAGING_PROOF_CONFIG_ID': 'config',
            'CALLER_ID_ENFORCEMENT_MODE': 'log', 'TELEPHONY_INBOUND_REQUIRE_TENANT': 'false',
            'INBOUND_STRICT_ROUTING': 'disabled', 'TELEPHONY_STRICT_INBOUND_ROUTING': 'off',
        }
        for key, value in bad.items():
            with self.subTest(key=key):
                self.env[key] = value
                self.write_env()
                with self.assertRaises(installer.InstallError):
                    installer.validate_config(self.config, inputs=True)
                with patch.dict(os.environ, self.env, clear=True), patch.object(production_gate.logger, 'error'):
                    with self.assertRaises(production_gate.ProductionGateError):
                        production_gate.enforce_production_gate()
                del self.env[key]

    def test_stripe_live_key_and_installed_dependency_prerequisites(self):
        self.env.pop('STRIPE_BILLING_DISABLED')
        self.write_env()
        with self.assertRaisesRegex(installer.InstallError, 'STRIPE_SECRET_KEY'):
            installer.validate_config(self.config, inputs=True)
        self.env['STRIPE_SECRET_KEY'] = 'sk_test_synthetic'
        self.write_env()
        with self.assertRaisesRegex(installer.InstallError, 'live key'):
            installer.validate_config(self.config, inputs=True)
        self.env['STRIPE_SECRET_KEY'] = 'sk_live_synthetic_for_test_only'
        self.write_env()
        with self.assertRaisesRegex(installer.InstallError, 'include Stripe'):
            installer.validate_config(self.config, inputs=True)
        Path(self.config['python_requirements_lock']).write_text('stripe==14.0 --hash=sha256:' + 'd' * 64 + '\n')
        installer.validate_config(self.config, inputs=True)

    def test_runtime_environment_does_not_inherit_operator_database_flags(self):
        with patch.dict(os.environ, {'PGOPTIONS': '-c role=postgres', 'READ_DATABASE_URL': 'secret',
                                     'TELEPHONY_LOCAL_DEV': 'true', 'PYTHONPATH': '/operator/code'}):
            effective = installer.runtime_environment(self.env)
        for key in ('PGOPTIONS', 'READ_DATABASE_URL', 'TELEPHONY_LOCAL_DEV', 'PYTHONPATH'):
            self.assertNotIn(key, effective)

    def test_database_check_rejects_createdb_and_createrole_before_schema_reads(self):
        for flags in ((False, False, True, False), (False, False, False, True)):
            queries = []
            cursor = unittest.mock.MagicMock()
            cursor.__enter__.return_value = cursor
            cursor.execute.side_effect = lambda query: queries.append(query)
            cursor.fetchone.return_value = flags
            connection = unittest.mock.MagicMock()
            connection.__enter__.return_value = connection
            connection.cursor.return_value = cursor
            fake_psycopg = types.SimpleNamespace(connect=lambda *args, **kwargs: connection)
            modules = {'psycopg2': fake_psycopg,
                       'alembic.config': types.SimpleNamespace(Config=lambda value: value),
                       'alembic.script': types.SimpleNamespace(ScriptDirectory=object)}
            with patch.dict(sys.modules, modules), patch.dict(os.environ, self.env):
                with self.assertRaisesRegex(RuntimeError, 'CREATEDB/CREATEROLE'):
                    exec(compile(installer.DATABASE_CHECK_SCRIPT, '<database-check>', 'exec', optimize=2), {})
            self.assertEqual(len(queries), 1)
            self.assertIn('rolcreatedb, rolcreaterole', queries[0])
    def test_linux_frontend_dummy_auth_is_rejected(self):
        self.config['frontend_mode'] = 'linux'
        Path(self.config['admin_env_file']).write_text('VITE_API_BASE_URL=https://api.example.com/api/v1\nVITE_ADMIN_DEV_MODE=true\n')
        with self.assertRaisesRegex(installer.InstallError, 'VITE_ADMIN_DEV_MODE'):
            installer.validate_config(self.config, inputs=True)

    def test_missing_admin_cors_and_wrong_storage_paths_rejected(self):
        for name, value in (('CORS_ORIGINS', 'https://app.example.com'),
                            ('LOCAL_RECORDINGS_DIR', '/root/recordings')):
            old = self.env.get(name)
            self.env[name] = value
            self.write_env()
            with self.assertRaises(installer.InstallError):
                installer.validate_config(self.config, inputs=True)
            if old is None:
                del self.env[name]
            else:
                self.env[name] = old

    def test_plan_never_executes_commands_or_opens_secret_files(self):
        with (patch.object(installer, 'run', side_effect=AssertionError('command executed')),
              patch.object(installer, 'read_env', side_effect=AssertionError('secret opened'))):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(installer.main(['plan']), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['mode'], 'read_only_plan')
        self.assertFalse(result['external_inputs_checked'])

    def test_apply_requires_confirmation_before_host_changes(self):
        path = self.path / 'config.json'
        path.write_text(json.dumps(self.config))
        with patch.object(installer, 'apply') as apply:
            with contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(installer.main(['apply', '--config', str(path)]), 2)
        apply.assert_not_called()
        self.assertIn('--confirm-new-host', output.getvalue())

    def test_unsafe_database_connections_rejected(self):
        for value in ('postgresql://u:p@127.0.0.1/db?sslmode=verify-full',
                      'postgresql://postgres:p@db.example.net/db?sslmode=verify-full',
                      'postgresql://u:p@db.example.net/db?sslmode=require',
                      'postgresql://u:p@db.example.net/db'):
            with self.subTest(value=value):
                self.env['DATABASE_URL'] = value
                self.write_env()
                with self.assertRaises(installer.InstallError):
                    installer.validate_config(self.config, inputs=True)

    def test_migration_credentials_rejected_in_runtime(self):
        self.env['MIGRATION_DATABASE_URL'] = 'secret'
        self.write_env()
        with self.assertRaisesRegex(installer.InstallError, 'migration credentials'):
            installer.validate_config(self.config, inputs=True)

    def test_read_replica_cannot_bypass_database_qualification(self):
        self.env['READ_DATABASE_URL'] = 'postgresql://u:p@localhost/old'
        self.write_env()
        with self.assertRaisesRegex(installer.InstallError, 'READ_DATABASE_URL'):
            installer.validate_config(self.config, inputs=True)

    def test_database_query_overrides_and_loopback_variants_rejected(self):
        original = self.env['DATABASE_URL']
        for value in (original + '&host=127.0.0.1', original + '&user=postgres',
                      original + '&sslmode=disable', original + '&service=other',
                      original.replace('db.example.net', '127.0.0.2'),
                      original.replace('/talky?', '/?')):
            self.env['DATABASE_URL'] = value
            self.write_env()
            with self.assertRaises(installer.InstallError):
                installer.validate_config(self.config, inputs=True)

    def test_ari_credential_or_port_mismatch_rejected(self):
        for name, value in (('ASTERISK_ARI_PASSWORD', 'b' * 32), ('ASTERISK_ARI_PORT', '9999')):
            original = self.env.get(name)
            self.env[name] = value
            self.write_env()
            with self.assertRaises(installer.InstallError):
                installer.validate_config(self.config, inputs=True)
            if original is None:
                del self.env[name]
            else:
                self.env[name] = original

    def test_unpinned_python_requirements_rejected(self):
        for contents in ('package>=1.0\n', 'package==1.0\n', '--extra-index-url https://example.com\n',
                         'package @ https://example.com/pkg.whl\n'):
            Path(self.config['python_requirements_lock']).write_text(contents)
            with self.assertRaisesRegex(installer.InstallError, 'Python lock'):
                installer.validate_config(self.config, inputs=True)

    def test_missing_apt_pin_rejected(self):
        Path(self.config['apt_versions_file']).write_text('{}')
        with self.assertRaisesRegex(installer.InstallError, 'apt_versions_file'):
            installer.validate_config(self.config, inputs=True)

    def test_transitive_apt_pins_can_be_replayed(self):
        versions = {k: '1.0-1' for k in installer.PACKAGES}
        versions['libexample1'] = '2.0-1'
        Path(self.config['apt_versions_file']).write_text(json.dumps(versions))
        installer.validate_config(self.config, inputs=True)
        versions['--allow-unauthenticated'] = '1'
        Path(self.config['apt_versions_file']).write_text(json.dumps(versions))
        with self.assertRaises(installer.InstallError):
            installer.validate_config(self.config, inputs=True)

    def test_existing_host_refuses_before_git_or_package_commands(self):
        with (patch.object(installer.sys, 'platform', 'linux'),
              patch.object(installer.platform, 'machine', return_value='x86_64'),
              patch.object(installer.os, 'geteuid', return_value=0, create=True),
              patch.object(Path, 'read_text', return_value='ID=ubuntu\nVERSION_ID="24.04"\n'),
              patch.object(Path, 'is_dir', return_value=True),
              patch.object(Path, 'exists', return_value=True),
              patch.object(installer, 'run') as run):
            with self.assertRaisesRegex(installer.InstallError, 'existing path'):
                installer.ensure_fresh_host(self.config, self.path)
        run.assert_not_called()

    def test_unsafe_firewall_and_proxy_inputs_rejected(self):
        for key, value in (('ssh_source_cidrs', ['0.0.0.0/0']),
                           ('media_source_cidrs', ['::/0']),
                           ('api_domain', 'example.com; return 200;'), ('ssh_port', True)):
            candidate = copy.deepcopy(self.config)
            candidate[key] = value
            with self.assertRaises(installer.InstallError):
                installer.validate_config(candidate)

    def test_asterisk_lab_credentials_and_absolute_includes_rejected(self):
        path = Path(self.config['asterisk_config_directory']) / 'ari.conf'
        for contents in ('password=talky_local_only_change_me\n', '#include /tmp/secret.conf\n',
                         '#include ../outside.conf\n', '#exec arbitrary\n'):
            path.write_text(contents)
            with self.assertRaises(installer.InstallError):
                installer.validate_config(self.config, inputs=True)

    def test_secret_values_not_printed_on_failure(self):
        self.env['DATABASE_URL'] = 'postgresql://postgres:NEVER_PRINT_ME@db.example.net/talky'
        self.write_env()
        path = self.path / 'config.json'
        path.write_text(json.dumps(self.config))
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(installer.main(['validate', '--config', str(path)]), 2)
        self.assertNotIn('NEVER_PRINT_ME', output.getvalue())

    def test_no_shell_evaluation_of_environment_values(self):
        path = self.path / 'env'
        path.write_text('KEY=$(whoami)\nQUOTED="literal value"\n')
        self.assertEqual(installer.read_env(path), {'KEY': '$(whoami)', 'QUOTED': 'literal value'})
        path.write_text('KEY=one\nKEY=two\n')
        with self.assertRaisesRegex(installer.InstallError, 'duplicate'):
            installer.read_env(path)

    def test_external_mode_does_not_host_frontends(self):
        rendered = installer.nginx_site(self.config)
        self.assertNotIn('server_name app.example.com', rendered)
        self.assertIn('http://127.0.0.1:8000', rendered)
        self.config['frontend_mode'] = 'linux'
        rendered = installer.nginx_site(self.config)
        self.assertIn('server_name app.example.com', rendered)
        self.assertIn('http://127.0.0.1:3000', rendered)
        self.assertIn('root /opt/talky/Admin/frontend/dist', rendered)

    def test_firewall_preserves_ssh_before_enabling(self):
        with patch.object(installer, 'run') as run:
            installer.configure_firewall(self.config)
        calls = [call.args[0] for call in run.call_args_list]
        ssh = ['ufw', 'allow', 'from', '192.0.2.10/32', 'to', 'any', 'port', '22', 'proto', 'tcp']
        self.assertLess(calls.index(ssh), calls.index(['ufw', '--force', 'enable']))
        self.assertFalse(any('5432' in call or '6379' in call or '8088' in call for call in calls))

    def test_non_linux_apply_refuses_before_any_command(self):
        with patch.object(installer.sys, 'platform', 'win32'), patch.object(installer, 'run') as run:
            with self.assertRaisesRegex(installer.InstallError, 'Ubuntu'):
                installer.ensure_fresh_host(self.config, self.path)
        run.assert_not_called()

    def test_origin_preserves_remote_and_rejects_embedded_credentials(self):
        for value in ('https://github.com/org/repo.git', 'git@github.com:org/repo.git'):
            with patch.object(installer, 'run', return_value=value):
                self.assertEqual(installer.checked_origin(self.path), value)
        for value in ('https://token@example.com/repo.git', 'https://u:p@example.com/repo.git',
                      '/root/preparation-checkout', 'file:///root/preparation-checkout'):
            with patch.object(installer, 'run', return_value=value):
                with self.assertRaises(installer.InstallError):
                    installer.checked_origin(self.path)


if __name__ == '__main__':
    unittest.main()

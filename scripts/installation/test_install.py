"""Offline tests for destructive boundaries and generated installation contracts."""
import base64
import contextlib
import copy
import importlib.util
import io
import json
import os
import stat
from pathlib import Path
import tempfile
import sys
import types
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('talky_install', Path(__file__).with_name('install.py'))
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
# Load the actual stdlib billing helper without the services package's eager
# imports of FastAPI and Redis: installer tests must work before dependencies.
BILLING_SPEC = importlib.util.spec_from_file_location('app.domain.services.billing_mode',
    Path(__file__).resolve().parents[2] / 'backend/app/domain/services/billing_mode.py')
billing_mode = importlib.util.module_from_spec(BILLING_SPEC)
BILLING_SPEC.loader.exec_module(billing_mode)
GATE_SPEC = importlib.util.spec_from_file_location('installer_actual_prod_gate',
    Path(__file__).resolve().parents[2] / 'backend/app/core/prod_gate.py')
production_gate = importlib.util.module_from_spec(GATE_SPEC)
sys.modules[GATE_SPEC.name] = production_gate
with patch.dict(sys.modules, {BILLING_SPEC.name: billing_mode}):
    GATE_SPEC.loader.exec_module(production_gate)
# This module only inspects configuration; importing it does not initialize
# providers or connect to any service. Keep parity with the API startup check.
PROVIDER_SPEC = importlib.util.spec_from_file_location('installer_actual_provider_validation',
    Path(__file__).resolve().parents[2] / 'backend/app/core/validation.py')
provider_validation = importlib.util.module_from_spec(PROVIDER_SPEC)
sys.modules[PROVIDER_SPEC.name] = provider_validation
PROVIDER_SPEC.loader.exec_module(provider_validation)


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
            'TOTP_ENCRYPTION_KEY': base64.urlsafe_b64encode(b't' * 32).decode('ascii'),
            'SECRETS_MASTER_KEY': 'c' * 64, 'TELEPHONY_METRICS_TOKEN': 'm' * 32,
            'TELEPHONY_ADAPTER': 'asterisk', 'STRIPE_BILLING_DISABLED': '1',
            'INTERNAL_SERVICE_TOKEN': 'i' * 32, 'VOICE_GATEWAY_AUTH_TOKEN': 'g' * 32,
            'BACKEND_INTERNAL_URL': 'http://127.0.0.1:8000',
            'VOICE_GATEWAY_CALLBACK_HOST': '127.0.0.1',
            'ASTERISK_ARI_USER': 'talky', 'ASTERISK_ARI_PASSWORD': 'a' * 32,
            'FRONTEND_URL': 'https://app.example.com', 'API_BASE_URL': 'https://api.example.com',
            'CORS_ORIGINS': 'https://app.example.com,https://admin.example.com',
            'TELEPHONY_PJSIP_AUTO_RELOAD': 'on',
            # Offline validation fixtures only; never used to start an API or
            # contact a provider, and not evidence of credential acceptance.
            'DEEPGRAM_API_KEY': 'offline-fixture-deepgram',
            'CARTESIA_API_KEY': 'offline-fixture-cartesia',
            'GROQ_API_KEY': 'offline-fixture-groq',
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
        self.config['frontend_mode'] = 'external'
        installer.validate_config(self.config, inputs=True)

    def test_valid_fixture_also_passes_actual_production_gate(self):
        with patch.dict(os.environ, self.env, clear=True):
            production_gate.enforce_production_gate()
            valid, results = provider_validation.ProviderValidator(strict=True).validate_all()
        self.assertTrue(valid, [result.setting for result in results if not result.is_valid])

    def test_required_provider_credentials_fail_before_apply(self):
        # Derive the required names from the real startup validator so a new
        # mandatory provider cannot silently drift past installer preflight.
        required = [name for group in ('stt', 'tts', 'llm')
                    for name, _ in provider_validation.ProviderValidator.REQUIRED_ENV_VARS[group]]
        config_path = self.path / 'config.json'
        config_path.write_text(json.dumps(self.config))
        for key in required:
            original = self.env.pop(key)
            for value in (None, '', '   ', 'REPLACE_WITH_NEVER_PRINT_THIS', 'change_me_NEVER_PRINT_THIS'):
                with self.subTest(key=key, value=value):
                    if value is None:
                        self.env.pop(key, None)
                    else:
                        self.env[key] = value
                    self.write_env()
                    if value in (None, ''):
                        with patch.dict(os.environ, self.env, clear=True):
                            valid, results = provider_validation.ProviderValidator(strict=True).validate_all()
                        self.assertFalse(valid)
                        self.assertTrue(any(result.setting == key and not result.is_valid for result in results))
                    # An operator-shell credential cannot fill a missing or
                    # invalid value in the file copied to the installed service.
                    with (patch.dict(os.environ, {key: 'ambient-offline-fixture'}),
                          patch.object(installer, 'apply') as apply,
                          contextlib.redirect_stderr(io.StringIO()) as output):
                        self.assertEqual(installer.main(['apply', '--config', str(config_path),
                                                         '--confirm-new-host']), 2)
                    apply.assert_not_called()
                    self.assertIn(key, output.getvalue())
                    self.assertNotIn('NEVER_PRINT_THIS', output.getvalue())
            self.env[key] = original
        self.write_env()

    def test_missing_production_secrets_fail_before_apply(self):
        for key in ('SECRETS_MASTER_KEY', 'TELEPHONY_METRICS_TOKEN', 'TOTP_ENCRYPTION_KEY'):
            original = self.env.pop(key)
            self.write_env()
            config_path = self.path / 'config.json'
            config_path.write_text(json.dumps(self.config))
            with patch.object(installer, 'apply') as apply:
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(installer.main(['apply', '--config', str(config_path), '--confirm-new-host']), 2)
            apply.assert_not_called()
            self.env[key] = original

    def test_invalid_totp_key_fails_before_host_changes_without_echoing_key(self):
        config_path = self.path / 'config.json'
        config_path.write_text(json.dumps(self.config))
        for value in ('NOT_A_FERNET_KEY_DO_NOT_PRINT', 'a' * 32, 'a' * 64,
                      base64.urlsafe_b64encode(b't' * 31).decode('ascii'),
                      base64.urlsafe_b64encode(b't' * 33).decode('ascii'),
                      base64.b64encode(b'\xff' * 32).decode('ascii'),
                      'A' * 42 + 'B=', 'A' * 43, 'A' * 43 + '==',
                      ' ' + self.env['TOTP_ENCRYPTION_KEY']):
            with self.subTest(value=value):
                self.env['TOTP_ENCRYPTION_KEY'] = value
                self.write_env()
                with patch.object(installer, 'apply') as apply, \
                     patch.object(installer, 'run') as run, \
                     contextlib.redirect_stderr(io.StringIO()) as output:
                    self.assertEqual(installer.main(['apply', '--config', str(config_path),
                                                     '--confirm-new-host']), 2)
                apply.assert_not_called()
                run.assert_not_called()
                self.assertIn('TOTP_ENCRYPTION_KEY', output.getvalue())
                self.assertNotIn(value, output.getvalue())

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
        self.env['STRIPE_WEBHOOK_SECRET'] = 'whsec_synthetic_for_test_only'
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

    def test_frontend_build_uses_reviewed_env_instead_of_operator_values(self):
        with patch.dict(os.environ, {'NEXT_PUBLIC_API_BASE_URL': 'https://old.example.net/api/v1',
                                     'VITE_USE_DUMMY_AUTH': 'true', 'DATABASE_URL': 'operator-secret',
                                     'TALKLEE_ENABLE_STUB_API': 'true'}):
            dashboard = installer.frontend_build_environment(Path(self.config['dashboard_env_file']))
            admin = installer.frontend_build_environment(Path(self.config['admin_env_file']))
        self.assertEqual(dashboard['NEXT_PUBLIC_API_BASE_URL'], 'https://api.example.com/api/v1')
        self.assertEqual(admin['VITE_USE_DUMMY_AUTH'], 'false')
        for effective in (dashboard, admin):
            self.assertNotIn('DATABASE_URL', effective)
            self.assertNotIn('TALKLEE_ENABLE_STUB_API', effective)
            self.assertEqual(effective['NODE_ENV'], 'production')

    def test_full_linux_stack_is_the_read_only_example_default(self):
        self.assertEqual(self.config['frontend_mode'], 'linux')
        self.assertIn('Build dashboard/Admin from npm lockfiles and serve on Linux', installer.steps(self.config))

    def test_pip_compile_platform_markers_are_accepted_with_hashes(self):
        requirement = ('webrtc-noise-gain==1.3.0 ; sys_platform == "linux" '
                       'and platform_machine == "x86_64" \\\n    --hash=sha256:' + 'd' * 64 + '\n')
        Path(self.config['python_requirements_lock']).write_text(requirement)
        installer.validate_config(self.config, inputs=True)
        for marker in ('sys_platform == "linux"', '(python_version >= "3.12" and os_name != "nt")',
                       'platform_machine in "x86_64 aarch64"'):
            self.assertTrue(installer.valid_lock_row('example==1.0 ; ' + marker + ' --hash=sha256:' + 'a' * 64))

    def test_marker_support_cannot_admit_unpinned_or_executable_requirements(self):
        for row in ('example>=1.0 ; sys_platform == "linux"',
                    'example==1.0 ; sys_platform == "linux"',
                    'example==1.0 ; __import__("os").system("bad") --hash=sha256:' + 'a' * 64,
                    'example==1.0 ; unknown_variable == "linux" --hash=sha256:' + 'a' * 64,
                    'example==1.0 ; sys_platform == "linux" --index-url https://example.net --hash=sha256:' + 'a' * 64):
            self.assertFalse(installer.valid_lock_row(row), row)

    def test_billing_modes_match_existing_production_gate(self):
        Path(self.config['python_requirements_lock']).write_text('stripe==14.0 --hash=sha256:' + 'd' * 64 + '\n')
        scenarios = [
            ({'STRIPE_BILLING_DISABLED': 'on', 'STRIPE_SECRET_KEY': 'sk_test_synthetic'}, True),
            ({'STRIPE_SECRET_KEY': 'sk_test_synthetic', 'STRIPE_TEST_MODE_IN_PRODUCTION': '1',
              'STRIPE_WEBHOOK_SECRET': 'whsec_synthetic'}, True),
            ({'STRIPE_SECRET_KEY': 'sk_test_synthetic', 'STRIPE_WEBHOOK_SECRET': 'whsec_synthetic'}, False),
            ({'STRIPE_SECRET_KEY': 'sk_live_synthetic', 'STRIPE_MOCK_MODE': '1',
              'STRIPE_WEBHOOK_SECRET': 'whsec_synthetic'}, False),
            ({'STRIPE_SECRET_KEY': 'sk_live_synthetic'}, False),
        ]
        self.env.pop('STRIPE_BILLING_DISABLED')
        for overrides, allowed in scenarios:
            with self.subTest(overrides=overrides):
                env = {**self.env, **overrides}
                Path(self.config['backend_env_file']).write_text(''.join(f'{k}={v}\n' for k, v in env.items()))
                with (patch.dict(os.environ, env, clear=True),
                      patch.object(billing_mode.importlib.util, 'find_spec', return_value=object()),
                      patch.object(billing_mode.importlib, 'import_module', return_value=object()),
                      patch.object(production_gate.logger, 'error'), patch.object(production_gate.logger, 'warning')):
                    if allowed:
                        installer.validate_config(self.config, inputs=True)
                        production_gate.enforce_production_gate()
                    else:
                        with self.assertRaises(installer.InstallError):
                            installer.validate_config(self.config, inputs=True)
                        with self.assertRaises(production_gate.ProductionGateError):
                            production_gate.enforce_production_gate()

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

    def test_database_check_requires_login_identity_and_exact_public_schema(self):
        for same_identity, actual, expected, error in (
                (False, ['head'], ['head'], 'own login role'),
                (True, ['old-head'], ['head'], 'prepare and verify schema'),
                (True, [], [], 'prepare and verify schema'),
                (True, ['head'], ['head'], None)):
            with self.subTest(same_identity=same_identity, actual=actual, expected=expected):
                queries = []
                cursor = unittest.mock.MagicMock()
                cursor.__enter__.return_value = cursor
                cursor.execute.side_effect = lambda query: queries.append(query)
                cursor.fetchone.side_effect = [(False, False, False, False), (same_identity,), (True,)]
                cursor.fetchall.return_value = [(head,) for head in actual]
                connection = unittest.mock.MagicMock()
                connection.__enter__.return_value = connection
                connection.cursor.return_value = cursor
                scripts = types.SimpleNamespace(from_config=lambda _: types.SimpleNamespace(get_heads=lambda: expected))
                modules = {'psycopg2': types.SimpleNamespace(connect=lambda *args, **kwargs: connection),
                           'alembic.config': types.SimpleNamespace(Config=lambda value: value),
                           'alembic.script': types.SimpleNamespace(ScriptDirectory=scripts)}
                with patch.dict(sys.modules, modules), patch.dict(os.environ, self.env):
                    if error:
                        with self.assertRaisesRegex(RuntimeError, error):
                            exec(compile(installer.DATABASE_CHECK_SCRIPT, '<database-check>', 'exec', optimize=2), {})
                    else:
                        exec(compile(installer.DATABASE_CHECK_SCRIPT, '<database-check>', 'exec', optimize=2), {})
                if same_identity:
                    self.assertIn('SELECT version_num FROM public.alembic_version', queries)
                else:
                    self.assertEqual(len(queries), 2)
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

    def test_emitted_services_separate_application_and_operator_identities(self):
        target = self.path / 'systemd'
        target.mkdir()
        installer.install_service_units(installer.ROOT / 'backend', target)
        operator_units = {'talky-migrate', 'talky-asterisk-reconcile',
                          'talky-db-backup', 'talky-inbound-synthetic'}
        for path in target.glob('*.service'):
            effective = installer.configparser.ConfigParser(interpolation=None, strict=False)
            overrides = sorted((target / (path.name + '.d')).glob('*.conf'))
            effective.read([str(path), *(str(item) for item in overrides)])
            service = effective['Service']
            expected = 'root' if path.stem in operator_units else 'talky'
            with self.subTest(service=path.name):
                self.assertEqual(service['User'], expected)
                self.assertEqual(service['Group'], expected)
                self.assertEqual(service['NoNewPrivileges'], 'true')
                self.assertEqual(service['UMask'], '0077' if expected == 'root' else '0027')
                if path.stem == 'talky-migrate':
                    self.assertEqual(service['EnvironmentFile'], '/etc/talky/migration.env')
                    self.assertEqual(service['ExecStartPre'],
                        '/opt/talky/backend/venv/bin/python /opt/talky/scripts/installation/install.py check-migration-identity')
                    self.assertNotIn('Install', effective)
                else:
                    self.assertNotIn('migration.env', '\n'.join(item.read_text() for item in [path, *overrides]))
        reconciler = (target / 'talky-asterisk-reconcile.service').read_text()
        self.assertIn('/opt/talky/backend/deploy/asterisk-reconcile-on-drift.sh', reconciler)
        # Retain the existing guarded reconciler, rather than inventing another
        # config writer or changing its live-channel/digest/lock boundaries.
        self.assertEqual(reconciler,
            (installer.ROOT / 'backend/systemd/talky-asterisk-reconcile.service').read_text())

    def test_service_activation_uses_current_trunk_loop_without_unsafe_schedules(self):
        for mode in ('linux', 'external'):
            with self.subTest(mode=mode), patch.object(installer, 'run') as run:
                installer.start_host_services(mode)
                commands = [call.args[0] for call in run.call_args_list]
                enabled = {unit for command in commands if command[:3] == ['systemctl', 'enable', '--now']
                           for unit in command[3:]}
                self.assertIn('talky-trunk-status.service', enabled)
                self.assertIn('talky-asterisk-reconcile.timer', enabled)
                self.assertIn('talky-healthwatch.timer', enabled)
                self.assertEqual('talky-dashboard' in enabled, mode == 'linux')
                self.assertTrue(set(installer.SERVICES) <= enabled)
                for forbidden in ('talky-trunk-status.timer', 'talky-migrate', 'talky-migrate.service',
                                  'talky-db-backup.timer', 'talky-inbound-synthetic.timer', 'talky-cleanup.timer'):
                    self.assertNotIn(forbidden, enabled)
                disable = commands.index(['systemctl', 'disable', '--now', 'talky-trunk-status.timer'])
                start = next(index for index, command in enumerate(commands)
                             if command[:3] == ['systemctl', 'enable', '--now']
                             and 'talky-trunk-status.service' in command)
                self.assertLess(disable, start)

    def test_systemctl_failure_identifies_only_reviewed_installer_actions_and_units(self):
        result = types.SimpleNamespace(returncode=3, stdout='PRIVATE_STDOUT', stderr='PRIVATE_STDERR')
        commands = [
            ['systemctl', 'daemon-reload'],
            ['systemctl', 'enable', '--now', 'docker', 'talky-redis', 'asterisk'],
            ['systemctl', 'disable', '--now', 'talky-trunk-status.timer'],
            ['systemctl', 'is-active', '--quiet', 'talky-api'],
            ['systemctl', 'is-active', '--quiet', 'talky-asterisk-reconcile.timer'],
        ]
        for command in commands:
            with self.subTest(command=command), patch.object(installer.subprocess, 'run', return_value=result):
                with self.assertRaises(installer.InstallError) as error:
                    installer.run(command, env={'PASSWORD': 'PRIVATE_ENV'})
                message = str(error.exception)
                self.assertIn(' '.join(command) + ' failed (exit 3)', message)
                self.assertNotIn('PRIVATE_', message)

    def test_systemctl_diagnostic_rejects_unreviewed_actions_units_and_options(self):
        result = types.SimpleNamespace(returncode=1, stdout='PRIVATE_STDOUT', stderr='PRIVATE_STDERR')
        commands = [
            ['systemctl', 'show', 'talky-api', '--property=Environment'],
            ['systemctl', 'set-environment', 'PASSWORD=PRIVATE_SECRET'],
            ['systemctl', 'enable', '--now', 'private-secret.service'],
            ['systemctl', 'enable', '--now', 'talky-api', 'PRIVATE_SECRET'],
            ['systemctl', 'enable', '--now', '--host=PRIVATE_SECRET', 'talky-api'],
            ['systemctl', 'is-active', '--quiet', 'talky-api\nPRIVATE_SECRET'],
            ['systemctl', 'daemon-reload', 'PRIVATE_SECRET'],
            ['systemctl', 'is-active', '--quiet'],
            ['python', '-c', 'PRIVATE_SECRET'],
        ]
        for command in commands:
            with self.subTest(command=command), patch.object(installer.subprocess, 'run', return_value=result):
                with self.assertRaises(installer.InstallError) as error:
                    installer.run(command, env={'PASSWORD': 'PRIVATE_ENV'})
                self.assertEqual(str(error.exception), command[0] + ' failed (exit 1); '
                    'inspect the service/package locally; command output was suppressed to protect secrets')

    def test_migration_identity_requires_root_private_regular_file_and_exact_loaded_value(self):
        path = self.path / 'migration.env'
        value = 'postgresql://migration:synthetic@db.example.test/db?sslmode=verify-full'
        path.write_text('MIGRATION_DATABASE_URL=' + value + '\n')
        path.chmod(0o600)
        regular = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0)
        with patch.object(installer.os, 'geteuid', return_value=0, create=True), \
             patch.object(Path, 'lstat', return_value=regular), \
             patch.dict(os.environ, {'MIGRATION_DATABASE_URL': value}, clear=True):
            installer.check_migration_identity(path)
            for metadata in (types.SimpleNamespace(st_mode=stat.S_IFREG | 0o640, st_uid=0),
                             types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0),
                             types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1000),
                             types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o600, st_uid=0)):
                with self.subTest(metadata=metadata), patch.object(Path, 'lstat', return_value=metadata), \
                     patch.object(installer, 'read_env') as reader:
                    with self.assertRaisesRegex(installer.InstallError, 'root-owned regular file'):
                        installer.check_migration_identity(path)
                    reader.assert_not_called()
            with patch.object(installer.os, 'geteuid', return_value=1000):
                with self.assertRaisesRegex(installer.InstallError, 'root operator'):
                    installer.check_migration_identity(path)
            for contents, loaded in (('MIGRATION_DATABASE_URL=\n', ''),
                                     ('MIGRATION_DATABASE_URL=' + value + '\nJWT_SECRET=synthetic\n', value),
                                     ('MIGRATION_DATABASE_URL=' + value + '\n', 'different')):
                path.write_text(contents)
                with patch.dict(os.environ, {'MIGRATION_DATABASE_URL': loaded}):
                    with self.assertRaisesRegex(installer.InstallError, 'only the loaded nonempty'):
                        installer.check_migration_identity(path)

    def test_migration_unit_preflight_never_loads_install_config_or_runs_host_commands(self):
        with patch.object(installer, 'check_migration_identity') as check, \
             patch.object(installer, 'validate_config', side_effect=AssertionError('installer config opened')), \
             patch.object(installer, 'run', side_effect=AssertionError('host command executed')):
            self.assertEqual(installer.main(['check-migration-identity']), 0)
        check.assert_called_once_with()

    def test_read_replica_cannot_bypass_database_qualification(self):
        self.env['READ_DATABASE_URL'] = 'postgresql://u:p@localhost/old'
        self.write_env()
        with self.assertRaisesRegex(installer.InstallError, 'READ_DATABASE_URL'):
            installer.validate_config(self.config, inputs=True)

    def test_database_query_overrides_and_loopback_variants_rejected(self):
        original = self.env['DATABASE_URL']
        for value in (original + '&host=127.0.0.1', original + '&user=postgres',
                      original + '&sslmode=disable', original + '&service=other',
                      original + '&options=-c%20role%3Dpostgres',
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

    def test_secret_copy_is_private_during_copy_and_atomic_on_failure(self):
        source = self.path / 'secret-source'
        target = self.path / 'secret-target'
        source.write_text('new-secret')
        target.write_text('previous-secret')
        original_copy = installer.shutil.copyfile
        def checked_copy(source_path, destination):
            if os.name == 'posix':
                self.assertEqual(Path(destination).stat().st_mode & 0o777, 0o600)
            return original_copy(source_path, destination)
        with patch.object(installer.shutil, 'copyfile', side_effect=checked_copy):
            installer.copy_secret(source, target)
        self.assertEqual(target.read_text(), 'new-secret')
        with patch.object(installer.shutil, 'copyfile', side_effect=OSError('copy failed')):
            with self.assertRaises(OSError):
                installer.copy_secret(source, target)
        self.assertEqual(target.read_text(), 'new-secret')
        self.assertEqual(list(self.path.glob('.talky-input-*')), [])

    def test_host_umask_cannot_make_service_source_unreadable_or_world_writable(self):
        for mask in (0o077, 0o027, 0o000):
            with patch.object(installer.os, 'umask', return_value=mask) as umask:
                with self.assertRaisesRegex(installer.InstallError, 'umask 0022'):
                    installer.require_install_umask()
                self.assertEqual(umask.call_args_list[-1].args, (mask,))
        with patch.object(installer.os, 'umask', return_value=0o022):
            installer.require_install_umask()

    def test_external_mode_does_not_host_frontends(self):
        self.config['frontend_mode'] = 'external'
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

    def test_source_git_trust_is_per_command_and_exactly_scoped(self):
        source = self.path.resolve()
        with patch.object(installer, 'run', return_value='') as run:
            installer.source_git(source, ['status', '--porcelain'])
        self.assertEqual(run.call_args.args[0],
                         ['git', '-c', 'safe.directory=', '-c', f'safe.directory={source}', 'status', '--porcelain'])
        self.assertEqual(run.call_args.kwargs['cwd'], source)

    def test_clone_trust_is_private_and_removed_after_failure(self):
        original_config = self.path / 'operator-git-config'
        original_config.write_text('operator configuration is unchanged\n')
        observed = {}

        def command(args, *, env, cwd):
            if args[1] != 'clone':
                return ''
            trust = Path(env['GIT_CONFIG_GLOBAL'])
            self.assertEqual(cwd, trust.parent)
            observed['trust'] = trust
            self.assertTrue(trust.is_file())
            if os.name == 'posix':
                self.assertEqual(trust.stat().st_mode & 0o777, 0o600)
                self.assertEqual(trust.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(env['GIT_CONFIG_NOSYSTEM'], '1')
            self.assertNotIn('GIT_DIR', env)
            self.assertNotIn('GIT_CONFIG_COUNT', env)
            self.assertNotIn('GIT_CONFIG_PARAMETERS', env)
            self.assertNotIn('GIT_CONFIG_KEY_0', env)
            raise installer.InstallError('fixture clone failure')

        with patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(original_config),
                                    'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'safe.directory',
                                    'GIT_CONFIG_VALUE_0': '*', 'GIT_CONFIG_PARAMETERS': 'fixture',
                                    'GIT_DIR': str(self.path / 'wrong-repository')}):
            with patch.object(installer, 'source_git', return_value=str(self.path / '.git')):
                with patch.object(installer, 'run', side_effect=command):
                    with self.assertRaisesRegex(installer.InstallError, 'fixture clone failure'):
                        installer.clone_source(self.path, self.path / 'target')
            self.assertEqual(os.environ['GIT_CONFIG_GLOBAL'], str(original_config))
        self.assertFalse(observed['trust'].parent.exists())
        self.assertEqual(original_config.read_text(), 'operator configuration is unchanged\n')

    @unittest.skipUnless(installer.shutil.which('git'), 'Git is needed for the local ownership regression')
    def test_nonowner_source_and_linked_worktree_can_be_checked_and_cloned(self):
        source = self.path / 'reviewed-source'
        linked = self.path / 'linked-source'
        hooks = self.path / 'empty-hooks'
        hooks.mkdir()
        installer.run(['git', 'init', str(source)])
        (source / 'README').write_text('Reviewed local fixture\n')
        installer.run(['git', 'add', 'README'], cwd=source)
        installer.run(['git', '-c', 'user.name=Installer Test', '-c', 'user.email=installer@example.test',
                       '-c', 'commit.gpgsign=false', '-c', f'core.hooksPath={hooks}',
                       'commit', '-m', 'Local installer fixture'], cwd=source)
        installer.run(['git', 'remote', 'add', 'origin', 'https://example.test/reviewed.git'], cwd=source)
        installer.run(['git', 'worktree', 'add', '--detach', str(linked)], cwd=source)
        with patch.dict(os.environ, {'GIT_TEST_ASSUME_DIFFERENT_OWNER': '1'}):
            for candidate in (source, linked):
                with self.subTest(source=candidate.name):
                    rejected = installer.subprocess.run(['git', '-c', 'safe.directory=', 'status', '--porcelain'],
                        cwd=candidate, text=True, capture_output=True, check=False)
                    self.assertEqual(rejected.returncode, 128)
                    self.assertIn('dubious ownership', rejected.stderr)
                    self.assertEqual(installer.source_git(candidate, ['status', '--porcelain']).strip(), '')
                    expected = installer.source_git(candidate, ['rev-parse', 'HEAD']).strip()
                    self.assertEqual(installer.checked_origin(candidate), 'https://example.test/reviewed.git')
                    target = self.path / (candidate.name + '-installed')
                    installer.clone_source(candidate, target)
                    self.assertEqual(installer.source_git(target, ['rev-parse', 'HEAD']).strip(), expected)


if __name__ == '__main__':
    unittest.main()

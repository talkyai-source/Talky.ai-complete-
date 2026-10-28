"""Release boundaries using real temporary Linux files/links and Git.

These tests do not substitute for the full disposable-VM update rehearsal.
External service calls are mocked only in the explicit transaction-fault cases.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack

SPEC = importlib.util.spec_from_file_location('fresh_release_test_module', Path(__file__).with_name('fresh_release.py'))
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)
OLD, NEW = 'a' * 40, 'b' * 40


class InputBoundaries(unittest.TestCase):
    def test_privileged_tools_never_use_application_home_or_execution_hooks(self):
        result = release.operator_environment(dict(HOME='/var/lib/talky', XDG_CONFIG_HOME='/var/lib/talky/.config',
            XDG_CACHE_HOME='/var/lib/talky/.cache', PIP_TARGET='/unexpected/root/path',
            PIP_CONFIG_FILE='/var/lib/talky/pip.conf', PYTHONPATH='/var/lib/talky/python',
            PYTHONHOME='/var/lib/talky/python-home', LD_PRELOAD='/var/lib/talky/hook.so',
            BASH_ENV='/var/lib/talky/hook.sh', DATABASE_URL='synthetic-literal-value'))
        self.assertEqual(result['HOME'], '/root')
        self.assertEqual(result['XDG_CONFIG_HOME'], '/root/.config')
        self.assertEqual(result['PIP_CONFIG_FILE'], os.devnull)
        self.assertEqual(result['PYTHONNOUSERSITE'], '1')
        self.assertEqual(result['DATABASE_URL'], 'synthetic-literal-value')
        for name in ('PIP_TARGET', 'PYTHONPATH', 'PYTHONHOME', 'LD_PRELOAD', 'BASH_ENV'):
            self.assertNotIn(name, result)

    def test_release_path_rejects_traversal_and_partial_shas(self):
        for value in ('../owned', 'a' * 39, 'A' * 40, '', None):
            with self.subTest(value=value), self.assertRaises(release.ReleaseError):
                release.Layout().release(value)

    def test_database_identity_must_match_explicit_tls_endpoint_and_role(self):
        service = argparse.Namespace(values=dict(host='db.invalid', port='5432', dbname='fixture',
                                                 user='runtime', sslrootcert='/private/ca.crt'))
        url = 'postgresql://runtime:synthetic@db.invalid/fixture?sslmode=verify-full&sslrootcert=/private/ca.crt'
        release.service_matches_url(service, url)
        for invalid in (url.replace('db.invalid', 'other.invalid'), url.replace('runtime:', 'owner:'),
                        url.replace('verify-full', 'require'), url + '&sslmode=disable',
                        url.replace('/private/ca.crt', '/other/ca.crt'),
                        *(url + '&' + key + '=different' for key in
                          ('host', 'hostaddr', 'port', 'dbname', 'database', 'user', 'password', 'service', 'passfile', 'options', 'ssl')),
                        url + '&connect_timeout=1&connect_timeout=2'):
            with self.subTest(value=invalid), self.assertRaises(release.ReleaseError):
                release.service_matches_url(service, invalid)

    def test_actual_migration_login_must_match_restricted_owner_and_verified_endpoint(self):
        valid = dict(user='owner', session='owner', database='fixture', endpoint='known', tls=True,
                     restricted=True, heads=['base'])
        expected = {key: valid[key] for key in ('database', 'endpoint', 'heads')}
        with patch.object(release, 'backend_command', return_value=json.dumps(valid)):
            release.migration_preflight(Path('/unused'), {}, 'owner', expected)
        for key, changed in (('user', 'superuser'), ('session', 'other-owner'), ('database', 'another'),
                             ('endpoint', 'other-host'), ('tls', False), ('restricted', False), ('heads', ['new'])):
            with self.subTest(field=key), patch.object(release, 'backend_command', return_value=json.dumps({**valid, key: changed})):
                with self.assertRaises(release.ReleaseError):
                    release.migration_preflight(Path('/unused'), {}, 'owner', expected)

    def test_command_errors_never_include_output_or_private_arguments(self):
        result = subprocess.CompletedProcess([], 7, 'private-value', 'secret-database-url')
        with patch.object(release.subprocess, 'run', return_value=result):
            with self.assertRaises(release.ReleaseError) as caught:
                release.command(['/usr/bin/fixture', 'secret-argument'])
        self.assertNotIn('private-value', str(caught.exception))
        self.assertNotIn('secret', str(caught.exception))

    def test_private_diagnostic_preserves_failure_without_echoing_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'private'
            result = subprocess.CompletedProcess([], 1, 'discarded-leading-output' + 'x' * 100000 + 'synthetic-private-stdout',
                                                 'discarded-leading-error' + 'y' * 100000 + 'synthetic-private-stderr')
            with patch.object(release, 'DIAGNOSTIC_ROOT', path), \
                 patch.object(release, 'protected'), \
                 patch.object(release.subprocess, 'run', return_value=result):
                with self.assertRaises(release.ReleaseError) as caught:
                    release.command(['/usr/bin/fixture', 'private-argument'])
            self.assertNotIn('synthetic-private', str(caught.exception))
            saved = list(path.glob('failed-*.log'))
            self.assertEqual(len(saved), 1)
            self.assertIn('synthetic-private-stderr', saved[0].read_text())
            self.assertNotIn('private-argument', saved[0].read_text())
            self.assertNotIn('discarded-leading', saved[0].read_text())
            self.assertLessEqual(saved[0].stat().st_size, 2 * 65536 + 64)
            if os.name == 'posix':
                self.assertEqual(stat.S_IMODE(saved[0].stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)

    def test_migration_boundary_refuses_rollback_before_any_database_or_host_command(self):
        with patch.object(release, 'database_state') as database, patch.object(release, 'command') as command:
            with self.assertRaisesRegex(release.ReleaseError, 'Migration began'):
                release.rollback_journal(release.Layout(), {'migration_started': True}, Path('/unused'))
        database.assert_not_called()
        command.assert_not_called()

    def test_incomplete_or_nonzero_observation_blocks_release(self):
        for result in ({'complete': False, 'all_zero_observed': True},
                       {'complete': True, 'all_zero_observed': False}):
            with patch.object(release, 'backend_command', return_value=json.dumps(result)):
                with self.assertRaises(release.ReleaseError):
                    release.observe(Path('/unused'))

    def test_activating_oneshot_is_not_mistaken_for_an_idle_maintenance_job(self):
        for blocked in ('activating', 'reloading', 'deactivating', 'active'):
            calls = []

            def systemctl(args, **kwargs):
                calls.append(args)
                return blocked if args[-1] == 'talky-migrate' else 'inactive'

            journal = {'timers': {'talklee-db-backup.timer': {'enabled': 'enabled', 'active': True}}}
            with self.subTest(state=blocked), patch.object(release, 'command', side_effect=systemctl):
                with self.assertRaisesRegex(release.ReleaseError, 'not fully stopped'):
                    release.freeze_timers(journal)
            self.assertEqual(calls[0], ['systemctl', 'disable', '--now', 'talklee-db-backup.timer'])

    def test_unknown_application_timer_is_rejected_before_any_disable(self):
        with patch.object(release, 'command', return_value='talky-unreviewed-writer.timer enabled enabled') as command:
            with self.assertRaisesRegex(release.ReleaseError, 'Unknown Talky'):
                release.timer_states()
        self.assertEqual(command.call_count, 1)

    def test_frontend_https_checks_compare_served_artifacts_instead_of_accepting_200_alone(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            static = root / 'Talk-Leee/.next/static/test-build'
            static.mkdir(parents=True)
            (static / '_buildManifest.js').write_text('qualified-dashboard-build;\n')
            (static.parent.parent / 'BUILD_ID').write_text('test-build\n')
            admin = root / 'Admin/frontend/dist'
            admin.mkdir(parents=True)
            (admin / 'index.html').write_text('<html>qualified admin</html>\n')
            with patch.object(release, 'command', side_effect=['qualified-dashboard-build;', '<html>qualified admin</html>']) as run:
                release.verify_frontend_artifacts(root, ['api.fixture', 'app.fixture', 'admin.fixture'])
            self.assertIn('https://app.fixture/_next/static/test-build/_buildManifest.js', run.call_args_list[0].args[0])
            with patch.object(release, 'command', return_value='old deployment still returns HTTP200'):
                with self.assertRaisesRegex(release.ReleaseError, 'artifact differs'):
                    release.verify_frontend_artifacts(root, ['api.fixture', 'app.fixture', 'admin.fixture'])


@unittest.skipUnless(os.name == 'posix' and getattr(os, 'geteuid', lambda: -1)() == 0,
                     'Real protected-path and source-publication tests require isolated Linux root')
class NativePublication(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='talklee-fresh-release-test-')
        self.addCleanup(self.temporary.cleanup)
        self.layout = release.Layout(Path(self.temporary.name))
        self.layout.current.mkdir(parents=True)
        self.layout.releases.mkdir()
        self.candidate = self.layout.release(NEW)
        self.candidate.mkdir()
        (self.candidate / '.git').mkdir()
        for name in release.MUTABLE:
            path = self.layout.current / name
            path.mkdir(parents=True, exist_ok=True)
            (path / 'retained-customer-marker').write_text(name)
        for name in release.CONFIGS:
            path = self.layout.current / name
            path.parent.mkdir(parents=True, exist_ok=True)
            text = '\n'.join(key + '=synthetic-' + key for key in release.KEYS) + '\nOTHER_BINDING=preserved\n'
            path.write_text(text)
            path.chmod(0o640)
            target = self.candidate / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
            target.chmod(0o640)
        self.initial_data = release.data_identity(self.layout.current)
        self.initial_keys = release.key_fingerprints(self.layout.current)
        self.initial_config = release.configuration_invariants(self.layout.current)

    def test_first_conversion_and_candidate_publication_preserve_exact_data_and_keys(self):
        seed = self.candidate / 'backend/audio_files/storage'
        seed.mkdir(parents=True)
        (seed / 'source-fixture').write_text('seeded build input')
        release.switch_current(self.layout, OLD, OLD)
        self.assertEqual(self.layout.current.resolve(), self.layout.release(OLD))
        release.share_state(self.layout, OLD, NEW)
        release.switch_current(self.layout, OLD, NEW)
        self.assertEqual(self.layout.current.resolve(), self.candidate)
        self.assertEqual(release.data_identity(self.candidate), self.initial_data)
        self.assertEqual(release.key_fingerprints(self.candidate), self.initial_keys)
        self.assertEqual(release.configuration_invariants(self.candidate), self.initial_config)
        for name in release.MUTABLE:
            self.assertEqual((self.candidate / name / 'retained-customer-marker').read_text(), name)
        self.assertEqual((self.candidate / '.git/talky-seeded-state/backend/audio_files/storage/source-fixture').read_text(),
                         'seeded build input')
        # A later publication restores the previous source without touching state.
        release.switch_current(self.layout, NEW, OLD)
        self.assertEqual(self.layout.current.resolve(), self.layout.release(OLD))
        self.assertEqual(release.data_identity(self.layout.current), self.initial_data)

    def test_existing_prior_destination_is_never_overwritten(self):
        self.layout.release(OLD).mkdir()
        with self.assertRaisesRegex(release.ReleaseError, 'destination exists'):
            release.switch_current(self.layout, OLD, NEW)
        self.assertFalse(self.layout.current.is_symlink())
        self.assertEqual(release.data_identity(self.layout.current), self.initial_data)

    def test_candidate_secret_copy_is_private_at_the_moment_bytes_are_written(self):
        original = release.shutil.copyfile
        observed = []

        def inspect_copy(source, target, *args, **kwargs):
            observed.append(stat.S_IMODE(Path(target).stat().st_mode))
            self.assertEqual(observed[-1], 0o600)
            return original(source, target, *args, **kwargs)

        with patch.object(release.install.shutil, 'copyfile', side_effect=inspect_copy):
            release.copy_candidate_configuration(self.layout.current, self.candidate)
        self.assertEqual(observed, [0o600] * len(release.CONFIGS))

    def test_sealing_never_temporarily_makes_config_world_readable(self):
        import grp
        (self.candidate / 'runtime/bin').mkdir(parents=True)
        (self.candidate / 'runtime/bin/voice_gateway').write_text('synthetic executable')
        original = Path.chmod
        observed = []

        def inspect_chmod(path, mode, *args, **kwargs):
            if str(path.relative_to(self.candidate)) in release.CONFIGS:
                observed.append(mode)
                self.assertEqual(mode & 0o007, 0)
            return original(path, mode, *args, **kwargs)

        with patch.object(grp, 'getgrnam', return_value=argparse.Namespace(gr_gid=os.getgid())), \
             patch.object(release, 'command'), patch.object(Path, 'chmod', inspect_chmod):
            release.seal_tree(self.candidate)
        self.assertGreaterEqual(len(observed), len(release.CONFIGS))

    def test_atomic_publication_failure_keeps_prior_release_reachable(self):
        with patch.object(release.os, 'replace', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                release.switch_current(self.layout, OLD, NEW)
        self.assertEqual(self.layout.current.resolve(), self.layout.release(OLD))
        self.assertEqual(release.data_identity(self.layout.current), self.initial_data)
        self.assertEqual(list(self.layout.current.parent.glob('.talky-release-*')), [])

    def test_initial_link_failure_restores_original_directory(self):
        with patch.object(Path, 'symlink_to', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):
                release.switch_current(self.layout, OLD, NEW)
        self.assertFalse(self.layout.current.is_symlink())
        self.assertTrue(self.layout.current.is_dir())
        self.assertFalse(self.layout.release(OLD).exists())

    def test_shared_path_outside_owned_store_is_rejected(self):
        release.switch_current(self.layout, OLD, OLD)
        root = self.layout.release(OLD)
        path = root / release.MUTABLE[0]
        outside = self.layout.root / 'outside'
        path.rename(outside)
        path.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(release.ReleaseError, 'leaves the release store'):
            release.share_state(self.layout, OLD, NEW)
        self.assertTrue((outside / 'retained-customer-marker').is_file())

    def test_json_journal_is_atomic_private_and_does_not_follow_existing_link(self):
        outside = self.layout.root / 'outside.json'
        outside.write_text('original')
        target = self.layout.root / 'journal.json'
        target.symlink_to(outside)
        release.private_json(target, {'migration_started': True})
        self.assertFalse(target.is_symlink())
        self.assertEqual(outside.read_text(), 'original')
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(json.loads(target.read_text()), {'migration_started': True})

    def test_release_lock_rejects_parallel_operator(self):
        with release.release_lock(self.layout):
            with self.assertRaisesRegex(release.ReleaseError, 'Another local release'):
                with release.release_lock(self.layout):
                    self.fail('Second operation acquired the lock')

    def test_changed_schema_blocks_code_rollback_with_no_service_stop(self):
        journal = dict(migration_started=False, previous_sha=OLD, candidate_sha=NEW, old_heads=['old'], timers={})
        with patch.object(release, 'database_state', return_value={'heads': ['new']}), \
             patch.object(release, 'git', side_effect=lambda path, *args: OLD if args == ('rev-parse', 'HEAD') else ''), \
             patch.object(release, 'verify_previous_artifacts'), \
             patch.object(release, 'freeze_timers'), patch.object(release, 'stop_runtime') as stop:
            with self.assertRaisesRegex(release.ReleaseError, 'Schema changed'):
                release.rollback_journal(self.layout, journal, self.layout.root / 'journal.json')
        stop.assert_not_called()

    def test_real_git_detects_code_drift_while_allowing_only_explicit_runtime_overlay(self):
        root = self.layout.current
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        (root / 'source.py').write_text('trusted source\n')
        subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-qm', 'isolated source fixture'], check=True)
        self.assertTrue(release.tracked_source_clean(root))
        release.protected_source(root)
        (root / 'source.py').chmod(0o666)
        with self.assertRaisesRegex(release.ReleaseError, 'root-owned'):
            release.protected_source(root)
        (root / 'source.py').chmod(0o644)
        (root / 'backend/recordings/retained-customer-marker').write_text('real runtime write')
        self.assertTrue(release.tracked_source_clean(root))
        (root / 'source.py').write_text('unreviewed source\n')
        self.assertFalse(release.tracked_source_clean(root))

    def test_artifact_manifest_detects_untracked_asset_changes_and_excludes_only_next_cache(self):
        for name in release.ARTIFACT_ROOTS:
            directory = self.candidate / name
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'qualified-output').write_text(name)
        cache = self.candidate / 'Talk-Leee/.next/cache'
        cache.mkdir()
        (cache / 'runtime-cache').write_text('before')
        before = release.artifact_manifest(self.candidate)
        (cache / 'runtime-cache').write_text('after')
        self.assertEqual(release.artifact_manifest(self.candidate), before)
        (self.candidate / 'Admin/frontend/dist/qualified-output').write_text('unreviewed API bundle')
        self.assertNotEqual(release.artifact_manifest(self.candidate), before)

    def test_rollback_publishes_a_receipt_for_the_actual_restored_source(self):
        release.switch_current(self.layout, OLD, OLD)
        release.share_state(self.layout, OLD, NEW)
        release.switch_current(self.layout, OLD, NEW)
        path = self.layout.state / 'transactions/owned-fixture/journal.json'
        journal = dict(migration_started=False, previous_sha=OLD, candidate_sha=NEW, old_heads=['base'],
                       keys=self.initial_keys, configuration_invariants=self.initial_config,
                       data_identity=self.initial_data, host_configuration={}, timers={})
        release.private_json(path, journal)
        release.private_json(self.layout.state / 'current.json', {'candidate_sha': NEW})
        with patch.object(release, 'database_state', return_value={'heads': ['base']}), \
             patch.object(release, 'git', side_effect=lambda path, *args: OLD if args == ('rev-parse', 'HEAD') else ''), \
             patch.object(release, 'verify_previous_artifacts'), \
             patch.object(release, 'stop_runtime'), patch.object(release, 'restore_units'), \
             patch.object(release, 'freeze_timers'), \
             patch.object(release, 'publish_version'), patch.object(release, 'command'), \
             patch.object(release, 'start_runtime'), patch.object(release, 'host_configuration', return_value={}):
            release.rollback_journal(self.layout, journal, path)
        self.assertEqual(self.layout.current.resolve(), self.layout.release(OLD))
        self.assertEqual(json.loads((self.layout.state / 'current.json').read_text()),
                         {'candidate_sha': OLD, 'journal': str(path)})

    def transaction_fixture(self, stack, *, migrating=False):
        heads = ['forward' if migrating else 'base']
        proof = dict(status='prepared', candidate_sha=NEW, previous_sha=OLD,
                     old_heads=['base'], new_heads=heads, domains=['api.fixture', 'app.fixture', 'admin.fixture'],
                     configuration=release.configuration_fingerprints(self.layout.current),
                     configuration_invariants=self.initial_config, keys=self.initial_keys,
                     data_identity=self.initial_data, host_configuration={})
        release.private_json(self.layout.prepared(NEW), proof)
        stack.enter_context(patch.object(release, 'installed', return_value=self.layout.current))
        stack.enter_context(patch.object(release, 'git', side_effect=lambda path, *args: NEW if args == ('rev-parse', 'HEAD') else ''))
        stack.enter_context(patch.object(release, 'schema_heads', return_value=heads))
        stack.enter_context(patch.object(release, 'verify_artifacts'))
        stack.enter_context(patch.object(release, 'verify_previous_artifacts'))
        stack.enter_context(patch.object(release, 'database_state', return_value={'heads': ['base']}))
        stack.enter_context(patch.object(release, 'migration_preflight'))
        stack.enter_context(patch.object(release, 'host_configuration', return_value={}))
        stack.enter_context(patch.object(release, 'timer_states', return_value={
            'talky-inbound-synthetic.timer': {'enabled': 'enabled', 'active': True},
            'talklee-db-backup.timer': {'enabled': 'enabled', 'active': True}}))
        stack.enter_context(patch.object(release, 'active', side_effect=lambda name: name in (*release.install.SERVICES, 'talky-dashboard', *release.TIMERS)))
        stack.enter_context(patch.object(release, 'observe', return_value={'complete': True, 'all_zero_observed': True}))
        stack.enter_context(patch.object(release, 'unit_snapshot', side_effect=lambda layout, path: path.mkdir()))
        stack.enter_context(patch.object(release, 'database_backup', return_value={'status': 'archive_verified'}))
        stop = stack.enter_context(patch.object(release, 'stop_runtime'))
        rollback = stack.enter_context(patch.object(release, 'rollback_journal'))
        run = stack.enter_context(patch.object(release, 'command', side_effect=lambda args, **kwargs:
            'inactive' if '--property=ActiveState' in args else ''))
        # Actual manifest verifier still checks candidate/freshness/authority and
        # writes a real replay marker in the owned test tree.
        verifier = release.load_module('native_test_drain', Path(__file__).resolve().parents[2] / 'backend/scripts/verify_deploy_drain_manifest.py')
        owner_values = dict(host='db.invalid', port='5432', dbname='fixture', user='owner', sslrootcert='/private/ca.crt')
        recovery = argparse.Namespace(Service=lambda *args: argparse.Namespace(values=owner_values))
        stack.enter_context(patch.object(release, 'load_module', side_effect=lambda name, path: recovery if name == 'fresh_migration_recovery' else verifier))
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        manifest = self.layout.root / 'manifest.json'
        manifest.write_text(json.dumps(dict(schema_version=2, manifest_id='synthetic-release-' + NEW[:12],
            candidate_sha=NEW, environment='production', issued_at=now.isoformat().replace('+00:00', 'Z'),
            expires_at=(now + timedelta(minutes=5)).isoformat().replace('+00:00', 'Z'),
            traffic=dict(ingress_disabled=True, outbound_origination_disabled=True,
                         active_counts=dict(gateway_sessions=0, asterisk_legs=0, redis_leases=0, db_live_calls=0)),
            evidence=dict(topology_ref='synthetic:owned-test-topology', change_ref='synthetic:owned-test-change'),
            approval_mode='single-owner', owner_authorization_ref='synthetic:test-only-authority',
            approvers=[dict(principal='synthetic-test-owner', role='platform-owner', approval_ref='synthetic:unit-test-only')]), sort_keys=True))
        migration = self.layout.root / 'etc/talky/migration.env'
        migration.parent.mkdir(parents=True)
        migration.write_text('MIGRATION_DATABASE_URL=postgresql://owner:synthetic@db.invalid/fixture?sslmode=verify-full&sslrootcert=/private/ca.crt\n')
        migration.chmod(0o600)
        services = argparse.Namespace(file=self.layout.root / 'services.conf', runtime='runtime', owner='owner', backup='backup')
        return manifest, release.install.sha256(manifest), services, stop, rollback, run

    def test_before_migration_fault_invokes_rollback_and_retains_private_failure_journal(self):
        with ExitStack() as stack:
            manifest, digest, services, stop, rollback, run = self.transaction_fixture(stack)
            stack.enter_context(patch.object(release, 'grants', side_effect=release.ReleaseError('synthetic ACL refusal')))
            with self.assertRaisesRegex(release.ReleaseError, 'traffic remains frozen'):
                release.activate(self.layout, NEW, manifest, digest, services)
            self.assertGreaterEqual(stop.call_count, 1)
            rollback.assert_called_once()
            self.assertFalse(rollback.call_args.args[1]['migration_started'])
        journals = list((self.layout.state / 'transactions').glob('*/journal.json'))
        self.assertEqual(len(journals), 1)
        self.assertEqual(json.loads(journals[0].read_text())['status'], 'failed_before_migration')
        self.assertEqual(stat.S_IMODE(journals[0].stat().st_mode), 0o600)
        self.assertFalse(self.layout.current.is_symlink())

    def test_failure_during_forward_migration_never_attempts_code_rollback(self):
        with ExitStack() as stack:
            manifest, digest, services, stop, rollback, run = self.transaction_fixture(stack, migrating=True)

            def fail_migration(root, args, env=None):
                if '-m' in args and 'alembic' in args:
                    journals = list((self.layout.state / 'transactions').glob('*/journal.json'))
                    self.assertTrue(json.loads(journals[0].read_text())['migration_started'])
                    raise release.ReleaseError('synthetic migration fault')
                return ''

            stack.enter_context(patch.object(release, 'backend_command', side_effect=fail_migration))
            with self.assertRaisesRegex(release.ReleaseError, 'traffic remains frozen'):
                release.activate(self.layout, NEW, manifest, digest, services, allow_migration=True)
            rollback.assert_not_called()
            self.assertGreaterEqual(stop.call_count, 2)
        journal = json.loads(next((self.layout.state / 'transactions').glob('*/journal.json')).read_text())
        self.assertEqual(journal['status'], 'failed_migration_started')
        self.assertFalse(self.layout.current.is_symlink())

    def test_schema_change_without_explicit_forward_flag_stops_before_manifest_or_services(self):
        with ExitStack() as stack:
            manifest, digest, services, stop, rollback, run = self.transaction_fixture(stack, migrating=True)
            with self.assertRaisesRegex(release.ReleaseError, 'allow-forward-migration'):
                release.activate(self.layout, NEW, manifest, digest, services)
            stop.assert_not_called()
            rollback.assert_not_called()
            self.assertFalse((self.layout.state / 'used-manifests').exists())

    def test_failed_drain_after_timer_freeze_never_stops_live_calls_or_starts_rollback(self):
        with ExitStack() as stack:
            manifest, digest, services, stop, rollback, run = self.transaction_fixture(stack)
            stack.enter_context(patch.object(release, 'observe', side_effect=release.ReleaseError('nonzero active calls')))
            with self.assertRaisesRegex(release.ReleaseError, 'traffic remains frozen'):
                release.activate(self.layout, NEW, manifest, digest, services)
            stop.assert_not_called()
            rollback.assert_not_called()
        journal = json.loads(next((self.layout.state / 'transactions').glob('*/journal.json')).read_text())
        self.assertEqual(journal['status'], 'blocked_before_runtime_stop')
        self.assertFalse(journal['runtime_stop_started'])


if __name__ == '__main__':
    unittest.main()

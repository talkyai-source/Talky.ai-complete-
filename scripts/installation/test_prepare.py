"""Offline checks for protected preparation; no apt mutations or public network."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('talky_prepare', Path(__file__).with_name('prepare.py'))
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'source'
        self.templates = self.source / 'scripts/installation'
        self.templates.mkdir(parents=True)
        (self.source / 'backend').mkdir()
        self.output = self.base / 'protected'
        self.sha = 'a' * 40
        self.node = 'b' * 64
        self.redis = 'redis:7.4-alpine@sha256:' + 'c' * 64
        template = json.loads(Path(__file__).with_name('install.example.json').read_text())
        (self.templates / 'install.example.json').write_text(json.dumps(template))
        for name in ('backend.env', 'dashboard.env', 'admin.env'):
            (self.templates / (name + '.example')).write_text('EXAMPLE=REPLACE_ME\n')
        self.lock = b'example==1.0 --hash=sha256:' + b'd' * 64 + b'\n'
        requirements = b'example==1.0\n'
        (self.templates / prepare.LOCK_NAME).write_bytes(self.lock)
        (self.source / 'backend/requirements.txt').write_bytes(requirements)
        self.provenance = {
            'hash_normalization': 'UTF-8 text with LF newlines',
            'lock_sha256': hashlib.sha256(self.lock).hexdigest(),
            'requirements_sha256': hashlib.sha256(requirements).hexdigest(),
        }
        (self.templates / prepare.PROVENANCE_NAME).write_text(json.dumps(self.provenance))
        for name, value in [('check_host', None), ('checked_source', self.sha),
                            ('installer_packages', ('one', 'two')),
                            ('apt_candidates', {'one': '1.0', 'two': '2.0'}),
                            ('node_checksum', self.node), ('redis_image', self.redis)]:
            mock = patch.object(prepare, name, return_value=value)
            setattr(self, name + '_mock', mock.start())
            self.addCleanup(mock.stop)

    def test_outputs_are_pinned_protected_and_explicitly_incomplete(self):
        receipt = prepare.prepare(self.source, self.output)
        config = json.loads((self.output / 'install.json').read_text())
        self.assertEqual(config['source_sha'], self.sha)
        self.assertEqual(config['frontend_mode'], 'linux')
        self.assertFalse(config['database_prepared'])
        self.assertEqual(config['api_domain'], 'api.example.com')
        self.assertEqual(config['node_archive_sha256'], self.node)
        self.assertEqual(config['redis_image'], self.redis)
        self.assertEqual(config['backend_env_file'], str(self.output.resolve() / 'backend.env'))
        self.assertEqual((self.output / prepare.LOCK_NAME).read_bytes(), self.lock)
        self.assertFalse((self.output / 'privkey.pem').exists())
        self.assertEqual(list((self.output / 'asterisk').iterdir()), [])
        self.assertFalse(receipt['installation_performed'])
        self.assertFalse(receipt['database_accessed'])
        self.assertTrue(receipt['remaining_inputs'])
        self.assertEqual(self.checked_source_mock.call_count, 2)
        if os.name == 'posix':
            self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
            for path in self.output.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)

    def test_existing_output_is_not_overwritten_or_looked_up(self):
        self.output.mkdir()
        sentinel = self.output / 'keep'
        sentinel.write_text('existing input')
        with self.assertRaisesRegex(prepare.PreparationError, 'Output already exists'):
            prepare.prepare(self.source, self.output)
        self.assertEqual(sentinel.read_text(), 'existing input')
        self.apt_candidates_mock.assert_not_called()

    def test_output_within_checkout_refused(self):
        with self.assertRaisesRegex(prepare.PreparationError, 'outside the source'):
            prepare.prepare(self.source, self.source / 'inputs')
        self.assertFalse((self.source / 'inputs').exists())

    def test_dirty_source_leaves_no_output(self):
        self.checked_source_mock.side_effect = prepare.PreparationError('dirty source')
        with self.assertRaisesRegex(prepare.PreparationError, 'dirty source'):
            prepare.prepare(self.source, self.output)
        self.assertFalse(self.output.exists())
        self.node_checksum_mock.assert_not_called()

    def test_source_change_during_metadata_reads_leaves_no_output(self):
        self.checked_source_mock.side_effect = [self.sha, 'f' * 40]
        with self.assertRaisesRegex(prepare.PreparationError, 'Source changed'):
            prepare.prepare(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_missing_lock_fails_before_metadata_and_writes(self):
        (self.templates / prepare.LOCK_NAME).unlink()
        with self.assertRaisesRegex(prepare.PreparationError, 'hash lock and its provenance'):
            prepare.prepare(self.source, self.output)
        self.assertFalse(self.output.exists())
        self.apt_candidates_mock.assert_not_called()

    def test_provenance_rejects_changed_lock_and_requirements(self):
        for target in (self.templates / prepare.LOCK_NAME, self.source / 'backend/requirements.txt'):
            original = target.read_bytes()
            with self.subTest(target=target.name):
                target.write_bytes(original + b'changed\n')
                with self.assertRaises(prepare.PreparationError):
                    prepare.prepare(self.source, self.output)
                self.assertFalse(self.output.exists())
                target.write_bytes(original)

    def test_provenance_is_independent_of_checkout_line_endings(self):
        for target in (self.templates / prepare.LOCK_NAME, self.source / 'backend/requirements.txt'):
            target.write_bytes(target.read_bytes().replace(b'\n', b'\r\n'))
        prepare.prepare(self.source, self.output)
        self.assertEqual((self.output / prepare.LOCK_NAME).read_bytes(), self.lock)

    def test_reviewed_metadata_flags_skip_network(self):
        prepare.prepare(self.source, self.output, node_sha256=self.node, redis=self.redis)
        self.node_checksum_mock.assert_not_called()
        self.redis_image_mock.assert_not_called()

    def test_invalid_pin_flags_do_not_create_output(self):
        for flags in ({'node_sha256': 'not-a-hash'}, {'redis': 'redis:latest'}):
            with self.subTest(flags=flags), self.assertRaises(prepare.PreparationError):
                prepare.prepare(self.source, self.output, **flags)
            self.assertFalse(self.output.exists())


class MetadataTests(unittest.TestCase):
    def test_apt_candidates_are_exact_and_missing_fails(self):
        with patch.object(prepare, 'run', return_value='one:\n  Candidate: 1:2.0-1ubuntu1\ntwo:amd64:\n  Candidate: 3.0\n') as command:
            self.assertEqual(prepare.apt_candidates(('one', 'two')), {'one': '1:2.0-1ubuntu1', 'two': '3.0'})
            command.assert_called_once_with(['apt-cache', 'policy', 'one', 'two'])
        with patch.object(prepare, 'run', return_value='one:\n  Candidate: (none)\n'):
            with self.assertRaisesRegex(prepare.PreparationError, 'No APT candidate for: one'):
                prepare.apt_candidates(('one',))

    def test_node_metadata_selects_only_exact_archive(self):
        text = ('a' * 64 + '  node-v22.22.0-linux-arm64.tar.xz\n' +
                'b' * 64 + '  node-v22.22.0-linux-x64.tar.xz\n').encode()
        with patch.object(prepare, 'fetch', return_value=(text, {})):
            self.assertEqual(prepare.node_checksum(), 'b' * 64)
        with patch.object(prepare, 'fetch', return_value=(b'', {})):
            with self.assertRaises(prepare.PreparationError):
                prepare.node_checksum()

    def test_redis_digest_is_tied_to_manifest_with_linux_amd64(self):
        body = json.dumps({'manifests': [{'platform': {'os': 'linux', 'architecture': 'amd64'}}]}).encode()
        digest = 'sha256:' + hashlib.sha256(body).hexdigest()
        with patch.object(prepare, 'fetch', side_effect=[(b'{"token":"public-pull-token"}', {}),
                                                       (body, {'Docker-Content-Digest': digest})]):
            self.assertEqual(prepare.redis_image(), 'redis:7.4-alpine@' + digest)
        with patch.object(prepare, 'fetch', side_effect=[(b'{"token":"public-pull-token"}', {}),
                                                       (body, {'Docker-Content-Digest': 'sha256:' + 'f' * 64})]):
            with self.assertRaisesRegex(prepare.PreparationError, 'manifest digest'):
                prepare.redis_image()

    def test_checked_source_uses_git_read_only_and_rejects_dirt(self):
        source = prepare.ROOT
        with patch.object(prepare, 'run', side_effect=[str(source), '', 'a' * 40]) as command:
            self.assertEqual(prepare.checked_source(source), 'a' * 40)
            self.assertEqual([call.args[0][:2] for call in command.call_args_list],
                             [['git', 'rev-parse'], ['git', 'status'], ['git', 'rev-parse']])
        with patch.object(prepare, 'run', side_effect=[str(source), '?? new-input.env']):
            with self.assertRaisesRegex(prepare.PreparationError, 'completely clean'):
                prepare.checked_source(source)


if __name__ == '__main__':
    unittest.main()

"""Offline destructive-boundary checks for the opt-in Docker fixture."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("tls_canonical_fixture", Path(__file__).with_name("tls_canonical_rehearsal.py"))
fixture_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture_module)


class FixtureBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = fixture_module.Fixture(Path(self.tmp.name), "postgres:16-bookworm")

    def test_remote_host_rejection_does_not_contact_docker_even_during_cleanup(self):
        with (patch.object(fixture_module.sys, "platform", "linux"),
              patch.dict(fixture_module.os.environ, {"DOCKER_HOST": "tcp://remote.example.invalid:2376"}),
              patch.object(self.fixture, "docker") as docker):
            with self.assertRaisesRegex(RuntimeError, "Remote Docker hosts"):
                self.fixture.prepare()
            self.fixture.cleanup()
        docker.assert_not_called()

    def test_rejected_context_is_not_contacted_by_cleanup(self):
        with (patch.object(fixture_module.sys, "platform", "linux"),
              patch.dict(fixture_module.os.environ, {"DOCKER_HOST": ""}),
              patch.object(self.fixture, "docker", return_value=json.dumps("ssh://remote.example.invalid")) as docker):
            with self.assertRaisesRegex(RuntimeError, "local Unix socket"):
                self.fixture.prepare()
            self.fixture.cleanup()
        docker.assert_called_once_with("context", "inspect", "--format", "{{json .Endpoints.docker.Host}}")

    def test_non_linux_refuses_before_dependencies_clients_or_fixture_creation(self):
        with (patch.object(fixture_module.sys, "platform", "win32"),
              patch.object(fixture_module.sys, "argv", ["fixture", "--run", "--receipt", str(Path(self.tmp.name) / "receipt.json")]),
              patch.object(fixture_module, "Fixture") as fixture,
              patch.object(fixture_module.recovery, "Postgres") as postgres):
            with self.assertRaisesRegex(RuntimeError, "requires Linux"):
                fixture_module.main()
        fixture.assert_not_called()
        postgres.assert_not_called()

    def test_matching_label_cannot_authorize_removing_an_unregistered_container(self):
        self.fixture.docker_verified = True
        container = "c" * 64

        def docker(*args):
            if args[:2] == ("container", "ls"):
                return container
            if args[0] == "inspect":
                return json.dumps([{"Id": container, "Name": "/unregistered-container",
                                    "Config": {"Labels": {fixture_module.LABEL: self.fixture.token}}}])
            if args[:2] == ("network", "ls"):
                return ""
            raise AssertionError("An unowned resource reached a mutation command")

        with patch.object(self.fixture, "docker", side_effect=docker) as command:
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                self.fixture.cleanup()
        self.assertFalse(any(call.args[0] == "rm" for call in command.call_args_list))


if __name__ == "__main__":
    unittest.main()

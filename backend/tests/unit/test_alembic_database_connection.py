"""Exercise Alembic through the real SQLAlchemy driver boundary without I/O."""

from __future__ import annotations

import inspect
import os
from contextlib import nullcontext
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import alembic
from alembic.config import Config
import asyncpg


ENV_FILE = Path(__file__).parents[2] / "Alembic" / "env.py"


class _BeforeNetwork(Exception):
    """Stop at asyncpg.connect, before it can open a socket."""


class AlembicDatabaseConnectionTests(unittest.TestCase):
    def _connection_arguments(self, environment):
        config = Config()
        context = SimpleNamespace(config=config, is_offline_mode=lambda: False)
        signature = inspect.signature(asyncpg.connect)
        observed = []

        async def capture(*args, **kwargs):
            # A misplaced sslmode keyword fails this real driver contract.
            signature.bind(*args, **kwargs)
            observed.append((args, kwargs))
            raise _BeforeNetwork

        with (
            patch.dict(os.environ, environment, clear=True),
            patch.object(alembic, "context", context),
            patch.object(asyncpg, "connect", capture),
            self.assertRaises(_BeforeNetwork),
        ):
            runpy.run_path(str(ENV_FILE))

        self.assertEqual(len(observed), 1)
        # The INI configuration must never contain either credential URL.
        self.assertEqual(config.get_main_option("sqlalchemy.url"), "postgresql+asyncpg://")
        return observed[0]

    def test_external_tls_and_encoded_credentials_reach_driver_unchanged(self):
        dsn = (
            "postgresql://migration:p%40ss%25word@db.example.test:5432/talky"
            "?sslmode=verify-full&sslrootcert=%2Fetc%2Ftalky%2Fdb-ca.pem"
            "&application_name=talky-migration"
        )
        args, kwargs = self._connection_arguments({"DATABASE_URL": dsn})
        self.assertEqual(args, ())
        self.assertEqual(kwargs, {"dsn": dsn})

    def test_tls_options_are_not_forwarded_as_unsupported_driver_keywords(self):
        dsn = "postgresql://owner:fixture@db.example.test/talky?sslmode=verify-full"
        _, kwargs = self._connection_arguments({"DATABASE_URL": dsn})
        self.assertEqual(kwargs, {"dsn": dsn})

    def test_migration_credentials_override_runtime_credentials(self):
        dsn = "postgresql://owner:fixture@db.example.test/talky?sslmode=verify-full"
        _, kwargs = self._connection_arguments(
            {
                "DATABASE_URL": "postgresql://runtime:fixture@db.example.test/talky",
                "MIGRATION_DATABASE_URL": dsn,
            }
        )
        self.assertEqual(kwargs, {"dsn": dsn})

    def test_legacy_local_and_postgres_scheme_remain_usable(self):
        for dsn in (
            "postgresql://runtime:fixture@127.0.0.1:5432/talky",
            "postgres://runtime:fixture@127.0.0.1:5432/talky",
        ):
            with self.subTest(scheme=dsn.split(":", 1)[0]):
                _, kwargs = self._connection_arguments({"DATABASE_URL": dsn})
                self.assertEqual(kwargs, {"dsn": dsn})

    def test_sqlalchemy_scheme_is_normalized_before_asyncpg(self):
        dsn = "postgresql+asyncpg://owner:p%25ss@db.example.test/talky?sslmode=require"
        _, kwargs = self._connection_arguments({"MIGRATION_DATABASE_URL": dsn})
        self.assertEqual(kwargs["dsn"], dsn.replace("postgresql+asyncpg://", "postgresql://", 1))

    def test_explicit_empty_migration_credentials_do_not_use_runtime_role(self):
        config = Config()
        context = SimpleNamespace(config=config, is_offline_mode=lambda: False)
        with (
            patch.dict(
                os.environ,
                {"DATABASE_URL": "postgresql://runtime:fixture@localhost/talky", "MIGRATION_DATABASE_URL": ""},
                clear=True,
            ),
            patch.object(alembic, "context", context),
            patch.object(asyncpg, "connect") as connect,
            self.assertRaisesRegex(RuntimeError, "MIGRATION_DATABASE_URL or DATABASE_URL"),
        ):
            runpy.run_path(str(ENV_FILE))
        connect.assert_not_called()

    def test_offline_migrations_do_not_connect_or_store_credentials(self):
        config = Config()
        configure = Mock()
        run_migrations = Mock()
        context = SimpleNamespace(
            config=config,
            is_offline_mode=lambda: True,
            configure=configure,
            begin_transaction=nullcontext,
            run_migrations=run_migrations,
        )
        with (
            patch.dict(
                os.environ,
                {"DATABASE_URL": "postgresql://owner:p%25ss@db.example.test/talky?sslmode=verify-full"},
                clear=True,
            ),
            patch.object(alembic, "context", context),
            patch.object(asyncpg, "connect") as connect,
        ):
            runpy.run_path(str(ENV_FILE))
        connect.assert_not_called()
        run_migrations.assert_called_once_with()
        self.assertEqual(configure.call_args.kwargs["url"], "postgresql+asyncpg://")


if __name__ == "__main__":
    unittest.main()

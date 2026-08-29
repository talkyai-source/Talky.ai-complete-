"""Real-PostgreSQL proof of the repaired bootstrap contract at Alembic head."""

from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = importlib.import_module("Alembic.versions.0033_bootstrap_contract_repair")
CURRENT_HEAD = "0035_user_profiles_role_widen"
BACKEND = Path(__file__).resolve().parents[2]
_LOCAL_DATABASE_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_TEST_DATABASE_NAME = re.compile(r"(?:^|[_-])(?:test|ci|tmp|ephemeral)(?:[_-]|$)")
_NEW_USER_ROLES = ("campaign_manager", "agent", "billing_user")


def _dsn_or_skip() -> str:
    dsn = os.getenv("TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("explicit TEST_DATABASE_URL is required")
    try:
        url = make_url(dsn)
    except (TypeError, ValueError):
        pytest.fail("TEST_DATABASE_URL is malformed", pytrace=False)
    host = (url.host or "").lower()
    database = (url.database or "").lower()
    if not url.drivername.startswith("postgresql"):
        pytest.fail("TEST_DATABASE_URL must use PostgreSQL", pytrace=False)
    if host not in _LOCAL_DATABASE_HOSTS:
        pytest.fail(
            "destructive bootstrap integration requires a loopback PostgreSQL host",
            pytrace=False,
        )
    if not _TEST_DATABASE_NAME.search(database):
        pytest.fail(
            "destructive bootstrap integration requires an explicitly test-named database",
            pytrace=False,
        )
    return dsn


def _engine_or_fail():
    dsn = _dsn_or_skip()
    engine = None
    try:
        engine = create_engine(dsn, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return engine
    except SQLAlchemyError as exc:
        if engine is not None:
            engine.dispose()
        pytest.fail(
            "configured TEST_DATABASE_URL is not reachable "
            f"({type(exc).__name__})",
            pytrace=False,
        )


def _run_alembic(dsn: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = dsn
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(BACKEND / "alembic.ini"),
            *arguments,
        ],
        cwd=BACKEND,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _run_upgrade(connection) -> None:
    original_op = MIGRATION.op
    try:
        MIGRATION.op = Operations(MigrationContext.configure(connection))
        MIGRATION.upgrade()
    finally:
        MIGRATION.op = original_op


@pytest.mark.integration
def test_current_head_has_the_full_bootstrap_contract_and_test_call_boundary() -> None:
    engine = _engine_or_fail()
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                current = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
                if current != CURRENT_HEAD:
                    pytest.skip(f"database is at {current!r}, not {CURRENT_HEAD!r}")

                # These campaign fields are projected unconditionally by the
                # inbound-admission route lookup.  Compile the runtime-bound
                # projection, not just the migration's own catalog metadata.
                connection.execute(
                    text(
                        """
                        SELECT tts_provider, prompt_version_pin,
                               knowledge_mode, knowledge_model
                        FROM campaigns
                        LIMIT 0
                        """
                    )
                )

                for table, expected_columns in MIGRATION._REQUIRED_COLUMNS.items():
                    actual_columns = set(
                        connection.execute(
                            text(
                                """
                                SELECT a.attname
                                FROM pg_attribute AS a
                                WHERE a.attrelid = to_regclass(:table_name)
                                  AND a.attnum > 0
                                  AND NOT a.attisdropped
                                """
                            ),
                            {"table_name": f"public.{table}"},
                        ).scalars()
                    )
                    assert set(expected_columns) <= actual_columns, table

                for index in MIGRATION._REQUIRED_INDEXES:
                    assert connection.execute(
                        text("SELECT to_regclass(:name) IS NOT NULL"),
                        {"name": f"public.{index}"},
                    ).scalar_one(), index

                for table in MIGRATION._RLS_TABLES:
                    policy = (
                        connection.execute(
                            text(
                                """
                            SELECT c.relrowsecurity, c.relforcerowsecurity,
                                   count(p.policyname) AS policy_count,
                                   bool_and(
                                       COALESCE(p.qual, '') LIKE '%app.bypass_rls%'
                                       AND COALESCE(p.with_check, '')
                                           LIKE '%app.bypass_rls%'
                                   ) AS bypass_aware
                            FROM pg_class AS c
                            LEFT JOIN pg_policies AS p
                              ON p.schemaname='public'
                             AND p.tablename=c.relname
                            WHERE c.oid=to_regclass(:table_name)
                            GROUP BY c.relrowsecurity, c.relforcerowsecurity
                            """
                            ),
                            {"table_name": f"public.{table}"},
                        )
                        .mappings()
                        .one()
                    )
                    assert policy["relrowsecurity"] is True, table
                    assert policy["relforcerowsecurity"] is True, table
                    if table in MIGRATION._APPEND_ONLY_RLS_TABLES:
                        assert policy["policy_count"] == 4, table
                    else:
                        assert policy["policy_count"] == 1, table
                        assert policy["bypass_aware"] is True, table

                assert (
                    connection.execute(
                        text(
                            """
                        SELECT count(*) FROM topup_packages
                        WHERE (code,minutes,price_cents,currency,sort_order) IN (
                            ('mins_250',250,2500,'GBP',1),
                            ('mins_600',600,5400,'GBP',2),
                            ('mins_1500',1500,12000,'GBP',3)
                        )
                        """
                        )
                    ).scalar_one()
                    == 3
                )
                # The schema-only bootstrap must not fabricate 0019 tenant
                # migration evidence or replay its historical data update.
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM ai_config_migrations")
                    ).scalar_one()
                    == 0
                )

                connection.execute(text("SET LOCAL app.bypass_rls = 'on'"))
                tenant_id = uuid.uuid4()
                campaign_id = uuid.uuid4()
                real_call_id = uuid.uuid4()
                test_call_id = uuid.uuid4()
                connection.execute(
                    text(
                        "INSERT INTO tenants (id,business_name) "
                        "VALUES (:tenant_id,'Bootstrap repair fixture')"
                    ),
                    {"tenant_id": tenant_id},
                )
                connection.execute(
                    text(
                        "INSERT INTO campaigns (id,tenant_id,name) "
                        "VALUES (:campaign_id,:tenant_id,'Bootstrap repair campaign')"
                    ),
                    {"campaign_id": campaign_id, "tenant_id": tenant_id},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO calls
                            (id,tenant_id,campaign_id,phone_number,is_test)
                        VALUES
                            (:real_call_id,:tenant_id,:campaign_id,
                             '+15550002001',FALSE),
                            (:test_call_id,:tenant_id,:campaign_id,
                             '+15550002002',TRUE)
                        """
                    ),
                    {
                        "real_call_id": real_call_id,
                        "test_call_id": test_call_id,
                        "tenant_id": tenant_id,
                        "campaign_id": campaign_id,
                    },
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM calls WHERE tenant_id=:tenant_id"),
                        {"tenant_id": tenant_id},
                    ).scalar_one()
                    == 2
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM billable_calls " "WHERE tenant_id=:tenant_id"),
                        {"tenant_id": tenant_id},
                    ).scalar_one()
                    == 1
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM calls " "WHERE tenant_id=:tenant_id AND is_test"
                        ),
                        {"tenant_id": tenant_id},
                    ).scalar_one()
                    == 1
                )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_idempotent_repair_preserves_legitimate_topup_catalog_changes() -> None:
    engine = _engine_or_fail()
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                current = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
                if current != CURRENT_HEAD:
                    pytest.skip(f"database is at {current!r}, not {CURRENT_HEAD!r}")
                audit_function_definitions = {
                    signature: connection.execute(
                        text("SELECT pg_get_functiondef(to_regprocedure(:signature))"),
                        {"signature": signature},
                    ).scalar_one()
                    for signature in (
                        "public.log_tenant_policy_mutation()",
                        "public.prune_tenant_policy_audit_log(integer)",
                    )
                }
                connection.execute(
                    text(
                        """
                        UPDATE topup_packages
                           SET name='Repriced 250 minutes',
                               minutes=275,
                               price_cents=4321,
                               currency='USD',
                               sort_order=9
                         WHERE code='mins_250'
                        """
                    )
                )
                connection.execute(text("DELETE FROM topup_packages WHERE code='mins_600'"))

                # Execute the revision again under a real Alembic Operations
                # context. Every DDL path and the catalog validator run; a
                # pre-existing catalogue must not be seeded or rewritten.
                _run_upgrade(connection)

                for signature, definition in audit_function_definitions.items():
                    assert (
                        connection.execute(
                            text("SELECT pg_get_functiondef(to_regprocedure(:signature))"),
                            {"signature": signature},
                        ).scalar_one()
                        == definition
                    )

                preserved = connection.execute(
                    text(
                        """
                        SELECT name,minutes,price_cents,currency,sort_order
                        FROM topup_packages WHERE code='mins_250'
                        """
                    )
                ).one()
                assert preserved == (
                    "Repriced 250 minutes",
                    275,
                    4321,
                    "USD",
                    9,
                )
                assert (
                    connection.execute(
                        text("SELECT count(*) FROM topup_packages WHERE code='mins_600'")
                    ).scalar_one()
                    == 0
                )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("mutations", "expected_error"),
    (
        (
            (
                "ALTER TABLE review_reward_ledger DROP CONSTRAINT " "review_reward_once_per_review",
                "ALTER TABLE review_reward_ledger ADD CONSTRAINT "
                "review_reward_once_per_review UNIQUE (id)",
            ),
            "review_reward_once_per_review",
        ),
        (
            (
                "ALTER TABLE billing_ledger DROP CONSTRAINT " "billing_ledger_kind_valid",
                "ALTER TABLE billing_ledger ADD CONSTRAINT "
                "billing_ledger_kind_valid CHECK (TRUE)",
            ),
            "billing_ledger_kind_valid",
        ),
        (
            (
                "DROP INDEX idx_billing_ledger_event",
                "CREATE UNIQUE INDEX idx_billing_ledger_event " "ON billing_ledger (id)",
            ),
            "idx_billing_ledger_event",
        ),
        (
            ("ALTER TABLE topup_orders ALTER COLUMN tenant_id DROP NOT NULL",),
            "topup_orders.tenant_id",
        ),
        (
            (
                "ALTER TABLE tenant_sip_trunks ENABLE REPLICA TRIGGER "
                "trg_audit_tenant_sip_trunks",
            ),
            "trg_audit_tenant_sip_trunks",
        ),
        (
            (
                "CREATE FUNCTION bootstrap_wrong_audit_trigger() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RETURN COALESCE(NEW, OLD); END $$",
                "DROP TRIGGER trg_audit_tenant_sip_trunks ON tenant_sip_trunks",
                "CREATE TRIGGER trg_audit_tenant_sip_trunks "
                "AFTER INSERT OR UPDATE OR DELETE ON tenant_sip_trunks "
                "FOR EACH ROW EXECUTE FUNCTION bootstrap_wrong_audit_trigger()",
            ),
            "trg_audit_tenant_sip_trunks",
        ),
    ),
)
def test_repair_rejects_malformed_partial_contract_and_rollback_restores_head(
    mutations: tuple[str, ...],
    expected_error: str,
) -> None:
    engine = _engine_or_fail()
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                for statement in mutations:
                    connection.execute(text(statement))
                with pytest.raises(RuntimeError, match=expected_error):
                    _run_upgrade(connection)
            finally:
                transaction.rollback()

        # The bad shape and every DDL statement the failed repair attempted
        # were transactional. A new transaction sees the original head and can
        # rerun the complete validator cleanly.
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                assert (
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                    == CURRENT_HEAD
                )
                _run_upgrade(connection)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_legacy_telephony_rls_isolates_tenants_and_honors_authorized_bypass() -> None:
    engine = _engine_or_fail()
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                assert connection.execute(
                    text("SELECT to_regrole('authenticated') IS NOT NULL")
                ).scalar_one(), "bootstrap fixture must create the hardened authenticated role"
                role_capabilities = connection.execute(
                    text(
                        """
                        SELECT rolsuper, rolbypassrls
                        FROM pg_roles
                        WHERE rolname = 'authenticated'
                        """
                    )
                ).one()
                assert tuple(role_capabilities) == (False, False)
                tenant_a = uuid.uuid4()
                tenant_b = uuid.uuid4()
                connection.execute(text("SET LOCAL app.bypass_rls='on'"))
                connection.execute(
                    text(
                        """
                        INSERT INTO tenants (id,business_name) VALUES
                            (:tenant_a,'RLS fixture A'),
                            (:tenant_b,'RLS fixture B')
                        """
                    ),
                    {"tenant_a": tenant_a, "tenant_b": tenant_b},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO tenant_sip_trunks
                            (tenant_id,trunk_name,sip_domain)
                        VALUES
                            (:tenant_a,'fixture-a','a.invalid'),
                            (:tenant_b,'fixture-b','b.invalid')
                        """
                    ),
                    {"tenant_a": tenant_a, "tenant_b": tenant_b},
                )
                connection.execute(
                    text("GRANT SELECT,INSERT ON tenant_sip_trunks " "TO authenticated")
                )
                connection.execute(
                    text(
                        "GRANT SELECT,INSERT,UPDATE,DELETE ON "
                        "tenant_policy_audit_log TO authenticated"
                    )
                )
                connection.execute(text("SET LOCAL ROLE authenticated"))
                connection.execute(text("SET LOCAL app.bypass_rls='off'"))
                connection.execute(
                    text("SELECT set_config('app.current_tenant_id',:tenant,TRUE)"),
                    {"tenant": str(tenant_a)},
                )
                assert (
                    connection.execute(text("SELECT count(*) FROM tenant_sip_trunks")).scalar_one()
                    == 1
                )

                same_tenant_trunk_id = uuid.uuid4()
                connection.execute(
                    text(
                        """
                        INSERT INTO tenant_sip_trunks
                            (id,tenant_id,trunk_name,sip_domain)
                        VALUES (:id,:tenant_a,'same-tenant','same.invalid')
                        """
                    ),
                    {"id": same_tenant_trunk_id, "tenant_a": tenant_a},
                )
                audit_row_id = connection.execute(
                    text(
                        """
                        SELECT id
                        FROM tenant_policy_audit_log
                        WHERE table_name='tenant_sip_trunks'
                          AND record_id=:record_id
                          AND action='INSERT'
                        """
                    ),
                    {"record_id": same_tenant_trunk_id},
                ).scalar_one()
                # The audit ledger remains immutable even after granting SQL
                # UPDATE/DELETE: its explicit command policies expose no row.
                assert (
                    connection.execute(
                        text(
                            "UPDATE tenant_policy_audit_log SET source='tampered' "
                            "WHERE id=:audit_id"
                        ),
                        {"audit_id": audit_row_id},
                    ).rowcount
                    == 0
                )
                assert (
                    connection.execute(
                        text("DELETE FROM tenant_policy_audit_log WHERE id=:audit_id"),
                        {"audit_id": audit_row_id},
                    ).rowcount
                    == 0
                )
                assert (
                    connection.execute(
                        text("SELECT source FROM tenant_policy_audit_log WHERE id=:audit_id"),
                        {"audit_id": audit_row_id},
                    ).scalar_one()
                    == "db_trigger"
                )

                savepoint = connection.begin_nested()
                try:
                    with pytest.raises(DBAPIError):
                        connection.execute(
                            text(
                                """
                                INSERT INTO tenant_sip_trunks
                                    (tenant_id,trunk_name,sip_domain)
                                VALUES (:tenant_b,'wrong-tenant','wrong.invalid')
                                """
                            ),
                            {"tenant_b": tenant_b},
                        )
                finally:
                    savepoint.rollback()

                connection.execute(text("SET LOCAL app.current_tenant_id=''"))
                assert (
                    connection.execute(text("SELECT count(*) FROM tenant_sip_trunks")).scalar_one()
                    == 0
                )
                connection.execute(text("SET LOCAL app.bypass_rls='on'"))
                assert (
                    connection.execute(text("SELECT count(*) FROM tenant_sip_trunks")).scalar_one()
                    == 3
                )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_billing_ledger_is_append_only_for_tenant_service_and_owner_contexts() -> None:
    engine = _engine_or_fail()
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                current = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
                if current not in {
                    "0033_bootstrap_contract_repair",
                    "0034_inbound_billing_four_eye",
                    "0035_user_profiles_role_widen",
                }:
                    pytest.skip(f"database is at unsupported revision {current!r}")

                policies = list(
                    connection.execute(
                        text(
                            """
                            SELECT cmd, COALESCE(qual, '') AS qual,
                                   COALESCE(with_check, '') AS with_check
                            FROM pg_policies
                            WHERE schemaname='public'
                              AND tablename='billing_ledger'
                            """
                        )
                    ).mappings()
                )
                by_command = {row["cmd"]: row for row in policies}
                assert len(policies) == 4
                assert "app.bypass_rls" in by_command["SELECT"]["qual"]
                assert "app.bypass_rls" in by_command["INSERT"]["with_check"]
                assert by_command["UPDATE"]["qual"] == "false"
                assert by_command["DELETE"]["qual"] == "false"

                trigger = (
                    connection.execute(
                        text(
                            """
                            SELECT t.tgtype, t.tgenabled::text AS tgenabled,
                                   t.tgfoid=to_regprocedure(
                                       'public.prevent_billing_ledger_mutation()'
                                   ) AS canonical_function
                            FROM pg_trigger AS t
                            WHERE t.tgrelid='public.billing_ledger'::regclass
                              AND t.tgname='billing_ledger_immutable'
                              AND NOT t.tgisinternal
                            """
                        )
                    )
                    .mappings()
                    .one()
                )
                assert trigger["tgtype"] == 27
                assert trigger["tgenabled"] == "A"
                assert trigger["canonical_function"] is True

                tenant_a = uuid.uuid4()
                tenant_b = uuid.uuid4()
                ledger_a = uuid.uuid4().int % 9_000_000_000_000_000_000
                ledger_a_insert = uuid.uuid4().int % 9_000_000_000_000_000_000
                ledger_b_bypass = uuid.uuid4().int % 9_000_000_000_000_000_000
                connection.execute(text("SET LOCAL app.bypass_rls='on'"))
                connection.execute(
                    text(
                        """
                        INSERT INTO tenants (id,business_name) VALUES
                            (:tenant_a,'Billing ledger fixture A'),
                            (:tenant_b,'Billing ledger fixture B')
                        """
                    ),
                    {"tenant_a": tenant_a, "tenant_b": tenant_b},
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO billing_ledger (
                            id,tenant_id,kind,minutes_delta,amount_cents,
                            currency,provider_event_id,note
                        ) VALUES (
                            :id,:tenant_a,'topup',100,2500,'GBP',
                            :event_id,'immutable fixture'
                        )
                        """
                    ),
                    {
                        "id": ledger_a,
                        "tenant_a": tenant_a,
                        "event_id": f"bootstrap-ledger-{uuid.uuid4()}",
                    },
                )
                connection.execute(
                    text("GRANT SELECT,INSERT,UPDATE,DELETE ON billing_ledger " "TO authenticated")
                )

                # A normal tenant may read and append its own evidence, but
                # UPDATE/DELETE expose no row even when SQL grants exist.
                connection.execute(text("SET LOCAL ROLE authenticated"))
                connection.execute(text("SET LOCAL app.bypass_rls='off'"))
                connection.execute(
                    text("SELECT set_config('app.current_tenant_id',:tenant,TRUE)"),
                    {"tenant": str(tenant_a)},
                )
                assert (
                    connection.execute(text("SELECT count(*) FROM billing_ledger")).scalar_one()
                    == 1
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO billing_ledger (
                            id,tenant_id,kind,minutes_delta,amount_cents,note
                        ) VALUES (
                            :id,:tenant_a,'adjustment',5,0,'tenant append'
                        )
                        """
                    ),
                    {"id": ledger_a_insert, "tenant_a": tenant_a},
                )
                assert (
                    connection.execute(text("UPDATE billing_ledger SET minutes_delta=999")).rowcount
                    == 0
                )
                assert connection.execute(text("DELETE FROM billing_ledger")).rowcount == 0

                # The application service bypass retains cross-tenant
                # INSERT/SELECT, but it does not reopen mutation privileges.
                connection.execute(text("SET LOCAL app.bypass_rls='on'"))
                connection.execute(
                    text(
                        """
                        INSERT INTO billing_ledger (
                            id,tenant_id,kind,minutes_delta,amount_cents,note
                        ) VALUES (
                            :id,:tenant_b,'adjustment',7,0,'service append'
                        )
                        """
                    ),
                    {"id": ledger_b_bypass, "tenant_b": tenant_b},
                )
                assert (
                    connection.execute(text("SELECT count(*) FROM billing_ledger")).scalar_one()
                    == 3
                )
                assert (
                    connection.execute(text("UPDATE billing_ledger SET minutes_delta=888")).rowcount
                    == 0
                )
                assert connection.execute(text("DELETE FROM billing_ledger")).rowcount == 0

                # Table owners and BYPASSRLS roles do not pass through RLS.
                # The always-enabled trigger is the independent last boundary.
                connection.execute(text("RESET ROLE"))
                for statement in (
                    "UPDATE billing_ledger SET minutes_delta=777 WHERE id=:id",
                    "DELETE FROM billing_ledger WHERE id=:id",
                ):
                    savepoint = connection.begin_nested()
                    try:
                        with pytest.raises(DBAPIError, match="append-only"):
                            connection.execute(text(statement), {"id": ledger_a})
                    finally:
                        savepoint.rollback()

                persisted = connection.execute(
                    text("SELECT minutes_delta,note FROM billing_ledger " "WHERE id=:id"),
                    {"id": ledger_a},
                ).one()
                assert persisted == (100, "immutable fixture")
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0035_role_check_accepts_exact_new_roles_and_rejects_unknown() -> None:
    engine = _engine_or_fail()
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                for role in _NEW_USER_ROLES:
                    connection.execute(
                        text(
                            "INSERT INTO user_profiles (email, role) "
                            "VALUES (:email, :role)"
                        ),
                        {
                            "email": f"role-check-{role}-{uuid.uuid4()}@example.test",
                            "role": role,
                        },
                    )

                savepoint = connection.begin_nested()
                try:
                    with pytest.raises(DBAPIError):
                        connection.execute(
                            text(
                                "INSERT INTO user_profiles (email, role) "
                                "VALUES (:email, 'unexpected_superuser')"
                            ),
                            {"email": f"role-check-unknown-{uuid.uuid4()}@example.test"},
                        )
                finally:
                    savepoint.rollback()

                role_checks = list(
                    connection.execute(
                        text(
                            """
                            SELECT c.conname, c.convalidated,
                                   pg_get_expr(c.conbin, c.conrelid, true) AS expression
                            FROM pg_constraint AS c
                            JOIN pg_attribute AS a
                              ON a.attrelid = c.conrelid
                             AND a.attnum = ANY (c.conkey)
                            WHERE c.conrelid = 'public.user_profiles'::regclass
                              AND c.contype = 'c'
                              AND a.attname = 'role'
                              AND array_length(c.conkey, 1) = 1
                            """
                        )
                    ).mappings()
                )
                assert len(role_checks) == 1
                assert role_checks[0]["conname"] == "chk_user_profiles_role_valid"
                assert role_checks[0]["convalidated"] is True
                assert all(role in role_checks[0]["expression"] for role in _NEW_USER_ROLES)
                assert connection.execute(
                    text(
                        "SELECT count(*) FROM pg_constraint "
                        "WHERE conrelid='public.user_profiles'::regclass "
                        "AND conname='__talky_0035_role_check_probe'"
                    )
                ).scalar_one() == 0
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_0035_downgrade_refuses_atomically_while_new_role_is_retained() -> None:
    dsn = _dsn_or_skip()
    engine = _engine_or_fail()
    email = f"role-downgrade-guard-{uuid.uuid4()}@example.test"
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO user_profiles (email, role) "
                    "VALUES (:email, 'campaign_manager')"
                ),
                {"email": email},
            )

        result = _run_alembic(dsn, "downgrade", "0034_inbound_billing_four_eye")
        assert result.returncode != 0
        assert "Refusing to downgrade 0035" in result.stderr

        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == CURRENT_HEAD
            )
            assert connection.execute(
                text("SELECT count(*) FROM user_profiles WHERE email=:email"),
                {"email": email},
            ).scalar_one() == 1
            assert connection.execute(
                text(
                    "SELECT convalidated FROM pg_constraint "
                    "WHERE conrelid='public.user_profiles'::regclass "
                    "AND conname='chk_user_profiles_role_valid'"
                )
            ).scalar_one() is True
    finally:
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM user_profiles WHERE email=:email"),
                {"email": email},
            )
        repair = _run_alembic(dsn, "upgrade", "head")
        engine.dispose()
        assert repair.returncode == 0, repair.stdout + repair.stderr


@pytest.mark.integration
def test_0033_downgrade_refusal_keeps_marker_and_schema_intact() -> None:
    dsn = _dsn_or_skip()
    engine = _engine_or_fail()
    try:
        with engine.connect() as connection:
            before = connection.execute(text("SELECT count(*) FROM topup_packages")).scalar_one()

        result = _run_alembic(dsn, "downgrade", "0032_inbound_billing_hold")
        assert result.returncode != 0
        assert "Refusing to downgrade 0033" in result.stderr

        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == CURRENT_HEAD
            )
            assert (
                connection.execute(text("SELECT count(*) FROM topup_packages")).scalar_one()
                == before
            )
            assert connection.execute(
                text("SELECT to_regclass('public.billable_calls') IS NOT NULL")
            ).scalar_one()
    finally:
        engine.dispose()

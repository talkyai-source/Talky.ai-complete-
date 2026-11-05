#!/usr/bin/env python3
"""Read-only, fail-closed PAYG compatibility check before changing application code.

Retain this trusted controller helper outside the checkout before selecting a
release. Never execute a rollback target's own copy to authorize that target.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys

import asyncpg
from dotenv import dotenv_values

PAYG_INTRODUCTION = "290b15c1f341a326775732da376256279b3a56a4"
PRE_PAYG_HEAD = "0082_support_system"
PAYG_HEAD = "0083_pay_as_you_go"


class RollbackRefused(RuntimeError):
    """A safe diagnostic, containing no connection string or database values."""


@dataclass(frozen=True)
class PaygState:
    schema_state: str
    has_payg_tenants: bool | None
    has_active_payg_bundles: bool | None


async def read_payg_state(database_url: str) -> PaygState:
    """Observe the authoritative primary, without silently filtering RLS rows."""
    if database_url.startswith("postgresql+asyncpg://"):
        database_url = "postgresql://" + database_url[len("postgresql+asyncpg://"):]
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise RollbackRefused("PostgreSQL primary connection is required")
    conn = None
    observation_succeeded = False
    try:
        # Pass the full DSN through: asyncpg consumes sslmode, sslrootcert,
        # sslcert and sslkey. Never weaken TLS or strip its query parameters.
        conn = await asyncpg.connect(database_url, timeout=10, command_timeout=10)
        async with conn.transaction(readonly=True):
            await conn.execute("SET LOCAL statement_timeout = '5000ms'")
            await conn.execute("SET LOCAL lock_timeout = '1000ms'")
            await conn.execute("SET LOCAL row_security = off")
            if await conn.fetchval("SELECT pg_is_in_recovery()") is not False:
                raise RollbackRefused("Authoritative primary database was not proved")
            relations = await conn.fetchrow(
                "SELECT to_regclass('public.tenants') IS NOT NULL AS tenants, "
                "to_regclass('public.plans') IS NOT NULL AS plans, "
                "to_regclass('public.topup_packages') IS NOT NULL AS packages, "
                "to_regclass('public.alembic_version') IS NOT NULL AS version"
            )
            if not relations or not all(relations[name] is True for name in ("tenants", "plans", "packages", "version")):
                raise RollbackRefused("Required database schema was not proved")
            heads = await conn.fetch("SELECT version_num FROM public.alembic_version")
            if len(heads) != 1:
                raise RollbackRefused("Exactly one recognized database revision is required")
            head = heads[0]["version_num"]
            columns = await conn.fetchrow(
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute "
                "WHERE attrelid=to_regclass('public.plans') AND attname='billing_model' "
                "AND attnum > 0 AND NOT attisdropped) AS billing, "
                "EXISTS (SELECT 1 FROM pg_catalog.pg_attribute "
                "WHERE attrelid=to_regclass('public.topup_packages') AND attname='audience' "
                "AND attnum > 0 AND NOT attisdropped) AS audience"
            )
            if head == PRE_PAYG_HEAD and columns["billing"] is False and columns["audience"] is False:
                schema_state = "pre_payg"
            elif head == PAYG_HEAD and columns["billing"] is True and columns["audience"] is True:
                schema_state = "payg"
            else:
                raise RollbackRefused("Unknown or mixed PAYG schema; reviewed recovery is required")
            if schema_state == "payg":
                # This schema always forbids legacy code, even while empty.
                # Compatible deploys need no privileged customer-row reads.
                state = PaygState(schema_state, None, None)
            else:
                has_tenants = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM public.tenants WHERE plan_id='payg')"
                )
                has_bundles = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM public.topup_packages "
                    "WHERE is_active IS TRUE AND left(code,5)='payg_')"
                )
                if not isinstance(has_tenants, bool) or not isinstance(has_bundles, bool):
                    raise RollbackRefused("PAYG data visibility was not proved")
                if has_tenants or has_bundles:
                    raise RollbackRefused("PAYG records contradict the pre-PAYG schema")
                state = PaygState(schema_state, has_tenants, has_bundles)
        observation_succeeded = True
        return state
    except RollbackRefused:
        raise
    except Exception as exc:
        # Driver errors can embed credentials, SQL or returned values.
        raise RollbackRefused(f"Database observation failed ({type(exc).__name__})") from None
    finally:
        if conn is not None:
            try:
                await conn.close(timeout=5)
            except Exception as exc:
                # Preserve an existing refusal; cleanup must not replace it
                # with a driver error that can contain connection credentials.
                if observation_succeeded:
                    raise RollbackRefused(f"Database connection cleanup failed ({type(exc).__name__})") from None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RollbackRefused("Repository compatibility could not be inspected") from None


def _verify_target_commit(repo: Path, target_sha: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", target_sha):
        raise RollbackRefused("A full immutable target commit is required")
    target = _git(repo, "rev-parse", "--verify", f"{target_sha}^{{commit}}")
    if target.returncode or target.stdout.strip() != target_sha:
        raise RollbackRefused("Target commit was not proved")


def verify_target(repo: Path, target_sha: str, state: PaygState) -> None:
    _verify_target_commit(repo, target_sha)
    introduction = _git(repo, "cat-file", "-e", f"{PAYG_INTRODUCTION}^{{commit}}")
    if introduction.returncode:
        raise RollbackRefused("Trusted PAYG introduction commit is unavailable")
    ancestry = _git(repo, "merge-base", "--is-ancestor", PAYG_INTRODUCTION, target_sha)
    if ancestry.returncode not in (0, 1):
        raise RollbackRefused("Target PAYG compatibility could not be proved")
    if state.schema_state not in {"pre_payg", "payg"}:
        raise RollbackRefused("Unrecognized database compatibility state")
    if state.schema_state == "pre_payg" and (
        state.has_payg_tenants is not False or state.has_active_payg_bundles is not False
    ):
        raise RollbackRefused("Pre-PAYG data absence was not proved")
    if ancestry.returncode and (
        state.schema_state == "payg" or state.has_payg_tenants or state.has_active_payg_bundles
    ):
        # Empty tables are not permission to roll back: billing writers can
        # introduce prepaid data immediately after an otherwise-safe scan.
        raise RollbackRefused("Legacy target is incompatible with the PAYG schema; use compatible code or code-forward recovery")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--target-sha", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        _verify_target_commit(args.repo, args.target_sha)
        if not args.env_file.is_file():
            raise RollbackRefused("Authoritative database environment file is required")
        settings = dotenv_values(args.env_file, interpolate=False)
        database_url = settings.get("MIGRATION_DATABASE_URL") or settings.get("DATABASE_URL")
        if not database_url:
            raise RollbackRefused("Primary database URL is absent from the environment file")
        state = asyncio.run(read_payg_state(database_url))
        verify_target(args.repo, args.target_sha, state)
    except RollbackRefused as exc:
        print(f"CODE ROLLBACK CHECK FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"CODE ROLLBACK CHECK FAILED: inspection unavailable ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(f"CODE ROLLBACK CHECK PASSED: target={args.target_sha} schema={state.schema_state} "
          f"payg_tenants={state.has_payg_tenants} active_payg_bundles={state.has_active_payg_bundles}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Prepare a tenant recovery manifest in a NEW isolated PostgreSQL database.

Default and only mode: restore the full verified payload to an isolated database,
then inspect catalogs/counts/media. There is deliberately no live copy/apply.
Shared identity, global audit-chain and immutable receipt reconciliation require
owner/database review. No roles, grants, migrations or application workers run.
"""
from __future__ import annotations

import argparse
import configparser
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import time
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent / "database"))
import recovery  # noqa: E402
from recovery import RecoveryError, digest, identifier, write_json_new  # noqa: E402

CATALOG_SQL = """
SELECT COALESCE(json_agg(json_build_object(
 'schema', n.nspname, 'table', c.relname,
 'columns', (SELECT json_agg(a.attname ORDER BY a.attnum) FROM pg_attribute a
             WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
 'column_types', (SELECT json_object_agg(a.attname, format_type(a.atttypid,a.atttypmod))
                  FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
 'primary_key', COALESCE((SELECT json_agg(a.attname ORDER BY k.ord)
                   FROM pg_constraint p CROSS JOIN LATERAL unnest(p.conkey) WITH ORDINALITY k(num,ord)
                   JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.num
                   WHERE p.conrelid=c.oid AND p.contype='p'), '[]'::json),
 'foreign_keys', COALESCE((SELECT json_agg(json_build_object(
        'columns', (SELECT json_agg(a.attname ORDER BY k.ord)
                    FROM unnest(f.conkey) WITH ORDINALITY k(num,ord)
                    JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.num),
        'parent_schema', pn.nspname, 'parent_table', pc.relname,
        'parent_columns', (SELECT json_agg(a.attname ORDER BY k.ord)
                    FROM unnest(f.confkey) WITH ORDINALITY k(num,ord)
                    JOIN pg_attribute a ON a.attrelid=pc.oid AND a.attnum=k.num),
        'deferrable', f.condeferrable, 'validated', f.convalidated))
    FROM pg_constraint f JOIN pg_class pc ON pc.oid=f.confrelid
    JOIN pg_namespace pn ON pn.oid=pc.relnamespace
    WHERE f.conrelid=c.oid AND f.contype='f'), '[]'::json),
 'triggers', COALESCE((SELECT json_agg(t.tgname ORDER BY t.tgname) FROM pg_trigger t
                      WHERE t.tgrelid=c.oid AND NOT t.tgisinternal), '[]'::json),
 'rls_enabled', c.relrowsecurity, 'force_rls', c.relforcerowsecurity,
 'partition', c.relispartition
) ORDER BY n.nspname,c.relname), '[]'::json)
FROM pg_catalog.pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema')
  AND n.nspname NOT LIKE 'pg_%';
"""

IMMUTABLE = {
    "billing_ledger", "review_reward_ledger", "legal_acceptances",
    "invoice_snapshots", "billing_invoice_snapshots", "billing_refund_snapshots",
    "inbound_usage_receipts", "inbound_billing_usage_receipts", "inbound_audit_receipts",
    "admin_media_deletion_intents", "support_request_receipts",
}
MAX_TABLES = 2000
MAX_MEDIA = 100000


def _tenant(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RecoveryError("tenant must be a UUID") from exc


def _catalog(catalog):
    if not isinstance(catalog, list) or not 1 <= len(catalog) <= MAX_TABLES:
        raise RecoveryError("catalog is empty or exceeds the explicit table limit")
    result = {}
    for row in catalog:
        name = row["table"]
        identifier(name)
        identifier(row.get("schema", "public"))
        if name in result:
            raise RecoveryError("duplicate table names across schemas require a reviewed inventory")
        columns = row["columns"]
        if not isinstance(columns, list) or not columns:
            raise RecoveryError("catalog columns are missing")
        for column in columns:
            identifier(column)
        if any(column not in columns for column in row["primary_key"]):
            raise RecoveryError("catalog primary key is invalid")
        for link in row["foreign_keys"]:
            identifier(link["parent_table"])
            identifier(link.get("parent_schema", "public"))
            if (not link["columns"] or len(link["columns"]) != len(link["parent_columns"])
                    or any(column not in columns for column in link["columns"])):
                raise RecoveryError("catalog foreign key is incomplete")
            for column in link["parent_columns"]:
                identifier(column)
        result[name] = row
    if "tenants" not in result:
        raise RecoveryError("catalog does not contain the tenant root")
    return result


def _qualified(row):
    schema = row.get("schema", "public")
    return ("public" if schema == "public" else identifier(schema)) + "." + identifier(row["table"])


def scope_predicate(row, catalog, tenant_id):
    tenant = _tenant(tenant_id)
    identifier(row["table"])
    for column in row["columns"]:
        identifier(column)
    rows = _catalog(catalog) if catalog else {row["table"]: row}

    def scope(item, alias, seen):
        name = item["table"]
        if name in seen or item.get("schema", "public") != "public":
            return None
        if name == "tenants":
            return f'{alias}."id" = \'{tenant}\'::uuid'
        direct = f'{alias}."tenant_id" = \'{tenant}\'::uuid' if "tenant_id" in item["columns"] else None
        parents = []
        for index, link in enumerate(item.get("foreign_keys", [])):
            parent = rows.get(link["parent_table"])
            if parent is None or link.get("parent_schema", "public") != parent.get("schema", "public"):
                continue
            parent_alias = f"p{len(seen)}_{index}"
            condition = scope(parent, parent_alias, seen | {name})
            if condition is None:
                continue
            joins = " AND ".join(f'{parent_alias}.{identifier(target)} = {alias}.{identifier(source)}'
                                 for source, target in zip(link["columns"], link["parent_columns"]))
            parents.append(f"EXISTS (SELECT 1 FROM {_qualified(parent)} {parent_alias} WHERE {joins} AND ({condition}))")
        parent_condition = " OR ".join(parents)
        if direct and parent_condition:
            return f'({direct} OR ({alias}."tenant_id" IS NULL AND ({parent_condition})))'
        return direct or (f"({parent_condition})" if parent_condition else None)

    return scope(row, "r", set())


def build_plan(catalog, counts, *, tenant_id, media):
    rows = _catalog(catalog)
    if set(counts) != set(rows):
        raise RecoveryError("every catalog table must have a count receipt")
    blockers = {"live_copy_not_implemented", "destination_reconciliation_not_approved"}
    if counts["tenants"].get("tenant_rows") != 1:
        blockers.add("tenant_root_count_invalid")
    output = []
    dependencies = {}
    for name, row in rows.items():
        numbers = counts[name]
        if (type(numbers.get("total_rows")) is not int or numbers["total_rows"] < 0
                or numbers.get("tenant_rows") is not None and
                (type(numbers["tenant_rows"]) is not int or not 0 <= numbers["tenant_rows"] <= numbers["total_rows"])):
            raise RecoveryError("table count receipt is invalid")
        scope = scope_predicate(row, catalog, tenant_id)
        if name == "user_profiles":
            kind = "shared_identity"
            blockers.add(kind)
        elif name in {"audit_logs", "audit_chain_state"}:
            kind = "global_audit_chain"
            blockers.add(kind)
        elif name in IMMUTABLE or any("immutable" in trigger or "append_only" in trigger for trigger in row["triggers"]):
            kind = "immutable_evidence"
            blockers.add(kind)
        elif name == "alembic_version":
            kind = "schema_metadata"
        elif scope:
            kind = "tenant_root" if name == "tenants" else "tenant_column" if "tenant_id" in row["columns"] else "parent_owned"
        else:
            kind = "unresolved"
            blockers.add("unresolved_ownership")
        if row["triggers"]:
            blockers.add("unreviewed_triggers")
        if row.get("partition"):
            blockers.add("partition_count_overlap")
        dependencies[name] = {link["parent_table"] for link in row["foreign_keys"] if link["parent_table"] in rows}
        missing = [link for link in row["foreign_keys"] if link["parent_table"] not in rows]
        if missing:
            blockers.add("foreign_key_parent_outside_catalog")
        output.append({**row, **numbers, "classification": kind,
                       "scope_note": "candidate ownership counts only; shared identities and parent consistency need review"})
    order = []
    pending = dict(dependencies)
    while pending:
        ready = sorted(name for name, parents in pending.items() if not parents.intersection(pending))
        if not ready:
            break
        for name in ready:
            order.append(name)
            del pending[name]
    if pending:
        blockers.add("foreign_key_cycle")
    if media.get("missing") or media.get("external") or media.get("complete") is False:
        blockers.add("media_incomplete")
    return {"operation": "tenant_restore_prepare", "status": "prepared_only", "tenant_id": _tenant(tenant_id),
            "catalog_complete": True, "tenant_scope_complete": False, "full_tenant_restore_complete": False,
            "apply_allowed": False, "tables": output, "foreign_key_order": order,
            "cyclic_tables": sorted(pending), "media": media, "blockers": sorted(blockers),
            "limits": ["all table/catalog counts describe only the isolated restored database",
                       "no app-role isolation probe, live merge, queue replay, calls or provider actions",
                       "no shared identity rewind or interleaved audit/immutable-receipt import policy approved"]}


def _relative(value, source_recordings_root):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise RecoveryError("recording path is missing or unsafe")
    path = PurePosixPath(value)
    if path.is_absolute():
        root = PurePosixPath(source_recordings_root)
        if not root.is_absolute() or str(root) == "/":
            raise RecoveryError("original recording root must be an explicit absolute directory")
        try:
            path = path.relative_to(root)
        except ValueError as exc:
            raise RecoveryError("recording path is outside the declared original root") from exc
    if ".." in path.parts or ":" in str(path) or not path.parts:
        raise RecoveryError("recording path escapes the captured media root")
    return "recordings/" + str(path)


def _manifest(payload):
    payload = Path(payload).absolute()
    if any(path.is_symlink() for path in (payload, *payload.parents)) or not payload.is_dir():
        raise RecoveryError("verified payload must be a regular private directory")
    manifest_file = payload / "manifest.json"
    if manifest_file.is_symlink() or not manifest_file.is_file() or manifest_file.stat().st_size > 16 * 1024 * 1024:
        raise RecoveryError("verified payload manifest is missing or oversized")
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        files = manifest["files"]
        if manifest["format"] != 1 or "database.dump" not in files or len(files) > MAX_MEDIA:
            raise ValueError
        for name, info in files.items():
            relative = PurePosixPath(name)
            if (relative.is_absolute() or ".." in relative.parts or "\\" in name or ":" in name
                    or str(relative) != name or name != "database.dump" and not name.startswith("recordings/")):
                raise ValueError
            path = payload / name
            if (any(part.is_symlink() for part in (path, *path.parents)) or not path.is_file()
                    or path.stat().st_size != info["bytes"] or digest(path) != info["sha256"]):
                raise ValueError
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise RecoveryError("payload manifest or member integrity is invalid") from exc
    return manifest


def media_inventory(payload, rows, source_recordings_root="/opt/talky/backend/recordings"):
    if not isinstance(rows, list) or len(rows) > MAX_MEDIA:
        raise RecoveryError("recording row inventory exceeds the explicit limit")
    manifest = _manifest(payload)
    verified = missing = external = 0
    for row in rows:
        if row["storage"] != "local":
            external += 1
            continue
        if row.get("path") is None:
            missing += 1
            continue
        name = _relative(row["path"], source_recordings_root)
        if name in manifest["files"]:
            verified += 1
        else:
            missing += 1
    return {"rows": len(rows), "verified": verified, "missing": missing, "external": external,
            "complete": missing == 0 and external == 0,
            "limits": ["local files only; cloud and Drive artifacts require separate recovery"]}


def _read_sql(query):
    return ("BEGIN READ ONLY; SET LOCAL statement_timeout = '30s'; SET LOCAL lock_timeout = '5s'; "
            "SET LOCAL row_security = off; " + query + " ROLLBACK;")


def inspect_restored(pg, service, database, tenant_id, payload, *, source_recordings_root="/opt/talky/backend/recordings"):
    deadline = time.monotonic() + 300
    catalog = pg.json(service, _read_sql(CATALOG_SQL), database=database, readonly=True)
    rows = _catalog(catalog)
    counts = {}
    for name, row in rows.items():
        if time.monotonic() >= deadline:
            raise RecoveryError("catalog/count inventory exceeded its five-minute budget")
        predicate = scope_predicate(row, catalog, tenant_id)
        selected = f"(SELECT count(*) FROM {_qualified(row)} r WHERE {predicate})" if predicate else "NULL"
        query = (f"SELECT json_build_object('total_rows', (SELECT count(*) FROM {_qualified(row)} r), "
                 f"'tenant_rows', {selected});")
        counts[name] = pg.json(service, _read_sql(query), database=database, readonly=True)
    media_rows = []
    for name in ("recordings_s3", "recordings"):
        if time.monotonic() >= deadline:
            raise RecoveryError("catalog/count inventory exceeded its five-minute budget")
        if name not in rows:
            continue
        row = rows[name]
        predicate = scope_predicate(row, catalog, tenant_id)
        required = {"s3_bucket", "s3_key"} if name == "recordings_s3" else {"storage_path"}
        if not predicate or not required.issubset(row["columns"]):
            raise RecoveryError("recording table has unresolved ownership or storage schema")
        if name == "recordings_s3":
            fields = "CASE WHEN r.s3_bucket='local' THEN 'local' ELSE 'external' END AS storage, r.s3_key AS path"
        else:
            fields = ("CASE WHEN r.storage_path ~ '^[a-zA-Z][a-zA-Z0-9+.-]*://' THEN 'external' ELSE 'local' END AS storage, "
                      "r.storage_path AS path")
        query = ("SELECT COALESCE(json_agg(media), '[]'::json) FROM "
                 f"(SELECT '{name}' AS table, {fields} FROM {_qualified(row)} r WHERE {predicate} LIMIT {MAX_MEDIA + 1}) media;")
        found = pg.json(service, _read_sql(query), database=database, readonly=True)
        if not isinstance(found, list):
            raise RecoveryError("recording inventory response is invalid")
        media_rows.extend(found)
        if len(media_rows) > MAX_MEDIA:
            raise RecoveryError("recording inventory exceeds the explicit row limit")
    media = media_inventory(payload, media_rows, source_recordings_root)
    return build_plan(catalog, counts, tenant_id=tenant_id, media=media)


def prepare_restore(pg, service, *, payload, database, source_cluster_id, source_endpoint_fingerprint,
                    expected_heads, tenant_id, receipt_path, source_recordings_root="/opt/talky/backend/recordings"):
    tenant = _tenant(tenant_id)
    recovery.validate_restore_target(database, "")
    if (not re.fullmatch(r"[0-9]{1,20}", source_cluster_id)
            or not re.fullmatch(r"[0-9a-f]{64}", source_endpoint_fingerprint)
            or not expected_heads or any(not re.fullmatch(r"[A-Za-z0-9_]{1,128}", head) for head in expected_heads)):
        raise RecoveryError("approved source identity and schema heads are required")
    if service.values["dbname"] != "postgres":
        raise RecoveryError("isolated restore service must use the postgres maintenance database")
    receipt_path = Path(receipt_path)
    if receipt_path.exists() or receipt_path.is_symlink():
        raise RecoveryError("restore receipt must be new")
    _manifest(payload)  # no database contact before every payload member verifies
    started = time.monotonic()
    result = {"operation": "tenant_restore_prepare", "status": "failed", "tenant_id": tenant,
              "apply_allowed": False, "full_tenant_restore_complete": False,
              "isolated_database": database, "isolated_database_retained": False,
              "source_identity": "owner-pinned; cross-check against protected source inventory",
              "started_at": recovery.utcnow()}
    try:
        if recovery.cluster_identifier(pg, service) == source_cluster_id:
            raise RecoveryError("source cluster is prohibited as a restore destination")
        if recovery.endpoint_fingerprint(pg, service) == source_endpoint_fingerprint:
            raise RecoveryError("source endpoint is prohibited as a restore destination")
        exists = pg.json(service, f"SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname='{database}');")
        if exists is not False:
            raise RecoveryError("restore database already exists or absence cannot be proved")
        pg.sql(service, f"CREATE DATABASE {identifier(database)} TEMPLATE template0;", readonly=False)
        result["isolated_database_retained"] = True
        pg.run("pg_restore", ["--no-owner", "--no-privileges", "--single-transaction", "--exit-on-error",
                              "--no-password", "--dbname", service.connection(database),
                              str(Path(payload) / "database.dump")], service, readonly=False)
        heads = pg.json(service, "SELECT COALESCE(json_agg(version_num ORDER BY version_num), '[]'::json) FROM public.alembic_version;",
                        database=database)
        if not isinstance(heads, list) or set(heads) != set(expected_heads):
            raise RecoveryError("restored schema heads differ from the approved heads")
        result.update(inspect_restored(pg, service, database, tenant, payload,
                                      source_recordings_root=source_recordings_root))
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_json_new(receipt_path, result)
        return result
    except Exception as exc:
        result.update(status="failed", elapsed_seconds=round(time.monotonic() - started, 3),
                      failure="isolated preparation failed; inspect protected inputs and database privately")
        if not receipt_path.exists():
            write_json_new(receipt_path, result)
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError("isolated preparation failed; no live data was copied") from exc


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--service-file", type=Path, required=True)
    result.add_argument("--service", required=True)
    result.add_argument("--payload", type=Path, required=True)
    result.add_argument("--database", required=True)
    result.add_argument("--tenant-id", type=UUID, required=True)
    result.add_argument("--source-cluster-id", required=True)
    result.add_argument("--source-endpoint-fingerprint", required=True)
    result.add_argument("--expected-head", action="append", required=True)
    result.add_argument("--receipt", type=Path, required=True)
    result.add_argument("--source-recordings-root", default="/opt/talky/backend/recordings")
    result.add_argument("--bin-dir", type=Path)
    result.add_argument("--allow-local-plaintext", action="store_true", help="synthetic loopback rehearsal only")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        os.umask(0o077)
        service = recovery.Service(args.service_file, args.service, args.allow_local_plaintext)
        result = prepare_restore(recovery.Postgres(args.bin_dir, timeout=600), service, payload=args.payload,
                database=args.database, source_cluster_id=args.source_cluster_id,
                source_endpoint_fingerprint=args.source_endpoint_fingerprint,
                expected_heads=args.expected_head, tenant_id=args.tenant_id,
                receipt_path=args.receipt, source_recordings_root=args.source_recordings_root)
        print(json.dumps({key: result[key] for key in ("status", "apply_allowed", "full_tenant_restore_complete", "elapsed_seconds")},
                         sort_keys=True))
        return 0
    except (RecoveryError, OSError, ValueError, TypeError, KeyError, configparser.Error):
        print("TENANT RESTORE PREPARE FAILED: inspect the private receipt and isolated database; no live copy was performed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

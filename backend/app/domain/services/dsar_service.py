"""Audited, bounded DSAR exports and erase previews (QA F12-06).

This first slice never erases data. ``complete`` describes the declared
operational-data registry and its artifacts, not legal/financial records or a
complete GDPR erasure. ``full_dsar_complete`` remains false. Read-committed
exports are not a quiescence proof or an approval token for a later erase.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import BinaryIO
from uuid import UUID, uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from app.core.db_utils import acquire_with_tenant
from app.core.security.platform_admin_guard import require_platform_admin_mutation
from app.domain.services.audit_logger import AuditEvent
from app.domain.services.phone_number_normalizer import normalize_phone_for_capture


_TABLES = (
    "leads", "calls", "call_lead_details", "transcripts", "conversations",
    "recordings_s3", "recordings", "call_feedback", "conversation_reviews", "call_workflows",
    "crm_deliveries", "dnc_entries", "call_guard_decisions", "call_events",
    "call_legs", "stream_events", "dialer_jobs", "assistant_actions", "meetings", "reminders",
    "security_events", "campaign_alert_outbox", "campaign_alert_state", "tenant_alert_state",
)
_CALL_ONLY = {
    "transcripts", "conversations", "recordings_s3", "recordings", "call_feedback",
    "conversation_reviews", "crm_deliveries", "call_events", "call_legs", "call_workflows",
}
_ROW_BYTES = 65536
_ALL_ROW_BYTES = 16 * 1024 * 1024
_ARTIFACT_BYTES = 64 * 1024 * 1024
_ALL_ARTIFACT_BYTES = 256 * 1024 * 1024
_RETAINED_SCOPES = [
    {"scope": "audit_logs", "reason": "Hash-chain retention and person-redaction policy are unresolved."},
    {"scope": "admin_media_deletion_intents", "reason": "Immutable deletion/hold receipts are retained."},
    {"scope": "financial_and_inbound_receipts", "reason": "Immutable financial/inbound evidence needs a reviewed retention policy."},
    {"scope": "legal_acceptances", "reason": "0085 acceptance receipts are append-only and retained."},
    {"scope": "external_crm_and_drive", "reason": "Remote copies are not erased or retrieved through unreviewed provider calls."},
    {"scope": "backups", "reason": "30-day expiry and erasure replay after restore require owner verification."},
    {"scope": "alert_delivery_retention", "reason": "Delivery uncertainty and deduplication receipts need an owner-approved redaction policy."},
]


class DsarScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    phone: str | None = None
    email: str | None = None
    whole_tenant: bool = False

    @field_validator("phone")
    @classmethod
    def canonical_phone(cls, value):
        if value is None:
            return None
        # No country inference for a privacy operation.
        return normalize_phone_for_capture(value, region=None)

    @field_validator("email")
    @classmethod
    def canonical_email(cls, value):
        if value is None:
            return None
        value = value.strip().lower()
        if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("A valid email address is required")
        return value

    @model_validator(mode="after")
    def one_selector(self):
        if sum((self.phone is not None, self.email is not None, self.whole_tenant)) != 1:
            raise ValueError("Choose exactly one phone, email, or whole-tenant scope")
        return self


@dataclass(frozen=True)
class DsarExport:
    path: Path
    manifest: dict


class DsarArtifactReader:
    """Copy existing local/S3 media in bounded chunks; never fetch arbitrary URLs."""

    def __init__(self, *, s3_client=None):
        self._s3 = s3_client

    @staticmethod
    def _copy(source, target: BinaryIO, max_bytes: int) -> int:
        total = 0
        deadline = time.monotonic() + 45
        while True:
            if time.monotonic() >= deadline:
                raise ValueError("Artifact exceeds export time limit")
            chunk = source.read(min(65536, max_bytes - total + 1))
            if not chunk:
                return total
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Artifact exceeds export size limit")
            target.write(chunk)

    def _copy_sync(self, kind: str, row: dict, target: BinaryIO, max_bytes: int) -> int:
        if not 0 < max_bytes <= _ALL_ARTIFACT_BYTES:
            raise ValueError("Invalid export size limit")
        if kind == "recordings_s3":
            bucket, key = row.get("s3_bucket"), row.get("s3_key")
            local = bucket == "local"
        elif kind == "call_feedback":
            bucket, key = row.get("audio_bucket"), row.get("audio_key")
            provider = row.get("audio_storage_provider")
            if provider not in {"local", "s3"}:
                raise ValueError("Unsupported feedback storage")
            local = provider == "local"
        elif kind == "recordings":
            # Legacy Drive/URL entries are surfaced by the manifest, not fetched.
            bucket, key, local = "local", row.get("storage_path"), True
        else:
            raise ValueError("Unsupported artifact type")
        if not isinstance(key, str) or not key or "://" in key:
            raise ValueError("Unsupported artifact key")
        if local:
            if kind == "call_feedback":
                from app.domain.services.call_feedback_service import FeedbackAudioStorage

                # Local validation needs no credentials/client construction.
                storage = FeedbackAudioStorage.__new__(FeedbackAudioStorage)
                storage._local_dir = os.path.abspath(os.getenv("LOCAL_FEEDBACK_DIR", "./feedback_audio"))
                path = storage._validated_local_path(key)
            else:
                from app.api.v1.endpoints.recordings import _validated_recording_path

                path = _validated_recording_path(key)
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(path, flags), "rb") as source:
                if os.fstat(source.fileno()).st_size > max_bytes:
                    raise ValueError("Artifact exceeds export size limit")
                return self._copy(source, target, max_bytes)
        if not isinstance(bucket, str) or not bucket:
            raise ValueError("Missing artifact bucket")
        if self._s3 is None:
            from app.domain.services.recording_service import S3Client

            self._s3 = S3Client()
        if not self._s3.is_available():
            raise RuntimeError("Object storage is unavailable")
        response = self._s3._client.get_object(Bucket=bucket, Key=key)
        source = response["Body"]
        try:
            if response.get("ContentLength", 0) > max_bytes:
                raise ValueError("Artifact exceeds export size limit")
            return self._copy(source, target, max_bytes)
        finally:
            source.close()

    async def copy_into(self, kind, row, target, *, max_bytes):
        # Keep the private stream alive until the bounded storage thread exits,
        # including caller cancellation; otherwise it could write after close.
        task = asyncio.create_task(asyncio.to_thread(self._copy_sync, kind, row, target, max_bytes))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except Exception:
                pass
            raise


class DsarService:
    def __init__(self, pool, *, audit, artifacts=None, max_rows=250):
        if not 1 <= max_rows <= 10000:
            raise ValueError("Row limit must be between 1 and 10000")
        self.pool, self.audit = pool, audit
        self.artifacts = artifacts if artifacts is not None else DsarArtifactReader()
        self.max_rows = max_rows

    @staticmethod
    def _sql(table, *, identity=False):
        if table not in _TABLES:
            raise ValueError("Unregistered DSAR table")
        joins = ""
        tenant = "r.tenant_id = $1::uuid"
        if table == "leads":
            predicate = "r.id = ANY($5::uuid[]) OR r.phone_number = $3::text OR lower(btrim(r.email)) = $4::text"
        elif table == "calls":
            predicate = "r.lead_id = ANY($5::uuid[]) OR r.id = ANY($6::uuid[]) OR r.phone_number = $3::text"
        elif table == "call_lead_details" and identity:
            predicate = (
                "r.validation_status = 'confirmed' AND r.confirmed = TRUE AND "
                "((r.field_type = 'phone' AND r.normalized_value = $3::text) OR "
                "(r.field_type = 'email' AND lower(btrim(r.normalized_value)) = $4::text))"
            )
        elif table == "dnc_entries":
            predicate = """r.normalized_number = $3::text OR r.normalized_number IN (
                SELECT l.phone_number FROM public.leads l
                WHERE l.tenant_id = $1::uuid AND l.id = ANY($5::uuid[])
                UNION SELECT c.phone_number FROM public.calls c
                WHERE c.tenant_id = $1::uuid AND c.id = ANY($6::uuid[]))"""
        elif table == "call_guard_decisions":
            predicate = """r.call_id = ANY($6::uuid[]) OR r.phone_number = $3::text
                OR r.phone_number IN (SELECT l.phone_number FROM public.leads l
                WHERE l.tenant_id = $1::uuid AND l.id = ANY($5::uuid[])
                UNION SELECT c.phone_number FROM public.calls c
                WHERE c.tenant_id = $1::uuid AND c.id = ANY($6::uuid[]))"""
        elif table == "stream_events":
            predicate = "r.related_call_id = ANY($6::uuid[])"
        elif table == "reminders":
            predicate = """r.lead_id = ANY($5::uuid[]) OR r.meeting_id IN (
                SELECT m.id FROM public.meetings m WHERE m.tenant_id = $1::uuid
                AND (m.call_id = ANY($6::uuid[]) OR m.lead_id = ANY($5::uuid[])))
                OR r.action_id IN (SELECT a.id FROM public.assistant_actions a
                WHERE a.tenant_id = $1::uuid AND
                (a.call_id = ANY($6::uuid[]) OR a.lead_id = ANY($5::uuid[])))"""
        elif table in {"campaign_alert_state", "tenant_alert_state"}:
            # Aggregates include other contacts' activity. They belong in a
            # whole-tenant export, never in an individual contact's export.
            predicate = "FALSE"
        elif table in {"security_events", "campaign_alert_outbox"}:
            evidence, source = "r.evidence", "r.detection_source"
            if table == "campaign_alert_outbox":
                joins = "JOIN public.security_events e ON e.event_id=r.event_id AND e.tenant_id=$1::uuid"
                evidence, source = "e.evidence", "e.detection_source"
            # Selector IDs can originate in legacy captured fields. Verify the
            # actual parent owner as well; a corrupt capture must not turn a
            # foreign call ID into permission to export an alert about it.
            predicate = f"""{source}='campaign_alerts' AND (
                EXISTS (SELECT 1 FROM public.calls alert_call
                    WHERE alert_call.tenant_id=$1::uuid AND alert_call.id=ANY($6::uuid[])
                      AND alert_call.id::text={evidence}->>'call_id')
                OR EXISTS (SELECT 1 FROM public.leads alert_lead
                    WHERE alert_lead.tenant_id=$1::uuid AND alert_lead.id=ANY($5::uuid[])
                      AND alert_lead.id::text={evidence}->>'lead_id'))"""
        elif table in _CALL_ONLY:
            predicate = "r.call_id = ANY($6::uuid[])"
        else:
            predicate = "r.call_id = ANY($6::uuid[]) OR r.lead_id = ANY($5::uuid[])"
        if table in {"call_events", "call_legs"}:
            joins = "JOIN public.calls c ON c.id = r.call_id"
            tenant = "c.tenant_id = $1::uuid"
        elif table in {"recordings", "transcripts"}:
            # These legacy tables allow NULL tenant_id. The call is the owner;
            # an explicitly conflicting tenant value must still be excluded.
            joins = "JOIN public.calls c ON c.id = r.call_id"
            tenant = "c.tenant_id = $1::uuid AND (r.tenant_id = $1::uuid OR r.tenant_id IS NULL)"
        # The singleton scope CTE types all seven bound parameters, even where a
        # particular registry predicate only uses a subset. No identifier comes
        # from the request. Large JSON rows become visible omissions at the DB.
        return f"""WITH subject AS (SELECT $1::uuid tenant_id, $2::boolean whole_tenant,
                $3::text phone, $4::text email, $5::uuid[] lead_ids, $6::uuid[] call_ids)
            SELECT CASE WHEN octet_length(to_jsonb(r)::text) > {_ROW_BYTES}
                     THEN jsonb_build_object('_dsar_row_limit', TRUE, 'id', to_jsonb(r)->'id')
                     ELSE to_jsonb(r) END AS data, count(*) OVER() AS total_count
            FROM public.{table} AS r {joins} CROSS JOIN subject
            WHERE {tenant} AND ($2::boolean OR ({predicate}))
            LIMIT $7::integer"""

    async def _collect(self, conn, scope):
        manifest = {
            "version": 1, "request_id": str(uuid4()), "tenant_id": str(scope.tenant_id),
            "subject_kind": "tenant" if scope.whole_tenant else "phone" if scope.phone else "email",
            "mode": "dry_run", "destructive_apply_supported": False,
            "full_dsar_complete": False, "complete": True,
            "coverage": "Declared operational call/contact/alert tables and their supported media files only",
            "consistency": "Read committed; live writers are not quiesced; this is not an erasure approval",
            "counts": {}, "missing_tables": [], "issues": [], "legal_hold": None,
            "retained_or_unimplemented_scopes": _RETAINED_SCOPES,
            "limits": {"rows_per_table": self.max_rows, "row_bytes": _ROW_BYTES,
                       "all_row_bytes": _ALL_ROW_BYTES, "artifact_bytes": _ARTIFACT_BYTES,
                       "all_artifact_bytes": _ALL_ARTIFACT_BYTES, "artifact_work_seconds": 60},
        }
        if scope.whole_tenant:
            manifest["issues"].append({"code": "tenant_scope_not_complete"})
        present = {}
        for table in (*_TABLES, "suspension_events"):
            present[table] = bool(await conn.fetchval("SELECT to_regclass($1)", f"public.{table}"))
            if not present[table]:
                manifest["missing_tables"].append(table)
        if present["suspension_events"]:
            from app.api.v1.endpoints.admin.media import _has_active_compliance_hold

            manifest["legal_hold"] = await _has_active_compliance_hold(conn, scope.tenant_id)
        else:
            manifest["issues"].append({"code": "hold_status_unavailable"})

        leads, calls = set(), set()
        used_bytes = 0

        async def read(table, *, identity=False):
            nonlocal used_bytes
            if not present[table]:
                manifest["counts"].setdefault(table, None)
                return []
            # Required parent relations must also exist. Missing a dependency is
            # an incomplete export, not permission to use an unscoped fallback.
            parents = {"call_events": ("calls",), "call_legs": ("calls",),
                       "recordings": ("calls",), "transcripts": ("calls",),
                       "dnc_entries": ("leads", "calls"),
                       "call_guard_decisions": ("leads", "calls"),
                       "security_events": ("calls", "leads"),
                       "campaign_alert_outbox": ("security_events", "calls", "leads"),
                       "reminders": ("meetings", "assistant_actions")}.get(table, ())
            if any(not present[parent] for parent in parents):
                manifest["counts"][table] = None
                manifest["issues"].append({"code": "missing_dependency", "table": table})
                return []
            fetched = await conn.fetch(
                self._sql(table, identity=identity), scope.tenant_id, scope.whole_tenant,
                scope.phone, scope.email, sorted(leads), sorted(calls), self.max_rows + 1,
            )
            count = int(fetched[0]["total_count"]) if fetched else 0
            if not identity:
                manifest["counts"][table] = count
            if count > self.max_rows:
                manifest["issues"].append({"code": "row_limit", "table": table, "count": count})
            result = []
            for record in fetched[:self.max_rows]:
                row = json.loads(record["data"]) if isinstance(record["data"], str) else dict(record["data"])
                size = len(json.dumps(row, ensure_ascii=False, default=str).encode())
                if row.get("_dsar_row_limit") or size > _ROW_BYTES or used_bytes + size > _ALL_ROW_BYTES:
                    manifest["issues"].append({"code": "row_size_limit", "table": table})
                    continue
                result.append(row)
                used_bytes += size
            return result

        first_leads = await read("leads")
        leads.update(str(row["id"]) for row in first_leads)
        captures = await read("call_lead_details", identity=True)
        calls.update(str(row["call_id"]) for row in captures if row.get("call_id"))
        call_rows = await read("calls")
        calls.update(str(row["id"]) for row in call_rows)
        leads.update(str(row["lead_id"]) for row in call_rows if row.get("lead_id"))
        lead_rows = await read("leads")
        # A confirmed captured address may first resolve a call, then its lead.
        # Include the other calls belonging to that same tenant-scoped lead.
        call_rows = await read("calls")
        calls.update(str(row["id"]) for row in call_rows)
        rows = {"leads": lead_rows, "calls": call_rows}
        for table in _TABLES:
            if table not in rows:
                rows[table] = await read(table)
        for row in rows["transcripts"]:
            if row.get("drive_file_id"):
                manifest["issues"].append({"code": "unsupported_storage", "table": "transcripts",
                                            "resource_id": str(row.get("id", ""))})
        manifest["complete"] = not manifest["issues"] and not manifest["missing_tables"]
        return rows, manifest

    async def _audit(self, conn, scope, actor_id, manifest, action):
        await self.audit.log_in_transaction(
            conn, event_type=AuditEvent.RECORD_EXPORTED if action == "dsar.export" else AuditEvent.RECORD_VIEWED,
            action=action, actor_id=actor_id, actor_type="user", actor_role="platform_admin",
            tenant_id=scope.tenant_id, resource_type="dsar", resource_id=manifest["request_id"],
            metadata={"subject_kind": manifest["subject_kind"], "counts": manifest["counts"],
                      "complete": manifest["complete"], "full_dsar_complete": False,
                      "manifest_sha256": hashlib.sha256(
                          json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
                      ).hexdigest(),
                      "issue_codes": sorted({issue["code"] for issue in manifest["issues"]})},
        )

    async def _snapshot(self, scope, actor_id, action):
        scope = DsarScope.model_validate(scope)
        actor_id = UUID(str(actor_id))
        # Bound database work including the shared audit lock. Storage work is
        # outside this transaction and never blocks the global chain lock.
        async with asyncio.timeout(30):
            async with acquire_with_tenant(self.pool, str(scope.tenant_id), user_id=str(actor_id)) as conn:
                await self.audit.lock_transaction(conn)
                await require_platform_admin_mutation(conn, actor_id)
                rows, manifest = await self._collect(conn, scope)
                await self._audit(conn, scope, actor_id, manifest, action)
        return rows, manifest

    async def preview(self, scope, *, actor_id):
        _, manifest = await self._snapshot(scope, actor_id, "dsar.preview")
        return manifest

    async def export(self, scope, *, actor_id, output):
        scope = DsarScope.model_validate(scope)
        path = Path(output)
        # Never overwrite an operator's existing file or follow a destination
        # symlink. Creating with O_EXCL also handles competing exports safely.
        if path.exists() or path.is_symlink():
            raise FileExistsError("Export destination already exists")
        rows, manifest = await self._snapshot(scope, actor_id, "dsar.export_requested")
        manifest["mode"] = "export"
        created = False
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            created = True
            with os.fdopen(fd, "wb") as output_file, ZipFile(output_file, "w", compression=ZIP_DEFLATED) as archive:
                manifest["row_files"] = []
                for table, values in rows.items():
                    name = f"rows/{table}.jsonl"
                    digest = hashlib.sha256()
                    with archive.open(name, "w") as entry:
                        for row in values:
                            encoded = (json.dumps(row, ensure_ascii=False, default=str) + "\n").encode()
                            digest.update(encoded)
                            entry.write(encoded)
                            # Rows are capped at 64 KiB. Yield between bounded
                            # writes/compression so an export cannot monopolize
                            # the API loop; cancellation unwinds this same writer.
                            await asyncio.sleep(0)
                    manifest["row_files"].append({"table": table, "path": name, "rows": len(values),
                                                  "sha256": digest.hexdigest()})
                artifacts = []
                used_bytes = 0
                artifact_deadline = time.monotonic() + 60
                for kind in ("recordings_s3", "recordings", "call_feedback"):
                    for row in rows[kind]:
                        resource_id = str(row.get("id", ""))
                        if kind == "recordings" and (row.get("drive_file_id") or not row.get("storage_path")):
                            manifest["issues"].append({"code": "unsupported_storage", "table": kind,
                                                       "resource_id": resource_id})
                            continue
                        remaining = min(_ARTIFACT_BYTES, _ALL_ARTIFACT_BYTES - used_bytes)
                        if remaining <= 0 or time.monotonic() >= artifact_deadline:
                            manifest["issues"].append({"code": "artifact_size_limit", "table": kind,
                                                       "resource_id": resource_id})
                            continue
                        # A failed read never leaves a partial audio member.
                        with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as media:
                            try:
                                size = await self.artifacts.copy_into(kind, row, media, max_bytes=remaining)
                                if size != media.tell() or size > remaining:
                                    raise ValueError("Artifact exceeds export size limit")
                            except Exception as exc:
                                # Error text may contain a key, URL or credentials.
                                manifest["issues"].append({"code": "artifact_unavailable", "table": kind,
                                                           "resource_id": resource_id, "error_type": type(exc).__name__})
                                continue
                            media.seek(0)
                            # IDs/keys from storage never become ZIP paths.
                            name = f"audio/{kind}/{len(artifacts) + 1}.bin"
                            digest = hashlib.sha256()
                            with archive.open(name, "w") as entry:
                                while chunk := media.read(65536):
                                    digest.update(chunk)
                                    entry.write(chunk)
                                    await asyncio.sleep(0)
                            artifacts.append({"table": kind, "resource_id": resource_id, "path": name,
                                              "bytes": size, "sha256": digest.hexdigest()})
                            used_bytes += size
                manifest["artifacts"] = artifacts
                manifest["complete"] = not manifest["issues"] and not manifest["missing_tables"]
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True))
                await asyncio.sleep(0)
            # Fresh authority and durable audit are required before releasing
            # the archive; failure removes the private output, never a success.
            async with acquire_with_tenant(self.pool, str(scope.tenant_id), user_id=str(actor_id)) as conn:
                await self.audit.lock_transaction(conn)
                await require_platform_admin_mutation(conn, UUID(str(actor_id)))
                await self._audit(conn, scope, actor_id, manifest, "dsar.export")
            return DsarExport(path, manifest)
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise

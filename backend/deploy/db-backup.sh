#!/usr/bin/env bash
# db-backup.sh — nightly logical backup of the production Postgres (Docker).
#
# Run by talky-db-backup.service (oneshot, root), triggered daily by
# talky-db-backup.timer. Mirrors docs/RUNBOOK.md "Backups" with the two
# lessons production taught us:
#
#   1. The app role is NOT superuser and every tenant table is FORCE RLS
#      (0038+). A pg_dump without `app.bypass_rls` silently dumps SCHEMA ONLY —
#      the Aug-28/29/30 dumps in /home/admins/backups are exactly that (three
#      identical 22 MB files, 0 "TABLE DATA" entries). The bypass GUC is set on
#      the dump connection via PGOPTIONS and the result is verified: TABLE DATA
#      entries must be present; a dump under half the previous good one is
#      still published but exits non-zero once so the alert fires (refusing it
#      would pin the baseline and fail every later night). pg_dump ALSO needs --enable-row-security: by default
#      it sets row_security=off, which Postgres refuses on a FORCE RLS table
#      ("query would be affected by row-level security policy" — the first
#      prod run, 2026-09-10). With it the policies run, and the bypass GUC
#      admits tenant rows. Saved acknowledgements additionally require an active,
#      verified platform administrator. The helper validates the explicit UUID
#      in the exact exported read-only snapshot consumed by pg_dump, preventing
#      concurrent deactivation from silently filtering those receipts.
#   2. Nothing scheduled these. Every backup so far was a hand-run
#      pre-migration dump (Sep 6, Sep 8). A 2.3 GB database with no nightly
#      backup is not production.
#
# Output: ${TALKY_BACKUP_DIR}/talky-<UTC stamp>.dump (pg_dump custom format,
# compressed) + .sha256. Retention: TALKY_BACKUP_KEEP_DAYS (default 30).
# Restore (never into the live DB): see docs/RUNBOOK.md "Restore test".
set -Eeuo pipefail
umask 077

CONTAINER="${TALKY_PG_CONTAINER:-talky-postgres-1}"
DB_USER="${TALKY_PG_USER:-talkyai}"
DB_NAME="${TALKY_PG_DB:-talkyai}"
backup_dir="${TALKY_BACKUP_DIR:-/var/backups/talky}"
keep_days="${TALKY_BACKUP_KEEP_DAYS:-30}"
min_table_data="${TALKY_BACKUP_MIN_TABLE_DATA:-50}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Only timestamps and a fixed outcome are public to the local metrics reader.
# Dump files remain private. Both processes must use the same status directory.
status_dir="${TALKY_BACKUP_STATUS_DIR:-/var/lib/talky/monitoring}"
# Validate before chmod, trap installation or any public receipt publication.
# realpath -m follows existing symlinks without creating missing directories.
resolved_status_dir="$(realpath -m -- "$status_dir")"
resolved_backup_dir="$(realpath -m -- "$backup_dir")"
if [[ "$resolved_status_dir" == "$resolved_backup_dir" || "$resolved_status_dir" == "$resolved_backup_dir/"* ]]; then
    echo "DB-BACKUP CRITICAL: monitoring status must be outside the private archive directory" >&2
    exit 1
fi
started_at="$(date -u +%s)"
partial=""
partial_owned=0
write_backup_status() {
    python3 "${script_dir}/../../scripts/database/backup_status.py" \
        --directory "$status_dir" --started-at "$started_at" --result "$1"
}
cleanup_partial() {
    rc=$?
    trap - EXIT INT TERM HUP
    [[ "$partial_owned" -eq 0 ]] || rm -f -- "$partial"
    outcome="failed"
    if [[ "$rc" -eq 0 ]]; then
        outcome="succeeded"
    fi
    if ! write_backup_status "$outcome"; then
        echo "DB-BACKUP CRITICAL: could not publish backup monitoring receipt" >&2
        rc=1
    fi
    if [[ "$rc" -ne 0 ]]; then
        echo "DB-BACKUP CRITICAL: backup failed (rc=$rc)" >&2
    fi
    exit "$rc"
}
# Register before checking the principal so configuration failures are visible.
trap cleanup_partial EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
install -d -m 0755 "${status_dir}"
write_backup_status running
if [[ -z "${TALKY_BACKUP_PRINCIPAL_ID:-}" ]]; then
    echo "DB-BACKUP CRITICAL: TALKY_BACKUP_PRINCIPAL_ID is required" >&2
    exit 1
fi

install -d -m 0700 "${backup_dir}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump="${backup_dir}/talky-${stamp}.dump"
partial="${dump}.partial"
checksum_file="${dump}.sha256"
for output_path in "$dump" "$partial" "$checksum_file"; do
    if [[ -e "$output_path" ]]; then
        echo "DB-BACKUP CRITICAL: refusing to overwrite existing artifact: $output_path" >&2
        exit 1
    fi
done
# --- validated snapshot dump; no publication before keeper completion --------
partial_owned=1
python3 "${script_dir}/backup_snapshot.py" \
    --container "$CONTAINER" --user "$DB_USER" --database "$DB_NAME" \
    > "${partial}"
test -s "${partial}"

# --- verify: it is a restorable archive WITH data ----------------------------
listing="$(docker exec -i "$CONTAINER" pg_restore --list < "${partial}")"
table_data="$(printf '%s\n' "$listing" | grep -c 'TABLE DATA' || true)"
if [[ "$table_data" -lt "$min_table_data" ]]; then
    echo "DB-BACKUP CRITICAL: only ${table_data} TABLE DATA entries (< ${min_table_data}) — schema-only dump? (RLS bypass missing)" >&2
    exit 1
fi
size="$(stat -c %s "${partial}")"
# A shrink below half of the previous dump is suspicious but NOT a reason to
# throw the fresh dump away: the TABLE DATA check above already rejects the
# RLS-bypass failure, and a legitimate purge (audit-flood cleanup, big-table
# truncation) would otherwise fail every later night against the same old
# baseline and leave no backup at all. So publish it (the baseline moves), keep
# retention reachable, and exit non-zero ONCE after publishing so the alert fires.
shrunk_from=""
prev="$(ls -1t "${backup_dir}"/talky-*.dump 2>/dev/null | head -n1 || true)"
if [[ -n "$prev" && -s "$prev" ]]; then
    prev_size="$(stat -c %s "$prev")"
    if (( size * 2 < prev_size )); then
        shrunk_from="$prev_size"
    fi
fi

# --- publish atomically -------------------------------------------------------
digest="$(sha256sum "${partial}" | cut -d ' ' -f1)"
[[ "$digest" =~ ^[0-9a-f]{64}$ ]]
mv -- "${partial}" "${dump}"
printf '%s  %s\n' "$digest" "$dump" > "${checksum_file}"
sha256sum --check --quiet "${checksum_file}"
# --- retention ----------------------------------------------------------------
find "${backup_dir}" -maxdepth 1 -type f \( -name 'talky-*.dump' -o -name 'talky-*.dump.sha256' \) \
    -mtime "+${keep_days}" -delete

echo "db-backup: ok ${dump} size=${size} table_data=${table_data} sha256=${digest:0:12}… kept_days=${keep_days}"

if [[ -n "$shrunk_from" ]]; then
    echo "DB-BACKUP CRITICAL: dump is ${size} bytes, previous good was ${shrunk_from} — published anyway so the baseline moves; confirm the shrink was a legitimate purge (${dump})" >&2
    exit 1
fi

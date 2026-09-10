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
#      entries must be present and the dump must not shrink below half of the
#      previous good one. pg_dump ALSO needs --enable-row-security: by default
#      it sets row_security=off, which Postgres refuses on a FORCE RLS table
#      ("query would be affected by row-level security policy" — the first
#      prod run, 2026-09-10). With it the policies run, and the bypass GUC
#      makes them return every row.
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
cleanup_partial() {
    rc=$?
    rm -f -- "$partial"
    if [[ "$rc" -ne 0 ]]; then
        echo "DB-BACKUP CRITICAL: backup failed (rc=$rc)" >&2
    fi
    exit "$rc"
}
trap cleanup_partial EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- dump (bypass RLS on the dump connection; see header) --------------------
docker exec -e PGOPTIONS='-c app.bypass_rls=true' "$CONTAINER" \
    pg_dump -U "$DB_USER" -d "$DB_NAME" --no-owner --format=custom --compress=6 --enable-row-security \
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
prev="$(ls -1t "${backup_dir}"/talky-*.dump 2>/dev/null | head -n1 || true)"
if [[ -n "$prev" && -s "$prev" ]]; then
    prev_size="$(stat -c %s "$prev")"
    if (( size * 2 < prev_size )); then
        echo "DB-BACKUP CRITICAL: dump is ${size} bytes, previous good was ${prev_size} — refusing to publish a shrunken backup" >&2
        exit 1
    fi
fi

# --- publish atomically -------------------------------------------------------
digest="$(sha256sum "${partial}" | cut -d ' ' -f1)"
[[ "$digest" =~ ^[0-9a-f]{64}$ ]]
mv -- "${partial}" "${dump}"
printf '%s  %s\n' "$digest" "$dump" > "${checksum_file}"
sha256sum --check --quiet "${checksum_file}"
trap - EXIT INT TERM

# --- retention ----------------------------------------------------------------
find "${backup_dir}" -maxdepth 1 -type f \( -name 'talky-*.dump' -o -name 'talky-*.dump.sha256' \) \
    -mtime "+${keep_days}" -delete

echo "db-backup: ok ${dump} size=${size} table_data=${table_data} sha256=${digest:0:12}… kept_days=${keep_days}"

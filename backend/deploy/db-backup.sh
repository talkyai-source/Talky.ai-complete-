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
#      entries must be present. That count is only the number of tables with a
#      data entry (pg_dump writes one even when row security filtered every row
#      out), so it cannot prove the bypass worked; the row count of public.calls
#      read from the archive is the row check. A dump under half the previous good one is
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
trap 'exit 143' TERM HUP

# --- validated snapshot dump; no publication before keeper completion --------
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
# --- verify: rows survived (TABLE DATA above only counts tables) ---------------
# Count the rows of public.calls by reading the archive itself (no restore). A
# probe that PROVES the table short refuses the dump; a probe that cannot be
# read (pg_restore failed, no COPY header) warns and publishes: an unverified
# check must never refuse a good backup.
min_call_rows="${TALKY_BACKUP_MIN_CALL_ROWS:-1}"
[[ "$min_call_rows" =~ ^[0-9]+$ ]] || min_call_rows=1
min_call_rows=$((10#$min_call_rows))  # "08" is eight, not a bad octal number
# A stuck probe must not hold the nightly unit for ever: after the limit it
# counts as "could not be read" (warn and publish).
probe_limit_s="${TALKY_BACKUP_ROW_CHECK_TIMEOUT_S:-1800}"
[[ "$probe_limit_s" =~ ^[1-9][0-9]*$ ]] || probe_limit_s=1800
probe_limit=()
if command -v timeout >/dev/null 2>&1; then probe_limit=(timeout -k 5 "$probe_limit_s"); fi
probe_rc=0
probe_out="$(${probe_limit[@]+"${probe_limit[@]}"} docker exec -i "$CONTAINER" pg_restore --data-only --schema=public --table=calls --file=- < "${partial}" \
    | awk '/^COPY public\.calls /{seen=1; inside=1; next} inside && $0 == "\\." {inside=0; next} inside {n++} END {print (seen ? "seen" : "none"), n+0}')" || probe_rc=$?
call_rows="unknown"
if [[ "$probe_rc" -eq 0 && "$probe_out" =~ ^seen\ ([0-9]+)$ ]]; then
    call_rows="${BASH_REMATCH[1]}"
    if [[ "$call_rows" -lt "$min_call_rows" ]]; then
        echo "DB-BACKUP CRITICAL: the dump holds ${call_rows} rows in public.calls (fewer than ${min_call_rows}): rows were filtered out or the table was not dumped; not published" >&2
        exit 1
    fi
else
    echo "DB-BACKUP WARNING: could not count the rows of public.calls in the dump (the row check could not be read); published without it" >&2
fi
size="$(stat -c %s "${partial}")"
# A shrink below half of the previous dump is suspicious but NOT a reason to
# throw the fresh dump away: the row check above already rejects a dump whose
# calls rows were filtered out, and a legitimate purge (audit-flood cleanup, big-table
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
trap - EXIT INT TERM HUP

recordings_failed=0
offsite_failed=0
offhost_failed=0
# Preserve snapshot/principal verification and the flat artifacts. A configured
# encrypted off-host DB+recordings copy must verify before local retention.
if [[ -n "${TALKY_OFFHOST_ROOT:-}" ]]; then
    # A refused/unavailable mount must not skip the shrink check: remember it
    # and exit non-zero at the end (the local dump is safe).
    python3 "${script_dir}/../../scripts/database/offhost.py" publish --dump "${dump}" || offhost_failed=1
fi
# An encrypted copy on the owner's FTP server (TLS required), proved to decrypt
# to this dump and size-checked on the server before local retention runs.
# Settings live in /etc/talky/db-backup.env (root only); see offsite_ftp.py.
if [[ -n "${TALKY_FTP_BACKUP_URL:-}" ]]; then
    # A refused FTP login must not skip the shrink check or the "ok" line:
    # remember it and exit non-zero at the end.
    if ! python3 "${script_dir}/../../scripts/database/offsite_ftp.py" publish --dump "${dump}"; then
        offsite_failed=1
    # F8-02, opt-in: encrypted recordings to the same FTP account. Runs AFTER the
    # database copy; a failure is remembered and fails the unit at the end, it
    # never prevents or undoes the database upload. Skipped when the database
    # publish failed: it needs the same login.
    elif [[ "${TALKY_OFFSITE_FTP_RECORDINGS:-}" == "1" ]]; then
        python3 "${script_dir}/../../scripts/database/offsite_ftp.py" sync-recordings || recordings_failed=1
    fi
fi

# --- retention ----------------------------------------------------------------
# Local retention waits for a verified copy elsewhere: while the off-site or
# off-host copy fails, the copies on this server are the only recovery points,
# so none of them is aged out.
if [[ "$offsite_failed" == "0" && "$offhost_failed" == "0" ]]; then
    find "${backup_dir}" -maxdepth 1 -type f \( -name 'talky-*.dump' -o -name 'talky-*.dump.sha256' \) \
        -mtime "+${keep_days}" -delete
else
    echo "db-backup: local retention skipped, every copy on this server is kept until a copy elsewhere is verified"
fi

echo "db-backup: ok ${dump} size=${size} table_data=${table_data} call_rows=${call_rows} sha256=${digest:0:12}… kept_days=${keep_days}"

if [[ -n "$shrunk_from" ]]; then
    echo "DB-BACKUP CRITICAL: dump is ${size} bytes, previous good was ${shrunk_from} — published anyway so the baseline moves; confirm the shrink was a legitimate purge (${dump})" >&2
    exit 1
fi

# Severity order (the first match decides the exit): shrunk dump > failed
# off-site copy (FTP, then off-host mount) > failed recordings sync.
if [[ "$offsite_failed" == "1" ]]; then
    echo "DB-BACKUP CRITICAL: the database copy on this server is safe, but the off-site copy failed (offsite_ftp.py publish)" >&2
    exit 1
fi

if [[ "$offhost_failed" == "1" ]]; then
    echo "DB-BACKUP CRITICAL: the database copy on this server is safe, but the off-host copy failed (offhost.py publish)" >&2
    exit 1
fi

if [[ "$recordings_failed" == "1" ]]; then
    echo "DB-BACKUP CRITICAL: the database copy is safe, but the off-site FTP recordings sync failed (offsite_ftp.py sync-recordings)" >&2
    exit 1
fi

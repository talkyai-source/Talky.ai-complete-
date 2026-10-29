#!/bin/bash
# Keep Asterisk in step with the database, every minute
# (talky-asterisk-reconcile.timer).
#
# Lines, inbound numbers and inbound campaigns change on the website, but the
# inbound call plan (which carrier account answers as which number) was only
# rebuilt when an operator ran reconcile_asterisk_release.sh by hand, and
# production had no timer for it. On 2026-10-15 the live plan still routed three
# accounts whose lines had been switched off weeks earlier, and lines added on
# the website never got a route.
#
# This runs the reconciler's read-only check and applies ONLY when the database
# and Asterisk differ: the apply step always reloads Asterisk, so it must not run
# when nothing changed. The reconciler itself refuses to apply while calls are in
# progress or while another run holds its lock; those refusals are retried on the
# next tick instead of being reported as failures. Run as root.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="${TALKY_BACKEND_DIR:-$(cd -- "${SCRIPT_DIR}/.." && pwd)}"
ENV_FILE="${TALKY_BACKEND_ENV_FILE:-${BACKEND_DIR}/.env}"
PYTHON_BIN="${TALKY_BACKEND_PYTHON:-${BACKEND_DIR}/venv/bin/python}"
RECONCILER="${TALKY_RECONCILER:-${BACKEND_DIR}/scripts/reconcile_pjsip_configs.py}"
APPLY_SCRIPT="${TALKY_RECONCILE_APPLY:-${BACKEND_DIR}/scripts/reconcile_asterisk_release.sh}"
ENV_RUNNER="${SCRIPT_DIR}/../scripts/run_with_backend_env.py"

if [ ! -r "${ENV_FILE}" ]; then
    echo "ERROR: backend environment file is not readable." >&2
    exit 1
fi
# Values are dotenv data, including TLS URLs containing '&'; never shell code.
# Load only into the child, preserving this wrapper's fixed command structure.

# One line about a reconciler result: what differs, or why it was refused. The
# reconciler's JSON holds file names, counts and fixed error strings only.
summary() {
    printf '%s' "$1" | "${PYTHON_BIN}" -c '
import json, sys
raw = sys.stdin.read()
try:
    data = json.loads(raw[raw.index("{"):])
except ValueError:
    print("unreadable reconciler output")
    raise SystemExit
if data.get("status") == "blocked":
    print("blocked: " + str(data.get("error")) + "".join("; " + str(c) for c in data.get("cause", [])))
else:
    print("changed=%s missing=%s unexpected=%s routes=%s" % (
        data.get("changed", []), data.get("missing", []), data.get("unexpected", []), data.get("route_count")))
' 2>/dev/null || echo "unreadable reconciler output"
}

# A refusal that the next tick resolves by itself.
retry_later() {
    case "$1" in
        *"another Asterisk reconciliation is active"*|*"active channels"*) return 0 ;;
        *) return 1 ;;
    esac
}

set +e
CHECK_OUTPUT="$(cd -- "${BACKEND_DIR}" && "${PYTHON_BIN}" "${ENV_RUNNER}" "${ENV_FILE}" "${PYTHON_BIN}" "${RECONCILER}" 2>&1)"
CHECK_STATUS=$?
set -e

case "${CHECK_STATUS}" in
    0)
        exit 0  # in sync: nothing to apply, nothing to log
        ;;
    1)
        echo "call plan differs from the database ($(summary "${CHECK_OUTPUT}")); applying"
        ;;
    *)
        REASON="$(summary "${CHECK_OUTPUT}")"
        if retry_later "${REASON}"; then
            echo "check deferred (${REASON}); retrying next minute"
            exit 0
        fi
        echo "check blocked (${REASON})" >&2
        exit "${CHECK_STATUS}"
        ;;
esac

set +e
APPLY_OUTPUT="$("${PYTHON_BIN}" "${ENV_RUNNER}" "${ENV_FILE}" bash "${APPLY_SCRIPT}" 2>&1)"
APPLY_STATUS=$?
set -e
if [ "${APPLY_STATUS}" -eq 0 ]; then
    echo "applied ($(summary "${APPLY_OUTPUT}"))"
    exit 0
fi
REASON="$(summary "${APPLY_OUTPUT}")"
if retry_later "${REASON}"; then
    echo "apply deferred (${REASON}); retrying next minute"
    exit 0
fi
echo "apply FAILED (${REASON})" >&2
exit "${APPLY_STATUS}"

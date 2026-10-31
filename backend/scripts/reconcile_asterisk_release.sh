#!/bin/bash
# Candidate-first Asterisk configuration reconciliation used by BOTH the
# provisioning script and the standard production deploy. Run as root only.
set -euo pipefail
umask 077

CHECK_ONLY=0
if [ "$#" -eq 1 ] && [ "$1" = "--check-only" ]; then
    CHECK_ONLY=1
elif [ "$#" -ne 0 ]; then
    echo "Usage: $0 [--check-only]" >&2
    exit 64
fi

if [ "${EUID}" -ne 0 ]; then
    echo "ERROR: Asterisk reconciliation must run as root." >&2
    exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${TALKY_BACKEND_ENV_FILE:-${BACKEND_DIR}/.env}"
PYTHON_BIN="${TALKY_BACKEND_PYTHON:-${BACKEND_DIR}/venv/bin/python}"
RECONCILER="${SCRIPT_DIR}/reconcile_pjsip_configs.py"

if [ ! -r "${ENV_FILE}" ]; then
    echo "ERROR: backend environment file is not readable." >&2
    exit 1
fi
if [ ! -x "${PYTHON_BIN}" ]; then
    echo "ERROR: backend Python runtime is not executable." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090 -- production env path is operator-configurable.
. "${ENV_FILE}"
set +a

# Check returns 1 when drift exists; that is the expected input to apply.
set +e
CHECK_OUTPUT="$(cd -- "${BACKEND_DIR}" && "${PYTHON_BIN}" "${RECONCILER}")"
CHECK_STATUS=$?
set -e
if [ "${CHECK_STATUS}" -gt 1 ]; then
    echo "ERROR: Asterisk candidate generation was blocked." >&2
    exit "${CHECK_STATUS}"
fi

if [ "${CHECK_ONLY}" -eq 1 ]; then
    # Drift (the reconciler's exit 1) is safe during this preflight. The
    # important proof is that the database and reviewed carrier inventory can
    # produce one unambiguous candidate. Do not derive a digest or mutate /etc.
    printf '%s\n' "${CHECK_OUTPUT}"
    exit 0
fi

CANDIDATE_DIGEST="$(
    printf '%s' "${CHECK_OUTPUT}" | "${PYTHON_BIN}" -c \
        'import json,re,sys; value=json.load(sys.stdin).get("digest", ""); assert re.fullmatch(r"[0-9a-f]{64}", value); print(value)'
)"

# The apply process re-reads the database and rebuilds the candidate while
# holding the writer lock. Any assignment/config change between the two runs
# changes the digest and blocks the mutation.
cd -- "${BACKEND_DIR}"
"${PYTHON_BIN}" "${RECONCILER}" \
    --apply \
    --expected-digest "${CANDIDATE_DIGEST}"

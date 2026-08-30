#!/bin/bash
# ============================================================================
# Talky.ai — production deploy (git-based)
# ============================================================================
# Production at 144.76.17.150 is a GIT CHECKOUT of this repo (origin/main)
# with a sparse-checkout that excludes the frontends (Talk-Leee/, Admin/) —
# those deploy to Vercel from git, not to the backend box.
#
# Deploy model (the ONLY supported path — do NOT rsync source onto prod):
#   1. Commit + push your change to origin/main  (CI runs on push).
#   2. Run this script. It freezes the requested commit SHA, proves that SHA is
#      reachable from origin/main, and checks out that exact commit on prod.
#
# Rollback: follow docs/DEPLOYMENT.md — durably disable/prove ingress first,
# then select a full known-good SHA and require fail-closed health probes.
#
# Why git, not rsync: rsync-from-laptop made prod's source untracked and
# drift-prone (prod silently diverged from git). A checkout gives a known
# commit, history, and rollback. See docs/reference/ for the migration note.
# ----------------------------------------------------------------------------
set -euo pipefail

PROD="${TALKY_PROD_HOST:-admins@144.76.17.150}"
KEY="${TALKY_PROD_KEY:-$HOME/.ssh/talky_admin}"
BRANCH="${TALKY_DEPLOY_BRANCH:-main}"
DEPLOY_REF="${TALKY_DEPLOY_SHA:-HEAD}"
DRAIN_MANIFEST="${TALKY_DEPLOY_DRAIN_MANIFEST:-}"
DRAIN_MANIFEST_SHA256="${TALKY_DEPLOY_DRAIN_MANIFEST_SHA256:-}"

if [ -z "$DRAIN_MANIFEST" ] || [ -z "$DRAIN_MANIFEST_SHA256" ]; then
    echo "!! Set TALKY_DEPLOY_DRAIN_MANIFEST and TALKY_DEPLOY_DRAIN_MANIFEST_SHA256." >&2
    exit 1
fi

if ! [[ "$BRANCH" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || \
   ! git check-ref-format --branch "$BRANCH" >/dev/null 2>&1; then
    echo "!! Invalid deploy branch: ${BRANCH}" >&2
    exit 1
fi
if [ -n "$(git status --porcelain)" ]; then
    echo "!! Local checkout is dirty; commit or preserve every release change first." >&2
    git status --short >&2
    exit 1
fi

echo "==> Freezing and verifying the deploy commit"
DEPLOY_SHA="$(git rev-parse --verify "${DEPLOY_REF}^{commit}")"
if ! printf '%s\n' "$DEPLOY_SHA" | grep -Eq '^[0-9a-f]{40,64}$'; then
    echo "!! Could not resolve a full immutable commit SHA from ${DEPLOY_REF}." >&2
    exit 1
fi

# Refresh the remote-tracking ref before proving that the frozen candidate was
# pushed. The deploy below still uses DEPLOY_SHA, never the branch tip, so a
# subsequent push cannot change what this invocation deploys.
git fetch --quiet origin "$BRANCH"
if ! git merge-base --is-ancestor "$DEPLOY_SHA" "origin/${BRANCH}" 2>/dev/null; then
    echo "!! Frozen commit ${DEPLOY_SHA} is not on origin/${BRANCH}."
    echo "   Push first:  git push origin ${BRANCH}"
    exit 1
fi
echo "    frozen commit: ${DEPLOY_SHA}"
echo "    verified on:   origin/${BRANCH}"

# Consume the candidate-bound approval only after the full candidate SHA has
# been frozen and proven reachable, and before SSH can mutate production.  The
# manifest records externally attested carrier/Asterisk/Redis/DB facts; this
# repository cannot independently observe those systems yet.
if [ -n "${TALKY_LOCAL_PYTHON:-}" ]; then
    LOCAL_PYTHON="$TALKY_LOCAL_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    LOCAL_PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    LOCAL_PYTHON="$(command -v python)"
else
    echo "!! Python 3 is required to verify the drain manifest." >&2
    exit 1
fi
GIT_COMMON_DIR="$(git rev-parse --path-format=absolute --git-common-dir)"
DRAIN_REPLAY_DIR="${TALKY_DEPLOY_DRAIN_REPLAY_DIR:-${GIT_COMMON_DIR}/talky-deploy-drain-used}"
echo "==> Verifying candidate-bound production drain manifest"
"$LOCAL_PYTHON" backend/scripts/verify_deploy_drain_manifest.py \
    --manifest "$DRAIN_MANIFEST" \
    --candidate-sha "$DEPLOY_SHA" \
    --expected-sha256 "$DRAIN_MANIFEST_SHA256" \
    --replay-dir "$DRAIN_REPLAY_DIR"

echo "==> Deploying exact commit ${DEPLOY_SHA} to ${PROD}"
echo "    (you will be prompted for the prod sudo password to restart services)"

# -t so the remote sudo can prompt interactively. The sudo password is NOT
# stored anywhere — it is typed at the prompt by the operator.
ssh -t -i "$KEY" "$PROD" "
    set -euo pipefail
    cd /opt/talky
    echo '--> refusing tracked or untracked source drift'
    if [ -n \"\$(git status --porcelain)\" ]; then
        echo '!! /opt/talky is dirty; preserve and reconcile drift before deploying.' >&2
        git status --short >&2
        exit 1
    fi
    echo '--> fetching candidate reachability proof'
    git fetch --prune origin ${BRANCH}
    git cat-file -e '${DEPLOY_SHA}^{commit}'
    git merge-base --is-ancestor '${DEPLOY_SHA}' FETCH_HEAD
    echo '--> checking out frozen commit (detached)'
    git checkout --detach '${DEPLOY_SHA}'
    deployed_sha=\"\$(git rev-parse HEAD)\"
    if [ \"\$deployed_sha\" != '${DEPLOY_SHA}' ]; then
        echo \"!! Commit verification failed: expected ${DEPLOY_SHA}, got \$deployed_sha\" >&2
        exit 1
    fi
    if [ -n \"\$(git status --porcelain)\" ]; then
        echo '!! Checkout is not clean after selecting frozen commit.' >&2
        git status --short >&2
        exit 1
    fi
    echo \"    prod now exactly at: \$deployed_sha\"
    echo '--> import smoke test'
    (cd backend && venv/bin/python -c 'import app.main') >/dev/null 2>&1 && echo '    import app.main OK'
    echo '--> building and testing the exact-SHA C++ gateway candidate'
    gateway_candidate=\"\$(mktemp /tmp/talky-voice-gateway-candidate.XXXXXX)\"
    cleanup_gateway_candidate() {
        if [ -n \"\${gateway_candidate:-}\" ]; then
            rm -f -- \"\$gateway_candidate\"
        fi
    }
    trap cleanup_gateway_candidate EXIT
    bash backend/scripts/build_voice_gateway_release.sh \"\$gateway_candidate\"
    echo '--> proving the running gateway has zero active sessions'
    gateway_stats=\"\$(curl -fsS --max-time 10 http://127.0.0.1:18080/stats)\"
    active_sessions=\"\$(printf '%s' \"\$gateway_stats\" | backend/venv/bin/python -c 'import json,sys; value=json.load(sys.stdin).get(\"active_sessions\"); assert isinstance(value, int) and not isinstance(value, bool); print(value)')\"
    if [ \"\$active_sessions\" -ne 0 ]; then
        echo \"!! Gateway still owns \$active_sessions active session(s); deploy is blocked.\" >&2
        exit 1
    fi
    echo '--> publishing the tested gateway binary atomically'
    sudo install -d -o root -g admins -m 0750 /opt/talky/runtime/bin
    sudo install -o root -g admins -m 0750 \"\$gateway_candidate\" \
        '/opt/talky/runtime/bin/.voice_gateway.${DEPLOY_SHA}.new'
    sudo mv -f '/opt/talky/runtime/bin/.voice_gateway.${DEPLOY_SHA}.new' \
        /opt/talky/runtime/bin/voice_gateway
    echo '--> reconciling systemd units'
    sudo bash backend/systemd/install-services.sh
    echo '--> rechecking zero gateway sessions immediately before restart'
    gateway_stats=\"\$(curl -fsS --max-time 10 http://127.0.0.1:18080/stats)\"
    active_sessions=\"\$(printf '%s' \"\$gateway_stats\" | backend/venv/bin/python -c 'import json,sys; value=json.load(sys.stdin).get(\"active_sessions\"); assert isinstance(value, int) and not isinstance(value, bool); print(value)')\"
    if [ \"\$active_sessions\" -ne 0 ]; then
        echo \"!! A gateway session appeared after the drain proof; deploy is blocked.\" >&2
        exit 1
    fi
    echo '--> restarting the matching gateway first'
    sudo systemctl restart talky-voice-gateway
    gateway_stats=\"\$(curl -fsS --max-time 10 http://127.0.0.1:18080/stats)\"
    active_sessions=\"\$(printf '%s' \"\$gateway_stats\" | backend/venv/bin/python -c 'import json,sys; value=json.load(sys.stdin).get(\"active_sessions\"); assert isinstance(value, int) and not isinstance(value, bool); print(value)')\"
    if [ \"\$active_sessions\" -ne 0 ]; then
        echo '!! Restarted gateway did not return an exact zero-session health proof.' >&2
        exit 1
    fi
    gateway_ready=\"\$(curl -fsS --max-time 10 http://127.0.0.1:18080/ready)\"
    printf '%s' \"\$gateway_ready\" | backend/venv/bin/python -c 'import json,sys; p=json.load(sys.stdin); assert p.get(\"ready\") is True; assert p.get(\"protocol_version\") == 2; assert \"pcmu\" in p.get(\"codecs\", [])'
    cleanup_gateway_candidate
    gateway_candidate=''
    trap - EXIT
    echo '--> applying database migrations (service restart is blocked on failure)'
    sudo systemctl start talky-migrate.service
    echo '--> restarting backend services after the authenticated gateway is healthy'
    sudo systemctl restart talky-api talky-dialer-worker talky-voice-worker talky-reminder-worker
    sudo systemctl restart talky-trunk-status.timer
    sudo systemctl start talky-trunk-status.service
    sleep 6
    echo '--> service status:'
    service_failure=0
    for s in talky-api talky-dialer-worker talky-voice-worker talky-reminder-worker talky-voice-gateway talky-trunk-status.timer; do
        state=\"\$(systemctl is-active \"\$s\" 2>/dev/null || true)\"
        printf '    %-26s %s\n' \"\$s\" \"\$state\"
        if [ \"\$state\" != 'active' ]; then
            service_failure=1
        fi
    done
    if [ \"\$service_failure\" -ne 0 ]; then
        echo '!! One or more required services are not active.' >&2
        exit 1
    fi
    echo '--> health and readiness:'
    curl -fsS --max-time 10 http://127.0.0.1:8000/health >/dev/null
    echo '    liveness OK'
    curl -fsS --max-time 10 http://127.0.0.1:8000/api/v1/healthz/ready >/dev/null
    echo '    readiness OK'
    curl -fsS --max-time 10 http://127.0.0.1:8000/api/v1/healthz/deep >/dev/null
    echo '    dependency readiness OK'
"
echo "==> Deploy complete."

#!/bin/bash
# ============================================================================
# Talky.ai â€” production deploy (git-based)
# ============================================================================
# Production at 144.76.17.150 is a GIT CHECKOUT of this repo (origin/main)
# with a sparse-checkout that excludes the frontends (Talk-Leee/, Admin/) â€”
# those deploy to Vercel from git, not to the backend box.
#
# Deploy model (the ONLY supported path â€” do NOT rsync source onto prod):
#   1. Commit + push your change to origin/main  (CI runs on push).
#   2. Run this script. It freezes the requested commit SHA, proves that SHA is
#      reachable from origin/main, and checks out that exact commit on prod.
#
# Rollback: follow docs/DEPLOYMENT.md â€” durably disable/prove ingress first,
# then select a full known-good SHA and require fail-closed health probes.
#
# Why git, not rsync: rsync-from-laptop made prod's source untracked and
# drift-prone (prod silently diverged from git). A checkout gives a known
# commit, history, and rollback. See docs/reference/ for the migration note.
# ----------------------------------------------------------------------------
set -euo pipefail

PROD="${TALKY_PROD_HOST:-admins@144.76.17.150}"
KEY="${TALKY_PROD_KEY:-$HOME/.ssh/id_rsa_openssh}"
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
CONTROLLER_SHA="$(git rev-parse --verify HEAD^{commit})"
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
if ! git merge-base --is-ancestor "$CONTROLLER_SHA" "origin/${BRANCH}"; then
    echo '!! The trusted deploy controller must be pushed to the selected origin branch.' >&2
    exit 1
fi
git cat-file -e "${CONTROLLER_SHA}:backend/scripts/verify_code_rollback.py"

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
# stored anywhere â€” it is typed at the prompt by the operator.
ssh -t -i "$KEY" "$PROD" "
    set -euo pipefail
    trap '' HUP PIPE
    # Authenticate before mutation; no later command may wait for a lost TTY.
    command sudo -v
    sudo() { command sudo -n \"\$@\"; }
    deploy_log_dir=/home/admins/backups/talky-deploy
    test ! -L \"\$deploy_log_dir\"
    mkdir -p -m 0700 -- \"\$deploy_log_dir\"
    chmod 0700 -- \"\$deploy_log_dir\"
    deploy_log=\"\$(mktemp \"\$deploy_log_dir/deploy.${DEPLOY_SHA}.XXXXXX.log\")\"
    echo \"DEPLOY_LOG=\$deploy_log\"
    # A pipe/tee tied to SSH would die when its reader disappears.
    exec >\"\$deploy_log\" 2>&1 </dev/null
    echo 'DEPLOY_START candidate=${DEPLOY_SHA} controller=${CONTROLLER_SHA}'
    deploy_phase=preflight
    rollback_sha=''
    rollback_guard=''
    guard_dir=''
    candidate_worktree=''
    gateway_candidate=''
    on_exit() {
        exit_code=\$?
        trap - EXIT
        trap '' INT TERM
        set +e
        rollback_status=not_needed
        if [ \"\$exit_code\" -ne 0 ] && [ -n \"\$rollback_sha\" ]; then
            rollback_status=blocked
            echo \"!! deploy aborted before migration started; checking fallback \$rollback_sha\" >&2
            if sudo backend/venv/bin/python \"\$rollback_guard\" --repo /opt/talky --target-sha \"\$rollback_sha\" --env-file /opt/talky/backend/.env; then
                rollback_status=failed
                rollback_ok=1
                echo \"!! restoring checkout \$rollback_sha and its unit definitions\" >&2
                if git checkout --detach \"\$rollback_sha\"; then
                    if ! sudo bash backend/systemd/install-services.sh; then rollback_ok=0; fi
                    # Restore the pinned unit even if the old installer failed,
                    # but never pair that old unit with a failed code checkout.
                    if ! { sudo cp -a --remove-destination -- \"\$web_unit_backup/talky-api-web.service\" /etc/systemd/system/talky-api-web.service && sudo systemctl daemon-reload; }; then
                        rollback_ok=0
                    fi
                else
                    rollback_ok=0
                    echo '!! Fallback checkout failed; retaining candidate unit definitions.' >&2
                fi
                if [ \"\$rollback_ok\" -eq 1 ]; then
                    rollback_status=restored
                    echo '!! previous checkout restored; no service was restarted by the rollback.' >&2
                else
                    echo '!! ROLLBACK FAILED: operator recovery is required.' >&2
                fi
            else
                echo '!! ROLLBACK BLOCKED: compatibility unknown/unsafe; retain candidate and keep ingress frozen.' >&2
            fi
        elif [ \"\$exit_code\" -ne 0 ] && [ \"\$deploy_phase\" != preflight ]; then
            echo '!! Migration may have committed; retain candidate and keep ingress frozen for reviewed code-forward recovery.' >&2
        fi
        if declare -F cleanup_candidate >/dev/null; then cleanup_candidate; fi
        if [ -n \"\$rollback_guard\" ]; then rm -f -- \"\$rollback_guard\"; fi
        if [ -n \"\$guard_dir\" ]; then rmdir -- \"\$guard_dir\"; fi
        deploy_status=failed
        if [ \"\$exit_code\" -eq 0 ]; then deploy_status=success; fi
        printf 'DEPLOY_RESULT status=%s exit_code=%s phase=%s candidate=%s controller=%s rollback=%s\\n' \"\$deploy_status\" \"\$exit_code\" \"\$deploy_phase\" '${DEPLOY_SHA}' '${CONTROLLER_SHA}' \"\$rollback_status\"
        exit \"\$exit_code\"
    }
    trap on_exit EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    cd /opt/talky
    echo '--> refusing tracked or untracked source drift'
    if [ -n \"\$(git status --porcelain)\" ]; then
        echo '!! /opt/talky is dirty; preserve and reconcile drift before deploying.' >&2
        git status --short >&2
        exit 1
    fi
    echo '--> preflighting the complete gateway build toolchain before checkout'
    for required_tool in asterisk bash cmake ctest c++ make curl git install mktemp rmdir timeout; do
        if ! command -v \"\$required_tool\" >/dev/null 2>&1; then
            echo \"!! Missing required deploy tool before checkout: \$required_tool\" >&2
            exit 1
        fi
    done
    if [ ! -x backend/venv/bin/python ]; then
        echo '!! /opt/talky/backend/venv/bin/python is missing or not executable.' >&2
        exit 1
    fi
    if [ ! -x backend/venv/bin/alembic ]; then
        echo '!! /opt/talky/backend/venv/bin/alembic is missing or not executable.' >&2
        exit 1
    fi
    echo '--> fetching candidate reachability proof'
    git fetch --prune origin ${BRANCH}
    git cat-file -e '${DEPLOY_SHA}^{commit}'
    git merge-base --is-ancestor '${DEPLOY_SHA}' FETCH_HEAD
    git cat-file -e '${CONTROLLER_SHA}^{commit}'
    git merge-base --is-ancestor '${CONTROLLER_SHA}' FETCH_HEAD
    guard_dir=\"\$(mktemp -d \"\$deploy_log_dir/guard.XXXXXX\")\"
    rollback_guard=\"\$guard_dir/verify_code_rollback.py\"
    git show '${CONTROLLER_SHA}:backend/scripts/verify_code_rollback.py' >\"\$rollback_guard\"
    test -s \"\$rollback_guard\"
    chmod 0600 -- \"\$rollback_guard\"
    echo '--> checking target code against the authoritative primary database'
    sudo backend/venv/bin/python \"\$rollback_guard\" --repo /opt/talky --target-sha '${DEPLOY_SHA}' --env-file /opt/talky/backend/.env
    echo '--> building, testing, and importing candidate in an isolated worktree'
    gateway_candidate=\"\$(mktemp /tmp/talky-voice-gateway-candidate.XXXXXX)\"
    candidate_worktree=\"\$(mktemp -d /tmp/talky-deploy-worktree.XXXXXX)\"
    rmdir -- \"\$candidate_worktree\"
    cleanup_candidate() {
        if [ -n \"\$candidate_worktree\" ] && [ -e \"\$candidate_worktree\" ]; then
            git -C /opt/talky worktree remove --force \"\$candidate_worktree\" >/dev/null 2>&1 || true
        fi
        if [ -n \"\$gateway_candidate\" ]; then
            rm -f -- \"\$gateway_candidate\"
        fi
    }
    git worktree add --detach \"\$candidate_worktree\" '${DEPLOY_SHA}'
    (cd \"\$candidate_worktree/backend\" && /opt/talky/backend/venv/bin/python -c 'import app.main') >/dev/null 2>&1
    VOICE_GATEWAY_BUILD_SHA='${DEPLOY_SHA}' \
        bash \"\$candidate_worktree/backend/scripts/build_voice_gateway_release.sh\" \"\$gateway_candidate\"
    echo '--> validating candidate Asterisk account-to-DID inventory'
    sudo env \
        TALKY_BACKEND_ENV_FILE=/opt/talky/backend/.env \
        TALKY_BACKEND_PYTHON=/opt/talky/backend/venv/bin/python \
        bash \"\$candidate_worktree/backend/scripts/reconcile_asterisk_release.sh\" --check-only
    echo '--> validating mandatory carrier-hairpin synthetic configuration'
    sudo bash \"\$candidate_worktree/backend/deploy/inbound-synthetic-call.sh\" \
        --check-config-file /etc/talky/inbound-synthetic.env
    git -C /opt/talky worktree remove --force \"\$candidate_worktree\"
    candidate_worktree=''
    # Prove the drain BEFORE the live checkout moves: an active call must leave
    # /opt/talky and the unit definitions exactly as they were. This needs only
    # the old tree's venv python and curl. (Re-checked again right before restart.)
    echo '--> proving the running gateway has zero active sessions (before touching the checkout)'
    gateway_stats=\"\$(curl -fsS --max-time 10 http://127.0.0.1:18080/stats)\"
    active_sessions=\"\$(printf '%s' \"\$gateway_stats\" | backend/venv/bin/python -c 'import json,sys; value=json.load(sys.stdin).get(\"active_sessions\"); assert isinstance(value, int) and not isinstance(value, bool); print(value)')\"
    if [ \"\$active_sessions\" -ne 0 ]; then
        echo \"!! Gateway still owns \$active_sessions active session(s); deploy is blocked.\" >&2
        exit 1
    fi
    echo '--> preserving the exact API-web unit before checkout'
    sudo install -d -o root -g root -m 0700 /var/backups/talky/deploy-unit-recovery
    web_unit_backup=\"\$(sudo mktemp -d /var/backups/talky/deploy-unit-recovery/api-web.${DEPLOY_SHA}.XXXXXX)\"
    sudo cp -a -- /etc/systemd/system/talky-api-web.service \"\$web_unit_backup/talky-api-web.service\"
    echo \"    retained private unit recovery directory: \$web_unit_backup\"
    # Abort fallback is permitted only before migration starts and only after
    # the retained controller helper proves the previous code compatible.
    # Candidate build/preflight may take time: re-observe the schema at the
    # actual code switch, without mistaking the origination freeze for a billing lock.
    sudo backend/venv/bin/python \"\$rollback_guard\" --repo /opt/talky --target-sha '${DEPLOY_SHA}' --env-file /opt/talky/backend/.env
    rollback_sha=\"\$(git rev-parse HEAD)\"
    deploy_phase=checkout
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
    echo '    candidate import and gateway build already proved before checkout'
    echo '--> publishing the tested gateway binary atomically'
    sudo install -d -o root -g admins -m 0750 /opt/talky/runtime/bin
    sudo install -o root -g admins -m 0750 \"\$gateway_candidate\" \
        '/opt/talky/runtime/bin/.voice_gateway.${DEPLOY_SHA}.new'
    sudo mv -f '/opt/talky/runtime/bin/.voice_gateway.${DEPLOY_SHA}.new' \
        /opt/talky/runtime/bin/voice_gateway
    echo '--> reconciling systemd units'
    sudo bash backend/systemd/install-services.sh
    echo '--> proving required systemd units are installed and enabled'
    for required_unit in talky-migrate.service talky-inbound-synthetic.service; do
        unit_load_state=\"\$(systemctl show \"\$required_unit\" --property=LoadState --value 2>/dev/null || true)\"
        if [ \"\$unit_load_state\" != 'loaded' ]; then
            echo \"!! Required systemd unit \$required_unit is not loaded (state=\$unit_load_state).\" >&2
            exit 1
        fi
    done
    for enabled_unit in talky-api.service talky-api-web.service talky-dialer-worker.service talky-voice-worker.service talky-reminder-worker.service talky-voice-gateway.service talky-trunk-status.service talky-inbound-synthetic.timer; do
        if ! systemctl is-enabled --quiet \"\$enabled_unit\"; then
            echo \"!! Required systemd unit \$enabled_unit is not enabled.\" >&2
            exit 1
        fi
    done
    echo '--> rechecking zero gateway sessions immediately before restart'
    gateway_stats=\"\$(curl -fsS --max-time 10 http://127.0.0.1:18080/stats)\"
    active_sessions=\"\$(printf '%s' \"\$gateway_stats\" | backend/venv/bin/python -c 'import json,sys; value=json.load(sys.stdin).get(\"active_sessions\"); assert isinstance(value, int) and not isinstance(value, bool); print(value)')\"
    if [ \"\$active_sessions\" -ne 0 ]; then
        echo \"!! A gateway session appeared after the drain proof; deploy is blocked.\" >&2
        exit 1
    fi
    echo '--> restarting the matching gateway first'
    sudo systemctl restart talky-voice-gateway
    # Type=exec confirms exec, not that the new listener is accepting requests.
    # Retry transport/HTTP startup failures only; validate each successful
    # response below so malformed/nonzero/wrong-build results still fail closed.
    wait_gateway_probe() {
        local probe_url=\"\$1\" probe_attempt=0 probe_payload=''
        until probe_payload=\"\$(curl -fsS --max-time 5 \"\$probe_url\")\"; do
            probe_attempt=\$((probe_attempt + 1))
            if [ \"\$probe_attempt\" -ge 30 ]; then
                echo \"!! gateway probe did not become healthy after \$probe_attempt attempts: \$probe_url\" >&2
                return 1
            fi
            sleep 1
        done
        printf '%s' \"\$probe_payload\"
    }
    gateway_stats=\"\$(wait_gateway_probe http://127.0.0.1:18080/stats)\"
    active_sessions=\"\$(printf '%s' \"\$gateway_stats\" | backend/venv/bin/python -c 'import json,sys; value=json.load(sys.stdin).get(\"active_sessions\"); assert isinstance(value, int) and not isinstance(value, bool); print(value)')\"
    if [ \"\$active_sessions\" -ne 0 ]; then
        echo '!! Restarted gateway did not return an exact zero-session health proof.' >&2
        exit 1
    fi
    gateway_ready=\"\$(wait_gateway_probe http://127.0.0.1:18080/ready)\"
    printf '%s' \"\$gateway_ready\" | backend/venv/bin/python -c 'import json,sys; p=json.load(sys.stdin); assert p.get(\"ready\") is True; assert p.get(\"protocol_version\") == 2; assert \"pcmu\" in p.get(\"codecs\", []); assert p.get(\"build_sha\") == sys.argv[1]' '${DEPLOY_SHA}'
    cleanup_candidate
    gateway_candidate=''
    # Once migration starts, interruption cannot prove whether it committed.
    rollback_sha=''
    deploy_phase=migration_started
    echo '--> applying database migrations (service restart is blocked on failure)'
    sudo systemctl start talky-migrate.service
    deploy_phase=post_migration
    sudo env -u DATABASE_URL -u READ_DATABASE_URL backend/venv/bin/python backend/scripts/verify_alembic_current_heads.py
    echo '--> verifying the application runtime database privileges'
    sudo env -u DATABASE_URL -u READ_DATABASE_URL backend/venv/bin/python backend/scripts/verify_admin_runtime_privileges.py
    echo '--> reconciling repository-owned Asterisk configuration'
    sudo bash backend/scripts/reconcile_asterisk_release.sh
    echo '--> publishing the exact backend release version'
    sudo backend/venv/bin/python backend/scripts/set_release_version.py --env-file /opt/talky/backend/.env --candidate-sha '${DEPLOY_SHA}'
    echo '--> restarting backend services after the authenticated gateway is healthy'
    sudo systemctl restart talky-api talky-dialer-worker talky-voice-worker talky-reminder-worker talky-api-web
    sudo systemctl restart talky-inbound-synthetic.timer
    # Long-running loop since 2026-10-07 (its 15 s timer was retired). A failed
    # restart never aborts the deploy: the unit has Restart=always.
    if ! sudo systemctl restart talky-trunk-status.service; then
        echo '!! talky-trunk-status restart failed; Restart=always will keep retrying.' >&2
    fi
    # systemctl restart returns once the process is exec'd, not once uvicorn is
    # listening (import + lifespan + wait-for-deps), so poll instead of a fixed
    # sleep. Both loops are bounded (~2 min) and still fail the deploy at the deadline.
    echo '--> service status:'
    service_failure=0
    for s in talky-api talky-api-web talky-dialer-worker talky-voice-worker talky-reminder-worker talky-voice-gateway talky-trunk-status talky-inbound-synthetic.timer; do
        state=''
        for state_attempt in \$(seq 1 30); do
            state=\"\$(systemctl is-active \"\$s\" 2>/dev/null || true)\"
            if [ \"\$state\" = 'active' ] || [ \"\$state\" = 'failed' ]; then
                break
            fi
            sleep 2
        done
        printf '    %-26s %s\n' \"\$s\" \"\$state\"
        if [ \"\$state\" != 'active' ]; then
            service_failure=1
        fi
    done
    if [ \"\$service_failure\" -ne 0 ]; then
        echo '!! One or more required services are not active.' >&2
        exit 1
    fi
    retry_probe() {
        probe_name=\"\$1\"
        shift
        probe_attempt=0
        until \"\$@\" >/dev/null; do
            probe_attempt=\$((probe_attempt + 1))
            if [ \"\$probe_attempt\" -ge 60 ]; then
                echo \"!! \$probe_name did not become healthy after \$probe_attempt attempts.\" >&2
                return 1
            fi
            sleep 2
        done
    }
    echo '--> health and readiness:'
    retry_probe liveness curl -fsS --max-time 10 http://127.0.0.1:8000/health
    echo '    liveness OK'
    retry_probe readiness curl -fsS --max-time 10 http://127.0.0.1:8000/api/v1/healthz/ready
    echo '    readiness OK'
    retry_probe dependency-readiness curl -fsS --max-time 10 http://127.0.0.1:8000/api/v1/healthz/deep
    echo '    dependency readiness OK'
    retry_probe dashboard-liveness curl -fsS --max-time 10 http://127.0.0.1:8001/health
    echo '    dashboard API liveness OK'
    echo '--> verifying the running API process versions'
    for version_unit in talky-api talky-api-web; do
        version_pid=\"\$(systemctl show \"\$version_unit\" --property=MainPID --value)\"
        sudo backend/venv/bin/python backend/scripts/set_release_version.py --check-pid \"\$version_pid\" --candidate-sha '${DEPLOY_SHA}'
        if [ \"\$(systemctl show \"\$version_unit\" --property=MainPID --value)\" != \"\$version_pid\" ]; then
            echo \"!! \$version_unit changed process during version verification.\" >&2
            exit 1
        fi
    done
    deploy_phase=verified
"
echo "==> Deploy complete."

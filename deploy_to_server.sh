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
#   2. Run this script. It SSHes to prod, fast-forwards the checkout to
#      origin/main, and restarts the backend services.
#
# Rollback:  ssh to prod, `git -C /opt/talky checkout <good-sha>`, restart.
#
# Why git, not rsync: rsync-from-laptop made prod's source untracked and
# drift-prone (prod silently diverged from git). A checkout gives a known
# commit, history, and rollback. See docs/reference/ for the migration note.
# ----------------------------------------------------------------------------
set -euo pipefail

PROD="${TALKY_PROD_HOST:-admins@144.76.17.150}"
KEY="${TALKY_PROD_KEY:-$HOME/.ssh/talky_admin}"
BRANCH="${TALKY_DEPLOY_BRANCH:-main}"

echo "==> Verifying local branch is pushed"
LOCAL_SHA="$(git rev-parse --short HEAD)"
if ! git merge-base --is-ancestor HEAD "origin/${BRANCH}" 2>/dev/null; then
    echo "!! HEAD (${LOCAL_SHA}) is not on origin/${BRANCH}."
    echo "   Push first:  git push origin ${BRANCH}"
    exit 1
fi
echo "    local HEAD ${LOCAL_SHA} is on origin/${BRANCH}"

echo "==> Deploying origin/${BRANCH} to ${PROD}"
echo "    (you will be prompted for the prod sudo password to restart services)"

# -t so the remote sudo can prompt interactively. The sudo password is NOT
# stored anywhere — it is typed at the prompt by the operator.
ssh -t -i "$KEY" "$PROD" "
    set -e
    cd /opt/talky
    echo '--> git fetch + fast-forward'
    git fetch origin
    git checkout ${BRANCH}
    git pull --ff-only origin ${BRANCH}
    echo \"    prod now at: \$(git rev-parse --short HEAD)\"
    echo '--> import smoke test'
    backend/venv/bin/python -c 'import app.main' >/dev/null 2>&1 && echo '    import app.main OK'
    echo '--> restarting backend services (gateway is C++; restart separately if it changed)'
    sudo systemctl restart talky-api talky-dialer-worker talky-voice-worker talky-reminder-worker
    sleep 6
    echo '--> service status:'
    for s in talky-api talky-dialer-worker talky-voice-worker talky-reminder-worker talky-voice-gateway; do
        printf '    %-26s %s\n' \"\$s\" \"\$(systemctl is-active \$s)\"
    done
    echo '--> health:'
    curl -s -o /dev/null -w '    health HTTP %{http_code}\n' http://127.0.0.1:8000/health
"
echo "==> Deploy complete."

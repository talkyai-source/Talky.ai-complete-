#!/bin/bash
# Push the Talky codebase from this dev box to the production server
# at 144.76.17.150. Sends ONLY the working-tree of the currently
# checked-out branch (main) — no git history, no other branches, no
# heavy regenerable directories. Re-run any time to push updates;
# rsync only transfers what's changed.
#
# Run with:   bash ~/Desktop/Talky.ai-complete-/deploy_to_server.sh
set -e

SOURCE_DIR="/home/ai-lab/Desktop/Talky.ai-complete-"
DEST="admins@144.76.17.150:/opt/talky/"

if [ ! -d "$SOURCE_DIR" ]; then
    echo "ERROR: source directory not found: $SOURCE_DIR"
    exit 1
fi

cd "$SOURCE_DIR"

# Show what branch we're deploying so the user knows what's going.
if [ -d .git ]; then
    BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "?")
    COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "?")
    echo "==> Deploying branch '${BRANCH}' (commit ${COMMIT})"
fi
echo "==> Source: $SOURCE_DIR"
echo "==> Target: $DEST"
echo "    You will be prompted for the SERVER password (the admins"
echo "    user on 144.76.17.150). Type it carefully — the cursor"
echo "    won't move and no asterisks appear."
echo

rsync -avz --progress \
    --exclude=.git \
    --exclude=node_modules \
    --exclude=.next \
    --exclude=venv \
    --exclude=__pycache__ \
    --exclude='*.pyc' \
    --exclude=.pytest_cache \
    --exclude='*.wav' \
    --exclude='*.pcap' \
    --exclude=recordings \
    --exclude='.env*' \
    --exclude='*.service' \
    ./ "$DEST"

echo
echo "==> Push complete."
echo "    To push updates later, just re-run this same command."

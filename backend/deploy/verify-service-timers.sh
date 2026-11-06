#!/usr/bin/env bash
# Read-only release verification. Activation remains the installer's/operator's job.
set -euo pipefail
shopt -s nullglob

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
timers=("$script_dir/../systemd/"*.timer)
if [ "${#timers[@]}" -eq 0 ]; then
    echo '!! No repository timer definitions found; verification refused.' >&2
    exit 1
fi

failed=0
for path in "${timers[@]}"; do
    unit="${path##*/}"
    load_state="$(systemctl show "$unit" --property=LoadState --value 2>/dev/null || true)"
    enabled_state="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
    active_state="$(systemctl is-active "$unit" 2>/dev/null || true)"
    valid=0
    if [ "$unit" = talky-trunk-status.timer ]; then
        # Retained only so old installation symlinks can be disabled safely.
        if [[ "$load_state" = loaded || "$load_state" = not-found ]] \
            && [[ "$enabled_state" = disabled || "$enabled_state" = masked || "$enabled_state" = not-found ]] \
            && [ "$active_state" = inactive ]; then
            valid=1
        fi
    elif [ "$load_state" = loaded ] && [ "$enabled_state" = enabled ] && [ "$active_state" = active ]; then
        valid=1
    fi
    printf '    %s load=%s enabled=%s active=%s\n' "$unit" "$load_state" "$enabled_state" "$active_state"
    if [ "$valid" -ne 1 ]; then
        echo "!! Timer policy failed: $unit" >&2
        failed=1
    fi
done
exit "$failed"

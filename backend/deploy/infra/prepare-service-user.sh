#!/usr/bin/env bash
# --check is read-only and permits running services. Apply only after drain/stop.
# Custom storage paths need matching ownership and ReadWritePaths drop-ins;
# review those before using this default-path preparation on an existing host.
set -euo pipefail

check_only=0
if [[ $# -eq 1 && $1 == --check ]]; then
    check_only=1
elif [[ $# -ne 0 ]]; then
    echo 'Usage: prepare-service-user.sh [--check]' >&2
    exit 2
fi
[[ ${EUID} -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for path in /opt/talky-infra /opt/talky-infra/bin /opt/talky-infra/bin/wait-for-deps; do
    [[ ! -L "$path" ]] || { echo 'Refusing symlink privileged probe path.' >&2; exit 1; }
done
[[ -d /opt/talky/backend && -f /opt/talky/backend/.env ]] || {
    echo 'Expected installed backend and environment file.' >&2; exit 1;
}
[[ ! -L /opt/talky/backend/.env ]] || {
    echo 'Refusing a symlink environment file.' >&2; exit 1;
}
getent group asterisk >/dev/null || { echo 'Asterisk group missing.' >&2; exit 1; }
if id talky >/dev/null 2>&1; then
    [[ $(id -u talky) -ne 0 && $(id -gn talky) == talky ]] || {
        echo 'Existing talky identity is unsafe.' >&2; exit 1;
    }
    for group in $(id -nG talky); do
        case "$group" in
            talky) ;;
            *) echo 'Review unexpected talky supplementary groups before proceeding.' >&2; exit 1 ;;
        esac
    done
fi
for name in recordings audio_files feedback_audio support_attachments; do
    [[ ! -L "/opt/talky/backend/$name" ]] || { echo "Refusing symlink state directory: $name" >&2; exit 1; }
done
[[ ! -L /etc/asterisk/pjsip.d ]] || { echo 'Refusing symlink pjsip.d.' >&2; exit 1; }
if [[ $check_only -eq 1 ]]; then
    echo 'Service-user prerequisites checked; no changes made.'
    exit 0
fi
for unit in talky-api talky-api-web talky-dialer-worker talky-voice-worker talky-reminder-worker talky-trunk-status talky-cleanup talky-support-jobs talky-cleanup.timer talky-support-jobs.timer; do
    if systemctl is-active --quiet "$unit"; then
        echo "Stop application writers before changing ownership: $unit" >&2
        exit 1
    fi
done
if ! id talky >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin talky
fi

# systemd supplies environment values, and python-dotenv may also read the file.
# Keep the current owner; grant only the service group read access.
chgrp talky /opt/talky/backend/.env
chmod 0640 /opt/talky/backend/.env

# Do not chown the checkout to the application: root-run ops scripts and
# symlinked systemd definitions must remain outside its write permissions.
for name in recordings audio_files feedback_audio support_attachments; do
    path="/opt/talky/backend/$name"
    install -d -o talky -g talky -m 0700 "$path"
    find "$path" -xdev -type d -exec chown talky:talky {} + -exec chmod 0700 {} +
    find "$path" -xdev -type f -exec chown talky:talky {} + -exec chmod 0600 {} +
done

# API-generated trunk configs use the Asterisk reader group. Membership is
# scoped in the trunk-managing units, not granted to every process of this account.
install -d -o asterisk -g asterisk -m 2770 /etc/asterisk/pjsip.d

# Preserve Docker health checks without making the application a Docker admin.
# The privileged executable and its parent directory must be root-owned.
install -d -o root -g root -m 0755 /opt/talky-infra /opt/talky-infra/bin
install -o root -g root -m 0755 "$SCRIPT_DIR/wait-for-deps" /opt/talky-infra/bin/wait-for-deps
for unit in talky-api talky-api-web talky-dialer-worker talky-voice-worker talky-reminder-worker; do
    dropin="/etc/systemd/system/$unit.service.d/20-talky-deps.conf"
    if [[ -f "$dropin" ]]; then
        install -o root -g root -m 0644 "$SCRIPT_DIR/systemd/talky-deps.conf" "$dropin"
    fi
done
echo 'Prepared talky identity and default state directories; verify custom paths and Asterisk CLI permissions before restart.'

#!/usr/bin/env bash
# Originate an hourly carrier hairpin into the production inbound DID.
# Success is observed independently by inbound_last_success_timestamp_seconds
# only after the returned inbound leg delivers confirmed first agent audio.
set -Eeuo pipefail

load_config_file() {
    local path="$1" key value
    if [ ! -r "$path" ]; then
        echo "INBOUND_SYNTHETIC CRITICAL: config is not readable: $path" >&2
        exit 2
    fi
    while IFS='=' read -r key value || [ -n "$key" ]; do
        key="${key//[[:space:]]/}"
        case "$key" in
            ''|'#'*) continue ;;
            INBOUND_SYNTHETIC_DID|INBOUND_SYNTHETIC_TRUNK_ENDPOINT|INBOUND_SYNTHETIC_WAIT_SECONDS)
                export "$key=$value"
                ;;
        esac
    done < "$path"
}

validate_config() {
    local did="${INBOUND_SYNTHETIC_DID:-}"
    local endpoint="${INBOUND_SYNTHETIC_TRUNK_ENDPOINT:-}"
    local wait_seconds="${INBOUND_SYNTHETIC_WAIT_SECONDS:-20}"

    if ! [[ "$did" =~ ^\+?[0-9]{7,15}$ ]]; then
        echo "INBOUND_SYNTHETIC CRITICAL: DID must be 7-15 digits with optional leading +" >&2
        exit 2
    fi
    if ! [[ "$endpoint" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$ ]]; then
        echo "INBOUND_SYNTHETIC CRITICAL: invalid PJSIP endpoint name" >&2
        exit 2
    fi
    if ! [[ "$wait_seconds" =~ ^[0-9]+$ ]] ||
       [ "$wait_seconds" -lt 8 ] || [ "$wait_seconds" -gt 60 ]; then
        echo "INBOUND_SYNTHETIC CRITICAL: wait seconds must be an integer from 8 to 60" >&2
        exit 2
    fi
}

case "${1:-}" in
    --check-config-file)
        [ "$#" -eq 2 ] || { echo "usage: $0 --check-config-file PATH" >&2; exit 2; }
        load_config_file "$2"
        validate_config
        echo "inbound synthetic configuration valid"
        exit 0
        ;;
    --check-config)
        validate_config
        echo "inbound synthetic configuration valid"
        exit 0
        ;;
    '') ;;
    *) echo "usage: $0 [--check-config | --check-config-file PATH]" >&2; exit 2 ;;
esac

validate_config
command -v asterisk >/dev/null 2>&1 || {
    echo "INBOUND_SYNTHETIC CRITICAL: asterisk CLI is unavailable" >&2
    exit 1
}
command -v timeout >/dev/null 2>&1 || {
    echo "INBOUND_SYNTHETIC CRITICAL: timeout is unavailable" >&2
    exit 1
}

did="${INBOUND_SYNTHETIC_DID}"
endpoint="${INBOUND_SYNTHETIC_TRUNK_ENDPOINT}"
wait_seconds="${INBOUND_SYNTHETIC_WAIT_SECONDS:-20}"
command_text="channel originate PJSIP/${did}@${endpoint} application Wait ${wait_seconds}"

# Channels already on the inbound call plan BEFORE the probe is sent. A real
# customer call in the window must not be mistaken for the probe's own leg
# (ops-8, 2026-10-20): only a channel that was not here before can count.
inbound_channels() {
    timeout 5 asterisk -rx "core show channels concise" 2>/dev/null |
        awk -F'!' '$2 == "from-talky-inbound" { print $1 }' || true
}
baseline_channels="$(inbound_channels)"

echo "inbound synthetic: originating carrier hairpin did=${did} endpoint=${endpoint}"
if ! timeout "$((wait_seconds + 20))" asterisk -rx "$command_text"; then
    echo "INBOUND_SYNTHETIC CRITICAL: Asterisk rejected or timed out originating the probe" >&2
    exit 1
fi

# Prove the hairpin came back in. An accepted originate only means Asterisk took
# the request: from 2026-09-22 the probe's carrier account was rejected and the
# DID's line was off, yet every hourly run still printed "accepted" and the unit
# succeeded. Fail it (journal, systemctl --failed) unless a channel reaches the
# inbound call plan while the probe waits.
arrive_window=$((wait_seconds + 10))
arrived=""
for _ in $(seq 1 "$arrive_window"); do
    while IFS= read -r channel; do
        [ -n "$channel" ] || continue
        if ! grep -qxF -- "$channel" <<<"$baseline_channels"; then
            arrived=1
            break
        fi
    done < <(inbound_channels)
    if [ -n "$arrived" ]; then
        break
    fi
    sleep 1
done
if [ -z "$arrived" ]; then
    echo "INBOUND_SYNTHETIC CRITICAL: the probe never came back in on the inbound call plan within ${arrive_window}s (did=${did} endpoint=${endpoint})" >&2
    exit 1
fi
echo "inbound synthetic: the probe reached the inbound call plan; Prometheus must prove returned first audio"

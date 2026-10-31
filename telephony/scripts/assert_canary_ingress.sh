#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TELEPHONY_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

MODE=${1:-all}
ENV_FILE=${2:-}
OPENSIPS_CONFIG=${OPENSIPS_CONFIG:-$TELEPHONY_ROOT/opensips/conf/opensips.cfg}
DISPATCHER_CONFIG=${DISPATCHER_CONFIG:-$TELEPHONY_ROOT/opensips/conf/dispatcher.list}
ASTERISK_EXTENSIONS_CONFIG=${ASTERISK_EXTENSIONS_CONFIG:-$TELEPHONY_ROOT/asterisk/conf/extensions.conf}
ASTERISK_PJSIP_CONFIG=${ASTERISK_PJSIP_CONFIG:-$TELEPHONY_ROOT/asterisk/conf/pjsip.conf}
COMPOSE_CONFIG=${COMPOSE_CONFIG:-$TELEPHONY_ROOT/deploy/docker/docker-compose.telephony.yml}

case "$MODE" in
  all|env|opensips|asterisk) ;;
  *)
    echo "[ERROR] Usage: $0 [all|env|opensips|asterisk] [env_file]" >&2
    exit 2
    ;;
esac

load_key() {
  key=$1
  if [ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ]; then
    value=$(awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); value=$0} END {print value}' "$ENV_FILE")
    if [ -n "$value" ]; then
      case "$value" in
        \"*\") value=${value#\"}; value=${value%\"} ;;
        \'*\') value=${value#\'}; value=${value%\'} ;;
      esac
      export "$key=$value"
    fi
  fi
}

fail() {
  echo "[ERROR] $*" >&2
  exit 1
}

assert_file() {
  [ -f "$1" ] || fail "Missing required file: $1"
}

assert_env() {
  load_key OPENSIPS_CANARY_ENABLED
  load_key OPENSIPS_CANARY_PERCENT
  load_key OPENSIPS_CANARY_FREEZE
  load_key OPENSIPS_CANARY_DID
  load_key OPENSIPS_CANARY_AGENT_ID
  load_key OPENSIPS_CANARY_SOURCE_REGEX

  enabled=${OPENSIPS_CANARY_ENABLED:-0}
  percent=${OPENSIPS_CANARY_PERCENT:-0}
  freeze=${OPENSIPS_CANARY_FREEZE:-1}

  case "$enabled" in 0|1) ;; *) fail "OPENSIPS_CANARY_ENABLED must be 0 or 1" ;; esac
  case "$freeze" in 0|1) ;; *) fail "OPENSIPS_CANARY_FREEZE must be 0 or 1" ;; esac

  if [ "$enabled" = "0" ]; then
    # Preserve a safe rollback path for older env files that predate this key.
    source_regex=${OPENSIPS_CANARY_SOURCE_REGEX:-'^127\.0\.0\.1$'}
  else
    source_regex=${OPENSIPS_CANARY_SOURCE_REGEX:-}
  fi
  [ -n "$source_regex" ] || fail "OPENSIPS_CANARY_SOURCE_REGEX is required"
  case "$source_regex" in
    ^*'$') ;;
    *) fail "OPENSIPS_CANARY_SOURCE_REGEX must be anchored with ^ and $" ;;
  esac
  case "$source_regex" in
    *'*'*|*'+'*|*'?'*|*'['*|*']'*|*'{'*|*'}'*|'^$') fail "OPENSIPS_CANARY_SOURCE_REGEX must contain exact IPv4 alternatives only" ;;
  esac
  safe_source_chars=$(printf '%s\n' "$source_regex" | sed -e 's/\\\./ /g' -e 's/[0-9()|^$ ]//g')
  [ -z "$safe_source_chars" ] || fail "OPENSIPS_CANARY_SOURCE_REGEX contains unsafe or unescaped characters"
  case "$source_regex" in
    *'127\.0\.0\.1'*) ;;
    *) fail "OPENSIPS_CANARY_SOURCE_REGEX must retain exact loopback for the Asterisk dialog leg" ;;
  esac

  if [ "$enabled" = "0" ]; then
    [ "$percent" = "0" ] || fail "disabled canary must use OPENSIPS_CANARY_PERCENT=0"
    echo "[OK] Canary ingress disabled and fail-closed"
    return
  fi

  [ "$percent" = "100" ] || fail "enabled dedicated-DID canary must use OPENSIPS_CANARY_PERCENT=100"
  [ "$freeze" = "0" ] || fail "enabled canary must be explicitly unfrozen; freeze=1 blocks ingress"

  did=${OPENSIPS_CANARY_DID:-}
  agent_id=${OPENSIPS_CANARY_AGENT_ID:-}

  printf '%s\n' "$did" | grep -Eq '^[1-9][0-9]{6,14}$' || \
    fail "OPENSIPS_CANARY_DID must be one normalized 7-15 digit DID"
  printf '%s\n' "$agent_id" | grep -Eiq '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' || \
    fail "OPENSIPS_CANARY_AGENT_ID must be a concrete RFC 4122 UUID"
  echo "[OK] Explicit canary DID, agent, source, and 100% gate validated"
}

assert_opensips() {
  assert_file "$OPENSIPS_CONFIG"
  assert_file "$DISPATCHER_CONFIG"

  grep -Fq 'if ($def(OPENSIPS_CANARY_ENABLED) != 1' "$OPENSIPS_CONFIG" || \
    fail "OpenSIPS does not fail closed when canary ingress is disabled"
  grep -Fq '|| $def(OPENSIPS_CANARY_PERCENT) != 100' "$OPENSIPS_CONFIG" || \
    fail "OpenSIPS does not require the dedicated-DID canary at 100%"
  grep -Fq '|| $def(OPENSIPS_CANARY_FREEZE) != 0)' "$OPENSIPS_CONFIG" || \
    fail "OpenSIPS freeze flag does not fail closed"
  grep -Fq '$rU != "$def(OPENSIPS_CANARY_DID)"' "$OPENSIPS_CONFIG" || \
    fail "OpenSIPS exact DID admission is missing"
  grep -Fq 'append_hf("X-Talky-Agent-ID: $def(OPENSIPS_CANARY_AGENT_ID)\r\n")' "$OPENSIPS_CONFIG" || \
    fail "OpenSIPS explicit agent stamp is missing"
  grep -Fq 'if (!ds_select_dst(2, 4))' "$OPENSIPS_CONFIG" || \
    fail "OpenSIPS canary dispatcher selection is missing"
  if grep -Fq 'ds_select_dst(1,' "$OPENSIPS_CONFIG"; then
    fail "OpenSIPS contains a stable-set fallback from canary ingress"
  fi
  if grep -Eq 'rand_set_prob|falling back|FreeSWITCH.*backup' "$OPENSIPS_CONFIG"; then
    fail "OpenSIPS contains probabilistic or backup fallback routing"
  fi

  admission_line=$(grep -nF '$rU != "$def(OPENSIPS_CANARY_DID)"' "$OPENSIPS_CONFIG" | head -n1 | cut -d: -f1)
  target_line=$(grep -nF 'if (!ds_select_dst(2, 4))' "$OPENSIPS_CONFIG" | head -n1 | cut -d: -f1)
  media_line=$(grep -nF 'route(WS_M_MANAGE_RTP);' "$OPENSIPS_CONFIG" | tail -n1 | cut -d: -f1)
  [ "$admission_line" -lt "$target_line" ] || fail "DID admission must occur before target selection"
  [ "$target_line" -lt "$media_line" ] || fail "target selection must occur before media allocation"

  awk '
    /^[[:space:]]*#/ || NF == 0 {next}
    $1 != "1" && $1 != "2" {bad=1}
    $2 != "sip:127.0.0.1:5070" {bad=1}
    $1 == "2" {canary++}
    END {exit (bad || canary != 1) ? 1 : 0}
  ' "$DISPATCHER_CONFIG" || fail "dispatcher must contain one Asterisk-only canary target and no backup"

  echo "[OK] OpenSIPS admission ordering and Asterisk-only dispatcher validated"
}

assert_asterisk() {
  assert_file "$ASTERISK_EXTENSIONS_CONFIG"
  assert_file "$ASTERISK_PJSIP_CONFIG"
  inbound_block=$(awk '
    /^\[from-opensips\]$/ {inside=1; next}
    /^\[/ && inside {exit}
    inside {print}
  ' "$ASTERISK_EXTENSIONS_CONFIG")
  inbound_code=$(printf '%s\n' "$inbound_block" | grep -v '^[[:space:]]*;' || true)

  [ -n "$inbound_block" ] || fail "Asterisk from-opensips context is missing"
  if printf '%s\n' "$inbound_code" | grep -Fq 'Answer('; then
    fail "Asterisk answers before ARI admission"
  fi
  printf '%s\n' "$inbound_code" | grep -Fq 'X-Talky-Ingress-Policy' || fail "Asterisk ingress-policy check is missing"
  printf '%s\n' "$inbound_code" | grep -Fq 'X-Talky-Original-DID' || fail "Asterisk original-DID check is missing"
  printf '%s\n' "$inbound_code" | grep -Fq 'X-Talky-Agent-ID' || fail "Asterisk explicit-agent check is missing"
  printf '%s\n' "$inbound_code" | grep -Fq 'Hangup(21)' || fail "Asterisk pre-answer reject path is missing"
  if printf '%s\n' "$inbound_code" | grep -Fq 'Set(TALKY_ORIGINAL_DID=${EXTEN})'; then
    fail "Asterisk contains an unsafe DID fallback"
  fi

  default_block=$(awk '
    /^\[default\]$/ {inside=1; next}
    /^\[/ && inside {exit}
    inside {print}
  ' "$ASTERISK_EXTENSIONS_CONFIG")
  default_code=$(printf '%s\n' "$default_block" | grep -v '^[[:space:]]*;' || true)
  if printf '%s\n' "$default_code" | grep -Eq 'Answer\(|Playback\('; then
    fail "Asterisk default context can answer an unadmitted call"
  fi

  awk '
    /^\[/ {section=$0}
    /^[[:space:]]*context[[:space:]]*=[[:space:]]*from-opensips[[:space:]]*$/ && section != "[talky-opensips]" {bad=1}
    END {exit bad ? 1 : 0}
  ' "$ASTERISK_PJSIP_CONFIG" || fail "A non-OpenSIPS endpoint can enter the trusted ingress context"
  grep -Fq 'endpoint_identifier_order=ip' "$ASTERISK_PJSIP_CONFIG" || \
    fail "Asterisk permits spoofable username/header endpoint identification"
  grep -Fq 'match=127.0.0.1:15060' "$ASTERISK_PJSIP_CONFIG" || \
    fail "Asterisk trusted ingress endpoint is not pinned to the OpenSIPS socket"

  echo "[OK] Asterisk pre-answer admission handoff, source boundary, and reject path validated"
}

assert_compose() {
  assert_file "$COMPOSE_CONFIG"
  grep -Fq 'profiles: ["backup"]' "$COMPOSE_CONFIG" || fail "FreeSWITCH must be opt-in only"
  for key in OPENSIPS_CANARY_ENABLED OPENSIPS_CANARY_PERCENT OPENSIPS_CANARY_FREEZE OPENSIPS_CANARY_DID OPENSIPS_CANARY_AGENT_ID OPENSIPS_CANARY_SOURCE_REGEX; do
    grep -Fq "$key:" "$COMPOSE_CONFIG" || fail "Compose does not pass $key to OpenSIPS"
  done
  grep -Fq 'assert_canary_ingress.sh opensips' "$COMPOSE_CONFIG" || fail "OpenSIPS startup assertion is not wired"
  grep -Fq 'assert_canary_ingress.sh asterisk' "$COMPOSE_CONFIG" || fail "Asterisk startup assertion is not wired"
  echo "[OK] Deployment startup gates and disabled backup profile validated"
}

case "$MODE" in
  env) assert_env ;;
  opensips) assert_env; assert_opensips ;;
  asterisk) assert_env; assert_asterisk ;;
  all) assert_env; assert_opensips; assert_asterisk; assert_compose ;;
esac

echo "Canary ingress assertions PASSED."

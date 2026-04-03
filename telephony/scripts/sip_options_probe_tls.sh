#!/usr/bin/env bash
set -euo pipefail

HOST="${1:-127.0.0.1}"
PORT="${2:-15061}"
TIMEOUT_SECONDS="${3:-5}"
BRANCH="z9hG4bK-$(date +%s%N | cut -b1-12)"
CALL_ID="$(date +%s%N)@${HOST}"
REQUEST="OPTIONS sip:${HOST}:${PORT} SIP/2.0\r\nVia: SIP/2.0/TLS 127.0.0.1;branch=${BRANCH};rport\r\nFrom: <sip:probe@localhost>;tag=wsb1\r\nTo: <sip:${HOST}:${PORT}>\r\nCall-ID: ${CALL_ID}\r\nCSeq: 1 OPTIONS\r\nContact: <sip:probe@127.0.0.1>\r\nMax-Forwards: 70\r\nContent-Length: 0\r\n\r\n"

RESPONSE_LINE="$(
  printf '%b' "$REQUEST" \
    | timeout "$TIMEOUT_SECONDS" openssl s_client \
      -connect "${HOST}:${PORT}" \
      -servername localhost \
      -quiet 2>/dev/null \
    | head -n1 || true
)"

if [[ "$RESPONSE_LINE" == SIP/2.0* ]]; then
  echo "PASS: TLS SIP response: $RESPONSE_LINE"
  exit 0
fi

echo "FAIL: TLS SIP OPTIONS probe failed (response='$RESPONSE_LINE')" >&2
exit 2

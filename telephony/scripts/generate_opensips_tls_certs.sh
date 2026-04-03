#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEPHONY_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CERT_DIR="$TELEPHONY_ROOT/opensips/certs"
KEY_FILE="$CERT_DIR/server.key"
CRT_FILE="$CERT_DIR/server.crt"

mkdir -p "$CERT_DIR"

if [[ -s "$KEY_FILE" && -s "$CRT_FILE" ]]; then
  echo "[OK] OpenSIPS TLS certs already exist: $CRT_FILE"
  exit 0
fi

echo "[INFO] Generating self-signed TLS cert for staging..."
openssl req -x509 -newkey rsa:2048 -sha256 -nodes \
  -keyout "$KEY_FILE" \
  -out "$CRT_FILE" \
  -days 365 \
  -subj "/C=US/ST=Dev/L=Dev/O=Talky/OU=Telephony/CN=localhost" >/dev/null 2>&1

chmod 600 "$KEY_FILE"
chmod 644 "$CRT_FILE"

echo "[OK] Generated $KEY_FILE and $CRT_FILE"

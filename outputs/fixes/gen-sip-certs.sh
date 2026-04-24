#!/usr/bin/env bash
# scripts/gen-sip-certs.sh
# ============================================================
# Generate self-signed TLS certificates for SIP stack
# Use for INTERNAL / STAGING deployments only.
# For production internet-facing SIP, use Let's Encrypt instead.
#
# Usage:
#   bash scripts/gen-sip-certs.sh
#   bash scripts/gen-sip-certs.sh sip.your-domain.com
# ============================================================
set -euo pipefail

DOMAIN="${1:-sip.local}"
DAYS=365
OUT_DIR="./telephony/certs"

mkdir -p "$OUT_DIR/opensips" "$OUT_DIR/kamailio"

echo "[INFO] Generating CA key and certificate..."
openssl genrsa -out "$OUT_DIR/ca.key" 4096
openssl req -new -x509 -days "$DAYS" \
  -key "$OUT_DIR/ca.key" \
  -out "$OUT_DIR/ca.crt" \
  -subj "/C=US/O=Talky.ai/CN=Talky-SIP-CA"

generate_server_cert() {
  local name="$1" out_dir="$2"
  echo "[INFO] Generating $name server certificate for $DOMAIN..."

  openssl genrsa -out "$out_dir/server.key" 2048

  openssl req -new \
    -key "$out_dir/server.key" \
    -out "$out_dir/server.csr" \
    -subj "/C=US/O=Talky.ai/CN=${DOMAIN}"

  # SAN extension — required for modern TLS validation
  cat > "$out_dir/server.ext" <<EOF
authorityKeyIdentifier=keyid,issuer
basicConstraints=CA:FALSE
keyUsage=digitalSignature,nonRepudiation,keyEncipherment,dataEncipherment
subjectAltName=DNS:${DOMAIN},DNS:localhost,IP:127.0.0.1
EOF

  openssl x509 -req -days "$DAYS" \
    -in "$out_dir/server.csr" \
    -CA "$OUT_DIR/ca.crt" \
    -CAkey "$OUT_DIR/ca.key" \
    -CAcreateserial \
    -out "$out_dir/server.crt" \
    -extfile "$out_dir/server.ext"

  rm "$out_dir/server.csr" "$out_dir/server.ext"
  chmod 600 "$out_dir/server.key"
  echo "[OK] $name: $out_dir/server.crt + server.key"
}

generate_server_cert "OpenSIPS" "$OUT_DIR/opensips"
generate_server_cert "Kamailio" "$OUT_DIR/kamailio"

echo ""
echo "[INFO] ✅ Certificates generated in $OUT_DIR/"
echo ""
echo "Next steps:"
echo "  1. Mount certs into containers via docker-compose volumes:"
echo "       opensips:"
echo "         volumes:"
echo "           - ./telephony/certs/opensips:/etc/opensips/certs:ro"
echo "           - ./telephony/certs/ca.crt:/etc/ssl/certs/talky-ca.crt:ro"
echo "       kamailio:"
echo "         volumes:"
echo "           - ./telephony/certs/kamailio:/etc/kamailio/certs:ro"
echo "           - ./telephony/certs/ca.crt:/etc/ssl/certs/talky-ca.crt:ro"
echo ""
echo "  2. Update tls.cfg ca_list to point to /etc/ssl/certs/talky-ca.crt"
echo "     for internal CA validation, OR keep /etc/ssl/certs/ca-certificates.crt"
echo "     if using Let's Encrypt / commercial certs."
echo ""
echo "  3. For production: replace these with Let's Encrypt certs:"
echo "     certbot certonly --standalone -d $DOMAIN"
echo "     Cert: /etc/letsencrypt/live/$DOMAIN/fullchain.pem"
echo "     Key:  /etc/letsencrypt/live/$DOMAIN/privkey.pem"

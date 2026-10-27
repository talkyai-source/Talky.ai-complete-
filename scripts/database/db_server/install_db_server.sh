#!/usr/bin/env bash
# Bootstrap a dedicated Talklee PostgreSQL host (Ubuntu 24.04). Run as root.
# Idempotent. Prints no secrets; it never handles database passwords.
#
# Required env:
#   LISTEN_ADDRESS   private IP of this host, e.g. 10.0.0.5
#   APP_ALLOW_CIDRS  comma-separated client CIDRs, e.g. 10.0.0.2/32,10.0.0.3/32
#   CERT_SRC KEY_SRC server certificate / key (PEM) already copied to this host
# Optional env:
#   CA_SRC           CA certificate to install (ssl_ca_file)
#   DB_NAME          default talkyai
#   PG_MAJOR         default 16
#   MEMORY_MB        default: detected from /proc/meminfo
#   ALLOW_REPLICATION=1  also allow talklee_replication (pitr.py base backups) from the CIDRs
#   ADMIN_ROLE       administrator allowed into the postgres maintenance database (cut-over)
set -Eeuo pipefail
trap 'echo "install_db_server: failed at line $LINENO" >&2' ERR

PG_MAJOR="${PG_MAJOR:-16}"
DB_NAME="${DB_NAME:-talkyai}"
PORT="${PORT:-5432}"
: "${LISTEN_ADDRESS:?set LISTEN_ADDRESS to this host's private IP}"
: "${APP_ALLOW_CIDRS:?set APP_ALLOW_CIDRS (comma-separated)}"
: "${CERT_SRC:?set CERT_SRC}"
: "${KEY_SRC:?set KEY_SRC}"
CA_SRC="${CA_SRC:-}"
MEMORY_MB="${MEMORY_MB:-$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_DIR="/etc/postgresql/${PG_MAJOR}/main"
SSL_DIR="/etc/postgresql/ssl"
WAL_DIR="/var/lib/postgresql/wal_archive"
STAMP="$(date +%Y%m%d%H%M%S)"

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
case "$LISTEN_ADDRESS" in 0.0.0.0|'*'|::|"") echo "refusing public/wildcard LISTEN_ADDRESS" >&2; exit 1;; esac

echo "== 1. PostgreSQL ${PG_MAJOR} from PGDG"
if ! dpkg -s "postgresql-${PG_MAJOR}" >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y curl ca-certificates ufw python3 postgresql-common
  /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
  apt-get install -y "postgresql-${PG_MAJOR}"
fi
# initdb with data checksums if the packaged cluster does not exist yet.
if ! pg_lsclusters -h | awk '{print $1" "$2}' | grep -qx "${PG_MAJOR} main"; then
  pg_createcluster "${PG_MAJOR}" main -- --data-checksums
fi

echo "== 2. TLS material"
install -d -o postgres -g postgres -m 0750 "$SSL_DIR"
install -o postgres -g postgres -m 0644 "$CERT_SRC" "$SSL_DIR/server.crt"
install -o postgres -g postgres -m 0600 "$KEY_SRC" "$SSL_DIR/server.key"
CA_ARGS=()
if [ -n "$CA_SRC" ]; then
  install -o postgres -g postgres -m 0644 "$CA_SRC" "$SSL_DIR/ca.crt"
  CA_ARGS=(--ssl-ca-file "$SSL_DIR/ca.crt")
fi

echo "== 3. WAL archive directory"
install -d -o postgres -g postgres -m 0700 "$WAL_DIR"

echo "== 4. Render and install configuration"
RENDER_DIR="$(mktemp -d)"
trap 'rm -rf "$RENDER_DIR"' EXIT
ALLOW_ARGS=()
IFS=',' read -r -a CIDRS <<< "$APP_ALLOW_CIDRS"
for cidr in "${CIDRS[@]}"; do ALLOW_ARGS+=(--allow "${cidr// /}"); done
EXTRA=()
[ "${ALLOW_REPLICATION:-0}" = "1" ] && EXTRA+=(--allow-replication)
[ -n "${ADMIN_ROLE:-}" ] && EXTRA+=(--admin-role "$ADMIN_ROLE")
python3 "$HERE/render_config.py" \
  --listen-address "$LISTEN_ADDRESS" "${ALLOW_ARGS[@]}" \
  --ssl-cert-file "$SSL_DIR/server.crt" --ssl-key-file "$SSL_DIR/server.key" "${CA_ARGS[@]}" \
  --database "$DB_NAME" --port "$PORT" --memory-mb "$MEMORY_MB" \
  --wal-archive-dir "$WAL_DIR" --admin-local-peer "${EXTRA[@]}" \
  --out-dir "$RENDER_DIR"

install -d -o postgres -g postgres -m 0755 "$CONF_DIR/conf.d"   # include_dir 'conf.d' is a stock Debian default
install -o postgres -g postgres -m 0644 "$RENDER_DIR/talklee.conf" "$CONF_DIR/conf.d/talklee.conf"
grep -Eq "^[[:space:]]*include_dir[[:space:]]*=[[:space:]]*'conf.d'" "$CONF_DIR/postgresql.conf" \
  || echo "include_dir = 'conf.d'" >> "$CONF_DIR/postgresql.conf"
if [ -f "$CONF_DIR/pg_hba.conf" ] && ! cmp -s "$CONF_DIR/pg_hba.conf" "$RENDER_DIR/pg_hba.conf"; then
  cp -p "$CONF_DIR/pg_hba.conf" "$CONF_DIR/pg_hba.conf.orig-${STAMP}"
fi
install -o postgres -g postgres -m 0640 "$RENDER_DIR/pg_hba.conf" "$CONF_DIR/pg_hba.conf"

echo "== 5. Firewall (ufw)"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
for cidr in "${CIDRS[@]}"; do ufw allow from "${cidr// /}" to any port "$PORT" proto tcp; done
ufw --force enable

echo "== 6. Restart and checks"
systemctl enable "postgresql@${PG_MAJOR}-main" >/dev/null 2>&1 || true
systemctl restart "postgresql@${PG_MAJOR}-main"
sleep 2
pg_lsclusters
ss -ltnp | grep ":${PORT} " || true
if ss -ltn | awk -v p=":${PORT}" '$4 ~ p"$" {print $4}' | grep -v "^${LISTEN_ADDRESS}:${PORT}$" | grep -q .; then
  echo "WARNING: port ${PORT} is listening on an address other than ${LISTEN_ADDRESS}" >&2
  exit 1
fi
echo "OK: ${PORT}/tcp bound to ${LISTEN_ADDRESS} only; original pg_hba.conf backed up as pg_hba.conf.orig-${STAMP} if it differed."

cat <<EOF

Next steps:
  1. Create roles (talklee_owner, talklee_app, talklee_backup) and the ${DB_NAME} database
     with scripts/database/roles.py, supplying passwords through its own secret input.
  2. Add a libpq service with sslmode=verify-full, sslrootcert=<CA> and host=<DB DNS name
     matching the certificate SAN> on each application host (see scripts/database/README.md).
  3. Load data with scripts/database/cutover.py (from a recovery.py backup bundle), then
     set role passwords with roles.py apply; see docs/production-readiness/database-separation.md.
  4. Re-run this script after changing APP_ALLOW_CIDRS; it is idempotent.
EOF

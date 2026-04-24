#!/usr/bin/env bash
# scripts/generate-secrets.sh
# ============================================================
# Talky.ai — Generate production .env with strong secrets
# Run once on your server: bash scripts/generate-secrets.sh
#
# This script:
#   1. Generates cryptographically strong random secrets
#   2. Creates /opt/talky/.env with correct permissions
#   3. Does NOT overwrite existing secrets (safe to re-run)
# ============================================================
set -euo pipefail

ENV_FILE="${1:-.env}"

GRN='\033[0;32m'
YLW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GRN}[INFO]${NC} $*"; }
warn()  { echo -e "${YLW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

gen_secret() {
  python3 -c "import secrets; print(secrets.token_hex($1))"
}

gen_password() {
  # URL-safe alphanumeric — safe for database passwords and URLs
  python3 -c "
import secrets, string
chars = string.ascii_letters + string.digits
print(''.join(secrets.choice(chars) for _ in range($1)))
"
}

if [[ -f "$ENV_FILE" ]]; then
  warn ".env already exists at $ENV_FILE"
  warn "Checking for empty required values only (won't overwrite existing secrets)..."
  EXISTING=true
else
  info "Creating new $ENV_FILE..."
  EXISTING=false
  cp .env.docker "$ENV_FILE"
fi

# Generate values
JWT_SECRET=$(gen_secret 32)           # 64-char hex
POSTGRES_PASSWORD=$(gen_password 24)  # 24-char alphanumeric
REDIS_PASSWORD=$(gen_password 24)     # 24-char alphanumeric
PGADMIN_PASSWORD=$(gen_password 16)   # 16-char alphanumeric

if [[ "$EXISTING" == "false" ]]; then
  # Fresh install — replace all placeholder values
  sed -i "s|^JWT_SECRET=.*|JWT_SECRET=${JWT_SECRET}|" "$ENV_FILE"
  sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${POSTGRES_PASSWORD}|" "$ENV_FILE"
  sed -i "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=${REDIS_PASSWORD}|" "$ENV_FILE"
  sed -i "s|^PGADMIN_PASSWORD=.*|PGADMIN_PASSWORD=${PGADMIN_PASSWORD}|" "$ENV_FILE"
  sed -i "s|REPLACE_WITH_POSTGRES_PASSWORD|${POSTGRES_PASSWORD}|" "$ENV_FILE"
  info "✅ Generated all secrets."
else
  # Existing install — only fill in empty values
  fill_if_empty() {
    local key="$1" val="$2"
    if grep -q "^${key}=$" "$ENV_FILE" 2>/dev/null; then
      sed -i "s|^${key}=$|${key}=${val}|" "$ENV_FILE"
      info "Filled empty: $key"
    fi
  }
  fill_if_empty "JWT_SECRET" "$JWT_SECRET"
  fill_if_empty "POSTGRES_PASSWORD" "$POSTGRES_PASSWORD"
  fill_if_empty "REDIS_PASSWORD" "$REDIS_PASSWORD"
  fill_if_empty "PGADMIN_PASSWORD" "$PGADMIN_PASSWORD"
fi

# Check for remaining placeholder values
if grep -q "please_change_me\|your_.*_here\|REPLACE_WITH" "$ENV_FILE"; then
  warn "⚠️  Placeholder values still found in $ENV_FILE:"
  grep -n "please_change_me\|your_.*_here\|REPLACE_WITH" "$ENV_FILE" || true
  echo ""
  warn "Fill in your API keys (Deepgram, Groq, Cartesia, Vonage) before starting."
fi

# Check for empty required vars
REQUIRED_KEYS=(
  DEEPGRAM_API_KEY GROQ_API_KEY CARTESIA_API_KEY
  VONAGE_API_KEY VONAGE_API_SECRET
  JWT_SECRET POSTGRES_PASSWORD REDIS_PASSWORD
  DATABASE_URL CORS_ORIGINS
)
MISSING=()
for key in "${REQUIRED_KEYS[@]}"; do
  val=$(grep "^${key}=" "$ENV_FILE" | cut -d'=' -f2- | tr -d '"' | tr -d "'")
  if [[ -z "$val" ]]; then
    MISSING+=("$key")
  fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
  warn "⚠️  These required variables are still empty in $ENV_FILE:"
  for k in "${MISSING[@]}"; do echo "    - $k"; done
  echo ""
  warn "Edit $ENV_FILE and fill in all required values before running docker compose up."
fi

# Set secure permissions
chmod 600 "$ENV_FILE"
info "Permissions set to 600 on $ENV_FILE"

echo ""
info "Secrets written to: $ENV_FILE"
info "Run 'docker compose config' to verify all values are loaded correctly."

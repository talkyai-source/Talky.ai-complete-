#!/bin/bash
# Add SIP subscriber with secure HA1 hash generation
# Supports MD5, SHA-256, and SHA-512-256 (RFC 8760)

set -euo pipefail

# Configuration
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-3306}"
DB_NAME="${DB_NAME:-opensips}"
DB_USER="${DB_USER:-opensips}"
DB_PASS="${DB_PASS:-opensipsrw}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

usage() {
    cat <<EOF
Usage: $0 -u USERNAME -d DOMAIN -p PASSWORD [-e EMAIL]

Add a SIP subscriber with secure digest authentication.

Options:
    -u USERNAME    SIP username (required)
    -d DOMAIN      SIP domain (required)
    -p PASSWORD    Password (required, min 12 characters)
    -e EMAIL       Email address (optional)
    -h             Show this help message

Examples:
    # Add subscriber with all algorithms
    $0 -u alice -d talky.local -p 'MySecurePass123!' -e [email protected]

    # Add subscriber without email
    $0 -u bob -d talky.local -p 'AnotherSecure456!'

Security:
    - Password must be at least 12 characters
    - Generates HA1 hashes for MD5, SHA-256, SHA-512-256
    - Never stores plaintext password in database
    - Uses RFC 8760 strengthened authentication

EOF
    exit 1
}

# Parse arguments
USERNAME=""
DOMAIN=""
PASSWORD=""
EMAIL=""

while getopts "u:d:p:e:h" opt; do
    case $opt in
        u) USERNAME="$OPTARG" ;;
        d) DOMAIN="$OPTARG" ;;
        p) PASSWORD="$OPTARG" ;;
        e) EMAIL="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done

# Validate required arguments
if [ -z "$USERNAME" ] || [ -z "$DOMAIN" ] || [ -z "$PASSWORD" ]; then
    echo -e "${RED}Error: Username, domain, and password are required${NC}"
    usage
fi

# Validate password strength
if [ ${#PASSWORD} -lt 12 ]; then
    echo -e "${RED}Error: Password must be at least 12 characters${NC}"
    exit 1
fi

# Check if mysql client is installed
if ! command -v mysql &> /dev/null; then
    echo -e "${RED}Error: mysql client not found. Please install mysql-client${NC}"
    exit 1
fi

echo "=== Adding SIP Subscriber ==="
echo "Username: $USERNAME"
echo "Domain:   $DOMAIN"
echo "Email:    ${EMAIL:-<not provided>}"
echo ""

# Calculate HA1 hashes
# HA1 = HASH(username:realm:password)
REALM="$DOMAIN"
AUTH_STRING="${USERNAME}:${REALM}:${PASSWORD}"

echo "Calculating HA1 hashes..."

# MD5 HA1 (legacy, but still widely supported)
HA1_MD5=$(echo -n "$AUTH_STRING" | md5sum | awk '{print $1}')
echo "  ✓ MD5 HA1 calculated"

# SHA-256 HA1 (RFC 8760)
HA1_SHA256=$(echo -n "$AUTH_STRING" | sha256sum | awk '{print $1}')
echo "  ✓ SHA-256 HA1 calculated"

# SHA-512-256 HA1 (RFC 8760 - truncated SHA-512)
# Note: sha512sum outputs 512 bits, we need to truncate to 256 bits (64 hex chars)
HA1_SHA512T256=$(echo -n "$AUTH_STRING" | sha512sum | awk '{print substr($1,1,64)}')
echo "  ✓ SHA-512-256 HA1 calculated"

# Prepare SQL query
SQL_QUERY="INSERT INTO subscriber (username, domain, password, ha1, ha1_sha256, ha1_sha512t256, email_address, datetime_created, datetime_modified)
VALUES (
    '${USERNAME}',
    '${DOMAIN}',
    '',
    '${HA1_MD5}',
    '${HA1_SHA256}',
    '${HA1_SHA512T256}',
    '${EMAIL}',
    NOW(),
    NOW()
)
ON DUPLICATE KEY UPDATE
    ha1 = '${HA1_MD5}',
    ha1_sha256 = '${HA1_SHA256}',
    ha1_sha512t256 = '${HA1_SHA512T256}',
    email_address = '${EMAIL}',
    datetime_modified = NOW();"

# Execute SQL
echo ""
echo "Inserting into database..."

if mysql -h"$DB_HOST" -P"$DB_PORT" -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" -e "$SQL_QUERY" 2>/dev/null; then
    echo -e "${GREEN}✓ Subscriber added successfully${NC}"
    echo ""
    echo "Subscriber Details:"
    echo "  SIP URI: sip:${USERNAME}@${DOMAIN}"
    echo "  Algorithms: MD5, SHA-256, SHA-512-256"
    echo ""
    echo "Test with:"
    echo "  sip:${USERNAME}@${DOMAIN}"
    echo "  Password: <your password>"
else
    echo -e "${RED}✗ Failed to add subscriber${NC}"
    echo "Check database connection and credentials"
    exit 1
fi

# Security reminder
echo ""
echo -e "${YELLOW}Security Reminders:${NC}"
echo "  • Password is NOT stored in plaintext"
echo "  • Only HA1 hashes are stored in database"
echo "  • Rotate credentials every 90 days"
echo "  • Monitor failed auth attempts"
echo ""

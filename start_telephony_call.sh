#!/usr/bin/env bash
# =============================================================================
# Talky.ai — Start Telephony Stack & Make a Call
# =============================================================================
#
# This script starts ALL required services and makes an outbound call to
# your softphone (extension 1002).
#
# Prerequisites:
#   - Docker and docker-compose installed
#   - .env file with API keys (DEEPGRAM_API_KEY, GROQ_API_KEY, etc.)
#   - Softphone registered to the PBX on 192.168.1.6:5060 as extension 1002
#
# Usage:
#   chmod +x start_telephony_call.sh
#   ./start_telephony_call.sh
# =============================================================================

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
TELEPHONY_DIR="${PROJECT_DIR}/telephony"
BACKEND_DIR="${PROJECT_DIR}/backend"
GATEWAY_DIR="${PROJECT_DIR}/services/voice-gateway-cpp"

log_step() { echo -e "${CYAN}[STEP]${NC} $1"; }
log_ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_err()  { echo -e "${RED}[ERROR]${NC} $1"; }

# =============================================================================
# STEP 1: Start Telephony Infrastructure (Asterisk, OpenSIPS, RTPEngine)
# =============================================================================
log_step "Starting telephony infrastructure (Asterisk, OpenSIPS, RTPEngine, FreeSWITCH)..."

cd "${TELEPHONY_DIR}/deploy/docker"

# Build and start. Use --build to pick up config changes.
docker compose -f docker-compose.telephony.yml up -d --build 2>&1 | tail -5

# Wait for Asterisk to be healthy
log_step "Waiting for Asterisk to become healthy..."
for i in $(seq 1 30); do
    if docker exec talky-asterisk asterisk -rx 'core show uptime seconds' >/dev/null 2>&1; then
        log_ok "Asterisk is healthy"
        break
    fi
    if [ "$i" -eq 30 ]; then
        log_err "Asterisk did not become healthy in 60s"
        exit 1
    fi
    sleep 2
done

# Reload dialplan to pick up our extensions.conf changes
log_step "Reloading Asterisk dialplan..."
docker exec talky-asterisk asterisk -rx "dialplan reload" 2>/dev/null || true
docker exec talky-asterisk asterisk -rx "module reload res_ari.so" 2>/dev/null || true
log_ok "Dialplan reloaded"

# Verify the Stasis app name
log_step "Verifying dialplan uses correct Stasis app name..."
STASIS_CHECK=$(docker exec talky-asterisk asterisk -rx "dialplan show from-opensips" 2>/dev/null | grep -c "talky_ai" || true)
if [ "$STASIS_CHECK" -ge 1 ]; then
    log_ok "Dialplan correctly uses Stasis(talky_ai)"
else
    log_warn "Dialplan might not have reloaded correctly - check extensions.conf"
fi

# Verify ARI user exists
log_step "Verifying ARI user 'talky' exists..."
ARI_CHECK=$(docker exec talky-asterisk asterisk -rx "ari show users" 2>/dev/null | grep -c "talky" || true)
if [ "$ARI_CHECK" -ge 1 ]; then
    log_ok "ARI user 'talky' is configured"
else
    log_warn "ARI user 'talky' not found - check ari.conf"
fi

# Check PJSIP registration to lan-pbx
log_step "Checking PJSIP endpoint status..."
docker exec talky-asterisk asterisk -rx "pjsip show endpoints" 2>/dev/null | head -20 || true

# =============================================================================
# STEP 2: Build & Start C++ Voice Gateway
# =============================================================================
log_step "Building and starting C++ Voice Gateway..."

cd "${GATEWAY_DIR}"

# Build if needed
if [ ! -f build/voice_gateway ]; then
    log_step "Building C++ Voice Gateway (first time)..."
    mkdir -p build
    cd build
    cmake .. -DCMAKE_BUILD_TYPE=Release
    make -j$(nproc)
    cd ..
fi

# Kill existing gateway
pkill -f "voice_gateway" 2>/dev/null || true
sleep 1

# Start gateway on port 18080
./build/voice_gateway --host 0.0.0.0 --port 18080 &
GATEWAY_PID=$!
log_ok "C++ Voice Gateway started (PID $GATEWAY_PID, port 18080)"

# Wait for gateway
sleep 2
if curl -sf http://127.0.0.1:18080/stats >/dev/null 2>&1; then
    log_ok "C++ Voice Gateway is responding"
else
    log_warn "C++ Voice Gateway may still be starting up"
fi

# =============================================================================
# STEP 3: Start Backend API Server
# =============================================================================
log_step "Starting Backend API server..."

cd "${BACKEND_DIR}"

# Activate virtual environment if it exists
if [ -f venv/bin/activate ]; then
    source venv/bin/activate
fi

# Kill existing backend
pkill -f "uvicorn app.main:app" 2>/dev/null || true
sleep 1

# Start backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --ws websockets --ws-ping-interval 30 --ws-ping-timeout 5 &
BACKEND_PID=$!
log_ok "Backend started (PID $BACKEND_PID, port 8000)"

# Wait for backend
log_step "Waiting for backend to be ready..."
for i in $(seq 1 20); do
    if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
        log_ok "Backend is healthy"
        break
    fi
    if [ "$i" -eq 20 ]; then
        log_err "Backend did not become healthy in 40s"
        exit 1
    fi
    sleep 2
done

# =============================================================================
# STEP 4: Connect Telephony Adapter
# =============================================================================
log_step "Connecting Asterisk telephony adapter..."

ADAPTER_RESP=$(curl -sf -X POST "http://127.0.0.1:8000/api/v1/sip/telephony/start?adapter_type=asterisk" 2>&1 || true)
echo "  Adapter response: ${ADAPTER_RESP}"

if echo "${ADAPTER_RESP}" | grep -q '"connected"\|"already_connected"'; then
    log_ok "Telephony adapter connected"
else
    log_err "Failed to connect telephony adapter"
    echo "  Response: ${ADAPTER_RESP}"
    echo ""
    log_warn "Troubleshooting:"
    echo "  1. Check Asterisk ARI is reachable: curl http://127.0.0.1:8088/ari/asterisk/info -u talky:talky_local_only_change_me"
    echo "  2. Check C++ Gateway: curl http://127.0.0.1:18080/stats"
    echo "  3. Check backend logs for errors"
    exit 1
fi

# =============================================================================
# STEP 5: Verify Telephony Status
# =============================================================================
log_step "Checking telephony status..."
STATUS=$(curl -sf "http://127.0.0.1:8000/api/v1/sip/telephony/status" 2>&1 || true)
echo "  Status: ${STATUS}"

# =============================================================================
# STEP 6: MAKE THE CALL!
# =============================================================================
echo ""
echo "=========================================="
echo -e "${GREEN}  ALL SYSTEMS GO — MAKING THE CALL!${NC}"
echo "=========================================="
echo ""

log_step "Calling extension 1002 (your softphone)..."
CALL_RESP=$(curl -sf -X POST "http://127.0.0.1:8000/api/v1/sip/telephony/call?destination=1002&caller_id=1001" 2>&1 || true)
echo "  Call response: ${CALL_RESP}"

if echo "${CALL_RESP}" | grep -q '"calling"'; then
    echo ""
    log_ok "CALL ORIGINATED SUCCESSFULLY!"
    echo ""
    echo -e "${GREEN}Your softphone should be ringing now!${NC}"
    echo ""
    echo "When you answer:"
    echo "  1. The AI will pick up your voice (STT → Deepgram)"
    echo "  2. Process what you say (LLM → Groq)"  
    echo "  3. Respond naturally (TTS → Google/Deepgram)"
    echo "  4. You'll hear the AI's response through your phone"
    echo ""
    echo "To monitor the call:"
    echo "  curl http://127.0.0.1:8000/api/v1/sip/telephony/status"
    echo "  curl http://127.0.0.1:18080/stats"
    echo ""
else
    log_err "Call origination failed"
    echo "  Response: ${CALL_RESP}"
fi

echo ""
log_step "Press Ctrl+C to stop all services when done"

# Wait for Ctrl+C
wait

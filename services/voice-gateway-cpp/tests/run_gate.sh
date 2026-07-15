#!/usr/bin/env bash
#
# Reproducible build + test + sanitizer gate for the C++ voice gateway.
#
# The production server has no cmake, so this mirrors the CMake/CTest targets
# with direct g++ invocations and the same flags. Every compile requires ZERO
# warnings (-Wall -Wextra -Wpedantic; build fails if the compiler prints
# anything). Every test run is wrapped in `timeout` so a re-introduced deadlock
# fails the gate cleanly instead of blocking forever (the process-level timeout
# the CTest TIMEOUT properties provide elsewhere).
#
# Usage:  bash tests/run_gate.sh
# Env:    CXX (default g++), GATE_TIMEOUT seconds per test run (default 300).
#
set -uo pipefail

cd "$(dirname "$0")/.."  # -> services/voice-gateway-cpp

CXX=${CXX:-g++}
STD="-std=c++20"
WARN="-Wall -Wextra -Wpedantic"
INC="-Iinclude"
LIB="src/rtp_packet.cpp src/session.cpp src/session_registry.cpp src/http_server.cpp"
GATE_TIMEOUT="${GATE_TIMEOUT:-300}"
OUT="$(mktemp -d)"
fail=0

cleanup() { rm -rf "$OUT"; }
trap cleanup EXIT

# build <name> <outfile> <compile-args...>   — 0 warnings required.
build() {
    local name="$1"; shift
    local out="$1"; shift
    local log="$OUT/build_${name}.log"
    echo "== build:${name} =="
    if $CXX $STD $WARN $INC "$@" -o "$out" -pthread 2>"$log" && [ ! -s "$log" ]; then
        echo "  [OK]"
    else
        echo "  [FAIL] build:${name}"
        sed 's/^/    /' "$log"
        fail=1
    fi
}

# run <name> <cmd...>   — timeout-wrapped; nonzero (incl. 124 timeout) fails.
run() {
    local name="$1"; shift
    echo "== run:${name} =="
    if timeout "$GATE_TIMEOUT" "$@"; then
        echo "  [OK]"
    else
        local rc=$?
        [ "$rc" -eq 124 ] && echo "  [FAIL] run:${name} TIMED OUT (possible deadlock)" \
                          || echo "  [FAIL] run:${name} exit=${rc}"
        fail=1
    fi
}

SAN_TSAN="-O1 -g -fsanitize=thread -fno-omit-frame-pointer"
SAN_ASAN="-O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer"

# ---- compile (warnings gate) ----
build gateway-O2  "$OUT/voice_gateway" -O2 $LIB src/main.cpp
build unit-O2     "$OUT/unit"          -O2 $LIB tests/test_voice_gateway.cpp
build conc-O2     "$OUT/conc"          -O2 $LIB tests/test_concurrency.cpp
build fixes-O2    "$OUT/fixes"         -O2 $LIB tests/test_gateway_fixes.cpp

# ---- run at -O2 ----
run unit-O2   "$OUT/unit"
run conc-O2   "$OUT/conc"
run fixes-O2  "$OUT/fixes"

# ---- ThreadSanitizer (setarch -R disables ASLR for TSan on newer kernels) ----
build conc-tsan  "$OUT/conc_tsan"  $SAN_TSAN $LIB tests/test_concurrency.cpp
build fixes-tsan "$OUT/fixes_tsan" $SAN_TSAN $LIB tests/test_gateway_fixes.cpp
run conc-tsan  setarch -R env TSAN_OPTIONS="halt_on_error=1" "$OUT/conc_tsan"
run fixes-tsan setarch -R env TSAN_OPTIONS="halt_on_error=1" "$OUT/fixes_tsan"

# ---- AddressSanitizer + UBSan ----
build conc-asan  "$OUT/conc_asan"  $SAN_ASAN $LIB tests/test_concurrency.cpp
build fixes-asan "$OUT/fixes_asan" $SAN_ASAN $LIB tests/test_gateway_fixes.cpp
run conc-asan  env ASAN_OPTIONS="abort_on_error=1 detect_leaks=1" UBSAN_OPTIONS="halt_on_error=1 print_stacktrace=1" "$OUT/conc_asan"
run fixes-asan env ASAN_OPTIONS="abort_on_error=1 detect_leaks=1" UBSAN_OPTIONS="halt_on_error=1 print_stacktrace=1" "$OUT/fixes_asan"

echo "======================================"
if [ "$fail" -eq 0 ]; then
    echo "GATE: PASS"
    exit 0
else
    echo "GATE: FAIL"
    exit 1
fi

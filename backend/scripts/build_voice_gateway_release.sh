#!/usr/bin/env bash
# Build and test one voice-gateway candidate outside the git checkout.
# Usage: bash backend/scripts/build_voice_gateway_release.sh /absolute/output/path

set -Eeuo pipefail

if [[ "$#" -ne 1 || "$1" != /* ]]; then
  echo "usage: $0 /absolute/output/path" >&2
  exit 2
fi

output_path="$1"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
source_dir="${repo_root}/services/voice-gateway-cpp"
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/talky-voice-gateway-build.XXXXXX")"
build_sha="${VOICE_GATEWAY_BUILD_SHA:-$(git -C "${repo_root}" rev-parse HEAD)}"

cleanup() {
  rm -rf -- "${build_dir}"
}
trap cleanup EXIT

test -f "${source_dir}/CMakeLists.txt"
command -v timeout >/dev/null 2>&1
if [[ ! "${build_sha}" =~ ^[0-9a-f]{40,64}$ ]]; then
  echo "VOICE_GATEWAY_BUILD_SHA must resolve to a full lowercase commit SHA" >&2
  exit 2
fi
cmake -S "${source_dir}" -B "${build_dir}" \
  -DCMAKE_BUILD_TYPE=Release \
  -DVOICE_GATEWAY_BUILD_SHA="${build_sha}"
cmake --build "${build_dir}" --parallel
ctest --test-dir "${build_dir}" --output-on-failure
test -x "${build_dir}/voice_gateway"

# A release binary must fail before binding when the callback credential is
# absent. This is intentionally a negative startup proof; it makes an old
# fail-open executable impossible to publish through this helper.
set +e
timeout 5s env -u INTERNAL_SERVICE_TOKEN "${build_dir}/voice_gateway" \
  --host 127.0.0.1 --port 18080 >/dev/null 2>&1
missing_token_rc=$?
set -e
if [[ "${missing_token_rc}" -ne 2 ]]; then
  echo "voice gateway did not fail closed on missing INTERNAL_SERVICE_TOKEN" >&2
  exit 1
fi

install -m 0750 "${build_dir}/voice_gateway" "${output_path}"
test -x "${output_path}"

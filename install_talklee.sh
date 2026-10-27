#!/usr/bin/env bash
# Fresh Ubuntu host installer. The default command only prints a plan.
set -Eeuo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Python 3 is required (included in the supported Ubuntu 24.04 server image).' >&2
  exit 2
fi
exec python3 "${script_dir}/scripts/installation/install.py" "$@"

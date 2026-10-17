#!/usr/bin/env bash
# Compatibility entrypoint: use the canonical Hero Shorts publishing route.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
exec bash "$SCRIPT_DIR/gen/run_zernio_publish.sh" "$@"

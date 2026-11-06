#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

if ! command -v uv >/dev/null 2>&1; then
    echo "error: uv is required to build kitup" >&2
    exit 1
fi

exec "${PYTHON:-python3}" tools/zipapp/build.py

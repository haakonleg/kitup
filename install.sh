#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

./build.sh

install -d -m 755 "$HOME/.local/bin"
install -m 755 dist/kitup "$HOME/.local/bin/kitup"

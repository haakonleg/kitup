#!/usr/bin/env bash
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
image=localhost/kitup-integration:44
container=kitup-integration-$$

cd -- "$root"

echo "==> building dist/kitup"
python3 tools/zipapp/build.py

echo "==> building $image"
podman build -t "$image" -f tests/integration/Containerfile .

echo "==> starting $container"
podman run -d --rm \
    --name "$container" \
    --systemd=always \
    --cap-drop=ALL \
    --cap-add=CHOWN \
    --cap-add=DAC_OVERRIDE \
    --cap-add=FOWNER \
    --cap-add=FSETID \
    --cap-add=KILL \
    --cap-add=SETGID \
    --cap-add=SETPCAP \
    --cap-add=SETUID \
    --network=none \
    --memory=2g \
    --memory-swap=2g \
    --cpus=2 \
    --pids-limit=512 \
    --ulimit=nofile=4096:4096 \
    --tmpfs=/tmp:rw,size=256m \
    -v "$root:/kitup-integration-test:Z,ro" \
    "$image" >/dev/null

trap 'podman rm -f "$container" >/dev/null 2>&1 || true' EXIT

# systemd needs a moment before systemctl can reach it.
for _ in $(seq 1 60); do
    if podman exec "$container" systemctl is-system-running 2>/dev/null |
        grep -Eq 'running|degraded'; then
        break
    fi
    sleep 0.5
done

echo "==> running integration tests"
podman exec \
    --workdir /kitup-integration-test \
    "$container" \
    runuser -u kitup -- \
    env \
        HOME=/home/kitup \
        XDG_RUNTIME_DIR=/home/kitup/.runtime \
        PYTHONPATH=/kitup-integration-test \
        PYTHONDONTWRITEBYTECODE=1 \
        KITUP_INTEGRATION=1 \
    dbus-run-session -- \
    python3 -m pytest -p no:cacheprovider tests/integration -v

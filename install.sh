#!/usr/bin/env bash
# Arahort — arahort.pro, alex@arahort.pro
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo 'Запусти установщик: sudo bash install.sh' >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo 'Нужен системный Python 3.10.12+ из Ubuntu Server 22.04 / 24.04 / 26.04 LTS.' >&2
  exit 1
fi
exec python3 "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/ops/install.py" "$@"

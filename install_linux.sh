#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
EXTRA=""
if [[ "${1:-}" == "--dev" ]]; then
  EXTRA="[dev]"
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python executable not found: $PYTHON_BIN" >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 11):
    raise SystemExit("ArchaeoForge requires Python 3.11 or newer.")
PY

"$PYTHON_BIN" -m venv "$ROOT/.venv"
# shellcheck disable=SC1091
source "$ROOT/.venv/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install --no-build-isolation -e "$ROOT$EXTRA"

echo
echo "ArchaeoForge installed in $ROOT/.venv"
echo "Activate it with: source $ROOT/.venv/bin/activate"

echo
if command -v blender >/dev/null 2>&1; then
  echo "Blender: $(command -v blender)"
else
  echo "Blender: not found. Scene build and render commands will be skipped or fail until installed."
fi
if command -v gdal_translate >/dev/null 2>&1 && command -v gdalwarp >/dev/null 2>&1; then
  echo "GDAL: available"
else
  echo "GDAL: not found. Install gdal-bin or QGIS for georeferencing execution."
fi

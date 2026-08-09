#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -x "$ROOT/.venv/bin/archaeoforge" ]]; then
  CLI="$ROOT/.venv/bin/archaeoforge"
elif command -v archaeoforge >/dev/null 2>&1; then
  CLI="$(command -v archaeoforge)"
else
  export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
  CLI="python3 -m archaeoforge.cli"
fi

# Word splitting is intentional for the Python module fallback.
# shellcheck disable=SC2086
$CLI run "$ROOT/projects/babylon_570_bce" --preview --skip-ai "$@"

echo
echo "Evidence report: $ROOT/projects/babylon_570_bce/outputs/reports/index.html"
echo "ChatGPT handoff: $ROOT/projects/babylon_570_bce/outputs/exports/chatgpt_handoff.json"

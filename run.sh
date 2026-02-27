#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/venv"

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <path/to/file.FCStd> [-o output.md]"
    exit 1
fi

if [[ ! -d "$VENV" ]]; then
    echo "Error: venv not found. Run ./init.sh first."
    exit 1
fi

source "$VENV/bin/activate"
python "$SCRIPT_DIR/main.py" "$@"

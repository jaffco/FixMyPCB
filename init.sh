#!/usr/bin/env bash
set -euo pipefail

FREECAD_PYTHON="${FREECAD_PYTHON:-/Applications/FreeCAD.app/Contents/Resources/bin/python}"

if [[ ! -x "$FREECAD_PYTHON" ]]; then
    echo "Error: FreeCAD Python not found at $FREECAD_PYTHON"
    echo "Install FreeCAD or set FREECAD_PYTHON to its bundled python executable."
    exit 1
fi

echo "Using Python: $FREECAD_PYTHON"
echo "Creating venv with system site-packages (includes FreeCAD modules)..."
"$FREECAD_PYTHON" -m venv --system-site-packages venv

echo ""
echo "Done. Activate with:"
echo "  source venv/bin/activate"
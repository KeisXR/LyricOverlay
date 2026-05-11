#!/usr/bin/env bash
# Lyricaod setup script
# Creates a Python venv and installs dependencies.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"

echo "==> Checking system dependencies..."
MISSING=""
python3 -c "import PySide6" 2>/dev/null || MISSING="$MISSING pyside6"
python3 -c "import dbus"     2>/dev/null || MISSING="$MISSING python-dbus"
python3 -c "import gi; gi.require_version('GLib', '2.0')" 2>/dev/null || MISSING="$MISSING python-gobject"

if [ -n "$MISSING" ]; then
    echo ""
    echo "  Missing system packages: $MISSING"
    echo "  Install with:"
    echo "    sudo pacman -S pyside6 python-dbus python-gobject python-httpx"
    echo ""
    echo "  Or use pip in a venv (slower, ~200MB wheel download):"
    echo "    python3 -m venv ${VENV_DIR}"
    echo "    ${VENV_DIR}/bin/pip install -r ${SCRIPT_DIR}/requirements.txt"
    echo ""
fi

# Check if PySide6 is importable (either system-wide or in our venv)
if python3 -c "import PySide6" 2>/dev/null; then
    echo "==> PySide6 found (system-wide). Creating venv with --system-site-packages..."
    python3 -m venv --system-site-packages "${VENV_DIR}"
    echo "==> Installing remaining pip deps..."
    "${VENV_DIR}/bin/pip" install httpx dbus-python
elif [ -f "${VENV_DIR}/bin/python" ]; then
    echo "==> Using existing venv..."
    echo "==> Installing deps..."
    "${VENV_DIR}/bin/pip" install --upgrade pip setuptools wheel
    "${VENV_DIR}/bin/pip" install -r "${SCRIPT_DIR}/requirements.txt"
else
    echo "==> Creating fresh venv..."
    python3 -m venv "${VENV_DIR}"
    echo "==> Installing deps (PySide6 is ~200MB, this will take a while)..."
    "${VENV_DIR}/bin/pip" install --upgrade pip setuptools wheel
    "${VENV_DIR}/bin/pip" install -r "${SCRIPT_DIR}/requirements.txt"
fi

echo ""
echo "==> Setup complete!"
echo "   Run:  ${VENV_DIR}/bin/python ${SCRIPT_DIR}/src/main.py"
echo "   Or:   source ${VENV_DIR}/bin/activate && python src/main.py"

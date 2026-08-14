#!/usr/bin/env bash
# Lyricaod Linux package builder.
# Creates dist/Lyricaod/Lyricaod with the locked Python toolchain.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_VENV="${PROJECT_DIR}/.build-venv"
WORK_DIR="${PROJECT_DIR}/.build-work"
PACKAGE_DIR="${PROJECT_DIR}/dist/Lyricaod"

echo "==> Checking Python..."
python3 --version

if ! python3 -c "import dbus, gi; gi.require_version('GLib', '2.0')" 2>/dev/null; then
    echo ""
    echo "Missing Linux system packages for MPRIS/D-Bus. Install them first, for example:"
    echo "  Arch:   sudo pacman -S python-dbus python-gobject"
    echo "  Ubuntu: sudo apt install python3-dbus python3-gi gir1.2-glib-2.0"
    echo ""
    exit 1
fi

if [ ! -d "${BUILD_VENV}" ]; then
    echo "==> Creating build virtual environment..."
    python3 -m venv --system-site-packages "${BUILD_VENV}"
fi

echo "==> Installing locked build dependencies..."
"${BUILD_VENV}/bin/python" -m pip install \
    --disable-pip-version-check \
    "pip==26.1.1"
"${BUILD_VENV}/bin/python" -m pip install \
    --disable-pip-version-check \
    -r "${PROJECT_DIR}/requirements-build-lock.txt"

echo "==> Building Linux executable..."
"${BUILD_VENV}/bin/python" -m PyInstaller \
    --clean \
    --noconfirm \
    --distpath "${PROJECT_DIR}/dist" \
    --workpath "${WORK_DIR}" \
    "${PROJECT_DIR}/packaging/lyricaod.spec"

if [ -d "${PROJECT_DIR}/extension" ]; then
    echo "==> Copying browser extension files..."
    rm -rf "${PACKAGE_DIR}/extension"
    cp -R "${PROJECT_DIR}/extension" "${PACKAGE_DIR}/extension"
fi

chmod +x "${PACKAGE_DIR}/Lyricaod"

echo "==> Generating package checksums..."
"${BUILD_VENV}/bin/python" "${PROJECT_DIR}/scripts/write_checksums.py" \
    generate "${PACKAGE_DIR}"

echo "==> Removing intermediate build files..."
rm -rf "${PROJECT_DIR}/build" "${WORK_DIR}"

echo ""
echo "==> Package ready: ${PACKAGE_DIR}"
echo "    Run: ${PACKAGE_DIR}/Lyricaod"

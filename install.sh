#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Scott Klein
# Install muesli for the current user: a launcher on PATH and an app menu entry.
#   ./install.sh              install
#   ./install.sh --autostart  also start muesli at login, as just the tray dot
#   ./install.sh --uninstall  remove everything this script installed
# Needs (Debian): python3-pyside6.qtwidgets python3-pyside6.qtnetwork
#                 ffmpeg pulseaudio-utils (pactl, parecord)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
BIN="$HOME/.local/bin/muesli"
DESKTOP="$HOME/.local/share/applications/muesli.desktop"
ICON="$HOME/.local/share/icons/hicolor/scalable/apps/muesli.svg"
AUTOSTART="$HOME/.config/autostart/muesli.desktop"

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$BIN" "$DESKTOP" "$ICON" "$AUTOSTART"
  echo "muesli uninstalled. Recordings in ~/Meetings are untouched."
  exit 0
fi

for tool in python3 ffmpeg pactl parecord; do
  command -v "$tool" >/dev/null || { echo "Missing: $tool" >&2; exit 1; }
done
python3 -c "import PySide6.QtWidgets, PySide6.QtNetwork" 2>/dev/null || {
  echo "Missing PySide6. On Debian: sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtnetwork" >&2
  exit 1
}

mkdir -p "$(dirname "$BIN")" "$(dirname "$DESKTOP")" "$(dirname "$ICON")"
ln -sf "$HERE/bin/muesli" "$BIN"
cp "$HERE/data/muesli.svg" "$ICON"
cat > "$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Name=Muesli
Comment=Record both sides of a call
Exec=$BIN
Icon=muesli
Categories=AudioVideo;Audio;Recorder;
StartupNotify=false
DESK
if [ "${1:-}" = "--autostart" ]; then
  mkdir -p "$(dirname "$AUTOSTART")"
  sed 's|^Exec=.*|& --hidden|' "$DESKTOP" > "$AUTOSTART"
  echo "muesli will start at login."
fi
echo "Installed. Start it from the app menu, or run: muesli"

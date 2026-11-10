#!/bin/bash
# Double-click on macOS to launch the Dragon Curve numbered menu.
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  python3 dragon_curve.py --menu
else
  echo 'Python 3.10 or newer is required. Install Python 3 and try again.'
fi
printf '\nPress Enter to close this window...'
read -r _ || true

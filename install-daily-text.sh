#!/bin/bash
# Schedule the daily deadline text with launchd.
#
# launchd rather than cron because it survives reboots, and rather than an
# in-app scheduler because it runs whether or not any app happens to be open.
# If the Mac is asleep at 08:00, launchd fires the job when it next wakes.
#
#   ./install-daily-text.sh            install, 08:00 daily
#   ./install-daily-text.sh 7 30       install at 07:30
#   ./install-daily-text.sh --remove   uninstall
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LABEL="com.saanvi.deadline-text"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed. No more daily texts."
  exit 0
fi

HOUR="${1:-8}"
MINUTE="${2:-0}"
PY="$(command -v python3)"

if [ ! -f "$HERE/notify.yml" ]; then
  echo "Run 'python3 notify.py' once first — it writes notify.yml for you to fill in."
  exit 1
fi
if ! grep -qE '^(to|ntfy_topic): *"?[^"[:space:]]' "$HERE/notify.yml"; then
  echo "notify.yml has no destination yet. Set 'to:' (your number) or 'ntfy_topic:'."
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$HERE/notify.py</string>
    <string>--send</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MINUTE</integer>
  </dict>
  <key>StandardOutPath</key><string>$HERE/notify.log</string>
  <key>StandardErrorPath</key><string>$HERE/notify.log</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

printf 'Installed. A digest goes out daily at %02d:%02d.\n' "$HOUR" "$MINUTE"
echo
echo "  Test it now:    launchctl kickstart -k gui/$(id -u)/$LABEL"
echo "  Watch the log:  tail -f $HERE/notify.log"
echo "  Remove it:      ./install-daily-text.sh --remove"
echo
echo "First run only: macOS will ask permission for Messages. Approve it, or the"
echo "job will log a permission error instead of texting."

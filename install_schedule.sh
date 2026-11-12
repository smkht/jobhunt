#!/bin/bash
# Optional: installs two macOS LaunchAgents: the <=2-day fast lane at 08:03 and 15:03
# daily, and the broader review-only lane every Sunday at 10:03.
# Uninstall: ./install_schedule.sh remove
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"; DATA="${JOBHUNT_DATA:-$DIR/data}"; PLIST="$HOME/Library/LaunchAgents/com.jobhunt.fast.plist"; BG_PLIST="$HOME/Library/LaunchAgents/com.jobhunt.background.plist"; PY="$(command -v python3)"
if [ "$1" = "remove" ]; then launchctl unload "$PLIST" 2>/dev/null; launchctl unload "$BG_PLIST" 2>/dev/null; rm -f "$PLIST" "$BG_PLIST"; echo "Schedules removed."; exit 0; fi
mkdir -p "$DATA/out" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.jobhunt.fast</string>
<key>ProgramArguments</key><array><string>$PY</string><string>$DIR/daily.py</string></array>
<key>WorkingDirectory</key><string>$DIR</string>
<key>EnvironmentVariables</key><dict><key>JOBHUNT_DATA</key><string>$DATA</string><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin</string></dict>
<key>StartCalendarInterval</key><array>
<dict><key>Hour</key><integer>8</integer><key>Minute</key><integer>3</integer></dict>
<dict><key>Hour</key><integer>15</integer><key>Minute</key><integer>3</integer></dict></array>
<key>StandardOutPath</key><string>$DATA/out/schedule.log</string>
<key>StandardErrorPath</key><string>$DATA/out/schedule.log</string>
</dict></plist>
PL
launchctl unload "$PLIST" 2>/dev/null; launchctl load "$PLIST" && echo "Scheduled: daily at 08:03 and 15:03 (runs when the Mac is awake; missed runs fire on wake). Log: data/out/schedule.log"
cat > "$BG_PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.jobhunt.background</string>
<key>ProgramArguments</key><array><string>$PY</string><string>$DIR/daily.py</string><string>--background</string></array>
<key>WorkingDirectory</key><string>$DIR</string>
<key>EnvironmentVariables</key><dict><key>JOBHUNT_DATA</key><string>$DATA</string><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin</string></dict>
<key>StartCalendarInterval</key><dict><key>Weekday</key><integer>0</integer><key>Hour</key><integer>10</integer><key>Minute</key><integer>3</integer></dict>
<key>StandardOutPath</key><string>$DATA/out/background-schedule.log</string>
<key>StandardErrorPath</key><string>$DATA/out/background-schedule.log</string>
</dict></plist>
PL
launchctl unload "$BG_PLIST" 2>/dev/null; launchctl load "$BG_PLIST" && echo "Scheduled: broad review-only scan Sundays at 10:03. Log: data/out/background-schedule.log"

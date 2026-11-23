#!/bin/zsh
# Installs the PolyPicker schedule as launchd agents (macOS, times in this Mac's local time zone):
#   com.polypicker.run-all   06:00 and 13:00 daily          -> polypicker run-all
#   com.polypicker.refresh   every 30 min, 17:00 to 22:30   -> polypicker refresh
#   com.polypicker.scores    every 3 minutes                -> polypicker scores (exits fast if no games are on)
# A run missed while the Mac slept happens once on wake. Logs: ~/Library/Logs/PolyPicker/
set -eu
HERE="${0:A:h}"
RUN="$HERE/run-job.sh"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$HOME/Library/Logs/PolyPicker"
chmod +x "$RUN"

times_xml() {  # args: "H:M" ...
  echo "  <key>StartCalendarInterval</key>"; echo "  <array>"
  for t in "$@"; do echo "    <dict><key>Hour</key><integer>${t%%:*}</integer><key>Minute</key><integer>${t##*:}</integer></dict>"; done
  echo "  </array>"
}

write_agent() {  # label job schedule-xml
  local label=$1 job=$2 sched=$3 plist="$AGENTS/$1.plist"
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array><string>/bin/zsh</string><string>$RUN</string><string>$job</string></array>
$sched
  <key>StandardOutPath</key><string>$HOME/Library/Logs/PolyPicker/launchd-$job.out</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/PolyPicker/launchd-$job.err</string>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
</dict>
</plist>
PLIST
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
  echo "installed $label"
}

write_agent com.polypicker.run-all run-all "$(times_xml 6:0 13:0)"
write_agent com.polypicker.refresh refresh "$(times_xml 17:0 17:30 18:0 18:30 19:0 19:30 20:0 20:30 21:0 21:30 22:0 22:30)"
write_agent com.polypicker.scores scores "  <key>StartInterval</key><integer>180</integer>"
echo "Schedule active. Check with: launchctl list | grep polypicker"

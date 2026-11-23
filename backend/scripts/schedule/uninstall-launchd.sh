#!/bin/zsh
# Removes the PolyPicker launchd agents (logs in ~/Library/Logs/PolyPicker are kept).
for label in com.polypicker.run-all com.polypicker.refresh com.polypicker.scores; do
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null && echo "stopped $label"
  rm -f "$HOME/Library/LaunchAgents/$label.plist"
done

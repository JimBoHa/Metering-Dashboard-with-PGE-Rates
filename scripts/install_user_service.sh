#!/usr/bin/env bash
# Install only this application's user service; never modify any other service.
set -euo pipefail
METERING_CHECKOUT="$(cd "$(dirname "$0")/.." && pwd)"
METERING_TARGET="$HOME/metering-dashboard"
if [[ "$METERING_CHECKOUT" != "$METERING_TARGET" ]]; then
  echo "Clone this project into ~/metering-dashboard before running this installer."
  exit 1
fi
python3 -m venv "$METERING_TARGET/.venv"
"$METERING_TARGET/.venv/bin/python" -m pip install -r "$METERING_TARGET/requirements.lock"
"$METERING_TARGET/.venv/bin/python" -m pip install --no-deps -e "$METERING_TARGET"
install -d -m 0700 "$HOME/.local/share/metering-dashboard"
install -d "$HOME/.config/systemd/user"
install -m 0644 "$METERING_TARGET/deploy/metering-dashboard.service" "$HOME/.config/systemd/user/metering-dashboard.service"
systemctl --user daemon-reload
systemctl --user enable --now metering-dashboard.service
systemctl --user --no-pager status metering-dashboard.service
printf '\nInitial access is stored privately at: %s\n' "$HOME/.local/share/metering-dashboard/initial-access.txt"

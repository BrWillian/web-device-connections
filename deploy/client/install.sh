#!/usr/bin/env bash
#
# Install (or upgrade) the device agent on a device.
#
#   sudo ./deploy/client/install.sh
#   sudo WDC_USER=operator ./deploy/client/install.sh
#
# Idempotent: re-run it to upgrade. The environment file at
# /etc/wdc-client/client.env is written once and never overwritten, so an
# upgrade never clobbers a device's token.
set -euo pipefail

INSTALL_DIR=${INSTALL_DIR:-/opt/wdc-client}
CONFIG_DIR=${CONFIG_DIR:-/etc/wdc-client}
CONFIG_FILE="$CONFIG_DIR/client.env"
UNIT_FILE=/etc/systemd/system/wdc-client.service

REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
CLIENT_DIR="$REPO_DIR/client"

# Runs as the user whose shell operators will get. SUDO_USER is the person who
# invoked sudo, which is nearly always the right answer on a device.
WDC_USER=${WDC_USER:-${SUDO_USER:-root}}

die() { echo "error: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run this with sudo"
[[ -d "$CLIENT_DIR/wdc_client" ]] || die "cannot find $CLIENT_DIR/wdc_client"
id "$WDC_USER" >/dev/null 2>&1 || die "user '$WDC_USER' does not exist"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

echo "==> Installing to $INSTALL_DIR for user $WDC_USER"
install -d -m 0755 "$INSTALL_DIR"

# The package is replaced wholesale so a file removed upstream does not linger
# in the installed copy and get imported instead of its replacement.
rm -rf "$INSTALL_DIR/wdc_client"
cp -r "$CLIENT_DIR/wdc_client" "$INSTALL_DIR/wdc_client"
cp "$CLIENT_DIR/requirements.txt" "$INSTALL_DIR/requirements.txt"

echo "==> Setting up the virtualenv"
[[ -x "$INSTALL_DIR/venv/bin/python" ]] || python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

echo "==> Configuration"
install -d -m 0750 "$CONFIG_DIR"
if [[ -f "$CONFIG_FILE" ]]; then
  echo "    keeping the existing $CONFIG_FILE"
else
  cp "$CLIENT_DIR/.env.example" "$CONFIG_FILE"
  echo "    wrote $CONFIG_FILE — set DEVICE_ID, DEVICE_TOKEN and SERVER_URL before starting"
fi
# The token lives in here, so it is not world readable.
chown root:"$(id -gn "$WDC_USER")" "$CONFIG_FILE"
chmod 0640 "$CONFIG_FILE"
chown -R "$WDC_USER":"$(id -gn "$WDC_USER")" "$INSTALL_DIR"

# Devices provisioned before the project was renamed still carry a wrc-client
# unit, the old name, pointing at /opt/wrc-client.
# Left alone the two agents would both connect as the same device id and fight
# over every session, so say so — loudly, but without deleting anything.
if systemctl list-unit-files 2>/dev/null | grep -q '^wrc-client\.service'; then
  cat >&2 <<'EOF'

WARNING: an old wrc-client unit is still installed on this device.
Remove it before starting the new one, or both agents will connect at once:

  sudo systemctl disable --now wrc-client
  sudo rm /etc/systemd/system/wrc-client.service
  sudo rm -rf /opt/wrc-client /etc/wrc-client
  sudo systemctl daemon-reload

EOF
fi

echo "==> Installing the systemd unit"
sed "s|__WDC_USER__|$WDC_USER|" "$REPO_DIR/deploy/client/wdc-client.service" > "$UNIT_FILE"
chmod 0644 "$UNIT_FILE"
systemctl daemon-reload
systemctl enable wdc-client >/dev/null

if grep -q '^DEVICE_TOKEN=$' "$CONFIG_FILE"; then
  cat <<EOF

Installed, but not started: $CONFIG_FILE has no DEVICE_TOKEN yet.

  1. On the relay:  python -m tools.mint_device_token <device-id>
  2. Edit $CONFIG_FILE
  3. sudo systemctl start wdc-client

EOF
else
  systemctl restart wdc-client
  echo
  echo "Running. Follow the logs with: journalctl -u wdc-client -f"
fi

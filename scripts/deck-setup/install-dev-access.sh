#!/bin/bash
# Run ONCE, on the Deck, as root (asks for the deck password):
#   ssh -t deck@<ip> "sudo bash /tmp/insertplay-deck-setup/install-dev-access.sh"
# Installs deploy-plugin.sh and the sudo rule that allows it without a password, and turns
# on wake-on-LAN (magic packet) for `deck.mjs wake` where the device can use it (s2idle).
# To undo:  sudo rm -rf /etc/insertplay-dev /etc/sudoers.d/zz-insertplay-dev \
#             /etc/NetworkManager/conf.d/zz-insertplay-wake.conf
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -d -o root -g root -m 755 /etc/insertplay-dev
install -o root -g root -m 755 "$HERE/deploy-plugin.sh" /etc/insertplay-dev/deploy-plugin.sh

# sudo applies the LAST rule that matches, and /etc/sudoers.d is read in alphabetical
# order. SteamOS has /etc/sudoers.d/wheel ("%wheel ALL=(ALL) ALL", with password);
# a rule named before "wheel" is overridden by it (measured on the Deck). Hence the "zz-".
RULE=$(mktemp)
echo 'deck ALL=(root) NOPASSWD: /etc/insertplay-dev/deploy-plugin.sh' > "$RULE"
visudo -cf "$RULE"
install -o root -g root -m 440 "$RULE" /etc/sudoers.d/zz-insertplay-dev
rm -f "$RULE" /etc/sudoers.d/insertplay-dev
visudo -c >/dev/null

# Wake-on-LAN for `deck.mjs wake` (issue #27). Both the Deck and the Steam Machine ship with
# WoWLAN off. NetworkManager applies these global defaults when a connection comes up
# (connections keep wake-on-wlan=default), so it survives reboots; `iw` switches it now.
#
# Only where the system sleeps in s2idle (Steam Machine). The Deck sleeps in S3 ("deep"),
# and its Wi-Fi root port (ACPI GPP3) can wake the system only from S0: with WoWLAN on, the
# magic packet reached the chip but did not wake the Deck (measured). There it would only
# keep the radio associated during sleep, so we turn it off.
WAKE_CONF=/etc/NetworkManager/conf.d/zz-insertplay-wake.conf
if grep -q '\[s2idle\]' /sys/power/mem_sleep; then
	install -d -m 755 /etc/NetworkManager/conf.d
	cat > "$WAKE_CONF" <<'EOF'
# Installed by InsertPlay install-dev-access.sh: wake-on-LAN for the dev tools.
[connection]
wifi.wake-on-wlan=magic
ethernet.wake-on-lan=magic
EOF
	chmod 644 "$WAKE_CONF"
	WOWLAN=(enable magic-packet)
else
	rm -f "$WAKE_CONF"
	WOWLAN=(disable)
	echo "wake-on-LAN skipped: this device sleeps in $(grep -o '\[[a-z0-9]*\]' /sys/power/mem_sleep), the Wi-Fi cannot wake it"
fi
nmcli general reload conf || true
if ! command -v iw >/dev/null; then
	echo "WARNING: iw not found; wake-on-wlan takes effect on the next Wi-Fi connection" >&2
else
	for net in /sys/class/net/*; do
		[[ -e "$net/phy80211/name" ]] || continue
		if iw phy "$(cat "$net/phy80211/name")" wowlan "${WOWLAN[@]}"; then
			echo "wake-on-wlan ${WOWLAN[0]}: $(basename "$net")"
		else
			echo "WARNING: iw wowlan ${WOWLAN[*]} failed on $(basename "$net"); the driver may not support it" >&2
		fi
	done
fi

echo "OK: development access installed."
echo "Test: sudo -n /etc/insertplay-dev/deploy-plugin.sh   (should show the 'usage:' without asking for a password)"

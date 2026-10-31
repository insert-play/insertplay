#!/bin/bash
# Run ONCE, on the Deck, as root (asks for the deck password):
#   ssh -t deck@<ip> "sudo bash /tmp/insertplay-deck-setup/install-dev-access.sh"
# Installs deploy-plugin.sh and the sudo rule that allows it without a password.
# To undo:  sudo rm -rf /etc/insertplay-dev /etc/sudoers.d/zz-insertplay-dev
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

echo "OK: development access installed."
echo "Test: sudo -n /etc/insertplay-dev/deploy-plugin.sh   (should show the 'usage:' without asking for a password)"

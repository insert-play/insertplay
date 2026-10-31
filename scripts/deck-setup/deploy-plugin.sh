#!/bin/bash
# Installed on the Deck as /etc/insertplay-dev/deploy-plugin.sh (root:root 755) and allowed
# for the deck user WITHOUT A PASSWORD via /etc/sudoers.d/zz-insertplay-dev.
#
# It is the only thing the automated access (AI/scripts) can do as root:
#   deploy   installs the contents of /tmp/insertplay-decky into ~/homebrew/plugins/InsertPlay
#            and restarts Decky
#   restart  restarts plugin_loader
#   remove   deletes ~/homebrew/plugins/InsertPlay and restarts Decky
#
# For safety it refuses a plugin.json with the "root" flag: without it Decky runs the
# plugin backend as a regular user, so this grant does not turn into a general sudo.
#
# The stage (/tmp/insertplay-decky) belongs to the deck user and can change at any time.
# So nothing is validated or installed FROM it (PR #2 review):
#   1. we copy the stage to a root-private snapshot (mktemp -d, 700), without symlinks
#      and with normalized permissions (no write for group/others);
#   2. we validate the SNAPSHOT;
#   3. we install the snapshot's bytes.
#
# Known and accepted residual risk: ~/homebrew belongs to the deck user by Decky's design,
# so deck can swap the whole `plugins` folder between the check and the copy. This
# is not new: with that same ownership, deck already controls what Decky (root) loads on
# the next boot. We check what can be checked (plugins is a real root-owned directory, the
# destination is not a symlink) and promise nothing more than that.
set -euo pipefail

STAGE=/tmp/insertplay-decky
PLUGINS=/home/deck/homebrew/plugins
DEST="$PLUGINS/InsertPlay"

die() {
	echo "$*" >&2
	exit 1
}

check_destination() {
	[[ -d "$PLUGINS" && ! -L "$PLUGINS" ]] || die "$PLUGINS missing or is a symlink"
	[[ "$(realpath -e "$PLUGINS")" == "$PLUGINS" ]] || die "$PLUGINS does not resolve to itself"
	[[ "$(stat -c %u "$PLUGINS")" == 0 ]] || die "$PLUGINS is not owned by root"
	# PLUGINS belongs to root (755): deck cannot create anything in here, including
	# a symlink in place of the destination; we check anyway
	[[ ! -e "$DEST" && ! -L "$DEST" ]] || [[ -d "$DEST" && ! -L "$DEST" ]] || die "$DEST is not a regular directory"
}

case "${1-}" in
	deploy)
		[[ -d "$STAGE" && ! -L "$STAGE" ]] || die "stage $STAGE missing or is a symlink"
		SNAP="$(mktemp -d /tmp/insertplay-deploy.XXXXXX)"
		trap 'rm -rf "$SNAP"' EXIT
		rsync -r --no-links --no-specials --no-devices --chmod=D755,F644 "$STAGE/" "$SNAP/"

		[[ -f "$SNAP/plugin.json" && -f "$SNAP/main.py" && -f "$SNAP/dist/index.js" ]] \
			|| die "incomplete stage (plugin.json, main.py, dist/index.js)"
		python3 - "$SNAP/plugin.json" <<'PY' || die "refused: plugin.json invalid or requests the root flag"
import json, sys
data = json.load(open(sys.argv[1]))
flags = data.get("flags", [])
sys.exit(1 if data.get("name") != "InsertPlay" or not isinstance(flags, list) or "root" in flags else 0)
PY

		check_destination
		mkdir -p "$DEST"
		rsync -r --delete --no-links --chmod=D755,F644 "$SNAP/" "$DEST/"
		chown -R root:root "$DEST"
		systemctl restart plugin_loader
		echo "InsertPlay installed at $DEST"
		;;
	restart)
		systemctl restart plugin_loader
		;;
	remove)
		check_destination
		rm -rf "$DEST"
		systemctl restart plugin_loader
		echo "InsertPlay removed"
		;;
	*)
		echo "usage: $0 deploy|restart|remove" >&2
		exit 2
		;;
esac

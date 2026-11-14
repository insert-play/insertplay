#!/bin/sh
# Re-indexes the remote library only when something in it changed since the last index,
# and only after the changes have settled (no change in the last QUIET_MIN minutes), so a
# game still being copied is not indexed half-way. Meant for a cron line, e.g. every 10 min:
#
#   */10 * * * * /path/to/server/reindex-if-changed.sh >>"$HOME/.cache/insertplay-reindex.log" 2>&1
#
# Variables (from server/.env, or the environment):
#   INSERTPLAY_LIBRARY  library root
#   INSERTPLAY_CONFIG   config folder (holds index/index.json)
#   INSERTPLAY_REINDEX  command that runs the indexer
#                       (default: docker compose -f <this folder>/docker-compose.yml run --rm indexer)
#   QUIET_MIN           minutes without changes before indexing (default 2)
#
# Copies made from Windows (Samba) keep the files' modification time, so the scan also
# looks at the change time (ctime) and at directory times (a new or removed entry changes
# its parent folder).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
[ -f "$HERE/.env" ] && . "$HERE/.env"
: "${INSERTPLAY_LIBRARY:?set INSERTPLAY_LIBRARY}"
: "${INSERTPLAY_CONFIG:?set INSERTPLAY_CONFIG}"
QUIET_MIN=${QUIET_MIN:-2}
INDEX="$INSERTPLAY_CONFIG/index/index.json"
LOCK="$INSERTPLAY_CONFIG/index/.reindex.lock"

# One run at a time: a cold index of a big library takes minutes.
exec 9>"$LOCK"
flock -n 9 || exit 0

[ -d "$INSERTPLAY_LIBRARY" ] || { echo "$(date -Is) library not mounted: $INSERTPLAY_LIBRARY"; exit 0; }

changed() {
	# $1: reference file. Prints the first path changed after it.
	find "$INSERTPLAY_LIBRARY" \( -newer "$1" -o -cnewer "$1" \) -print -quit
}

if [ -f "$INDEX" ]; then
	[ -n "$(changed "$INDEX")" ] || exit 0
	QUIET=$(mktemp)
	trap 'rm -f "$QUIET"' EXIT
	touch -d "$QUIET_MIN minutes ago" "$QUIET"
	if [ -n "$(changed "$QUIET")" ]; then
		echo "$(date -Is) changes still settling; next run"
		exit 0
	fi
fi

echo "$(date -Is) library changed; re-indexing"
if [ -n "${INSERTPLAY_REINDEX:-}" ]; then
	sh -c "$INSERTPLAY_REINDEX"
else
	docker compose -f "$HERE/docker-compose.yml" run --rm indexer
fi

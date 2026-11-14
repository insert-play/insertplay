# Remote library server

The server of the InsertPlay remote library ("Biblioteca Remota" in Steam): a static file server that the plugin downloads games from over the home network. The contract it implements (URLs, index, file lists, authentication) is [docs/remote-library.md](../docs/remote-library.md).

It runs on any Linux box or NAS with Docker and Docker Compose v2 (see [Without Compose v2](#without-compose-v2) for Debian 11 and other old releases): [Caddy](https://caddyserver.com/) serves the library read-only, a small indexer (Python 3.9+, standard library only) writes the index next to it, and a save server keeps each game's latest save ([step 6](#6-saves)).

| File | What |
|---|---|
| `docker-compose.yml` | `caddy` (the server, starts at boot), `saves` (the save server, starts at boot) and `indexer` (one-shot, `docker compose run --rm indexer`). |
| `Caddyfile` | Token check, the URL families of the contract, `404` for everything else. |
| `saves/saves_server.py` | The save server (Python, standard library only). Caddy forwards `/saves/*` to it. |
| `indexer/remote_library_index.py` | The indexer. Also runs without Docker: `python3 -I indexer/remote_library_index.py <library> <index folder>`. |
| `.env.example` | Template for `server/.env` (paths and port; no secrets). |

## 1. The library

The library root is a folder with one [InsertPlay card](../docs/card-format.md) per subfolder (`steam-shortcut.json` and `shortcut-identity.json` at the root of each). It may live on a disk with other content: only the library root is exposed, never the rest of the disk.

The server never writes to the library, so mount the disk **read-only**, and with `nofail` so the box still boots without it. In `/etc/fstab`:

```
# NTFS disk (ntfs-3g), library in /mnt/games/InsertPlay
LABEL=Games  /mnt/games  ntfs-3g  ro,nofail,x-systemd.device-timeout=30s,x-systemd.before=docker.service,uid=1000,gid=1000  0  0

# ext4 disk
UUID=<uuid>  /mnt/games  ext4     ro,nofail,x-systemd.device-timeout=30s,x-systemd.before=docker.service  0  2
```

- Find the label or UUID with `lsblk -f`. The label is safer than `/dev/sdX`, which can change between boots.
- `x-systemd.before=docker.service` makes Docker wait for the mount attempt at boot; with `nofail` Docker still starts when the disk is missing. In that case Caddy refuses to start (`bind source path does not exist`) instead of serving an empty folder; mount the disk and run `docker compose up -d`.
- Apply without rebooting: `sudo systemctl daemon-reload && sudo mount /mnt/games`, then check `findmnt /mnt/games` shows `ro`.
- A USB hard disk on a small board needs a powered hub: without one, it may drop off the bus under continuous reads.

## 2. The config folder and the token

Pick a config folder **outside the library disk**, for example `~/.config/insertplay/remote-library`. It holds the token and the index:

```sh
CONFIG=~/.config/insertplay/remote-library
mkdir -p "$CONFIG/index"
umask 077
printf 'INSERTPLAY_TOKEN=%s\n' "$(openssl rand -hex 32)" > "$CONFIG/token.env"
chmod 600 "$CONFIG/token.env"
```

(Without `openssl`: `python3 -c 'import secrets; print(secrets.token_hex(32))'`.)

- The token goes into the plugin panel on the device, together with the server URL. Read it with `cat "$CONFIG/token.env"` only when you need to type it there.
- Never put it in this repository, an issue, a log or a chat. `server/.gitignore` ignores `.env`, `*.env` and `token*` in case one lands here by accident.
- Caddy refuses to start when the token is missing or shorter than 32 characters.
- There is no TLS on the LAN: the token travels in clear text on the home network. Accepted for the proof of concept; access from outside the LAN is out of scope.

## 3. Configure and start

```sh
cd server
cp .env.example .env
$EDITOR .env        # INSERTPLAY_LIBRARY, INSERTPLAY_CONFIG (absolute paths), port, uid/gid
mkdir -p /mnt/games/InsertPlay-saves      # INSERTPLAY_SAVES, see step 6
docker compose run --rm indexer
docker compose up -d caddy saves
```

| Variable | Meaning |
|---|---|
| `INSERTPLAY_LIBRARY` | Library root, e.g. `/mnt/games/InsertPlay`. Mounted read-only in both containers. |
| `INSERTPLAY_CONFIG` | Config folder from step 2 (absolute path: Compose doesn't expand `~`). |
| `INSERTPLAY_SAVES` | Save storage ([step 6](#6-saves)), e.g. `/mnt/games/InsertPlay-saves`. Written by the `saves` service only. |
| `INSERTPLAY_SAVE_BACKUP_MB` | Backups kept per game, in MB (default `50`). |
| `INSERTPLAY_SAVE_MAX_MB` | Largest save accepted, in MB (default `1024`). |
| `INSERTPLAY_PORT` | Host port, default `8080`. The server URL is then `http://<server ip>:8080/`. |
| `INSERTPLAY_UID` / `INSERTPLAY_GID` | Owner of the files the indexer writes (`id -u`, `id -g`), default `1000`. |

`restart: always` brings Caddy back after a reboot, as long as the Docker service is enabled (`sudo systemctl enable docker`). Not `unless-stopped`: docker.io 20.10.5 (Debian 11) marks the container as manually stopped at shutdown and leaves it down after the reboot (measured on a BeaglePlay, 2026-11-13). Give the server a fixed IP (a DHCP reservation in the router), since the plugin stores its URL.

## 4. Re-index

Run the indexer after adding, removing or changing a game:

```sh
docker compose run --rm indexer
```

It prints one line per game indexed (files, empty directories, symlinks, size, artwork) and one per folder skipped, with the reason: missing or invalid `steam-shortcut.json`, missing, invalid or template guid, duplicate guid (both folders are skipped), a path that breaks the contract's path rules, a `.part` file, a symlink that leaves the game folder, or an entry that is not a file, directory or symlink. Fix the folder and run it again.

- It writes `index.json`, `lists/<id>.json` and a hash cache (`hash-cache.json`) in `$INSERTPLAY_CONFIG/index`, each to a temporary file renamed at the end, so Caddy never serves a half-written index. Caddy doesn't need a restart.
- The cache skips rehashing files whose size and modification time didn't change. `--no-cache` forces a full rehash: `docker compose run --rm indexer python3 -I /opt/indexer/remote_library_index.py /srv/library /srv/index --no-cache`.
- The library is read-only and the container has no network.
- A file replaced in place with the same size and an older modification time (a copy over Samba keeps the original date) keeps its old hash in the cache: after replacing files that way, run the indexer once with `--no-cache`.
- To re-index by itself when the library changes, use [`reindex-if-changed.sh`](reindex-if-changed.sh) from a cron line (`crontab -e` as the user that owns the config folder, a member of the `docker` group; run `mkdir -p ~/.cache` first, for the log):

  ```
  */10 * * * * /path/to/server/reindex-if-changed.sh >>$HOME/.cache/insertplay-reindex.log 2>&1
  ```

  Every run scans the library for anything changed since the last `index.json` (modification and change times, so files copied with their old dates count too, and a removed game counts through its parent folder). With no change it stops there; with a change it waits until nothing changed for `QUIET_MIN` minutes (default 5), so a game still being copied is not indexed half-way, then runs the indexer. A change made while the indexer runs is picked up by the next run. A copy that stalls longer than `QUIET_MIN` (e.g. on a "replace or skip" dialog) gets indexed half-way and fixed on a later run. On a BeaglePlay with 2 games (27 354 files) on an NTFS disk over USB 2.0, the scan takes ~6 s and a re-index ~50 s. `INSERTPLAY_REINDEX` replaces the indexer command, e.g. without Compose v2 (next section): `INSERTPLAY_REINDEX=$HOME/insertplay-library/indexer-run.sh`.

## 5. Test with curl

From another machine on the network (the token is read from the env file into the shell, so it doesn't show up in the command line):

```sh
URL=http://192.168.1.83:8080
. ~/.config/insertplay/remote-library/token.env     # or copy the value by hand
AUTH="Authorization: Bearer $INSERTPLAY_TOKEN"

curl -s -o /dev/null -w '%{http_code}\n' "$URL/index.json"                       # 401: no token
curl -s -H "$AUTH" "$URL/index.json" | python3 -m json.tool | head -20             # the index
curl -s -o /dev/null -w '%{http_code}\n' -H "$AUTH" "$URL/library/"                # 404: no listing
curl -s -o /dev/null -w '%{http_code}\n' -H "$AUTH" --path-as-is "$URL/library/../index.json"   # 404
# Range: the rest of a file from byte 10 answers 206 with Content-Range
curl -s -D - -o /dev/null -H "$AUTH" -H 'Range: bytes=10-' "$URL/library/<game folder>/steam-shortcut.json"
```

## 6. Saves

The plugin keeps each game's latest save on the server, so the same game can be played on two devices ("Sincronizar saves" in the panel; contract in [docs/remote-library.md](../docs/remote-library.md#saves)). The `saves` service stores them in `INSERTPLAY_SAVES`:

```
<INSERTPLAY_SAVES>/<guid>/
├── current.tar.gz      the latest save
├── meta.json           rev, hash, newest file time, device, upload time, size
├── strategy.json       where the game keeps its save, when the plugin had to infer it
└── backups/<time>-<device>.tar.gz
```

- **Outside the library**: the folder must not be inside `INSERTPLAY_LIBRARY`, so the indexer never hashes a save (it would change the game's `version`) and the Samba share doesn't show it. The disk has to be mounted read-write; the library itself stays read-only for Caddy and the indexer.
- **Owner**: the service runs as `INSERTPLAY_UID:INSERTPLAY_GID` and refuses to start when it can't write to the folder. On ext4, `sudo chown 1000:1000 /mnt/games/InsertPlay-saves` (the uid/gid from `.env`); on NTFS (`ntfs-3g`), the `uid=`/`gid=` mount options already make everything owned by it.
- **Backups**: when a device replaces the save, the previous one becomes a backup (if the device has "Guardar backup dos saves" on). The oldest are removed to keep each game within `INSERTPLAY_SAVE_BACKUP_MB`. A save larger than that limit gets no backup at all and still syncs normally.
- **Restore a backup by hand**: `ls <guid>/backups/`, then copy one over `current.tar.gz` and edit `meta.json` so its `hash` no longer matches (for example, set it to 64 zeros): the next device that opens the game sees the server changed and downloads it.
- The service has no port on the host: only Caddy reaches it, after the token check, and it checks the token again. With the service stopped, `/saves/*` answers `502` and the plugin plays with the local save and uploads later.

Test with curl (variables from step 5):

```sh
G=00000000-0000-4000-8000-000000000001     # any guid
curl -s -o /dev/null -w '%{http_code}
' -H "$AUTH" "$URL/saves/$G/meta"     # 404: no save yet
printf hi > /tmp/s.tgz
curl -s -X PUT -H "$AUTH" -H 'If-None-Match: *' -H "X-Save-Hash: $(printf %064d 0)"   -H 'X-Save-Device: test' -H 'X-Save-Newest-Mtime: 0' --data-binary @/tmp/s.tgz "$URL/saves/$G/current.tar.gz"
curl -s -H "$AUTH" "$URL/saves/$G/meta"                                        # rev 1
# then remove the test folder on the server: rm -r "$INSERTPLAY_SAVES/$G"
```

## Without Compose v2

Debian 11 (Bullseye) has no Compose v2 package, only `docker-compose` 1.25, which does not read this `docker-compose.yml` (long bind syntax with `create_host_path`, `profiles`). Run the same containers with plain `docker run` instead, with the same settings as the compose file. Two small scripts next to `server/` (with `server/.env` loaded by them) are enough:

```sh
#!/bin/sh
# caddy-run.sh: starts Caddy detached; Docker brings it back at boot
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/server/.env"
docker network inspect insertplay >/dev/null 2>&1 || docker network create insertplay >/dev/null
docker rm -f insertplay-caddy >/dev/null 2>&1 || true
exec docker run -d --restart always --name insertplay-caddy --network insertplay   -p "${INSERTPLAY_PORT:-8080}:8080"   --env-file "$INSERTPLAY_CONFIG/token.env"   --mount "type=bind,src=$INSERTPLAY_LIBRARY,dst=/srv/library,readonly"   --mount "type=bind,src=$INSERTPLAY_CONFIG/index,dst=/srv/index,readonly"   --mount "type=bind,src=$HERE/server/Caddyfile,dst=/etc/caddy/Caddyfile,readonly"   --read-only --tmpfs /data --tmpfs /config   --cap-drop ALL --security-opt no-new-privileges:true   --entrypoint /bin/sh caddy:2 -c '
if [ "${#INSERTPLAY_TOKEN}" -lt 32 ]; then
  echo "INSERTPLAY_TOKEN missing or shorter than 32 characters in token.env" >&2
  exit 1
fi
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile'
```

```sh
#!/bin/sh
# indexer-run.sh: one-shot indexer; extra arguments go to it (e.g. --no-cache)
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/server/.env"
exec docker run --rm --name insertplay-indexer   --user "${INSERTPLAY_UID:-1000}:${INSERTPLAY_GID:-1000}"   --mount "type=bind,src=$INSERTPLAY_LIBRARY,dst=/srv/library,readonly"   --mount "type=bind,src=$INSERTPLAY_CONFIG/index,dst=/srv/index"   --mount "type=bind,src=$HERE/server/indexer,dst=/opt/indexer,readonly"   --network none --read-only --cap-drop ALL --security-opt no-new-privileges:true   python:3.12-alpine python3 -I /opt/indexer/remote_library_index.py /srv/library /srv/index "$@"
```

```sh
#!/bin/sh
# saves-run.sh: starts the save server detached, on the network Caddy uses
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
. "$HERE/server/.env"
docker network inspect insertplay >/dev/null 2>&1 || docker network create insertplay >/dev/null
docker rm -f insertplay-saves >/dev/null 2>&1 || true
exec docker run -d --restart always --name insertplay-saves --network insertplay --network-alias saves   --user "${INSERTPLAY_UID:-1000}:${INSERTPLAY_GID:-1000}"   --env-file "$INSERTPLAY_CONFIG/token.env"   -e INSERTPLAY_SAVES_DIR=/srv/saves -e "INSERTPLAY_SAVE_BACKUP_MB=${INSERTPLAY_SAVE_BACKUP_MB:-50}" -e "INSERTPLAY_SAVE_MAX_MB=${INSERTPLAY_SAVE_MAX_MB:-1024}"   --mount "type=bind,src=$INSERTPLAY_SAVES,dst=/srv/saves"   --mount "type=bind,src=$HERE/server/saves,dst=/opt/saves,readonly"   --read-only --cap-drop ALL --security-opt no-new-privileges:true   --log-driver journald   python:3.12-alpine python3 -I /opt/saves/saves_server.py
```

Both scripts use the `insertplay` network: Caddy reaches the save server there as `saves:8081`.

`--mount` with a missing `src` fails instead of creating an empty folder, like `create_host_path: false`. Use `--restart always`, not `unless-stopped` (see step 3).

## Editing the library from a Windows PC (Samba)

To copy games into the library from a PC, share **only the library root** over SMB. The disk then has to be mounted read-write (`rw` instead of `ro` in step 1); Caddy and the indexer still see it read-only through their `readonly` binds. On Debian, `sudo apt install samba`, then add to `/etc/samba/smb.conf` (keep a backup of the original):

```ini
[global]
   server role = standalone server
   server min protocol = SMB2
   restrict anonymous = 2
   unix extensions = no
   load printers = no
   disable spoolss = yes

[InsertPlay]
   path = /mnt/games/InsertPlay
   valid users = debian
   force user = debian
   force group = debian
   read only = no
   create mask = 0644
   veto files = /Thumbs.db/desktop.ini/.DS_Store/._*/
   delete veto files = yes
```

- Set the SMB password of that user with `sudo smbpasswd -a debian`, check the file with `testparm -s` and restart with `sudo systemctl restart smbd`. `nmbd` (NetBIOS names) is not needed: `sudo systemctl disable --now nmbd`.
- On Windows, map it from PowerShell (the Git Bash mangles UNC paths): `net use L: \\<server ip>\InsertPlay /persistent:yes`, with the password stored in the Credential Manager (`cmdkey /add:<server ip> /user:debian /pass`). Never put that password in a script or in this repository.
- `wide links` stays at its default (`no`): symlinks inside a game (Proton prefixes have them) keep working, but none reaches outside the share. No guest access: without `map to guest`, an unknown user is refused.
- After copying a game, re-index (step 4), or let `reindex-if-changed.sh` do it.

## What the server answers

| Request | Answer |
|---|---|
| Any request without `Authorization: Bearer <token>`, or with a wrong one | `401`, before anything else |
| `/saves/<guid>/…` | forwarded to the save server ([docs/remote-library.md](../docs/remote-library.md#saves)); `502` when it is not running |
| A method other than `GET` or `HEAD`, outside `/saves/` | `405` |
| `/index.json` | the index |
| `/lists/<guid>.json` | a file list (only lowercase guid names) |
| `/library/<game>/<file>` | the file; `Range: bytes=N-` answers `206` |
| A directory, a `.` or `..` segment (also `%2e`), a backslash, anything else | `404`; never a listing or a redirect |

Caddy runs with a read-only root filesystem, no capabilities and no admin API. It sees only the library (read-only), the index folder (read-only) and its `Caddyfile`. The save server runs the same way and sees only the save folder (read-write) and its own script.

Symlinks: the indexer refuses games whose symlinks leave the game folder, so the plugin never asks for such a path. Caddy itself follows symlinks, but only inside its container: a link in the library can never reach the host's disk. Whoever can write to the library could still point a link at a file inside the container (including the token in its environment), so only trusted users should write to the library.

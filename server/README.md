# Remote library server

The server of the InsertPlay remote library ("Biblioteca Remota" in Steam): a static file server that the plugin downloads games from over the home network. The contract it implements (URLs, index, file lists, authentication) is [docs/remote-library.md](../docs/remote-library.md).

It runs on any Linux box or NAS with Docker and Docker Compose v2: [Caddy](https://caddyserver.com/) serves the library read-only, and a small indexer (Python 3.9+, standard library only) writes the index next to it.

| File | What |
|---|---|
| `docker-compose.yml` | `caddy` (the server, starts at boot) and `indexer` (one-shot, `docker compose run --rm indexer`). |
| `Caddyfile` | Token check, the three URL families of the contract, `404` for everything else. |
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
docker compose run --rm indexer
docker compose up -d caddy
```

| Variable | Meaning |
|---|---|
| `INSERTPLAY_LIBRARY` | Library root, e.g. `/mnt/games/InsertPlay`. Mounted read-only in both containers. |
| `INSERTPLAY_CONFIG` | Config folder from step 2 (absolute path: Compose doesn't expand `~`). |
| `INSERTPLAY_PORT` | Host port, default `8080`. The server URL is then `http://<server ip>:8080/`. |
| `INSERTPLAY_UID` / `INSERTPLAY_GID` | Owner of the files the indexer writes (`id -u`, `id -g`), default `1000`. |

`restart: unless-stopped` brings Caddy back after a reboot, as long as the Docker service is enabled (`sudo systemctl enable docker`). Give the server a fixed IP (a DHCP reservation in the router), since the plugin stores its URL.

## 4. Re-index

Run the indexer after adding, removing or changing a game:

```sh
docker compose run --rm indexer
```

It prints one line per game indexed (files, empty directories, symlinks, size, artwork) and one per folder skipped, with the reason: missing or invalid `steam-shortcut.json`, missing, invalid or template guid, duplicate guid (both folders are skipped), a path that breaks the contract's path rules, a `.part` file, a symlink that leaves the game folder, or an entry that is not a file, directory or symlink. Fix the folder and run it again.

- It writes `index.json`, `lists/<id>.json` and a hash cache (`hash-cache.json`) in `$INSERTPLAY_CONFIG/index`, each to a temporary file renamed at the end, so Caddy never serves a half-written index. Caddy doesn't need a restart.
- The cache skips rehashing files whose size and modification time didn't change. `--no-cache` forces a full rehash: `docker compose run --rm indexer python3 -I /opt/indexer/remote_library_index.py /srv/library /srv/index --no-cache`.
- The library is read-only and the container has no network.
- To re-index on a schedule, a cron line or systemd timer running `docker compose -f /path/to/server/docker-compose.yml run --rm indexer` is enough.

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

## What the server answers

| Request | Answer |
|---|---|
| Any request without `Authorization: Bearer <token>`, or with a wrong one | `401`, before anything else |
| A method other than `GET` or `HEAD` | `405` |
| `/index.json` | the index |
| `/lists/<guid>.json` | a file list (only lowercase guid names) |
| `/library/<game>/<file>` | the file; `Range: bytes=N-` answers `206` |
| A directory, a `.` or `..` segment (also `%2e`), a backslash, anything else | `404`; never a listing or a redirect |

Caddy runs with a read-only root filesystem, no capabilities and no admin API. It sees only the library (read-only), the index folder (read-only) and its `Caddyfile`.

Symlinks: the indexer refuses games whose symlinks leave the game folder, so the plugin never asks for such a path. Caddy itself follows symlinks, but only inside its container: a link in the library can never reach the host's disk. Whoever can write to the library could still point a link at a file inside the container (including the token in its environment), so only trusted users should write to the library.

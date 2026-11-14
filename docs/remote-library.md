# Remote library: contract v1

The remote library ("Biblioteca Remota" in the Steam UI) lets the plugin install games from a home server over the network. A game installed this way works like an InsertPlay card that never leaves the device: a "virtual card" on the internal drive.

This document is the contract between the three parts of the proof of concept:

- the **server** ([#44](https://github.com/insert-play/insertplay/issues/44)): plain static files over HTTP, plus an indexer;
- the **download engine** in the plugin backend ([#47](https://github.com/insert-play/insertplay/issues/47)): `py_modules/insertplay_decky/remote_library.py` and its RPCs in `main.py`;
- the **Steam and panel integration** in the frontend ([#45](https://github.com/insert-play/insertplay/issues/45)).

Scope of v1: home network only, one server, install and uninstall. Game updates, access from outside the LAN and installing to a microSD card are out of scope. [Save sync](#saves) ([#63](https://github.com/insert-play/insertplay/issues/63)) came later and is described at the end.

## Server

### Library layout

The library root is a folder with **one folder per game**, each in the [card layout](card-format.md): `steam-shortcut.json` and `shortcut-identity.json` at its root, the launch script, the guard, `steam-artwork/`, the emulator and the game.

```
<library root>/                 e.g. /mnt/games/InsertPlay on the reference server
├── test-drive-6/               one InsertPlay card
│   ├── steam-shortcut.json
│   ├── shortcut-identity.json
│   ├── insertplay-start.sh
│   ├── insertplay-guid-guard.sh
│   ├── steam-artwork/
│   ├── duckstation/
│   └── game.cue, game.bin
└── captain-claw/
    └── …
```

- A folder without a valid `steam-shortcut.json` and `shortcut-identity.json` at its root is not a game: the indexer skips it with a warning. Older cards that keep them in `insertplay/` must be migrated first ([card-format.md](card-format.md#migrating-an-older-card)).
- The folder name is the game's `path` in the index. It must follow the [path rules](#path-rules) (one segment).
- The library is read-only for the server and the indexer. The library root may live on a disk with other content: the server exposes **only** the library root, never the whole disk.

### HTTP

No custom API: the server is a static file server. The reference server is Caddy (`file-server`) in Docker, with the library and the index mounted read-only; its files and setup live in [`server/`](../server/README.md).

| URL (relative to the server base URL) | Served from |
|---|---|
| `index.json` | the index, in the server's config folder |
| `lists/<id>.json` | the per-game file lists, in the server's config folder |
| `library/<game path>/<file path>` | the library root |
| anything else | `404` |

Rules:

- **Authentication.** Every request needs `Authorization: Bearer <token>`, also on the LAN. Missing or wrong token: `401`, checked **before** anything else, so a request without the token can't tell an existing path from a missing one.
- **Methods.** `GET` and `HEAD` (any other method: `405`). `GET` supports `Range: bytes=<start>-` and answers `206` with `Content-Range: bytes <start>-<end>/<size>`.
- **No directory listings.** A directory URL answers `404`. Paths with a `.` or `..` segment (encoded or not) answer `404`. The indexer refuses games whose symlinks leave the game folder, so no indexed path goes through such a link; the reference server sees only the library and the index (in a container), so a link can never reach the rest of the disk.
- **No redirects.** The server never redirects; the client treats any `3xx` as an error and never resends the token elsewhere.
- **The token** lives only on the server (its config folder, mode `0600`) and in the plugin's settings on the device. It never goes into the repository, the index, logs or issues. Without TLS on the LAN it travels in clear text: accepted for the proof of concept, to be revisited with remote access.

The **server base URL** is what the user types in the panel, for example `http://192.168.1.83:8080/`. The client normalizes it to end with `/` and resolves every other URL against it.

### Indexer

A script ([`server/indexer/remote_library_index.py`](../server/indexer/remote_library_index.py)) that reads the library root (read-only) and writes the index and the file lists to an output folder **outside the library disk**, in the server's config folder. It writes to temporary files and renames them at the end, so the server never serves a half-written index.

For each game folder it:

1. reads `shortcut-identity.json` (guid, lowercased) and `steam-shortcut.json` (stored as is in the index as `manifest`);
2. walks the folder, without following symlinks, and builds the file list: every regular file with its size, sha256 and [`executable`](#executable-files) flag, every empty directory, and every symlink that stays inside the game folder;
3. resolves the artwork the same way the plugin does for a card (the manifest path, then the fallback `steam-artwork/<kind>.png`). `{SD_ROOT}/` at the start of an artwork path is removed; an absolute path, or one that breaks the [path rules](#path-rules), is ignored and the fallback applies;
4. computes `version` and writes `lists/<id>.json`, then adds the game to `index.json`.

A game is **skipped** (reported by the indexer, absent from the index) when: its guid is missing, invalid or the all-zero template guid; two folders have the same guid (both skipped); any path breaks the [path rules](#path-rules); a symlink points outside the game folder or is absolute; a file name ends in `.part` (reserved by the client); an entry is neither a regular file, a directory nor a symlink (FIFO, socket, device); the file list would exceed the client's 64 MiB limit.

The indexer may cache hashes by `(path, size, mtime)` to avoid rehashing unchanged games; that is an optimization, not part of the contract.

### `version`

The first 16 hex digits of the sha256 of the game's file list: in Python, `sha256(json.dumps({"files": files, "dirs": dirs, "links": links}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]`, with `dirs` and `links` as `[]` when the game has none. It changes whenever any file, directory or link changes, and needs no new field in the card format. The client only compares versions for equality.

## `index.json` v1

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-11-13T21:00:00Z",
  "library": "library/",
  "games": [
    {
      "id": "fa41e0e7-0133-4f21-b5f4-1a143a2f52ef",
      "version": "b32d2fa489eeee59",
      "path": "test-drive-6",
      "name": "Test Drive 6",
      "totalSize": 532381696,
      "fileCount": 41,
      "files": "lists/fa41e0e7-0133-4f21-b5f4-1a143a2f52ef.json",
      "filesSha256": "419c19ba095971f800f71f9c027f657502fa0b193c69fafe68aa7689363c1856",
      "manifest": {
        "schemaVersion": 1,
        "shortcut": {
          "appName": "Test Drive 6",
          "launch": {
            "exe": "/bin/bash",
            "args": "\"{SD_ROOT}/insertplay-start.sh\"",
            "startDir": "{SD_ROOT}"
          },
          "artwork": {
            "icon": "steam-artwork/icon.png",
            "capsule": "steam-artwork/capsule.png",
            "horizontal": "steam-artwork/horizontal.png",
            "hero": "steam-artwork/hero.png",
            "logo": "steam-artwork/logo.png"
          }
        },
        "game": { "platform": "ps1", "emulator": "duckstation" }
      },
      "artwork": {
        "icon": { "path": "steam-artwork/icon.png", "size": 18210, "sha256": "1b0819332433143493500f91c9cc39acf6087e5983fbd8ff334a2cf693967617" },
        "capsule": { "path": "steam-artwork/capsule.png", "size": 402117, "sha256": "11a7577862a351e4de949c07eb27d35e25733476ffeb506e62a358a9a69b2efd" },
        "horizontal": { "path": "steam-artwork/horizontal.png", "size": 351904, "sha256": "5c7d4dbca9efb609e3e5a1a1dda764fe358771485fc8a85971d48d0b67a99642" },
        "hero": { "path": "steam-artwork/hero.png", "size": 1203311, "sha256": "50904c0570f9ffc00e0935d5b4aa904c72749add51b09d89e36b24640cc9cd37" },
        "logo": { "path": "steam-artwork/logo.png", "size": 96412, "sha256": "a6101c10b7bd218da76245110a90db5dfe4b109b9bef882b77c37fc2eba97440" }
      }
    },
    {
      "id": "b7898be4-146a-42c2-8dec-a8797a259379",
      "version": "a03463ac255651b7",
      "path": "captain-claw",
      "name": "Captain Claw",
      "totalSize": 1904214016,
      "fileCount": 31877,
      "files": "lists/b7898be4-146a-42c2-8dec-a8797a259379.json",
      "filesSha256": "9c44244caa1f0869ae7cc84302aa744f7d72c19d2cc646ba164a97739d4ca576",
      "manifest": {
        "schemaVersion": 1,
        "shortcut": {
          "appName": "Captain Claw",
          "launch": {
            "exe": "/bin/bash",
            "args": "\"{SD_ROOT}/insertplay-start.sh\"",
            "startDir": "{SD_ROOT}"
          }
        }
      },
      "artwork": {
        "capsule": { "path": "steam-artwork/capsule.png", "size": 388020, "sha256": "a4dc85ed9d7bc460b01bb3ac1cc6d079bb00886d3387202221fdce1b585da47b" }
      }
    }
  ]
}
```

The guids, sizes and hashes above are illustrative, not the real test games of [#46](https://github.com/insert-play/insertplay/issues/46).

| Field | Meaning |
|---|---|
| `schemaVersion` | `1`. A client refuses any other value (`bad_index`). Within v1, new fields may be added; clients ignore unknown fields. A change that old clients would misread bumps the version. |
| `generatedAt` | When the indexer ran (UTC, ISO 8601). Informational. |
| `library` | URL of the library root, relative to the base URL. |
| `games[].id` | The guid from `shortcut-identity.json`, lowercase. Unique in the index. It is also the install folder name and the key of the plugin's shortcut registry, so the same game on a physical card and in the library maps to one shortcut. |
| `games[].version` | See [`version`](#version). |
| `games[].path` | The game folder under `library`: one segment that follows the [path rules](#path-rules). |
| `games[].name` | `manifest.shortcut.appName`, copied for display. |
| `games[].totalSize` | Sum of the sizes of all files in the file list, in bytes. |
| `games[].fileCount` | Number of entries in the file list's `files`. |
| `games[].files` | URL of the file list, relative to the base URL. |
| `games[].filesSha256` | sha256 of the file list's bytes as served. The client checks it after download. |
| `games[].manifest` | The game's `steam-shortcut.json`, as is (`shortcut`, `game` and any other key). |
| `games[].artwork` | Resolved artwork, per kind (`icon`, `capsule`, `horizontal`, `hero`, `logo`): `path` relative to the game folder, `size`, `sha256`. Kinds without a file are absent. The same files are also in the file list. |

All sizes are JSON integers in bytes; all sha256 values are 64 lowercase hex digits.

What the client checks in the index, besides `schemaVersion`:

- **URLs stay on the server.** `library` and every `games[].files` are relative URLs: no scheme, no host, no leading `/`, no `\`, no `?` or `#`, no `.` or `..` segment, and they resolve under the base URL (same scheme, host and port, path starting with the base path). Anything else is `bad_index` for the whole index: an index must never make the client send the token to another server.
- **`id`** matches `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` (lowercase), is not the all-zero template guid and is unique in the index. It becomes a folder name on the device, so a game with a bad `id` is dropped from the list (reported in the plugin log) and never reaches the disk.
- **`path`** and every `artwork.<kind>.path` follow the [path rules](#path-rules); `artwork` kinds outside the five known ones are ignored. A game that breaks this is dropped like a bad `id`.

Limits the client enforces: `index.json` up to 16 MiB, a file list up to 64 MiB (a game with ~32 000 entries makes a file list of about 4 MiB).

## Per-game file list

`lists/<id>.json`:

```json
{
  "schemaVersion": 1,
  "id": "fa41e0e7-0133-4f21-b5f4-1a143a2f52ef",
  "version": "b32d2fa489eeee59",
  "files": [
    { "path": "steam-shortcut.json", "size": 612, "sha256": "…", "executable": false },
    { "path": "shortcut-identity.json", "size": 50, "sha256": "…", "executable": false },
    { "path": "insertplay-start.sh", "size": 1431, "sha256": "…", "executable": true },
    { "path": "insertplay-guid-guard.sh", "size": 1688, "sha256": "…", "executable": true },
    { "path": "duckstation/DuckStation-x64.AppImage", "size": 49283072, "sha256": "…", "executable": true },
    { "path": "duckstation/bios/SCPH1001.BIN", "size": 524288, "sha256": "…", "executable": false },
    { "path": "game.cue", "size": 78, "sha256": "…", "executable": false },
    { "path": "game.bin", "size": 481998000, "sha256": "…", "executable": false },
    { "path": "steam-artwork/capsule.png", "size": 402117, "sha256": "…", "executable": false }
  ],
  "dirs": ["duckstation/memcards"],
  "links": [
    { "path": "proton/files/lib/wine/i386-windows/example.dll", "target": "../x86_64-windows/example.dll" }
  ]
}
```

(`…` stands for the full 64-digit hash; the `links` entry is only an example of the shape.)

| Field | Meaning |
|---|---|
| `id`, `version` | Must equal the index entry; otherwise `bad_index`. |
| `files[]` | Every regular file: `path` (see the [path rules](#path-rules)), `size`, `sha256`, `executable`. Sorted by `path`. |
| `dirs[]` | Optional. Empty directories to create (a directory with files is created implicitly). |
| `links[]` | Optional. Symlinks inside the game: `path` and `target`. The target is relative, non-empty, follows the character rules of the [path rules](#path-rules) (`..` segments allowed), and `dirname(path)` joined with `target` and normalized must stay inside the game folder. The client checks it again and refuses the game otherwise. |

Symlinks are where a lexical check alone falls short (`x → .` and then `sub/y → ../x/..` normalizes inside the folder but resolves to its parent), so the client also:

- creates the links only after every file is verified (step 4 of [Install](#install)), deleting and recreating any link already in `.incoming/<id>/` from an interrupted run;
- after creating them, resolves every link with `realpath` and refuses the game (`bad_path`) if one lands outside `.incoming/<id>/`;
- never writes or creates anything through a symlink: every file and directory is created with its parent checked not to be a link (`O_NOFOLLOW`, `lstat`).

### Executable files

NTFS mounted with `ntfs-3g` doesn't keep a reliable exec bit (without a user mapping every file looks executable, or none does), and exFAT cards have none at all. So the indexer ignores the file mode and decides from the **first bytes** of the file:

| Starts with | Kind | `executable` |
|---|---|---|
| `7F 45 4C 46` (`\x7fELF`) | ELF binary or shared object; AppImages (type 1 and 2) are ELF too | `true` |
| `23 21` (`#!`) | script with a shebang | `true` |
| anything else | data, Windows `.exe`/`.dll` (Proton runs those without the bit), images, etc. | `false` |

The installer creates files with mode `0755` when `executable` is `true` and `0644` otherwise, directories with `0755`. It never applies modes from the server. The launch script stays started as `/bin/bash "<root>/insertplay-start.sh"`, as on a card, so the game starts even if a bit is wrong.

### Path rules

Every `path` in a file list (and `games[].path`, which is a single segment) must be:

- relative, POSIX, UTF-8, segments separated by `/`;
- free of empty segments (no leading `/`, no trailing `/`, no `//`) and of `.` and `..` segments;
- free of `\`, NUL and other control characters (U+0000 to U+001F, U+007F);
- at most 255 bytes per segment and 4096 bytes in total;
- unique across `files`, `dirs` and `links` together, and never an ancestor of another entry when it is a file or a link (no `a` as a file or link next to `a/b`);
- not ending in `.part` (reserved for the client's partial files).

The indexer refuses a game that breaks them; **the client enforces them again** on every list it reads and refuses the whole game (`bad_path`), never just the file. Paths are URL-encoded segment by segment when building a download URL: `urljoin(base, library) + quote(game path) + "/" + quote(file path, safe="/")`.

## Client

### Folders on the device

| Path | Content |
|---|---|
| `~/Games/InsertPlay/<id>/` | Installed game: the "virtual card". Exists only when the game is fully installed and verified. |
| `~/Games/InsertPlay/.incoming/<id>/` | Download in progress, same filesystem as the install folder. |
| `~/homebrew/data/InsertPlay/library/state.json` | Queue, per-game state, installed games (id, version, size, install date). |
| `~/homebrew/data/InsertPlay/library/index.json` | Last index fetched, so the panel lists games without the server. |
| `~/homebrew/data/InsertPlay/library/lists/<id>.json` | File list pinned when the install started. |
| `~/homebrew/data/InsertPlay/library/artwork/<id>/<kind>.<ext>` | Artwork cache (see [Artwork](#artwork)). |
| `~/homebrew/settings/InsertPlay/remote_library.json` | Server URL and token, mode `0600`. |

`~/homebrew/data/InsertPlay` is Decky's `DECKY_PLUGIN_RUNTIME_DIR` and `~/homebrew/settings/InsertPlay` is `DECKY_PLUGIN_SETTINGS_DIR`. State files are written atomically (temporary file + rename, like the registry).

`{SD_ROOT}` in the manifest resolves to the install folder (`/home/deck/Games/InsertPlay/<id>`), so the shortcut, the launch script, the guid guard and achievements work as on a card.

### Install

One game downloads at a time; the others wait in a FIFO queue. For the active game, a single backend thread:

1. **Fetches the file list**, checks `filesSha256`, `id`, `version` and the path rules, and pins it in `lists/<id>.json`.
2. **Checks free space** with `statvfs` on `~/Games/InsertPlay` (`f_bavail × f_frsize`): needed = `totalSize` − bytes already verified in `.incoming/<id>/` + a margin of 256 MiB. Not enough: `no_space`, nothing is downloaded. The same check runs synchronously in `remote_install`, so the user sees it at once.
3. **Downloads each file** to `.incoming/<id>/<path>.part`:
   - a file already verified is skipped. A file gets its final name in `.incoming/<id>/` only after its sha256 matched, so a regular file with the final name and the listed size counts as verified when a download resumes; the state keeps only the counters (`bytesDone`, `bytesVerified`, `filesDone`), since a list of ~30 000 verified paths rewritten every 2 s would cost more than checking the names. When a new install finds a `.incoming/<id>/` the state doesn't know (state lost, a cancel that timed out), its first scan hashes those files instead and deletes the ones that don't match;
   - a `.part` with `n` bytes resumes with `Range: bytes=n-`; the answer must be `206` with `Content-Range: bytes n-<size-1>/<size>`. A `200` restarts the file from 0; a `416` or a different total size means the server changed (`server_changed`);
   - a `.part` larger than `size` is deleted;
   - the client reads at most `size − n` bytes of a body; a longer or shorter body is `server_changed` (a shorter one only after the retries, since a dropped connection also ends a body early);
   - the sha256 is computed while writing (an existing `.part` is hashed first);
   - at `size` bytes, a hash mismatch deletes the `.part` and downloads the file again, once; a second mismatch is `hash_mismatch`;
   - a match renames `.part` to the final name with mode `0644`/`0755` and records the file as verified.
4. **Creates** the `dirs` and the `links`.
5. **Installs**: renames `.incoming/<id>/` to `~/Games/InsertPlay/<id>/` (atomic, same filesystem) and records the game as installed with its `version`. Only then does the state become `installed`. A half-downloaded game never looks installed.

If `~/Games/InsertPlay/<id>/` already exists and the state doesn't record the game as installed, the install stops with `io_error`: the client never merges into or deletes a folder it didn't install.

HTTP answers during an install:

| Answer | Outcome |
|---|---|
| Network error (timeout, refused connection, reset, e.g. after a suspend) or `5xx` | Retried with backoff (2, 5, 10, 30, 60 s); after that the game goes to `error` (`unreachable`) and keeps its verified files. |
| `401` | `unauthorized` at once, no retry. |
| `404` or another `4xx` on a file | `server_changed`. |
| `3xx` | Error (`server_changed`); redirects are never followed. |

Socket timeout: 30 s. All requests of an install go over one persistent HTTP/1.1 connection (keep-alive), reopened after an error: with ~30 000 files, a connection per file would cost more than the files themselves.

The state is saved at every state change and at most every 2 s while downloading (a game with ~30 000 small files would otherwise rewrite it ~30 000 times). When the plugin loads, games that were `downloading` or `queued` go back to the queue and resume; `paused` and `error` stay as they were.

Resuming uses the pinned file list. If the server's file list changed in the meantime, the client notices by a size, `404` or `416` mismatch and stops with `server_changed` (a file changed with the same size shows up as `hash_mismatch`); cancelling and installing again starts with the new list.

### Uninstall and cancel

- **Uninstall** deletes `~/Games/InsertPlay/<id>/` and the game's state, list and installed record. The game goes back to `not_installed` (its artwork cache stays while the game is in the index). Saves written inside the game folder (memory cards, `CLAW.USR`, a Wine prefix) are deleted with it: the panel must say so before confirming.
- **Cancel** stops a queued, downloading, paused or failed install and deletes `.incoming/<id>/`. The game goes back to `not_installed`.
- Before deleting, the client checks that `<id>` is a valid lowercase guid (the regex in [`index.json` v1](#indexjson-v1)), that the folder is exactly `~/Games/InsertPlay/<id>` or `~/Games/InsertPlay/.incoming/<id>` (its parent's `realpath` equal to the `realpath` of `~/Games/InsertPlay` or of `.incoming`), that it is not a symlink, and deletes without following symlinks (`shutil.rmtree` on a checked real directory). In the state folder, only `lists/<id>.json` and `artwork/<id>/`, with the same checks. Nothing outside these paths is ever deleted.

### Artwork

The "Instalar" shortcut exists before the game is downloaded, so it needs its artwork first. On every index refresh the backend downloads each game's `artwork` files into `library/artwork/<id>/<kind>.<ext>`, keeping the source's extension (`png`, `jpg` or `jpeg`; any other extension is skipped), since the frontend tells Steam the image format from it. Each file is checked by sha256 against the index (and limited to its `size`) and skipped when the cached hash already matches; artwork of games no longer in the index (and not installed) is removed. The frontend reads it with `remote_artwork_base64`, and #45 lets `shortcut_icon` take the cached icon as its source (today it accepts only files on a mounted card). After install the cache is still the source, so the artwork doesn't change between "Instalar" and "Jogar".

## Backend RPC contract

Methods of the `Plugin` class in `main.py`, called from the frontend with `callable` (and by `deck.mjs rpc`). The logic lives in `py_modules/insertplay_decky/remote_library.py` without `import decky`; one worker thread does network and disk work, and the RPCs only read and change the state under a lock, so none of them blocks Decky's event loop on the network except `remote_refresh` and `remote_install` (which run in `asyncio.to_thread`).

### Results and errors

Actions return `{"ok": true, ...}` or `{"ok": false, "code": "<code>", "message": "<pt-BR text for the UI>"}`. As with the other RPCs (`guarded`), an unexpected exception returns `null`: "don't know", which the frontend must not read as "no games". Error messages never contain the token or request headers.

| `code` | When |
|---|---|
| `not_configured` | No URL or no token. |
| `bad_config` | `remote_set_config` with a URL that is not `http://` or `https://`, has user info (`user:pass@`), a query or a fragment, or a token with characters not allowed in an HTTP header (control characters, spaces). |
| `unauthorized` | The server answered `401`. |
| `unreachable` | Connection error or timeout after the retries. |
| `bad_index` | Index or file list unreadable, wrong `schemaVersion`, too big, hash or `id`/`version` mismatch. |
| `bad_path` | A path in the file list breaks the [path rules](#path-rules). |
| `unknown_game` | The id is not in the index (or, for uninstall, not installed). |
| `invalid_state` | The action doesn't apply to the game's state (e.g. pausing an installed game). |
| `no_space` | Not enough free space; the result also has `needed` and `free` in bytes. |
| `hash_mismatch` | A file failed the sha256 twice. |
| `server_changed` | The server's file differs from the pinned list. |
| `io_error` | Local disk error (permission, disk full during the download). |

### Game status

The shape every method below uses for a game:

```json
{
  "id": "fa41e0e7-0133-4f21-b5f4-1a143a2f52ef",
  "name": "Test Drive 6",
  "state": "downloading",
  "percent": 42,
  "bytesDone": 223600000,
  "bytesTotal": 532381696,
  "speed": 30800000,
  "filesDone": 12,
  "fileCount": 41,
  "error": null,
  "version": "b32d2fa489eeee59",
  "installedVersion": null,
  "updateAvailable": false,
  "inIndex": true,
  "installPath": "/home/deck/Games/InsertPlay/fa41e0e7-0133-4f21-b5f4-1a143a2f52ef",
  "shortcut": {
    "guid": "fa41e0e7-0133-4f21-b5f4-1a143a2f52ef",
    "appName": "Test Drive 6",
    "exe": "/bin/bash",
    "startDir": "/home/deck/Games/InsertPlay/fa41e0e7-0133-4f21-b5f4-1a143a2f52ef",
    "launchOptions": "\"/home/deck/Games/InsertPlay/fa41e0e7-0133-4f21-b5f4-1a143a2f52ef/insertplay-start.sh\" --guid fa41e0e7-0133-4f21-b5f4-1a143a2f52ef",
    "artwork": { "capsule": "/home/deck/homebrew/data/InsertPlay/library/artwork/fa41e0e7-0133-4f21-b5f4-1a143a2f52ef/capsule.png" },
    "icon": "/home/deck/homebrew/data/InsertPlay/library/artwork/fa41e0e7-0133-4f21-b5f4-1a143a2f52ef/icon.png",
    "game": { "platform": "ps1", "emulator": "duckstation", "retroAchievements": null }
  }
}
```

| State | Meaning | Extra fields |
|---|---|---|
| `not_installed` | In the index, nothing on the device. | — |
| `queued` | Waiting for the active download. | `bytesDone`/`percent` if part of it was already downloaded |
| `downloading` | Active download. | `percent` (0–99, integer), `bytesDone`, `bytesTotal`, `speed` (bytes/s, average of the last 5 s), `filesDone`, `fileCount` |
| `paused` | Stopped by the user; verified files and `.part` kept. | same as `downloading`, `speed` 0 |
| `installed` | Folder in place, every hash checked. | `installedVersion`; `updateAvailable` when the index has another `version` (updating is out of the POC) |
| `error` | Stopped by an error; files kept. | `error: {"code", "message"}` |

- `percent` is `floor(100 × bytesDone / bytesTotal)` (0 when `bytesTotal` is 0), capped at 99 until the game is `installed`; `bytesDone` counts verified files plus `.part` bytes.
- `shortcut` has the shape of a card in `current_cards` (`manifest.load_card`), with `{SD_ROOT}` resolved to `installPath` and the artwork pointing to the cache, so #45 creates the shortcut with the code it already uses for cards. It is present in every state, including `not_installed`.
- `inIndex: false`: an installed game that is no longer in the index. It stays installed and can be uninstalled; it can't be installed again.

### Methods

| Method | Arguments | Returns |
|---|---|---|
| `remote_get_config()` | — | `{"url": str or null, "tokenSet": bool}`. Never the token. |
| `remote_set_config(url, token)` | `url`: base URL or `""` to clear; `token`: new token, `""` to clear, `null` to keep the current one | `{"ok": true, "url", "tokenSet"}` or an error (`bad_config`). Does not contact the server. |
| `remote_refresh()` | — | Fetches `index.json` and the artwork. `{"ok": true, "games": [status…], "generatedAt"}` or an error; on error the cached index stays. |
| `remote_games()` | — | From the cached index and the state, no network: `{"ok": true, "games": [status…], "stale": bool, "fetchedAt": epoch seconds or null}`. `stale` is true when the last refresh failed. Installed games missing from the index are included with `inIndex: false`. |
| `remote_install(id)` | guid | Checks the config, the state and the free space, then queues. `{"ok": true, "game": status}` (`queued` or `downloading`), or an error (`no_space` with `needed`/`free`). On a game already `queued` or `downloading`, it changes nothing and returns `ok` with the status (a second press of "Instalar" is harmless). On a game in `error` or `paused`, it retries keeping the verified files. On `installed`: `invalid_state`. |
| `remote_pause(id)` | guid | `downloading` or `queued` → `paused`. The worker stops after the current block and starts the next game in the queue. `{"ok": true, "game": status}`. |
| `remote_resume(id)` | guid | `paused` or `error` → `queued` (at the end of the queue; starts at once if nothing is active). |
| `remote_cancel(id)` | guid | Any state except `installed` and `not_installed` → `not_installed`, deleting `.incoming/<id>/`. |
| `remote_uninstall(id)` | guid | `installed` → `not_installed`, deleting the install folder (a game with `inIndex: false` then leaves the list). The frontend checks first that the game isn't running (`SteamUIStore.RunningApps`). |
| `remote_progress()` | — | `{"active": id or null, "queue": [id…], "games": [status…]}` with only the games in `queued`, `downloading`, `paused` or `error`. Cheap: for a timer in the frontend. |
| `remote_artwork_base64(id, kind)` | guid, artwork kind | Base64 of the cached artwork, or `""` if there is none. `id` must be a valid guid and `kind` one of the five kinds; it reads only from `library/artwork/<id>/`. |

Event: the backend emits `remote_state` (payload: the `remote_progress()` result) when a game changes state, and at most once a second while downloading. The frontend may use the event, the timer, or both; both carry the same data.

### What #45 builds on this

Implemented in `src/flow.ts` (shortcuts, panel actions) and `src/remote-steam.ts` (Steam's game page); measurements in [steam-api-notes.md](steam-api-notes.md#remote-library-in-steams-game-page-issue-45-measured-2026-11-13-steamos-395-decky-v331).

- One shortcut per game of `remote_games()` (index and installed games), created from `status.shortcut` with the cached artwork (`remote_artwork_base64`) and icon (`shortcut_icon` accepts the icon of the game's own artwork cache), recorded in the same registry (`guid → appid`) with `source: "library"`. "Hide on removal" skips those while the game is in the library list (and all of them while the list is unknown). Creating or updating them never toasts nor runs "Ao inserir": an installed game is not an insertion.
- What the game page shows, from the status: `not_installed` and `error` → `installed=false, display_status=9` ("Instalar"); `downloading` → `display_status=7` plus the downloads overview ("BAIXANDO / Progresso: N%", "Pausar"); `paused` → 22 ("DOWNLOAD PAUSADO", "Retomar"); `queued` → 23 ("DOWNLOAD NA FILA"); `installed` → Steam's own "Jogar". Re-applied on `RegisterForAppOverviewChanges` and every second, written only when different.
- Steam's buttons on a library shortcut: "Instalar" (`OpenInstallWizard`) → `remote_install(id)` (errors such as `no_space` become a toast); "Retomar" (`ResumeAppUpdate`) → `remote_resume(id)`; "Pausar" (`EnableAllDownloads(false)`) → `remote_pause(id)` while the page shows our download. Any other appid goes to Steam unchanged, and the originals come back on plugin unload.
- Steam has no uninstall entry for a non-Steam shortcut: the panel's "Desinstalar" calls `remote_uninstall(id)` after a confirmation that says saves inside the game folder go with it, and refuses while the game is running.
- A physical card with the same guid is the same shortcut; the inserted card wins for `{SD_ROOT}` (and the page shows "Jogar") while it is mounted. When it leaves, the shortcut goes back to the install folder and the library state.
- Library tabs (#52, `src/library-tabs.tsx`): every library shortcut is listed in Game Mode's "Todos os jogos" tab; an `installed` game also in "Instalados". Membership comes from the game state, not from Steam's `per_client_data`, which the plugin rewrites for "Instalar".

## Saves

Save sync ([#63](https://github.com/insert-play/insertplay/issues/63)) keeps the latest save of each remote library game on the server, so the same game can be played on two devices. v1 covers non-Steam games of the library, by guid, whether installed from the library or played from a card with the same guid.

### Key

A save is stored under a **key**: the game's lowercase guid today, `steam-<appid>` for Steam games later. Regex: `^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|steam-[0-9]{1,10})$`.

### Server API

Served by the save server ([`server/saves/saves_server.py`](../server/saves/saves_server.py)) behind Caddy, under the same base URL and token. `/saves/*` is the only route that accepts `PUT` and `POST`; the save server checks the token again.

| Request | Answer |
|---|---|
| `GET saves/<key>/meta` | `200` with `meta.json`, `404` with no save yet. |
| `GET saves/<key>/current.tar.gz` | `200` with the save and the `X-Save-Rev` and `X-Save-Hash` of that same file (a `meta` read before may already be stale), `404` with none. |
| `PUT saves/<key>/current.tar.gz` | Replaces the save. `201` (first save) or `200`, with `{"meta": …, "backup": …}`. |
| `GET saves/<key>/strategy.json` | `200` with the inferred strategy, `404` with none. |
| `PUT saves/<key>/strategy.json` | Stores it (a JSON object with a non-empty `strategy`, at most 64 KiB). |
| `GET saves/<key>/backups` | `{"backups": [{"name", "size", "mtime"}…], "limit"}`, newest first. |
| `GET saves/<key>/backups/<name>` | One backup. |
| `POST saves/<key>/backups` | Stores a save the device is about to overwrite, or that lost a conflict. Header `X-Save-Device`. `200` with `{"stored": true, "name"}` or `{"stored": false, "reason": "limit"}`. |

`meta.json`: `{"rev": "<n>", "hash": "<sha256>", "newestMtime": <unix time>, "device": "<name>", "uploadedAt": <unix time>, "size": <bytes>}`.

`PUT current.tar.gz` needs a body with `Content-Length` (no chunked encoding) and:

- `If-Match: <rev>` with the rev the device last saw, or `If-None-Match: *` for the first save of the key. Missing: `428`. A rev that no longer matches (the other device uploaded first), or `*` when a save already exists: `409` with `{"error": "conflict", "meta": <current meta or null>}`. Nothing changes; the device pulls and decides again.
- `X-Save-Hash` (the [save hash](#save-hash), 64 hex digits), `X-Save-Newest-Mtime` (newest file time of the save), `X-Save-Device` (letters, digits, `.`, `_`, `-`; up to 64). Invalid: `400`.
- `X-Save-Backup: 1` keeps the save being replaced as a backup named after the device that made it.

Other errors: `411` without a plain `Content-Length`, `400` when the body ends early, `507` when the server's disk fails (the old save stays), `405` for other methods, `500` in JSON for anything unexpected. A meta edited by hand without a valid `rev` counts as rev `0`.

Backups are kept within `INSERTPLAY_SAVE_BACKUP_MB` per game (default 50), oldest removed first. The limit applies only to backups: a save larger than it gets no backup (`"stored": false, "reason": "limit"`) and still syncs normally (Alan Wake 2: 159 MB). Bodies larger than `INSERTPLAY_SAVE_MAX_MB` (default 1024): `413`. Uploads are written to a temporary file and renamed, under a lock per key.

### Where a game keeps its save

In order:

1. the `saves` block of the game's manifest **in the current index** (the plugin hands it to the wrapper in `saves/hints/<key>.json`), so a block fixed in the library reaches games installed before the fix; without an index entry, the block of the installed `steam-shortcut.json` (or the card's) ([card-format.md](card-format.md#saves));
2. `saves/<key>/strategy.json` on the server, written by an earlier inference;
3. **inference**: with `game.emulator` declared, that emulator's strategy; with a `prefix/` folder and no `game` block, `proton-default`. It counts only when the strategy's folder exists and has at least one file. The result (strategy and its default `exclude`) is written to the server, never to the manifest: the library stays read-only.

No strategy found: the game opens without sync and the plugin shows an error notification.

### Save hash

The sha256 of the sorted list of `(path, size, sha256)` of every regular file of the save, paths relative to the game folder with `/`. Symlinks are never followed nor stored (a Proton prefix has `My Documents → Documents`, which would count every file twice). Excluded paths are left out.

### Client

State per key on the device: `lastLocalHash` and `lastServerHash` (each side's hash after the last sync), `lastRev` (the server rev then) and a pending upload flag. The two hashes differ only after a download that left out paths this device excludes (they stay on the server, see below); "local changed" and "server changed" in the table compare against them.

**When the game opens**, before the game itself:

| Server | Local save | Action |
|---|---|---|
| no save | any | upload the local one (if any) |
| same hash as local | | nothing |
| changed since `lastSyncedHash` | unchanged | download. The local save it replaces is sent to the backups first, when backups are on |
| any, and this device never synced the game (at launch, no upload pending) | different | the server's save wins and the local one goes to the backups: a first sync can't trust file times (a save shipped with the game has the time of the download). If the local save can't be kept (larger than the backup limit), the times decide as below |
| changed | changed too | the newer `newestMtime` wins; the loser goes to the backups. Also for a save played without history (server unreachable at launch, upload pending) |

An unreachable server at a launch without history queues nothing: the local files may be the install's own, untouched.
| unreachable (~10 s) | | play with the local save; the upload stays pending |

A download is checked file by file and as a whole (the archive carries its file list and hash), extracted to a staging folder in the game folder (`.insertplay-saves.partial`), then each file is renamed into place and the save files the archive doesn't have are removed. It only writes inside the strategy's save folders and never through a symlink; a path this device excludes (synced by another device or by an older `saves` block) is skipped, not written. A path outside the save folders refuses the whole archive. Excluded paths and symlinks already on the device stay as they are. A local save that would be overwritten goes to the server's backups first (when backups are on, and always for a conflict loser), unless it is larger than the server's backup limit.

**When the game closes**, the save is uploaded if its hash changed. A pending upload is retried on the next close, when the plugin starts and periodically. An upload never runs while the game is open (a lock per key, held by the launch wrapper for the whole game).

### Launch hook

The shortcut of a library game (installed, or a card with the same guid) runs a wrapper in front of the card's script. With `exe` `/bin/bash`, the options become:

```
"/home/deck/.local/share/insertplay/bin/insertplay-run" <key> "<game folder>" -- "/bin/bash" "<game folder>/insertplay-start.sh" --guid <guid>
```

The card and the manifest are not changed. A card whose `exe` is not a shell, or a game folder with `"`, `$`, `` ` `` or `\` in its path, keeps its line and has no sync. The wrapper:

- checks that `<root>/shortcut-identity.json` has the same guid as `<key>` (the card's guard refuses a wrong card but exits 0);
- downloads the save, runs the script without `exec` and uploads at the end;
- after Steam's "Sair do jogo", which kills the whole game ~1.7 s after SIGTERM (measured: [steam-api-notes.md](steam-api-notes.md#save-sync-launch-hook-issue-63-stage-0-measured-2026-11-14-steamos-395-decky-v331)), starts the upload in a detached `systemd-run --user` unit that waits for the lock;
- holds a `flock` on `saves/locks/<key>.lock` for the whole game (the game inherits it);
- falls back to running the command directly when `python3` or its module is missing, sync is off, the card is not the game, or no strategy is found.

The plugin also uploads when Steam reports the game closed, as a safety net. The wrapper lives outside the plugin folder so it keeps working when the plugin is removed.

### Files on the device

| Path | Content |
|---|---|
| `~/.local/share/insertplay/bin/insertplay-run` | The wrapper (bash). |
| `~/.local/share/insertplay/lib/` | `insertplay-saves.py` and a copy of `py_modules/insertplay_decky/saves/` as `insertplay_saves/`, run with the system `python3 -I`. |
| `~/.local/share/insertplay/saves/config.json` | Server URL and token, `sync`, `backup`, device name (mode `0600`). Written by the plugin from its settings. |
| `~/.local/share/insertplay/saves/state/<key>.json` | `lastLocalHash`, `lastServerHash`, `lastRev`, `pending`, the game folder last used and the strategy last read from the server or inferred (used offline). |
| `~/.local/share/insertplay/saves/hints/<key>.json` | `{"saves": <block or null>}` from the index for each library game, rewritten at plugin load, on every successful index refresh and on a server change. `null`: the index has no block, so the installed one is ignored too (the server's `strategy.json` or inference decide). A game out of the index has no hint. |
| `~/.local/share/insertplay/saves/last/<key>.json` | Result of the last operation (`op`, `result`, `message`, `at`, `strategy`, `source`, `hash`, `rev`…). |
| `~/.local/share/insertplay/saves/sync.log` | One line per operation. |

The plugin rewrites the wrapper and the module at load when their content changed.

### RPCs

| Method | Returns |
|---|---|
| `saves_status(guid)` | `{guid, last, pending, rev}`: `last` is the last result record, or `null`. |
| `saves_overview()` | `{<guid>: {last, pending, rev}}` for the library games with any sync history. |
| `saves_push(guid)` | The result record of an upload attempt (`uploaded`, `unchanged`, `offline`, `busy` while the game runs, `not_library`, `not_here`…). |
| `saves_retry_pending()` | The result records of the pending uploads it retried. |

Result values: `uploaded`, `downloaded`, `unchanged`, `empty` (no save on either side), `conflict_uploaded`, `conflict_downloaded`, `offline`, `disabled`, `wrong_card`, `no_strategy`, `busy`, `error`. The settings `savesSync` (default off) and `savesBackup` (default on) are the panel's "Sincronizar saves" and "Guardar backup dos saves".

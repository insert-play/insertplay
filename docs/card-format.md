# InsertPlay card format

An InsertPlay card is an SD card (or USB drive) with a game and a small manifest at its root. When the card is mounted, the plugin reads the manifest, creates or updates a non-Steam shortcut for the game, applies the artwork and, if enabled, starts the game. The plugin never writes these files: cards are prepared by hand, starting from [`card-template/`](../card-template/). A tool to build cards from the plugin is tracked in [#19](https://github.com/insert-play/insertplay/issues/19).

## Layout

Every card uses the same layout at its root. Only what InsertPlay and RetroAchievements use is fixed; the emulator's and the game's own folders are up to you.

```
<card root>/
├── steam-shortcut.json        manifest, with the "game" block on emulator cards (required)
├── shortcut-identity.json     card guid (required)
├── insertplay-start.sh        launch script; the card root is the script's own folder
├── insertplay-guid-guard.sh   guid check, sourced by the launch script
├── steam-artwork/             icon.png, capsule.png, horizontal.png, hero.png, logo.png (optional)
├── insertplay/decky/InsertPlay.zip   the plugin itself, for installing and updating (optional)
├── libraryfolder.vdf, steamapps/, lost+found/   created by Steam's "Format" and by ext4: keep them
└── …                          the emulator and the game (e.g. duckstation/, game.cue, game.bin)
```

The plugin looks for cards in `/run/media/*` and `/run/media/*/*` every second. `steam-shortcut.json` and `shortcut-identity.json` are read from the card root first and then from `insertplay/`, the layout of older cards; new cards keep them at the root.

## `steam-shortcut.json`

```json
{
  "schemaVersion": 1,
  "shortcut": {
    "appName": "My Game",
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
  }
}
```

| Field | Required | Meaning |
|---|---|---|
| `shortcut.appName` | yes | Name of the shortcut in the Steam library. |
| `shortcut.launch.exe` | yes | Executable of the shortcut. Use `/bin/bash` with the script in `args`: the script then needs no exec bit, which exFAT cards can't store. |
| `shortcut.launch.args` | no | Launch options. The plugin appends ` --guid <guid>` to them. |
| `shortcut.launch.startDir` | no | Working directory. Defaults to the card root. |
| `shortcut.artwork.<kind>` | no | Artwork path, relative to the card root or absolute. Kinds: `icon`, `capsule`, `horizontal`, `hero`, `logo`. |

- `{SD_ROOT}` is replaced by the card mount point in `exe`, `args`, `startDir` and the artwork paths. The mount point depends on the card label and the device, so always use it instead of a fixed path.
- Keys are case-insensitive (`appName` and `AppName` both work).
- A missing or unreadable artwork falls back to `steam-artwork/<kind>.png`; without it, Steam's default stays.
- Steam keeps only the path of a shortcut icon, so the plugin copies `icon` to the device (`~/homebrew/data/InsertPlay/icons/`). The icon stays visible after the card is removed. An icon the user picked in Steam is never replaced.

### `game`

Optional block, at the top level next to `shortcut`, that says which game and emulator the card holds. The plugin uses it for RetroAchievements (see the [README](../README.md#achievements-retroachievements)); a card without it works as before.

```json
{
  "schemaVersion": 1,
  "shortcut": { "...": "..." },
  "game": {
    "platform": "ps2",
    "emulator": "pcsx2",
    "retroAchievements": { "gameId": 5613 }
  }
}
```

| Field | Meaning |
|---|---|
| `game.platform` | `ps1`, `ps2`, `gc`, `wii`, `wiiu`, … Lowercase letters, digits, `-` and `_`. |
| `game.emulator` | `duckstation`, `pcsx2`, `dolphin`, `cemu`, … Same format. |
| `game.retroAchievements.gameId` | The game's ID on retroachievements.org: the number in `retroachievements.org/game/<id>`. A JSON integer greater than 0 (not a string). Only cards with it show achievements. |

- Keys are case-insensitive, as in `shortcut`.
- An invalid value is ignored without refusing the card: the shortcut still works, only that field is missing (`rpc current_cards` shows the block as the plugin read it).
- For a game with subsets, use the main game's ID. Subsets are not shown.

## `shortcut-identity.json`

```json
{ "guid": "3f2b8c1e-5d4a-4f7b-9c2e-8a1d6e0b7c45" }
```

The guid ties the card to its shortcut: the same card inserted again, even on another slot or under another label, reuses the same shortcut. **Every card needs its own guid.** Never copy one between cards, and replace the all-zero guid of the template. To generate one:

- Linux: `uuidgen`
- Windows (PowerShell): `[guid]::NewGuid()`

Without a valid `shortcut-identity.json`, the card is refused and the panel shows the reason.

## Launch script

The plugin starts the shortcut as `exe args --guid <guid>`. The template's [`insertplay-start.sh`](../card-template/insertplay-start.sh) lives at the card root and takes the card root from its own folder (`SD_ROOT`). Then:

1. **Guid check.** It sources [`insertplay-guid-guard.sh`](../card-template/insertplay-guid-guard.sh), which compares `--guid` with the card's `shortcut-identity.json` and refuses to start if they differ. Without it, a shortcut left in the library could start whatever game is on the card currently inserted. Keep it, and keep the guard file the same on every card.
2. **Game.** It runs an emulator AppImage on the card with the game's disc image, and points the emulator's data folder (settings, BIOS, memory cards, saves) to the card, so everything travels with it. Edit `EMULATOR`, `GAME` and the data-folder variable for your emulator:

| Emulator | How | Data folder |
|---|---|---|
| DuckStation | `export XDG_DATA_HOME="$SD_ROOT"` | `duckstation/` |
| PCSX2 | `-portable` argument | `PCSX2/` next to the AppImage (e.g. `pcsx2/PCSX2/`) |
| Cemu | a `portable/` folder next to the AppImage; game with `-g`, fullscreen with `-f` | `cemu/portable/` |

Keep paths in the emulator's settings relative to its data folder, so the card works under any mount point.

- **Several discs:** write a `.m3u` with one disc file name per line (e.g. `disc1.cue`, `disc2.cue`) and point `GAME` to it. The emulator then swaps discs by itself. DuckStation names a per-game memory card after the `.m3u` file, so name it like the existing card (e.g. `Heart of Darkness (USA).m3u` for `Heart of Darkness (USA)_1.mcd`).
- **`.cue` files:** the `FILE` line must name the `.bin` next to it. A `.bin` renamed after the `.cue` was written (e.g. to `game.bin`) needs the line fixed.
- **A Windows game:** replace the game block with Proton, the Steam Linux Runtime and a Wine prefix on the card:

```bash
GAME="$SD_ROOT/game/Game.exe"
PREFIX="$SD_ROOT/prefix"
export STEAM_COMPAT_DATA_PATH="$PREFIX"
export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam"
export STEAM_RUNTIME_PREFER_HOST_LIBRARIES=0
mkdir -p "$PREFIX"
cd "$(dirname "$GAME")"
exec "$SD_ROOT/runtime/SteamLinuxRuntime_4/_v2-entry-point" -- "$SD_ROOT/proton/proton" run "$GAME"
```

A Windows game card has no `game` block.

## Migrating an older card

Cards made for the legacy InsertPlay (the C# CLI) work with the plugin as they are, but carry leftovers. To bring one to the layout above:

1. Back up, off the card, the files you change.
2. In `steam-shortcut.json`: fix `appName`, add the `game` block and drop `shortcut.visibility` and `shortcut.launchVerification` (the plugin ignores them).
3. Replace `insertplay-start.sh` and `insertplay-guid-guard.sh` with the template's, keeping the card's own game block. The old script found the card root by looking for `autorun.sh`.
4. Test that the game starts from its shortcut, then delete the leftovers: `autorun.sh`, `install-shortcut.sh`, `sd-monitor.sh`, `insertplay.sh`, `insertplay.update.lock`, `delay-suspend.sh`, `assets/`, a `pulse/` folder (written by the old script) and everything in `insertplay/` **except `insertplay/decky/`**. Make sure `steam-shortcut.json` and `shortcut-identity.json` are at the root first.
5. Remove and insert the card again. The plugin reads the manifest when the card is inserted, so a shortcut name or GameID edited on a card already inserted only shows up after that (or after "Atualizar agora" in the panel).

## Plugin package on the card

A card can carry the plugin itself at `insertplay/decky/InsertPlay.zip`. Decky installs it with "Install Plugin from ZIP File", and the plugin panel offers to update from it (see the [README](../README.md)). To put it there, either:

- copy `InsertPlay.zip` from a [release](https://github.com/insert-play/insertplay/releases) to `insertplay/decky/`; or
- with the card inserted in a device that has the plugin, use **Gravar plugin vX no cartão** ("write plugin vX to the card") in the panel's **Plugin** section.

## Filesystem

- Any filesystem SteamOS mounts works for InsertPlay cards.
- **Portable saves** (Steam games installed on the card, see the README) need **ext4**, the format Steam's "Format" option uses. On exFAT/NTFS that option is disabled.

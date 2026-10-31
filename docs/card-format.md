# InsertPlay card format

An InsertPlay card is an SD card (or USB drive) with a game and a small manifest at its root. When the card is mounted, the plugin reads the manifest, creates or updates a non-Steam shortcut for the game, applies the artwork and, if enabled, starts the game. The plugin never writes these files: cards are prepared by hand, starting from [`card-template/`](../card-template/). A tool to build cards from the plugin is tracked in [#19](https://github.com/insert-play/insertplay/issues/19).

## Layout

```
<card root>/
├── steam-shortcut.json      manifest (required)
├── shortcut-identity.json   card guid (required)
├── insertplay-start.sh      launch script (name and location are up to the manifest)
├── steam-artwork/           icon.png, capsule.png, horizontal.png, hero.png, logo.png (optional)
├── insertplay/decky/InsertPlay.zip   the plugin itself, for installing and updating (optional)
└── …                        the game, Proton, runtime, prefix, etc.
```

The plugin looks for cards in `/run/media/*` and `/run/media/*/*` every second. `steam-shortcut.json` and `shortcut-identity.json` are read from the card root first and then from `insertplay/`, the layout of older cards.

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

## `shortcut-identity.json`

```json
{ "guid": "3f2b8c1e-5d4a-4f7b-9c2e-8a1d6e0b7c45" }
```

The guid ties the card to its shortcut: the same card inserted again, even on another slot or under another label, reuses the same shortcut. **Every card needs its own guid.** Never copy one between cards, and replace the all-zero guid of the template. To generate one:

- Linux: `uuidgen`
- Windows (PowerShell): `[guid]::NewGuid()`

Without a valid `shortcut-identity.json`, the card is refused and the panel shows the reason.

## Launch script

The plugin starts the shortcut as `exe args --guid <guid>`. The template's [`insertplay-start.sh`](../card-template/insertplay-start.sh) does two things:

1. **Guid check.** It compares `--guid` with the card's `shortcut-identity.json` and refuses to start if they differ. Without it, a shortcut left in the library could start whatever game is on the card currently inserted. Keep this block.
2. **Game.** It runs a Windows game with a Proton build and the Steam Linux Runtime copied to the card, and with the Wine prefix on the card (`prefix/`), so the saves travel with the card. Edit `GAME`, the Proton and runtime paths and, if the game needs them, `WINEDLLOVERRIDES`. Any other launcher works too (an emulator with a ROM, a native Linux game): replace this block.

## Plugin package on the card

A card can carry the plugin itself at `insertplay/decky/InsertPlay.zip`. Decky installs it with "Install Plugin from ZIP File", and the plugin panel offers to update from it (see the [README](../README.md)). To put it there, either:

- copy `InsertPlay.zip` from a [release](https://github.com/insert-play/insertplay/releases) to `insertplay/decky/`; or
- with the card inserted in a device that has the plugin, use **Gravar plugin vX no cartão** ("write plugin vX to the card") in the panel's **Plugin** section.

## Filesystem

- Any filesystem SteamOS mounts works for InsertPlay cards.
- **Portable saves** (Steam games installed on the card, see the README) need **ext4**, the format Steam's "Format" option uses. On exFAT/NTFS that option is disabled.

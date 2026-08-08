# insertplay-cli

InsertPlay CLI MVP.

Current scope:
- setup
- users
- configure --user
- shortcut add
- shortcut update
- shortcut remove
- shortcut list

## Configuration

Configuration file:
- ~/.config/insertplay/config.json

## Commands

setup:
- detects Steam
- lists users
- saves steamPath and steamUserId
- automatically creates/updates the "Start SD Card" Steam shortcut

users:
- lists detected users
- marks the configured user with *

configure:
- changes the configured user
- usage: insertplay configure --user <steamUserId>

shortcuts:
- add: insertplay shortcut add --name <name> --exe <path> [--startdir <dir>] [--args <args>] [--icon <path>]
- update: insertplay shortcut update --name <name> --exe <path> [--startdir <dir>] [--args <args>] [--icon <path>]
- remove: insertplay shortcut remove --name <name>
- list: insertplay shortcut list

## Notes

- The project reuses the VDF/Steam logic as the foundation of the module in steam/.
- At this stage, setup only creates the "Start SD Card" shortcut; "Install SD Card" is left for future implementation.

## SD-card installation (self-contained)

- Build the SD-card package with: `bash src/build-sd-card.sh`
- Optional: pass the RID as an argument, e.g.: `bash src/build-sd-card.sh linux-x64`
- The script publishes the CLI (via `src/publish.sh`) and copies the result onto the mounted SD card's `insertplay/` folder, plus `autorun.sh`/`install-shortcut.sh`/`insertplay-guid-guard.sh` at the SD card root
- Expected SD-card structure:
	- `autorun.sh`
	- `install-shortcut.sh`
	- `insertplay-guid-guard.sh`
	- `insertplay/` (`InsertPlay.Cli`, `insertplay-service.sh`, `insertplay-start-bootstrap.sh`, `install.sh`, `uninstall.sh`)
- On the target PC, run: `bash insertplay/install.sh`
- The script checks whether a valid installation already exists.
- If not, it installs to `~/.local/share/insertplay`, creates `~/.local/bin/insertplay`, and runs `insertplay setup`.
- To uninstall, run: `bash ./uninstall.sh`
- To uninstall and remove configuration, run: `bash ./uninstall.sh --purge`

## Scripts

Scripts are split into dev tools, service scripts, SD-card scripts, and the installer.

### Dev tools (`src/`)

Used only during development and build automation. Never deployed to the target machine.

| Script | Purpose |
|---|---|
| `publish.sh` | Publishes the CLI as a self-contained binary into `publish/insertplay/` and `publish/assets/` |
| `build-sd-card.sh` | Runs `publish.sh` and copies the result onto a mounted SD card, alongside the SD-card scripts |

### Service scripts (`src/insertplay-service/`)

Linked into the CLI's build/publish output (flat, alongside `InsertPlay.Cli`) and deployed to the target machine via `install.sh`.

| Script | Purpose |
|---|---|
| `insertplay-service.sh` | systemd service that monitors removable media (`/run/media/`, `/media/`) and executes `autorun.sh` when an InsertPlay SD card is inserted |
| `insertplay-start-bootstrap.sh` | Steam shortcut launch script; always runs `install-shortcut.sh` (idempotent, never restarts Steam) and then delegates to the game's `insertplay-start.sh` on the SD card, regardless of install outcome |

### SD-card scripts (`src/insertplay-sdcard/`)

Copied to the SD card by `build-sd-card.sh`, not part of the CLI's own publish output.

| Script | Purpose |
|---|---|
| `autorun.sh` | Placed at the root of each InsertPlay SD card. Resolves the SD GUID from `shortcut-identity.json` and delegates to the CLI's `runtime autorun-launch` |
| `install-shortcut.sh` | Placed at the root of each InsertPlay SD card. Delegates to the CLI's `runtime install-shortcut`, which now bakes `--guid <shortcutGuid>` into the specific Steam shortcut's launch options |
| `insertplay-guid-guard.sh` | Placed at the root of each InsertPlay SD card. Sourced by `insertplay-start.sh`; refuses to start the game if the shortcut's `--guid` doesn't match the inserted card's `shortcut-identity.json` |
| `insertplay-start.sh` | Per-game launch template placed alongside the game/prefix/runtime/proton folders on the SD card. Walks up to the SD root and sources `insertplay-guid-guard.sh` before launching |

### Installer (`src/insertplay-cli/assets/installer/`)

Linked into the CLI's build/publish output (flat, alongside `InsertPlay.Cli`). Run manually on the target PC.

| Script | Purpose |
|---|---|
| `install.sh` | Deploys the CLI to `~/.local/share/insertplay`, copies the sibling `assets/` folder, creates `~/.local/bin/insertplay`, optionally installs the systemd service, and runs `insertplay setup` |
| `uninstall.sh` | Removes the CLI, launcher, and systemd service. Use `--purge` to also delete the configuration |

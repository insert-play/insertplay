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

- Build the SD-card package with: `bash scripts/build-sd-card.sh`
- Optional: pass the RID as an argument, e.g.: `bash scripts/build-sd-card.sh linux-x64`
- Output generated in: `dist/sd-card`
- Expected SD-card structure:
	- `install.sh`
	- `uninstall.sh`
	- `insertplay-cli-runtime/`
- On the target PC, run: `bash ./install.sh`
- The script checks whether a valid installation already exists.
- If not, it installs to `~/.local/share/insertplay`, creates `~/.local/bin/insertplay`, and runs `insertplay setup`.
- To uninstall, run: `bash ./uninstall.sh`
- To uninstall and remove configuration, run: `bash ./uninstall.sh --purge`

## Scripts

Scripts are split into two categories: **dev tools** and **runtime assets**.

### Dev tools (`scripts/`)

Used only during development and build automation. Never deployed to the target machine.

| Script | Purpose |
|---|---|
| `build-sd-card.sh` | Publishes the CLI as a self-contained binary and assembles the SD-card distribution package under `dist/sd-card/` |

### Runtime assets (`src/insertplay-cli/assets/runtime/`)

Copied into the build output and deployed to the target machine via `install.sh`.

| Script | Purpose |
|---|---|
| `insertplay-service.sh` | systemd service that monitors removable media (`/run/media/`, `/media/`) and executes `autorun.sh` when an InsertPlay SD card is inserted |
| `insertplay-start-bootstrap.sh` | Steam shortcut launch script; if specific shortcut mapping is missing it runs `install-shortcut.sh` and returns to Steam, otherwise it delegates to the game's `insertplay-start.sh` on the SD card |
| `autorun.sh` | Template script placed at the root of each InsertPlay SD card. Reads `runtime.env`, resolves the SD GUID from `shortcut-identity.json`, loads `~/.config/insertplay/shortcuts/<guid>.env` when present, and launches the game via `steam://rungameid/` with fallback to generic shortcut |

### Installer (`src/insertplay-cli/assets/installer/`)

Bundled into the SD-card distribution package by `build-sd-card.sh`. Run manually on the target PC.

| Script | Purpose |
|---|---|
| `install.sh` | Deploys the CLI to `~/.local/share/insertplay`, creates `~/.local/bin/insertplay`, optionally installs the systemd service, and runs `insertplay setup` |
| `uninstall.sh` | Removes the CLI, launcher, and systemd service. Use `--purge` to also delete the configuration |

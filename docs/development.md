# Development

Commands run from the repository root, on Windows (PowerShell) or WSL. AI agents: read [CLAUDE.md](../CLAUDE.md) too, which has the rules for working on the real devices.

## Build and test

```powershell
npx -y pnpm@9 install
npx -y pnpm@9 typecheck
npx -y pnpm@9 build     # read the output: TypeScript errors show up as warnings and the build still exits 0
uvx pytest tests -q     # backend; the symlink tests only run on Linux/WSL
bash scripts/package.sh # (WSL/Linux) build + out/InsertPlay.zip, fails on TypeScript errors
```

Layout:

| Path | What |
|---|---|
| `main.py` | Decky entry point (`Plugin` class, the methods the frontend calls). |
| `py_modules/insertplay_decky/` | Backend logic, without `import decky`, so it runs and is tested off the device. |
| `src/` | Frontend (React, runs inside Steam). Steam calls are in `src/steam.ts`. |
| `tests/` | pytest for the backend. |
| `scripts/` | `package.sh`, `deck.mjs` (device access), `spike.mjs`, `cdp.mjs`, `deck-setup/`. |
| `card-template/`, `docs/card-format.md` | Template and spec of an InsertPlay card. |

CI (`.github/workflows/ci.yml`) runs pytest, the typecheck and `package.sh` on every push and pull request, and keeps `InsertPlay.zip` as a build artifact.

## Release

1. Bump `version` in `package.json`. The plugin compares it with the card's package to decide which one is newer, so every release needs a new version.
2. Merge to `main`, then tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. On a `v*` tag, the `release` job of `.github/workflows/ci.yml` checks that the tag matches `package.json`, builds, and publishes a GitHub release with `InsertPlay.zip`. The `releases/latest/download/InsertPlay.zip` URL in the README always points to it.

To put a build on a card without a release: copy `out/InsertPlay.zip` to `insertplay/decky/` on the card, or, with the card mounted on the dev Deck, `node scripts/deck.mjs stage-card <label> [--version X]`. It builds and writes the zip with the plugin's own `write_card_package`; `--version` changes the version inside the zip only, to test "newer card" and "older card".

## Device access: `scripts/deck.mjs`

Devices are listed in `deck.config.json` (not in git; copy `deck.config.example.json`): `deck` is the dev Steam Deck (default) and `machine` the Steam Machine used as production. Add `--device machine` to any command to use another device.

| Command | What for |
|---|---|
| `check` | Diagnostics: SSH, Decky, passwordless deploy, plugin installed, Steam over CEF. |
| `deploy [--skip-build]` | Build, upload and install on the device; restarts Decky. |
| `logs [n]` | Decky journal lines about InsertPlay. |
| `eval "<js>"` | Runs JS inside Steam (SharedJSContext). |
| `screenshot [name]` / `panel [name]` | Captures Steam / the InsertPlay panel to `out/screens/`. |
| `toasts [name]` | Captures the toast popup window to `out/screens/` (the screenshot misses it). |
| `rpc <method> [args JSON…]` | Calls a `main.py` method through Decky. |
| `ssh "<cmd>"` | Command on the device as `deck`. |
| `stage-card <label> [--version X]` | Writes the plugin zip to a mounted card. |
| `restart` / `remove` | Restarts Decky / uninstalls InsertPlay. |
| `setup-access` | Uploads the passwordless deploy installer (below). |

`node scripts/spike.mjs` checks the Steam API calls the plugin uses ([steam-api-notes.md](steam-api-notes.md)). Run it after a Steam update.

## Prepare a Steam Deck for development (once)

Everything goes over the network (Wi-Fi). The Deck and the PC must be on the same network.

### Step 1: on the Deck (Desktop Mode → Konsole)
```bash
passwd                              # sets a password for the deck user (if it has none)
sudo systemctl enable --now sshd    # starts the SSH server
ip -4 addr show | grep inet         # shows the IP; use the one starting with 192.168... or 10...
```
Write down the IP, for example `192.168.0.42`, and use it as `<ip>` below.

### Step 2: on Windows (PowerShell), test the connection
```powershell
ssh deck@<ip>
```
- The first time it asks `Are you sure you want to continue connecting?`. Type `yes`.
- Then it asks for the password from step 1.
- If the prompt `(deck@steamdeck ~)$` shows up, it worked. Type `exit`.

### Step 3: on Windows, SSH key (no more password prompts)
```powershell
# creates the key (press Enter on every question); skip if ~\.ssh\id_ed25519.pub already exists
ssh-keygen -t ed25519

# sends the public key to the Deck (asks for the password one last time)
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh deck@<ip> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"
```
`ssh deck@<ip>` should now log in without a password. `sudo` still asks for it; step 5c removes that for the deploy only.

### Step 4: on the Deck, install Decky
- Install Decky Loader (official installer, from the browser in Desktop Mode).
- Back in Game Mode: `…` (Quick Access) → Decky icon → gear: turn on **Developer mode** and then, under Developer, **Allow Remote CEF Debugging**.

### Step 5: on Windows, open the Deck's Steam console
In Chrome or Edge: `chrome://inspect` → **Configure…** → add `<ip>:8081` → Done. After a few seconds the Deck's tabs show up; click **inspect** on **SharedJSContext**.

That is the DevTools **of the Deck's Steam**: everything run there runs inside Steam, like the plugin. Keep it open while testing: frontend errors show up in red in **Console**. If Steam restarts, go back to `chrome://inspect` and click **inspect** again.

### Step 5b: on Windows, set the IP and check everything
Copy `deck.config.example.json` to `deck.config.json` and fill in the IPs. Then:
```powershell
node scripts/deck.mjs check
```
It prints an OK/FAIL table: SSH, Decky, Steam over CEF, etc. "passwordless deploy" only turns OK after the next step.

### Step 5c: passwordless deploy (once)
To let the AI (or you) install and test the plugin without typing the password every time, install on the Deck a fixed script that `sudo` allows **only to install InsertPlay and restart Decky**. It isn't general sudo: the script refuses any plugin that asks for root. On Windows:
```powershell
node scripts/deck.mjs setup-access
ssh -t deck@<ip> "sudo bash /tmp/insertplay-deck-setup/install-dev-access.sh"
```
The first command uploads the installer. The second asks for the deck password once and installs it. Run `node scripts/deck.mjs check` again: "passwordless deploy" should be OK.

To undo it, on the Deck: `sudo rm -rf /etc/insertplay-dev /etc/sudoers.d/zz-insertplay-dev`.

The script validates and installs a root-owned private copy, never files the `deck` user can still change. Known limit: `~/homebrew` belongs to `deck` by Decky's design, and with that ownership `deck` already controls what Decky (root) loads at boot. The script doesn't create that risk, but doesn't remove it either. Use this only on the development device.

Run `setup-access` and the `ssh -t … install-dev-access.sh` command again when:
- **SteamOS updates**: the update wipes `/etc/insertplay-dev` and `/etc/sudoers.d/zz-insertplay-dev` (measured on 3.8.28 → 3.9.5). `check` shows "passwordless deploy: FAIL";
- `scripts/deck-setup/` changes: the Deck uses the copy installed in `/etc/insertplay-dev`.

### Step 6: install the plugin
```powershell
node scripts/deck.mjs deploy
```
InsertPlay shows up in the Decky menu (Quick Access). Plugin logs: `node scripts/deck.mjs logs`.

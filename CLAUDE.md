# InsertPlay: guide for AI agents

Decky Loader plugin. Overview in [README.md](README.md); build, release and one-time device setup in [docs/development.md](docs/development.md); card format in [docs/card-format.md](docs/card-format.md). This file says **which tools to use to work with the real devices** and the rules for using them.

Language: docs, comments and dev-tool output are in English. Strings shown inside Steam (panel, toasts, event log) stay in Brazilian Portuguese until i18n ([#21](https://github.com/insert-play/insertplay/issues/21)); don't translate them piecemeal.

## Devices

| Name (`--device`) | Device | Role |
|---|---|---|
| `deck` (default) | Steam Deck (Jupiter), on the bench | **Dev.** Main environment: deploy, restart, destructive tests with `IP Teste…` shortcuts. The user handles it physically when needed. |
| `machine` | Steam Machine (SteamOS, Navi33 GPU), in the gaming room | **Prod.** Simulates real use. It is farther away: ask for physical help only when necessary. |

Steam Machine (prod) rules:
- Read-only by default: `check`, query `eval`, `logs`, `screenshot`, `panel`, read `rpc`.
- The real install flow (card zip / Decky's "Install Plugin from ZIP File") was validated on it. To test a new version, `deploy --device machine --confirm-prod` over SSH also works.
- **Check its state before any install or test:** it is in another room and someone may be playing. Before `deploy`, `restart`, `remove` or asking to open a game, run `check` and `eval "SteamUIStore.RunningApps.map(a => a.display_name)"`. With a game running, do nothing and ask the user: the deploy restarts Decky in the middle of someone's session.
- `deploy`, `remove`, `restart`, `stage-card` and `setup-access` require `--confirm-prod`. Only use them after agreeing with the user **in that session**.
- Don't run `spike.mjs` on it: it creates test shortcuts.
- It has plugins the Deck doesn't (SteamGridDB, PlayCount). Something that works on the Deck may behave differently there. Non-Steam Badges and MicroSDeck were uninstalled on 2026-10-24: InsertPlay's own card badge replaces them.
- Don't change shortcuts the plugin didn't create. The legacy InsertPlay (C# CLI + `insertplay.service`) was removed from both devices on 2026-10-24 and from this repository in #16.

Devices live in `deck.config.json` (not in git; template in `deck.config.example.json`): `{"default": "deck", "devices": {"deck": {"host", "user", "role"}, "machine": {...}}}`. The old format, `{"host"}` at the root, still works as the `deck` device. Every command accepts `--device <name>`, in any position, or the `INSERTPLAY_DEVICE` variable, and prints on stderr which device it is using. IPs come from DHCP and can change: if `check` fails to connect, ask the user for the current IP and update the file.

## Device access

Prerequisites, done once and by hand by the user ([docs/development.md](docs/development.md), steps 1 to 5c):
- SSH with a key to `deck@<ip>`;
- Decky with "Allow Remote CEF Debugging";
- the restricted sudo rule installed by `setup-access`.

All commands run from the repository root, on Windows or WSL. Use `node scripts/deck.mjs <command> [--device machine]`:

| Command | What for |
|---|---|
| `check` | **Start here.** Diagnostics: SSH, Decky running, passwordless deploy, plugin installed, Steam over CEF. Also lists "plugin shortcuts missing from the registry" (issue #8). |
| `deploy [--skip-build]` | Build (fails on TypeScript errors), upload to `/tmp/insertplay-decky` and install via `sudo -n /etc/insertplay-dev/deploy-plugin.sh deploy`, which restarts Decky. |
| `logs [n]` | Last lines of `journalctl -u plugin_loader` filtered by "insertplay". |
| `eval "<js>"` | Runs JS inside Steam (SharedJSContext tab, where the plugin frontend runs). Promises are awaited; the value comes back as JSON. |
| `screenshot [name]` | Saves the current Steam screen to `out/screens/<name>.png`. Read the image to check the UI. |
| `panel [name]` | Opens Quick Access on the InsertPlay panel and captures it. Quick Access is another CEF window (`QuickAccess_uid2`); the plain `screenshot` doesn't catch it. Close it afterwards with `eval "(SteamUIStore.m_WindowStore ?? SteamUIStore.WindowStore).SteamUIWindows[0].MenuStore.CloseSideMenus()"`. |
| `toasts [name]` | Captures the toast popup window (`notificationtoasts_uid2`) to `out/screens/<name>.png`. Run it within the toast's duration (5 s by default). |
| `rpc <method> [args JSON…]` | Calls a `main.py` method through Decky, like the frontend does. E.g. `rpc log_tail 5`, `rpc current_cards`. Internally it uses `DeckyBackend.call("loader/call_plugin_method", "InsertPlay", method, ...args)`: arguments are **spread**; a list becomes one extra argument. |
| `ssh "<cmd>"` | Command on the device as user `deck`, no TTY, no password. |
| `stage-card <label> [--version X]` | Builds and writes the plugin zip to the mounted card (`insertplay/decky/InsertPlay.zip`), validated by the plugin's own `build_zip_from_dir`. `--version` changes the version only in the zip, to test "newer/older card". |
| `restart` / `remove` | Restart Decky / uninstall InsertPlay. |

Others:
- `node scripts/spike.mjs`: confirms the Steam API calls (see [docs/steam-api-notes.md](docs/steam-api-notes.md)). Run it after a Steam update or when using a new call.
- `node scripts/cdp.mjs <ip> "<js>" [tab]`: raw CDP to any tab (`QuickAccess_uid2`, `MainMenu_uid2`, `Steam — Modo Big Picture`).

Useful in `eval`:
- `SteamUIStore.Navigate("/library/app/<appid>")` and `SteamUIStore.Navigate("/library/home")` to bring the screen to what you want to capture;
- `appStore.GetAppOverviewByAppID(id)`;
- `collectionStore.BIsHidden(id)`.

## Development cycle

1. Backend logic goes in `py_modules/insertplay_decky/` (no `import decky`), with a test in `tests/`: `uvx pytest tests -q`.
2. `npx -y pnpm@9 typecheck`, then `node scripts/deck.mjs deploy`.
3. Verify on the device with `logs`, `eval` on the state (`appStore`, `collectionStore`) and `screenshot`. Don't claim "it works" without one of these.
4. A new Steam call goes in `src/steam.ts`, is measured first with `eval` on the Deck and recorded in `docs/steam-api-notes.md`.
5. Releases: bump `version` in `package.json` and tag `vX.Y.Z` after the merge (docs/development.md). Creating a tag publishes a release: only with the user's go-ahead.

## Rules

- **The Deck is the user's personal device.** Its library has hundreds of real games.
  - Never remove, hide or change shortcuts or games that weren't created by the test in progress.
  - Test shortcuts get clearly test names (`IP Teste …`) and are removed at the end, also on failure.
- **The Deck has two Steam accounts** (`wjsantos123`, 108436075, the main one; `dalilaviana`, 385089570). The plugin registry is per device (issue #8): with the other account active and a card inserted, the plugin creates a shortcut in it and overwrites the registry. Switch accounts only without a card, or fix it afterwards with `rpc registry_put`.
- Don't touch other Decky plugins (`~/homebrew/plugins/*` other than `InsertPlay`).
- Don't start the user's real games. `RunGame` only on a test shortcut or an InsertPlay card the user asked to test.
- Don't try to get root outside `deploy-plugin.sh`, don't ask for the user's password and don't change the sudo rule. If a permission is missing, explain and leave the step to the user.
- **A SteamOS update wipes the deploy access** (`/etc/insertplay-dev`, `/etc/sudoers.d/zz-insertplay-dev`). If `check` shows "passwordless deploy: FAIL", run `setup-access` and give the user the `ssh -t … install-dev-access.sh` command. After an update, also run `spike.mjs`: the Steam API may have changed.
- `deploy-plugin.sh` refuses a `plugin.json` with the `root` flag. Don't work around it; the backend must run as a regular user.
- Inserting and removing an SD card is physical: ask the user and confirm with `logs` or `eval`.
- **Restarting Steam** (to test startup): `eval "(SteamClient.User.StartRestart(false), true)"`.
  - Check first that no game is running (`SteamUIStore.RunningApps`).
  - Wait about 25 s between restarts. Restarts in quick succession made **Decky shut down** (`updater.do_shutdown`, service inactive, none of the user's plugins loading).
  - After each restart check `ssh "systemctl is-active plugin_loader"`; if inactive, `deck.mjs restart`.
- "It worked" needs positive evidence: one shortcut more or less can come from an error that cut the flow short. Read `rpc log_tail` (the startup check logs `reconcile`) besides counting state.
- `pnpm build` exits 0 even with TypeScript errors. `deploy` and `package.sh` already catch that; on a manual build, look for `plugin typescript` in the output.

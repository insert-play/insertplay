# Steam API: notes measured on the Deck

The plugin uses internal Steam functions (`SteamClient.Apps.*`, `appStore`, `collectionStore`) that have no documentation. All the code that depends on them lives in [src/steam.ts](../src/steam.ts). This file records what was **measured** on the device.

## How to measure

```powershell
node scripts/spike.mjs
```
The script connects to the Deck's Steam on port 8081 (Chrome DevTools Protocol) and:
- creates an "IP Teste" shortcut and measures each call;
- takes screenshots of the game page and the home screen (`out/screens/spike-*.png`);
- starts the game and waits for Steam to report that it opened and closed;
- deletes the shortcut at the end, even if something fails.

It asks no questions and needs nobody watching. The result goes to `out/spike-result.json`.

Run it again **after every Steam/SteamOS update**.

## Revalidation: 2026-10-24, SteamOS 3.9.5 + Decky v3.3.0

`spike.mjs`: 15/15 OK, same behavior as the table below, with artwork confirmed by screenshot. Nothing changed in the calls the plugin uses.

## Result: 2026-10-24, Steam Deck (Jupiter), SteamOS 3.8.28, Steam Deck Stable

| Call | Result |
|---|---|
| `await SteamClient.Apps.AddShortcut(nome, exe, startDir, opts)` | Returns the appid (number). **Ignores the name**: the tile is born as `bash` (executable name). |
| `SteamClient.Apps.SetShortcutName(appid, nome)` | Works, immediate effect in `appStore`. |
| `SteamClient.Apps.SetShortcutLaunchOptions(appid, opts)` | Works: with `-c 'sleep 15'` the game stayed running for 17 s. There is no reliable read of the string (`GetLaunchOptionsForApp` returns `[]` for shortcuts). |
| `await SteamClient.Apps.SetCustomArtworkForApp(appid, base64, "png", tipo)` | Works. Confirmed by screenshot: **0 = vertical cover**, **1 = hero**, **2 = logo**. Type 3 (horizontal) accepts the call; visual confirmation is still pending. |
| `collectionStore.SetAppsAsHidden([appid], bool)` + `BIsHidden(appid)` | Works both ways. |
| `appStore.GetAppOverviewByAppID(appid).m_gameid` | Equal to `(appid << 32) \| 0x02000000`. |
| `SteamClient.Apps.RunGame(gameid, "", -1, 100)` | Starts the shortcut. `GameSessions.RegisterForAppLifetimeNotifications` reports the start and the exit. |
| `SteamClient.Apps.SetShortcutExe(appid, '"/bin/bash"')` | Does not throw. The stored form (quotes) was not checked visually. |
| `SteamClient.Apps.RemoveShortcut(appid)` | Removes; `appStore` stops having the appid within ~1 s. |
| `SteamUIStore.Navigate(path)` | Navigates (`/library/app/<appid>`, `/library/home`). Useful in tests. |

## Steam startup (measured 2026-10-24)

Measured with `SteamClient.User.StartRestart(false)` and the card inserted. Times count from the restart request:

| ms | Event |
|---|---|
| ~7 000 | new SharedJSContext |
| ~7 900 | `appStore` defined, but still empty |
| ~11 900 | **Decky loads the plugin**, which already runs the initial check |
| ~12 450 | `appStore.m_bIsInitialized = true`; shortcuts appear in `allApps` and `GetAppOverviewByAppID` at the same instant |
| later | collections: `collectionStore.SetAppsAsHidden` → `GetCollection` throws "reading 'get'" until they load |

Consequences in the code:
- `waitForLibrary()` in `src/steam.ts` waits for `m_bIsInitialized` **and** `GetCollection("hidden")` without error before any decision.
- Before that, the plugin thought the shortcut did not exist and **created a duplicate** (real bug, reproduced and fixed).

Non-Steam shortcuts have `app_type === 1073741824` and `BIsShortcut() === true`, and appear in `appStore.allApps`.

**Exist and not used yet:**
- `GetShortcutDataForPath`;
- `LaunchNonSteamApp`;
- `SetShortcutSortAs`.

## Shortcut icon (issue #17, measured 2026-10-27, SteamOS 3.9.5)

Used by `ensureIcon` (`src/flow.ts`) and by the `shortcut_icon` RPC (`main.py`, logic in `py_modules/insertplay_decky/icons.py`). Test shortcuts `IP Teste…` and the NFS Underground card.

| Call / situation | Result |
|---|---|
| `SteamClient.Apps.SetShortcutIcon(appid, caminho)` | Steam stores **only the path** (`icon` field of `shortcuts.vdf`) and reads the file on demand. The overview gets `icon_data` (base64 PNG), `icon_data_format` and `icon_hash` in under 2.5 s, without a restart. Path `""` clears the field in `shortcuts.vdf`, but the old `icon_data` stays in the overview until the restart. |
| Write to `shortcuts.vdf` | ~0.16 s after the call (file mtime). That is why the backend reads the file to know the current icon. |
| Icon path in the frontend | Does not exist: neither the overview nor `appDetailsStore.GetAppDetails` carry the path. Only `shortcuts.vdf` (`userdata/<account>/config/`). The account comes from `App.m_CurrentUser.strSteamID` (steamid64 − 76561197960265728). |
| Icon on a card path, Steam restarted with the card out | Shortcut without icon. With the card back, neither `RequestIconDataForApp` nor a new `SetShortcutIcon` **with the same path** reload until a restart. That is why the icon is copied to `~/homebrew/data/InsertPlay/icons/<guid>-<short sha256>-<generation>.<ext>`. |
| Icon on the SSD, Steam restarted with the card out | The icon stays (`RequestIconDataForApp` → `icon_data`). |
| Path already loaded in the session | Going back to a path Steam already read in this session does not change `icon_data`. That is why all new content gets a never-used path (hash + generation), even when going back to an icon already used (A → B → A); the same content keeps its path. |
| Insertion with the card's icon changed | New path applied and `icon_data` changed within seconds, without a restart. The old copy is only removed on a following call, when no `shortcuts.vdf` (of any account) uses it anymore. |
| Icon outside the plugin folder on the shortcut (like SteamGridDB's) | The plugin does not apply its own. SteamGridDB does not use `SetShortcutIcon`: it edits `shortcuts.vdf` directly and stores the icon in `userdata/<account>/config/grid/<appid>_icon.<ext>`. |
| Reconciliation at Steam startup, card in, shortcut without icon | Applies and loads. |

**When the icon only shows up after the next restart** (the path is already correct in `shortcuts.vdf`, and the following restart loads it from the SSD):
- In the session opened by a `deploy` (the webhelper is relaunched), no `icon_data` reached the frontend for shortcuts that already existed, not even with `RequestIconDataForApp`. A shortcut created in that session did get it.
- NFS shortcut **without icon** and with the **card out** when Steam started: in that session it refused any path, even a never-used one and even after the card came back. The cause was not identified. Tested and ruled out: hidden shortcut, `RunGame` right after, the `updateShortcut` calls right before, and a nonexistent start folder (a test shortcut without icon and with a nonexistent start folder accepted it). Only one of the two factors is not enough: with an icon at startup and the card out, or without an icon and the card in, it applied. Affects shortcuts created before the icon existed; after the first icon, the case does not recur.

## Game icon in the toast (measured 2026-10-31, SteamOS 3.9.5, Decky `@decky/api` 1.1.3)

Used by `appIcon` (`src/steam.ts`) and `gameLogo` (`src/flow.ts`) for the insertion and removal toasts.

| Call / situation | Result |
|---|---|
| `appStore.GetIconURLForApp(overview)`, InsertPlay shortcut | `data:image/png;base64,…`, the icon applied by `SetShortcutIcon` (NFS Underground, 2483608115). Works offline. Right after a new `SetShortcutIcon` the old (or no) icon may still come back: `icon_data` takes up to 2.5 s (see above). |
| `appStore.GetIconURLForApp(overview)`, Steam game | CDN URL `https://shared.steamstatic.com/community_assets/images/apps/<appid>/<icon_hash>.jpg` (AoE DE, 1017900). Offline it depends on the CEF cache; `gameLogo` hides the image if it fails to load. |
| `toaster.toast({ …, logo: <img/> })` | Steam draws `logo` as the square left of the title, the image filling it with `width/height: 100%`. Toasts are drawn in their own CEF window, `notificationtoasts_uid2`: capture it with `deck.mjs toasts`, the plain `screenshot` misses it. Toasts fired together queue up and show one at a time. |

## Steam libraries on the card (measured 2026-10-24, SteamOS 3.9.5)

Used by the card badge (`src/steam-badges.tsx`) to also mark Steam games installed on the card, not only InsertPlay shortcuts, and by the notification and auto-launch of those games (issue #10, `checkSteamLibraries` in `src/flow.ts`).

| Call | Result |
|---|---|
| `await SteamClient.InstallFolder.GetInstallFolders()` | One entry per library Steam has ever seen, mounted or not: `strFolderPath`, `bIsDefaultFolder`, `bIsMounted`, `bIsFixed`, `vecApps[].nAppID`. The card in the reader (`/run/media/deck/Expedition 33`) came with `bIsMounted: true`, `bIsFixed: false` and the game's appid. Absent cards stay in the list with `bIsMounted: false` **and the old appids**, so you must filter by `bIsMounted`. |
| `SteamClient.InstallFolder.RegisterForInstallFolderChanges(cb)` | Returns `{ unregister }`. Calls `cb({ folderIndex })` once right after registration (`folderIndex: 0`). Card removal: notification ~0.5 s after the kernel unmount. Insertion: two notifications within 1 s after the mount. No polling needed. |
| Sequence of notifications (probe with 100 ms polling, 2026-10-24) | Removal: one notification, already with `bIsMounted: false` and the appids kept. Insertion: two identical notifications, already with `bIsMounted: true` and the appids. No intermediate state (mounted without apps). The insertion notification arrived ~1 s after the kernel mount. |
| Moving a game from the SSD to the card (Settings → Storage) | The start of the move generates no notification. At the end, `vecApps` switches folder **silently** (probe: 18:24:22) and the notification only arrives **~12 s later** (18:24:34). Pulling the card in that window made the removal go out without a toast, because the snapshot still had the library empty. That is why the removal uses the appids Steam keeps in the unmounted folder, not those from the last notification. Formatting through Steam remounts the card with the new label (`/run/media/deck/<label>`), and the new folder starts empty. |
| Installing a game on an already-inserted card | The appid enters `vecApps` **with a notification** as soon as the download starts, before it finishes. Uninstalling removes the appid from the list. In other words, the move is the case without a notification; installation is not. |
| List when restarting Steam | Starts **empty** (`[]`, not even the default folder). ~3 s later it appears all at once, **without a notification**; then ~30 notifications in a row with the same list. In the measurements it filled before `appStore.m_bIsInitialized` (3.0 s vs 5.6 s), but a snapshot taken with the empty list turned the already-inserted card into "absent" and the following removal got lost. That is why `installFolders()` waits for the internal library (index 0) to appear before using the list. |
| `bIsDefaultFolder` vs internal library (2026-10-25) | `bIsDefaultFolder` is the **default install location**, not the SSD: when installing the Prince of Persia trilogy choosing the `PoP Sands of Tim` card as default, the flag moved to the card (`nFolderIndex: 33`). The internal library (`/home/deck/.local/share/Steam`) is always `nFolderIndex: 0` (Deck and Machine). The plugin discarded the "default folder" and, with it, the whole card: no badge, no snapshot and no portable save. |
| `bIsFixed` on the Steam Machine (2026-10-24) | The Machine's card reader is USB (`/dev/sda1`, `lsblk`: `RM 1`, `HOTPLUG 1`, `TRAN usb`), and Steam marks the card library as **`bIsFixed: true`**, with `strDriveName` equal to the mount point. On the Deck (reader `mmcblk0`) it comes `false`. That is why the card is "not fixed for Steam **or** removable disk for the kernel": the backend (`disk_is_removable`) reads `removable` from the disk in `/sys/dev/block/<maj>:<min>/..`. On the Machine, `/sys/block/sda/removable` = 1 and the NVMe = 0. With the Expedition 33 card prepared on the Deck, the Machine listed the unmounted folder with 0 apps and, on insertion, started listing 1903340 (toast and badge ok). |
| `strUserLabel` | Label the user gave the library in Steam. Not always the card label (`/run/media/deck/The Witcher 3` came as "Assassins Creed Unity"). The toast uses the mount folder name, like the InsertPlay cards flow. |
| `strFolderPath` | Can be the card root (`/run/media/deck/Expedition 33`) or a subfolder (`/run/media/deck/Games/SteamLibrary`). The backend (`library_card`) climbs up to the mount point and looks at the InsertPlay manifest on disk. The NFS Underground card has an empty Steam library besides the manifest. |
| `SteamClient.Apps.RunGame("1903340", "", -1, 100)` | Opens the Steam game (Clair Obscur, appid 1903340): `SteamUIStore.RunningApps` started having the appid. A Steam game has no `m_gameid` in the overview (`undefined`); the gameid is the appid itself as a string. |
| `appStore.GetAppOverviewByAppID(1903340).display_name` | "Clair Obscur: Expedition 33". Used in the toast. |

Library DOM (window "Steam — Modo Big Picture", 854×534 CSS px on the Deck): on the home screen every card `div[role="listitem"]` has `data-id` with the appid, including store covers whose URL uses a hash instead of the appid.

## Plugin installation through Decky (measured 2026-10-24, Decky v3.3.0)

Not a Steam API, but it is the other undocumented global the plugin uses (`src/selfupdate.ts`).

- `DeckyBackend.call("utilities/install_plugin", artifact, name, version, hash, installType)`:
  - it is the same route as the store and "Install Plugin from ZIP File";
  - `artifact` can be `file:///absolute/path.zip`;
  - it installs nothing by itself: it opens **Decky's native modal** ("Tem certeza que você deseja atualizar InsertPlay 0.2.1?", i.e. "Are you sure you want to update InsertPlay 0.2.1?"), and only "Atualizar"/"Instalar" (Update/Install) installs;
  - `installType` (0 INSTALL, 1 REINSTALL, 2 UPDATE, 3 DOWNGRADE, 4 OVERWRITE) only changes the modal text;
  - Decky always removes the installed version's folder and extracts the zip as is into `~/homebrew/plugins`. The zip's root folder must be `InsertPlay/`, otherwise it becomes another plugin.
- `hash`: SHA-256 of the zip. If it does not match, Decky refuses the installation; empty skips the check. We pass the hash the backend read, so a zip swapped after the read is refused.
- With `version` other than `"dev"`, the name comes from the `name` argument and must equal the `name` in `plugin.json`.
- What survives the update: `~/homebrew/settings/InsertPlay` (guid → appid registry) and `~/homebrew/data/InsertPlay`. Decky only cleans the plugin's entries in the loader's `pluginOrder`, `hiddenPlugins` and `disabled_plugins`.
- After installing, Decky reloaded the plugin twice in a row (file watcher). That ran two reconciliations and did not duplicate a shortcut.
- Installed by Decky, the files are `deck:deck 755`. Through `deploy-plugin.sh` they are `root:root`.
- Decky's modal appears in the "Steam — Modo Big Picture" window. `Page.captureScreenshot` of that window comes out black while the modal is open, so check the text with `document.body.innerText` (via `cdp.mjs`).
- **File picker** of "Install Plugin from ZIP File": opens at `/home/deck`, but the ↑ button goes up to `/`. You can reach `/run/media/deck/<label>/insertplay/decky/InsertPlay.zip`. The picker backend (`utilities/filepicker_ls`) runs as root and does not restrict the path. Tapping the zip already opens the "instalar InsertPlay dev?" (install InsertPlay dev?) modal, and Decky reads the name from `plugin.json`.

## Proton prefix on the card via symlink (issue #13, measured 2026-10-24, SteamOS 3.9.5)

Call of Juarez Gunslinger (204450), ext4 card `Call of Juarez G`, Game Mode. Prefix copied to `<card>/.insertplay/prefixes/204450` and `~/.local/share/Steam/steamapps/compatdata/204450` replaced by a symlink to it.

| Situation | Result |
|---|---|
| Opening the game | Proton (11.0-100) and pressure-vessel follow the link to `/run/media`. `pfx/*.reg`, `version`, `tracked_files` and the game's `Settings` were written to the card; nothing new on the SSD. The link stayed a link. |
| `config_info` | Stores absolute paths (`/home/deck/.local/share/Steam/steamapps/common/Proton 11.0/files/...`) and the Proton version. Steam did not rewrite it in any of the tests. |
| "Running Script" step (before opening and after each move) | Writes through the link (`tracked_files`, `version`, `pfx`). Does not replace the link with a folder. |
| Verify integrity of game files | Link kept; "erro na validação de 0 arquivo(s)" (0 file(s) failed to validate) = no error. The downloaded shader cache goes to `shadercache/204450` on the SSD, separate from the prefix. |
| Move card → SSD (with the card's `steamapps/compatdata/204450` out of the way) | Passed. Link and card prefix intact. Steam did **not** recreate a folder on the SSD: the path already exists (the link). The game stays on the SSD with the prefix on the card. |
| Move SSD → card | Passed. Steam **follows the link and copies** the whole prefix to the card's `steamapps/compatdata/204450` (493 MB) and **does not delete the source**: link and `.insertplay/prefixes/204450` stayed the same (2,327 items). This copy is the one that, according to the issue, blocks the move back. |
| Pulling the card with the link active | The link stays broken and Steam does not touch it. Opening another Proton game (Duke Nukem 3D, 434050) changes nothing in `compatdata`. The card's game shows only as "Instalar" (Install). Reinserting the card makes the link resolve again. |
| Uninstalling with the link active (game on the card) | "finished uninstall (No Error)". Deleted `shadercache/204450` from the SSD; **did not touch** the link, `.insertplay` (2,329 items, no difference) or the card's `steamapps/compatdata/204450`. The link is left over on the SSD. |

Where the save lives is not a rule: CoJ writes the save to `userdata/<account>/204450/remote` (Steam Cloud) and, in the prefix, only the settings. To test the portable save you need a game that saves inside the prefix (to be checked in Bully and Metro Redux).

### Whole prefix on a slow card vs only `steamuser` (2026-10-25, Bully 12200)

Card `Bully`: old 8 GB SDHC (`SA08G`), ext4, ~0.4 MB/s write with the ext4 journal (`do_get_write_access`).

- **Whole prefix on the card:** each launch took minutes, with Proton (`python3 proton`) in state `D`.
  - Every run rewrites **~115 MB** in the prefix: Steam DLLs (`steamclient*.dll`, `GameOverlayRenderer*.dll`), `vrclient`, `openxr`, `Fonts` and part of `system32`/`syswow64`. This happens even with the same `config_info`: `try_copy` copies without comparing.
  - On the first launch after changing the prefix, the rewrite happens **twice**: in the install script (`iscriptevaluator`, `Install=1`) and when opening the game.
  - The save was written to the card (`Documents/Bully Scholarship Edition/BullyFile1`).
- **Only `pfx/drive_c/users/steamuser` on the card** (200 KB), with a link on the SSD:
  - launching became fast: the DLL copy went to the SSD and took less than 1 s;
  - the game writes through the link (`AppData/Local/Temp`, `BullyFile2`);
  - Wine follows the link, and Proton only touches the links inside (`My Documents` → `Documents`).
- **Device without a prefix** (simulated by moving `compatdata/12200` out of the way): with only `compatdata/12200/pfx/drive_c/users/steamuser` (the link) in place, Proton built the whole prefix around it (441 MB), kept the link and the game opened with both saves already on the first time.
- Turning off through the plugin (card → SSD copy of 441 MB) took 3.9 s. Reading from the card is fast; writing is not.
- **`STEAM_COMPAT_DATA_PATH` in launch options works on SteamOS** (Steam Machine, 2026-10-25).
  - With `STEAM_COMPAT_DATA_PATH="/run/media/deck/Bully/compatdata" %command%`, a leftover from an old test, Proton used the prefix at the card root (the `environ` of `proton waitforexitandrun` shows the variable) and ignored the link in `compatdata/12200`. The game opened without the saves and wrote ~115 MB of DLLs to the card, which is why the launch took ~2 min.
  - Launch options are stored **per device**: on the Deck the field was empty. They can be read in `appDetailsStore.GetAppDetails(appid).strLaunchOptions`, but that cache only exists after someone opens the game page. Without it, `SteamClient.Apps.RegisterForAppDetails(appid, cb)` loads the details: the callback arrived in ~40 ms (Deck and Machine).
- **The Deck's Bully on the Steam Machine, "Saves" mode** (2026-10-25, launch option removed):
  - on inserting the card, the plugin replaced the Machine's empty `steamuser` (from 19/09) with a link (`linked`) and kept the original as a backup;
  - the game opened fast and loaded the saves made on the Deck;
  - the resolution stayed each device's own (800p on the Deck, 4K on the Machine): Bully stores its settings outside `steamuser`.

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
| `SteamClient.System.SuspendPC()` | Dev tools only (`deck.mjs suspend`). Suspends the device like the menu: logind logs `suspend requested from client … ('dbus-send')`. Measured on the Deck (S3) and the Machine (s2idle), 2026-11-06. Call it delayed (`setTimeout`), or the CEF answer doesn't come back before the sleep. |
| `SteamUIStore.Navigate(path)` | Navigates (`/library/app/<appid>`, `/library/home`). Used by "Abrir página do jogo"; see [Opening the game page](#opening-the-game-page-measured-2026-11-01). |

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

## Achievements on a shortcut (issue #24, measured 2026-11-01, SteamOS 3.9.5)

Used by `src/achievements.ts` to show RetroAchievements progress in the game page's "Conquistas" section and in the list page `/library/app/<appid>/achievements/my/individual`. Same shapes on the Deck and on the Steam Machine (read-only there).

**The export names are minified** (`N_`, `p6`) and change between builds: every object is found by its shape, never by name. Each of the three parts is installed on its own, and one that is not found disables only its part (logged as `achievements patch: …`).

| Object | How it is found | Use |
|---|---|---|
| Class with `prototype.GetSections(app, …)` | webpack export that is a function with `prototype.GetSections` | Sections of the game page. For a shortcut (`BIsModOrShortcut()`) only `nonsteam`, `notes`, `screenshots`. Returns a **`Set`**, memoized in `this.m_setSectionsMemo` (same object while its contents don't change). The patch returns a derived `Set` with `achievements` after `nonsteam`, cached per Steam `Set`, so its identity is stable too. |
| `appDetailsStore.GetAchievements(appid)` | global | `return GetAppData(appid).details?.achievements`, and calls `RequestAchievements` when `vecHighlight` is `undefined`. The section reads from here. The patch returns our object for card appids and **writes nothing**. |
| `appDetailsStore.m_mapAppData.get(appid).details.achievements` | global | `{nAchieved, nTotal, vecHighlight, vecUnachieved, vecAchievedHidden}`. Only exists after Steam loads the details (opening the page); for a shortcut Steam sets `nTotal: 0` and writes it again on every reload of the page. **The plugin does not write it**: after a Steam restart it stayed `0/0` and the section showed 5/76 through the patched getter alone. |
| Achievements store | webpack export object with the key `m_mapMyAchievements` and a `LoadMyAchievements` method | `m_mapMyAchievements.get(appid) = {data: {achieved: {id: Ach}, unachieved: {…}, hidden: {}}}`, `m_mapGlobalAchievements.get(appid) = {data: {id: pct}}`. `GetMyAchievements(appid)` stores `{loading: true}` and calls `LoadMyAchievements` when the appid is missing, during render. The original `LoadMyAchievements` calls `SteamClient.Apps.GetMyAchievementsForApp` and, for a shortcut, stores `{error: 8}`; the patch ignores card appids and writes our data on a timer. |

`Ach`, the shape of a Steam game's achievement (AoE DE, 1017900): `{strID, strName, strDescription, bAchieved, rtUnlocked, strImage, bHidden, flMinProgress, flCurrentProgress, flMaxProgress, flAchieved}`, `flAchieved` being the global %. In that game (12/44), `vecHighlight` had the 12 achieved ones from the most recent `rtUnlocked`, and `vecUnachieved` the **12** most common locked ones (by `flAchieved`), not all 32.

**Render loop (do not repeat).** A first version wrote `details.achievements` (a mobx observable) inside the patched `GetAchievements`, which Steam calls while rendering. The steamwebhelper went to 133% CPU, the SharedJSContext stopped answering (CDP and the power button), and ~6 min later a watchdog dumped core and Steam reopened its UI. Rules in the code:
- never write an observable inside a getter Steam calls while rendering (`GetAchievements`, `GetSections`, `GetMyAchievements` → `LoadMyAchievements`);
- write once, from outside (card flow, timers), and only when the value changed (identity of the object built per fetch);
- write only what is needed: the achievements store for the list page. `details.achievements` is not needed (see the table).
- refresh on view: `GetAchievements` (section) and `GetMyAchievements` (list) only **schedule** a fetch (`setTimeout`), at most once a minute per game; the fetch result replaces the objects only if the unlocks changed (compared by achievement id + unlock time, not by fetch time), so a fetch with nothing new makes Steam redraw nothing. Measured: opening the page fetched once, opening the list 8 s later did not, and with the list open for a minute the steamwebhelper used ~1 s of CPU and no further fetch happened.
- a card shortcut with no data yet (first load failed offline) is still known by GameID: opening its game page (`GetSections`) schedules the retry. Measured with a refused key and no cache: the fetch on page open worked, but the section only shows on the **next** opening, since Steam had already computed the sections and nothing the plugin holds is observable. Accepted: forcing a redraw would mean writing an observable from that path.

Measured with the NFSU2 card (2026-11-01): section "5/76 alcançadas (6%)" in "Suas coisas" and the list "5 de 76 conquistas alcançadas (7%)" with badge, local unlock date and global %; the three patches found at plugin load, also right after a Steam restart; steamwebhelper at ~1% CPU with the list page idle.

`Navigation.NavigateToExternalWeb(url)` (`@decky/ui`) opens Steam's own browser in Game Mode. With `https://retroachievements.org/settings` the user logged in, copied the Web API key there and pasted it in the panel's text field: that is the "Abrir página da chave" button.

Not needed: `appStore` overview `.BHasStoreCategory(22)` (`false` for a shortcut, the section showed anyway) and `appAchievementProgressCache` (changed nothing on the page or the list).

**RetroAchievements API from the backend.** `urllib` in the Python embedded in Decky's PluginLoader reaches `https://retroachievements.org` (a login with a well-formed fake key came back 401, not a certificate error), so the requests stay in Python (`py_modules/insertplay_decky/retroachievements.py`). `API_GetGameInfoAndUserProgress.php?u=<user>&g=<gameId>&a=1`: 200, ~400 ms, 35 KB for 76 achievements. Dates are UTC (`YYYY-MM-DD HH:MM:SS`); the emulators of the measured cards log in hardcore, so unlocks come as `DateEarnedHardcore`.

## Opening the game page (measured 2026-11-01)

Used by "Ao inserir" → "Abrir página do jogo" (issue #38, `waitForApp` and `openGamePage` in `src/steam.ts`). Measured on the Deck (SteamOS 3.9.5, Game Mode) with `IP Teste Navegação` shortcuts, removed afterwards:

| Case | Result |
|---|---|
| `SteamUIStore.Navigate("/library/app/<appid>")` in the same tick as `AddShortcut` + `SetShortcutName` | `appStore.GetAppOverviewByAppID` still `undefined`. The route changes to the page and **bounces to `/library/home`** (screenshot: home). Returns `undefined`. |
| Polling `GetAppOverviewByAppID` every 25 ms after `AddShortcut` | The overview showed up after ~130 ms. `Navigate` then opened the page (screenshot: title, "Jogar", tabs). |
| `Navigation.Navigate` (`@decky/ui`) to an existing shortcut | Opens the page too. Not used: `SteamUIStore.Navigate` is enough and stays with the other Steam calls. |

Current route, to check a navigation: `(SteamUIStore.m_WindowStore ?? SteamUIStore.WindowStore).SteamUIWindows[0].m_history.location.pathname`.

## Steam libraries on the card (measured 2026-10-24, SteamOS 3.9.5)

Used by the card badge (`src/steam-badges.tsx`) to also mark Steam games installed on the card, not only InsertPlay shortcuts, and by the notification and the "Ao inserir" action of those games (issue #10, `checkSteamLibraries` in `src/flow.ts`).

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

**Updating a game installed on a card** (Cyberpunk 2077, 1091500, Deck, 2026-11-01/02). Steam downloaded and staged the update **on the internal drive** and only then moved the result to the card:

| | During the update | After |
|---|---|---|
| `appmanifest_1091500.acf` (on the card) | `StagingFolder "0"` (library 0 = internal drive), `BytesToDownload` 28.8 GB, `BytesToStage` 88 GB, `buildid` 20383525 → `TargetBuildID` 26161058 | `StateFlags "4"` (fully installed), `buildid` 26161058, `BytesStaged` = `BytesToStage` |
| Internal drive, `steamapps/downloading/` | `.delta` and `state_*.patch` files, and `downloading/1091500` already holding 83 GB (147 files, allocated at once, not sparse) | `downloading/1091500` gone |
| Card, `steamapps/downloading/` and `temp/` | `downloading/` empty, `temp/1091500` empty | both empty |
| Game folder on the card / free space on the card | 92 GB / 26 GB | 83 GB / 35 GB |

The staging needed 88 GB and the card had 26 GB free, so Steam most likely picked another library with room. A small update that fits on the card was not measured; it probably stages in the card's own `steamapps/downloading`. In practice: a big update of a card game needs that much free space on the internal drive for a while, and the card must stay in until the update finishes, since the last step writes to it.

## "Install" button and download progress on a shortcut (measured 2026-11-13, SteamOS 3.9.5, Decky v3.3.1)

Groundwork for the network library (games installed from a home server). Test shortcut `IP Teste Instalar` (`/bin/bash -c 'sleep 5'`), removed afterwards; Steam restarted at the end to drop the injected state. What the plugin uses is in the next section.

| Call / situation | Result |
|---|---|
| `appStore.GetAppOverviewByAppID(id).per_client_data` | Every app has it, shortcuts included. Shortcut: `[{clientid:"0", display_status:11, status_percentage:100, installed:true, …}]`. Uninstalled Steam game: `display_status:9`, no `installed`. The overview's `installed`, `display_status` and `status_percentage` are getters over it. Values seen: 9 ready to install, 11 ready to launch, 7 downloading. |
| `per_client_data[0].installed = false; display_status = 9` on a shortcut | The game page shows the blue **"Instalar"** (Install) button, but only on its **next render** (leave the page and come back): the overview is not observable. |
| Pressing that "Instalar" | Calls `SteamClient.Installs.OpenInstallWizard([appid], "0")`. Measured by wrapping the function (call logged, not forwarded). It is the hook point: handle the plugin's own appids, forward the others. |
| `display_status = 7; status_percentage = 42` | Game page: "Pausar" (Pause) button, "BAIXANDO / Progresso: 42%" (Downloading / Progress) and a progress bar under the button. |
| `downloadsStore.OnDownloadItems(true, [{remote_client_id:"0", item_data:[item, …real]}])` + `downloadsStore.OnDownloadOverview({…overview, update_appid:id, update_state:"Downloading", …})` | The top bar shows the download indicator (icon with a blue bar). `/library/downloads` (the route of the gamepad UI; `/downloads` renders nothing) opens, but the "downloading now" header stayed empty with a minimal item copied from a finished one; the "A seguir" (Up next) queue lists only `queue_index > 0`. Not investigated further. |
| `OnDownloadOverview` without `history` | It does `m_DownloadHistory.set(id, overview.history)`; with `undefined`, the speed graph of `/library/downloads` throws `reading 'length'` and Decky's error screen **stays** even after the store is fixed, until Steam restarts. Always pass `history: downloadsStore.DownloadHistory`. |
| How long the injected state lasts | Memory only: a Steam restart brings back `display_status:11`. During the download injection Steam rewrote `per_client_data` once (back to 11 within 29 s); idle, with a 250 ms poll, it stayed for 90 s. A real implementation has to re-apply on `RegisterForAppOverviewChanges` and on its own timer, like the achievements store. |

## Remote library in Steam's game page (issue #45, measured 2026-11-13, SteamOS 3.9.5, Decky v3.3.1)

What `src/remote-steam.ts` relies on. Measured on the Deck with an `IP Teste Instalar` shortcut (removed afterwards) and then with the two library games of #46 (Test Drive 6, Captain Claw).

| Call / situation | Result |
|---|---|
| `per_client_data[0]` after `AddShortcut` | Steam rewrote it (back to `display_status:11`) within the first second of a new shortcut. |
| What replaces it | Every overview change of the app gives a **new** `per_client_data` object with Steam's values: `SetShortcutName` (same or new name) is enough. So `updateShortcut` on every sync would undo "Instalar"; the plugin calls it only when the spec changed. |
| `SteamClient.Apps.RegisterForAppOverviewChanges(cb)` | Returns `undefined` (nothing to unregister). `cb` gets one buffer per batch (178 KB on registration, 89 bytes for a rename) and runs after Steam's own handler; re-applying in `setTimeout(0)` from it worked. Steam's `appStore` kept updating with our callback registered. The plugin registers it once per Steam JS context (`globalThis.__insertplayOverviewHook`) and swaps its handler to a no-op on unload. |
| `display_status` with `installed=false` | 9 "Instalar"; 7 "Pausar" + "BAIXANDO" (no percent by itself); 22 "Retomar" + "DOWNLOAD PAUSADO / Progresso: N%"; 23 "Baixar" + "DOWNLOAD NA FILA / Progresso: N%"; 21 "Atualizar" + "atualizações desativadas… modo off-line"; 18 "Retomar" + "ATUALIZAÇÃO PAUSADA"; 3 "Instalar" + "INSTALANDO". `status_percentage` drives the bar and the percent of 22/23. |
| "Progresso: N%" under "BAIXANDO" (status 7) | Comes from `downloadsStore.CurrentViewingDownloadOverview` / `LocalDownloadOverview` with `update_appid` = the app and `overall_percent_complete`. A synthetic `GetDownloadItem` did nothing. The plugin shadows those two getters **on the store instance** (no store write) while one of its games downloads and Steam's own overview is idle (`update_appid` 0). With 0 % Steam shows "BAIXANDO" without the percent. |
| Re-rendering the page | Writing `per_client_data` re-renders nothing. The page reads the overview getters in every state, so the shadowed getters read a mobx box of ours (`observable.box`, found with `findModuleExport`): `box.set` from a timer re-rendered the page at once (10 % → 55 %, "Pausar" → "Retomar", "Instalar" → "Jogar" → "Instalar"), without leaving it. |
| Buttons | "Instalar" → `SteamClient.Installs.OpenInstallWizard([appid], "0")`; "Retomar" (22) and "Baixar" (23) → `SteamClient.Downloads.ResumeAppUpdate(appid, "0")`; "Pausar" (7) → `SteamClient.Downloads.EnableAllDownloads(false, "0")`, **without an appid** (measured by wrapping every `Downloads`/`Installs`/`Apps` function without forwarding, then clicking). The plugin treats `EnableAllDownloads(false)` as its own only while its overview is the one shown, i.e. Steam had no download. |
| Uninstall entry | A non-Steam shortcut has none, installed or not: the gear menu has "Gerenciar → Ocultar jogo / Marcar como privado (indisponível) / Remover jogo não Steam / Alterar arte do jogo". "Remover jogo não Steam" deletes the shortcut, so it is not intercepted; uninstalling is the panel's "Desinstalar". `SteamClient.Installs.OpenUninstallWizard` exists but no shortcut menu calls it. |
| `/library/downloads` while a library game downloads (getters shadowed) | Renders normally ("A seguir (0)", "Não há downloads na fila", Steam's scheduled items); our game is not listed. The top bar shows the download indicator with the game's icon. |
| Library grid ("Não Steam" tab) | The not-installed Test Drive 6 tile looks like any other (full-color capsule, no dimming). |
| Steam restart with both shortcuts in "Instalar" | After `StartRestart`, `plugin_loader` active, both back to `installed:false, display_status:9`, same appids, no duplicate, not hidden; "Instalar" on the page (screenshot). |

End to end on the Deck (both games, `out/screens/`): "Instalar" → button pressed → "BAIXANDO / Progresso: N%" (Steam's "Pausar" and "Retomar" paused and resumed the engine) → "Jogar" → the game ran (gamescope screenshot: Test Drive 6 intro, Captain Claw main menu) → closed → panel "Desinstalar" (confirm modal) → folder gone, "Instalar" again. Test Drive 6 (580 MB, 15 files) installed in 57 s and 46 s; Captain Claw (2.3 GB, 27 339 files) in 1064 s, ~135 KB/s while on the small files.

Screenshots of the running game: the plain `screenshot` captures Steam's CEF window, not the game. `XDG_RUNTIME_DIR=/run/user/1000 gamescopectl screenshot /tmp/x.png` (over SSH) captures what is on screen.

## Library tabs "Todos os jogos" / "Instalados" (issue #52, measured 2026-11-14, SteamOS 3.9.5, Decky v3.3.1)

What `src/library-tabs.tsx` relies on. Measured on the Deck with a temporary build and `IP Teste Abas` / `IP Teste Coleção`, removed afterwards.

**Why shortcuts are missing.** `collectionStore.allGamesCollection` (id `my-games`, "Todos") is a mobx `computed` (keepAlive): `GetCollectionForAppType(game).allApps`, plus `.filter(a => !a.BIsShortcut())` when the gamepad UI is active. `localGamesCollection` (`local-install`) is the same with `local_per_client_data?.installed`. Both, and `collectionStore.GetCollection`, are own instance properties, non-configurable and non-writable, so the store cannot be patched. The Filter menu (X) only narrows a collection (features, status Installed/ReadyToPlay/PlayedPreviously/Unplayed/ValidPlatform/Private, app type, store tags): no option brings shortcuts back.

**Where the tabs come from.** Route `/library`: Decky wrapper → Steam's library route component (source has `GetLastLibraryTab`) → a `React.memo` whose inner function (source has `rgTabs`) renders the tabs element (`props.tabs`, an array). Tab shape: `{id: "AllGames" | "Installed" | …, title, renderTabAddon: () => <Count collection/>, content: <Provider>{<Grid collection eSortBy …/>}</Provider>, footer}`. The grid reads `collection.visibleApps` and passes `collection.id` to the sort/filter UI.

| Call / situation | Result |
|---|---|
| Swap `collection` in the AllGames/Installed `content` and `renderTabAddon` for an object of Steam's own class (`new (Object.getPrototypeOf(collectionStore.allGamesCollection).constructor)(id, name)`) filled with `SetApps(steam ids + ours)` | Todos 309 → 312, Instalados 3 → 4 (screenshots: Captain Claw in the Todos grid, an `IP Teste` shortcut in Instalados). The not-installed remote games appear only in Todos. |
| `SetApps` on our object from a timer | The grid and the tab count update at once, without leaving the page, with no render of the tab component (`m_rgApps` is observable). |
| Steam recomputes its collection (`AddShortcut` / `RemoveShortcut` of another app) | New collection identity, noticed in the next tab render; a `setTimeout(sync, 0)` rebuilt ours. |
| Library open and idle for 60 s | 0 renders, 0 syncs. |
| `routerHook.removePatch` | Back to 309/3 on the next router render (leave and re-enter the library). |
| Deploy and 2 × `StartRestart` | Re-applied at plugin load; 312/4 with the right contents. |
| Decky's element passed to the patch | Its `type` is a new arrow function on every router pass: the wrapper we put in its place must be a module-level component, or the library remounts. |
| `collectionStore.BIsHidden` at plugin load | Threw `reading 'get'` right after a deploy and after one of two restarts (collections not loaded yet). Unguarded, it aborted `definePlugin`. |
| `per_client_data.installed` of Remote Library shortcuts at plugin load | Still Steam's `true` until remote-steam re-applies "Instalar": "installed" must come from the plugin's state. |

How the plugin uses it: one collection object per tab, keeping Steam's id (sort and filter keep working), rebuilt by `syncTabs` only outside a render (plugin events through `refreshTabs` in `flow.ts`, and a `setTimeout(0)` when a render sees a new Steam collection) and only when the id list changed. A tab is swapped only while its collection id is still the one ours was built from; a shape not found leaves the tab as Steam built it and is logged once. `syncTabs` is retried every second (up to 2 minutes) while the collections are not loaded.

**Collections (measured, not used).** `NewUnsavedCollection(name, undefined, overviews)` + `SaveCollection`; `AddOrRemoveApp(appids, add, id)` saves by itself; `DeleteCollection(id)` leaves `is_deleted: true` in the cloud. `ToStorageFormat()` splits members: Steam apps in `roamingData` (cloud, `config/cloudstorage/cloud-storage-namespace-1.json`, key `user-collections.<id>`), shortcuts in `localData` (`SteamClient.Storage` key `user-collections`, i.e. `localconfig.vdf`, this device only). A shortcut member survived a Steam restart. `SaveCollection` of a new collection first deletes any user collection with the same name.

Not measured: the Steam Machine (other plugins, cloud view of collections), a physical A press on a remote tile, search and home shelves.

## Panel navigation: full-screen route vs Quick Access subpage (issue #53, measured 2026-11-14, SteamOS 3.9.5, Decky v3.3.1)

Test routes and components (`IP Teste …`) were injected by eval and removed afterwards. Buttons were simulated with `FocusNavController.DispatchVirtualGamepad("vgp_onbuttondown" | "vgp_onbuttonup", {button, source: 1})`, which goes through Steam's focus controller (`GamepadButton`: OK 1, CANCEL 2, DIR_DOWN 10, DIR_RIGHT 12). Focus was read from the window's `activeElement` and checked on screenshots.

**Full-screen route** (`routerHook.addRoute(path, Component)` + `Navigation.Navigate(path)` + `Navigation.CloseSideMenus()`):

| Situation | Result |
|---|---|
| A on a panel button that navigates | The Quick Access closes and the page opens in the Big Picture window. |
| `SidebarNavigation` with `pages[].route` (`/x/cartoes`, `/x/biblioteca`, `/x/geral`) | The focus lands on the sidebar item of the open page; a deep link selects that page. D-pad down switches pages (replacing the history entry); right enters the content. |
| B | In the content: the focus goes back to the sidebar. In the sidebar: back to the previous Steam screen; the Quick Access does not reopen. |
| Plain page (no sidebar) | B goes back too, but no button had the focus until a D-pad press. |
| `showModal(<ConfirmModal/>)` from the route | Over the page in Big Picture, focus on OK. B cancels and the focus returns to the button that opened it; OK keeps the route open. |
| `removeRoute` while the route is open | The page stays until the next navigation; then the path renders an empty screen. Leave the route before removing it on unload. |
| History entries (`m_history.length`, measured with the real build) | D-pad down on the sidebar **replaces** the entry (length 3 → 3); `Navigate` to another category **pushes** one (3 → 4), and so does A on a panel category button while the route is open. B goes back one entry per press. `m_history` is a memory history (`length, action, location, createHref, push, replace, go, goBack, goForward, block, listen`) without `entries` or `index`; each entry has its own `location.key`. |
| Leaving every route entry on unload | The plugin follows `m_history.listen` from load (PUSH/REPLACE/POP by `location.key`) and on unload calls `go(-k)`, k = route entries on top. Measured: `/insertplay/geral` + `/insertplay/biblioteca` on top of `/library/home`, `unloadPlugin("InsertPlay")` → `/library/home` rendered, and a virtual B stayed there. The panel's category button with the route open uses `replace` (length 2 → 2). |
| Current path | `(SteamUIStore.m_WindowStore ?? SteamUIStore.WindowStore).SteamUIWindows[0].m_history.location.pathname` follows the route (`currentPath()` in `src/steam.ts`). |

The route's pages mount when opened and unmount when left, so each page loads its own data on mount.

**Subpage inside the Quick Access panel** (React state):

| Situation | Result |
|---|---|
| Page swap | The focus goes to the Quick Access tab column. `navRef` on `Focusable` + `navRef.current.TakeFocus()` after the swap puts it on the first item. |
| B, no handler | Decky goes back to its plugin list. With `Focusable onCancel` + `stopPropagation()`: back to the main page. |
| `showModal` from the panel | Opens in Big Picture and closes the Quick Access. On cancel the Quick Access reopens with the panel remounted: the subpage state is lost. |

Decision: full-screen route with `SidebarNavigation` (`/insertplay`, `src/panel/route.tsx`). The panel keeps a summary and one button per category. Route paths must not collide with Steam's or other plugins' (Decky had `/decky/store`, `/decky/settings`, `/steamgriddb/:appid/:assetType?`, `/plugins/PowerTools/settings_store`). Not yet confirmed with a physical controller press, on the Steam Machine, or with a game running.

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

// The only file that touches Steam's undocumented API. When Valve changes a
// method, the fix goes here.
//
// Parts adapted from decky-nonsteam-sync (src/steam.ts),
// Copyright (c) 2026, JoseArkadio — BSD 3-Clause.
//
// Measured on the Deck (SteamOS 3.8.28, 2026-10-24) with scripts/spike.mjs: every call
// below works as used here. Results in docs/steam-api-notes.md; run the
// spike again after a Steam update.

declare const appStore: any;
declare const appDetailsStore: any;
declare const collectionStore: any;
declare const SteamUIStore: any;
// Typed as `any` on purpose: not every @decky/ui version declares the global,
// and the real signatures are the ones step 0 measures on the Deck.
declare const SteamClient: any;

const apps = (): any => (typeof appStore === "undefined" ? null : appStore);
const collections = (): any => (typeof collectionStore === "undefined" ? null : collectionStore);
const client = (): any => (typeof SteamClient === "undefined" ? null : (SteamClient as any));

function method(owner: any, name: string, label: string): (...args: any[]) => any {
  const fn = owner?.[name];
  if (typeof fn !== "function") throw new Error(`Steam method missing: ${label}`);
  return fn.bind(owner);
}

export interface ShortcutSpec {
  appName: string;
  exe: string;
  startDir: string;
  launchOptions: string;
}

/** Waits for Steam to finish loading the library and the collections.
 *
 *  Measured on the Deck when restarting Steam:
 *  - Decky loads the plugin ~0.5 s BEFORE `appStore.m_bIsInitialized`, and shortcuts
 *    only show up in `appStore` at that moment. Asking earlier answered "the shortcut does
 *    not exist" and the plugin created a duplicate;
 *  - the collections load AFTER that: `SetAppsAsHidden` → `GetCollection` threw
 *    "reading 'get'". We test the "hidden" collection itself, which is what we use.
 *  Until that is ready we decide nothing: throws instead of carrying on blindly. */
function libraryReady(): boolean {
  if (!apps()?.m_bIsInitialized) return false;
  try {
    return Boolean(collections()?.GetCollection?.("hidden"));
  } catch {
    return false;
  }
}

export async function waitForLibrary(timeoutMs = 60_000): Promise<void> {
  const start = Date.now();
  while (!libraryReady()) {
    if (Date.now() - start > timeoutMs) {
      throw new Error("Steam library did not finish loading; not touching shortcuts");
    }
    await new Promise((r) => setTimeout(r, 250));
  }
}

export function shortcutExists(appid: number): boolean {
  return Boolean(apps()?.GetAppOverviewByAppID?.(appid));
}

export async function addShortcut(spec: ShortcutSpec): Promise<number> {
  const apps = client()?.Apps;
  const appid: number = await method(apps, "AddShortcut", "SteamClient.Apps.AddShortcut")(
    spec.appName,
    spec.exe,
    startDirOf(spec.startDir),
    spec.launchOptions,
  );
  if (!appid) throw new Error("AddShortcut returned no appid");
  // Measured in nonsteam-sync: AddShortcut ignores the name and uses the executable's. Here the
  // exe is /bin/bash, so without this every card would become a tile named "bash".
  method(apps, "SetShortcutName", "SteamClient.Apps.SetShortcutName")(appid, spec.appName);
  method(apps, "SetShortcutLaunchOptions", "SteamClient.Apps.SetShortcutLaunchOptions")(appid, spec.launchOptions);
  return appid;
}

/** Reapplies name, exe, folder and options. The card's mount point changes between
 *  devices and labels, and the shortcut stores a fixed path. */
export function updateShortcut(appid: number, spec: ShortcutSpec): void {
  const apps = client()?.Apps;
  method(apps, "SetShortcutName", "SteamClient.Apps.SetShortcutName")(appid, spec.appName);
  // Measured in nonsteam-sync: AddShortcut quotes the exe, SetShortcutExe stores it verbatim.
  const quoted = spec.exe.startsWith('"') ? spec.exe : `"${spec.exe}"`;
  method(apps, "SetShortcutExe", "SteamClient.Apps.SetShortcutExe")(appid, quoted);
  apps?.SetShortcutStartDir?.(appid, startDirOf(spec.startDir));
  method(apps, "SetShortcutLaunchOptions", "SteamClient.Apps.SetShortcutLaunchOptions")(appid, spec.launchOptions);
}

function startDirOf(dir: string): string {
  return dir.endsWith("/") ? dir : `${dir}/`;
}

export function setHidden(appid: number, hidden: boolean): void {
  method(collections(), "SetAppsAsHidden", "collectionStore.SetAppsAsHidden")([appid], hidden);
}

export function isHidden(appid: number): boolean {
  return Boolean(collections()?.BIsHidden?.(appid));
}

// 0 = vertical capsule, 1 = hero, 2 = logo, 3 = horizontal capsule (measured in nonsteam-sync).
const ASSET_TYPE: Record<string, number> = { capsule: 0, hero: 1, logo: 2, horizontal: 3 };

/** Applies the card's artwork. One artwork failing does not block the others;
 *  returns the list of what failed. */
export async function applyArtwork(
  appid: number,
  artwork: Record<string, string>,
  toBase64: (path: string) => Promise<string>,
): Promise<string[]> {
  const set = method(client()?.Apps, "SetCustomArtworkForApp", "SteamClient.Apps.SetCustomArtworkForApp");
  const failed: string[] = [];
  for (const [kind, path] of Object.entries(artwork)) {
    const assetType = ASSET_TYPE[kind];
    if (assetType === undefined) continue;
    try {
      const data = await toBase64(path);
      if (!data) throw new Error("empty image");
      const ext = (path.split(".").pop() ?? "").toLowerCase();
      await set(appid, data, ext === "jpg" || ext === "jpeg" ? "jpg" : "png", assetType);
    } catch (err) {
      failed.push(`${kind}: ${err}`);
    }
  }
  return failed;
}

/** A shortcut's icon. Steam stores only the path and reads the file on demand (measured
 *  on the Deck, 2026-10-27): so the path must be on the SSD, never on the card. */
export function setShortcutIcon(appid: number, path: string): void {
  method(client()?.Apps, "SetShortcutIcon", "SteamClient.Apps.SetShortcutIcon")(appid, path);
}

/** steamid64 of the logged-in account; `null` = could not tell. */
export function currentSteamId(): string | null {
  const id = (globalThis as any).App?.m_CurrentUser?.strSteamID;
  return typeof id === "string" && /^\d+$/.test(id) ? id : null;
}

export interface SteamLibrary {
  path: string;
  mounted: boolean;
  /** Steam's `bIsFixed`. It only tells the card apart on the Deck: on the Steam Machine, the
   *  card in the USB reader comes as fixed (measured on 2026-10-24). `libraryCard` decides. */
  fixed: boolean;
  apps: number[];
}

/** Steam's internal library (`~/.local/share/Steam`) is always index 0 (measured
 *  on the Deck and the Steam Machine, 2026-10-25). Not `bIsDefaultFolder`: that flag is the
 *  default install location and moves to the card when the user picks it on install. With
 *  it, a card chosen as default vanished: no badge and no portable saves. */
const isInternal = (folder: any): boolean => folder?.nFolderIndex === 0;

/** Library list once loaded. Measured on the Deck when restarting Steam: it comes
 *  empty at first and, ~3 s later, shows up whole at once with no Steam notification.
 *  The internal library always exists, so a list without it = still loading. Using the
 *  empty list as a snapshot would make the inserted card look inserted on the next notice. */
async function installFolders(timeoutMs = 60_000): Promise<any[]> {
  const get = method(client()?.InstallFolder, "GetInstallFolders", "SteamClient.InstallFolder.GetInstallFolders");
  const start = Date.now();
  for (;;) {
    const folders: any[] = await get();
    if (Array.isArray(folders) && folders.some(isInternal)) return folders;
    if (Date.now() - start > timeoutMs) throw new Error("Steam library folders did not finish loading");
    await new Promise((r) => setTimeout(r, 250));
  }
}

/** Steam libraries other than the internal one, mounted or not, with the installed appids.
 *
 *  Measured on the Deck (2026-10-24, SteamOS 3.9.5): every library Steam has ever seen becomes
 *  a folder in `GetInstallFolders()`, mounted or not. The one of the card in the reader has
 *  `bIsMounted: true`. Those of absent cards stay in the list with `bIsMounted: false`
 *  and the appids they had: callers that want only what is in the reader filter by `mounted`. */
export async function steamLibraries(): Promise<SteamLibrary[]> {
  const folders = await installFolders();
  return folders
    .filter((f) => f && !isInternal(f) && f.strFolderPath)
    .map((f) => ({
      path: String(f.strFolderPath),
      mounted: Boolean(f.bIsMounted),
      fixed: Boolean(f.bIsFixed),
      apps: (f.vecApps ?? []).map((a: any) => Number(a?.nAppID)).filter((appid: number) => appid > 0),
    }));
}

/** Appids installed in each mounted library, including the internal one (SSD). */
export async function mountedLibraries(): Promise<{ path: string; apps: number[] }[]> {
  const folders = await installFolders();
  return folders
    .filter((f) => f?.strFolderPath && (f.bIsMounted || isInternal(f)))
    .map((f) => ({
      path: String(f.strFolderPath),
      apps: (f.vecApps ?? []).map((a: any) => Number(a?.nAppID)).filter((appid: number) => appid > 0),
    }));
}

/** Notifies when libraries appear, disappear or change contents. Returns the unsubscribe function.
 *  Measured on the Deck: the notification arrives less than 1 s after the card mounts or unmounts. */
export function onInstallFoldersChanged(callback: () => void): () => void {
  const registration = client()?.InstallFolder?.RegisterForInstallFolderChanges?.(callback);
  return () => registration?.unregister?.();
}

/** Shortcut gameid = (appid << 32) | 0x02000000. Prefers the value Steam exposes. */
function gameIdOf(appid: number): string {
  const fromStore = apps()?.GetAppOverviewByAppID?.(appid)?.m_gameid;
  if (fromStore) return String(fromStore);
  return ((BigInt(appid >>> 0) << 32n) | 0x02000000n).toString();
}

export function runGame(appid: number): void {
  method(client()?.Apps, "RunGame", "SteamClient.Apps.RunGame")(gameIdOf(appid), "", -1, 100);
}

/** Steam game: the overview has no `m_gameid` (measured on the Deck) and the gameid is the
 *  appid itself, without the shortcut bits. */
export function runSteamGame(appid: number): void {
  method(client()?.Apps, "RunGame", "SteamClient.Apps.RunGame")(String(appid), "", -1, 100);
}

/** Notifies when a game closes, with its appid (a shortcut's appid, not the gameid).
 *  Measured with spike.mjs: `RegisterForAppLifetimeNotifications` reports `{unAppID, bRunning}`
 *  at start and exit. Returns the unsubscribe function. */
export function onAppClosed(callback: (appid: number) => void): () => void {
  const registration = client()?.GameSessions?.RegisterForAppLifetimeNotifications?.((e: any) => {
    if (e && e.bRunning === false && Number(e.unAppID) > 0) callback(Number(e.unAppID));
  });
  return () => registration?.unregister?.();
}

export function appName(appid: number): string {
  return apps()?.GetAppOverviewByAppID?.(appid)?.display_name || `app ${appid}`;
}

/** The game's icon as an `<img>` URL; `null` = none. Measured on the Deck (2026-10-31): a
 *  shortcut gives a `data:` URI with its `SetShortcutIcon` icon, a Steam game gives the CDN
 *  URL. Cosmetic: never throws. */
export function appIcon(appid: number): string | null {
  try {
    const overview = apps()?.GetAppOverviewByAppID?.(appid);
    const url = overview ? apps()?.GetIconURLForApp?.(overview) : null;
    return typeof url === "string" && url ? url : null;
  } catch {
    return null;
  }
}

/** Some game is running now. When unsure (store missing), answers yes: the worst case is
 *  not auto-starting. */
export function gameRunning(): boolean {
  const running = typeof SteamUIStore === "undefined" ? undefined : SteamUIStore?.RunningApps;
  return !Array.isArray(running) || running.length > 0;
}

/** Appids of the running games. `null` = unknown (store missing or item without appid): when
 *  unsure, portable saves touch no prefix at all. */
export function runningAppids(): number[] | null {
  const running = typeof SteamUIStore === "undefined" ? undefined : SteamUIStore?.RunningApps;
  if (!Array.isArray(running)) return null;
  const ids = running.map((app: any) => Number(app?.appid));
  return ids.every((id) => id > 0) ? ids : null;
}

/** The game's launch options (`undefined` = could not tell).
 *
 *  Measured (2026-10-25): `appDetailsStore.GetAppDetails(appid)` only has data after
 *  Steam loads the details, which happens when the game's page is opened. Without a cache,
 *  `SteamClient.Apps.RegisterForAppDetails` forces the load: the callback arrived in ~40 ms with
 *  `strLaunchOptions`. The text is stored per device. */
export function launchOptions(appid: number): Promise<string | undefined> {
  try {
    const cached = typeof appDetailsStore === "undefined" ? undefined : appDetailsStore?.GetAppDetails?.(appid);
    if (typeof cached?.strLaunchOptions === "string") return Promise.resolve(cached.strLaunchOptions);
  } catch {
    // fall through to the load
  }
  return new Promise((resolve) => {
    let registration: any = null;
    let finished = false;
    const finish = (value: string | undefined) => {
      if (finished) return;
      finished = true;
      try {
        registration?.unregister?.();
      } catch {
        // nothing to undo
      }
      resolve(value);
    };
    try {
      registration = client()?.Apps?.RegisterForAppDetails?.(appid, (details: any) =>
        finish(typeof details?.strLaunchOptions === "string" ? details.strLaunchOptions : undefined),
      );
    } catch {
      return finish(undefined);
    }
    // the callback may have arrived before `registration` existed
    if (finished) registration?.unregister?.();
    else if (!registration) finish(undefined);
    else setTimeout(() => finish(undefined), 3000);
  });
}

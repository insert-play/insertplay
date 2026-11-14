// Remote library ("Biblioteca Remota", #45) in Steam's own game page: the "Instalar" button,
// "Baixando / Progresso: N%", "Download pausado" and "Jogar" on a non-Steam shortcut.
//
// Fourth place that touches Steam internals, kept out of steam.ts because it wraps Steam
// functions and shadows store getters instead of calling methods (like achievements.ts).
// Measured on the Deck (SteamOS 3.9.5, Decky 3.3.1, 2026-11-13; docs/steam-api-notes.md):
// - `appStore.GetAppOverviewByAppID(id).per_client_data[0]` drives the button:
//   `installed=false` + `display_status` 9 "Instalar", 7 "Pausar / BAIXANDO",
//   22 "Retomar / DOWNLOAD PAUSADO / Progresso: N%", 23 "Baixar / DOWNLOAD NA FILA / Progresso: N%";
// - Steam REPLACES that object on every overview change of the app (a rename, a launch...),
//   so the values are re-applied on `RegisterForAppOverviewChanges` and on a timer, and
//   written only when they differ (lesson of #24: never write from a render getter);
// - the page reads `downloadsStore.CurrentViewingDownloadOverview`/`LocalDownloadOverview` in
//   every state, and "Progresso: N%" under "BAIXANDO" comes from it (`update_appid` = the app,
//   `overall_percent_complete`). Those getters are shadowed on the store instance, and they
//   read a mobx box we own: bumping the box re-renders the page right away (no store write);
// - buttons: "Instalar" calls `SteamClient.Installs.OpenInstallWizard([appid], "0")`,
//   "Retomar" and "Baixar" call `SteamClient.Downloads.ResumeAppUpdate(appid, "0")`, "Pausar"
//   calls `SteamClient.Downloads.EnableAllDownloads(false, "0")` (no appid);
// - `RegisterForAppOverviewChanges` returns nothing to unregister: it is registered once per
//   Steam JS context (a plugin reload reuses it) and its handler goes to a no-op on unload;
// - Steam has no "Desinstalar" for a non-Steam shortcut (Gerenciar shows only "Ocultar",
//   "Remover jogo não Steam" and "Alterar arte"): uninstalling is the panel's job.

import { findModuleExport } from "@decky/ui";

declare const appStore: any;
declare const downloadsStore: any;
declare const SteamClient: any;

/** What the game page should show for a library shortcut. */
export interface RemoteDisplay {
  /** `installed=false` + `display_status`; `null` = Steam's own state ("Jogar", "Retomar" while it runs). */
  status: 9 | 7 | 22 | 23 | null;
  percent: number;
  /** bytes/s, for the downloads overview. */
  speed: number;
}

export interface RemoteSteamHandlers {
  /** "Instalar" pressed on a library shortcut. */
  install(appid: number): void;
  /** "Retomar" or "Baixar" pressed on a library shortcut. */
  resume(appid: number): void;
  /** "Pausar" pressed while the page showed our download. */
  pause(appid: number): void;
}

const READY_TO_LAUNCH = 11;
/** The `display_status` values this module writes for a game that is not installed. */
const INJECTED = new Set<number>([9, 7, 22, 23]);
const REAPPLY_MS = 1000;
const OVERVIEWS = ["CurrentViewingDownloadOverview", "LocalDownloadOverview"] as const;

/** appid → what to show. Only library shortcuts with the card absent are here. */
let desired = new Map<number, RemoteDisplay>();
/** Library appids (any state): their install/resume buttons are ours. */
let ours = new Set<number>();
/** appids whose per_client_data we changed, to put back when they leave `desired`. */
const touched = new Set<number>();
let handlers: RemoteSteamHandlers | null = null;
let timer: ReturnType<typeof setInterval> | null = null;
let box: { get(): number; set(v: number): void } | null = null;
const restore: (() => void)[] = [];

/** Bumps the box the shadowed getters read: the game page re-renders now. Called only from
 *  timers and callbacks, never from a getter. */
function redraw(): void {
  if (box) box.set(box.get() + 1);
}

function clientData(appid: number): any {
  try {
    return appStore?.GetAppOverviewByAppID?.(appid)?.per_client_data?.[0] ?? null;
  } catch {
    return null;
  }
}

/** Writes what differs; returns whether anything changed. */
function applyOne(appid: number, want: RemoteDisplay | null): boolean {
  const data = clientData(appid);
  if (!data) return false;
  // Installed: undo only what we injected. Steam's own states stay, e.g. 4 while the game runs
  // ("Retomar" + "X"; measured on the Deck, 2026-11-14, #57): forcing 11 showed "Jogar".
  if (want?.status == null && data.installed !== false && !INJECTED.has(data.display_status)) return false;
  const installed = want?.status == null;
  const status = want?.status ?? READY_TO_LAUNCH;
  const percent = want?.status == null ? 100 : want.percent;
  if (data.installed === installed && data.display_status === status && data.status_percentage === percent) return false;
  data.installed = installed;
  data.display_status = status;
  data.status_percentage = percent;
  return true;
}

function apply(): void {
  let changed = false;
  for (const [appid, want] of desired) {
    if (applyOne(appid, want)) changed = true;
    touched.add(appid);
  }
  for (const appid of [...touched]) {
    if (desired.has(appid)) continue;
    if (applyOne(appid, null)) changed = true;
    touched.delete(appid);
  }
  if (changed) redraw();
}

/** The library game being downloaded now, for the downloads overview. */
function downloading(): [number, RemoteDisplay] | null {
  for (const entry of desired) if (entry[1].status === 7) return entry;
  return null;
}

/** Shadowed getter results keep their identity while nothing changes. */
let shadowCache: { real: any; key: string; value: any } | null = null;
let shadowShown = 0;

function shadow(real: any): any {
  box?.get(); // the dependency that lets `redraw` re-render whoever reads the overview
  const active = downloading();
  // a real Steam download always wins: ours is shown only while Steam's is idle
  if (!active || !real || Number(real.update_appid) > 0) {
    shadowShown = 0;
    return real;
  }
  const [appid, want] = active;
  shadowShown = appid;
  const key = `${appid}|${want.percent}|${want.speed}`;
  if (shadowCache && shadowCache.real === real && shadowCache.key === key) return shadowCache.value;
  const value = {
    ...real,
    update_appid: appid,
    update_state: "Downloading",
    update_is_install: true,
    overall_percent_complete: want.percent,
    update_network_bytes_per_second: want.speed,
  };
  shadowCache = { real, key, value };
  return value;
}

function wrap(owner: any, name: string, make: (orig: (...args: any[]) => any) => (...args: any[]) => any): void {
  const orig = owner?.[name];
  if (typeof orig !== "function") throw new Error(`Steam method missing: ${name}`);
  const wrapped = make(orig.bind(owner));
  owner[name] = wrapped;
  restore.push(() => {
    if (owner[name] === wrapped) owner[name] = orig;
  });
}

/** One registration per Steam JS context (see the header). */
interface OverviewHook {
  handler: () => void;
}

function overviewHook(): OverviewHook {
  const g = globalThis as any;
  if (!g.__insertplayOverviewHook) {
    const hook: OverviewHook = { handler: () => undefined };
    SteamClient.Apps.RegisterForAppOverviewChanges(() => hook.handler());
    g.__insertplayOverviewHook = hook;
  }
  return g.__insertplayOverviewHook;
}

/** Installs the hooks. Returns what failed (each part is independent). */
export function installRemoteSteam(h: RemoteSteamHandlers): string[] {
  if (timer) return [];
  handlers = h;
  const failed: string[] = [];
  const part = (name: string, fn: () => void) => {
    try {
      fn();
    } catch (err) {
      failed.push(`${name}: ${err instanceof Error ? err.message : err}`);
    }
  };
  part("mobx", () => {
    const observable = findModuleExport(
      (e: any) => typeof e === "function" && typeof e.box === "function" && typeof e.map === "function",
    );
    if (!observable) throw new Error("mobx observable not found");
    box = observable.box(0);
  });
  part("install", () =>
    wrap(SteamClient?.Installs, "OpenInstallWizard", (orig) => (appids: any, ...rest: any[]) => {
      const list: number[] = Array.isArray(appids) ? appids.map(Number) : [];
      const mine = list.filter((a) => ours.has(a));
      if (!mine.length) return orig(appids, ...rest);
      for (const appid of mine) handlers?.install(appid);
      const others = list.filter((a) => !ours.has(a));
      return others.length ? orig(others, ...rest) : undefined;
    }),
  );
  part("resume", () =>
    wrap(SteamClient?.Downloads, "ResumeAppUpdate", (orig) => (appid: any, ...rest: any[]) => {
      if (!ours.has(Number(appid))) return orig(appid, ...rest);
      handlers?.resume(Number(appid));
      return undefined;
    }),
  );
  part("pause", () =>
    wrap(SteamClient?.Downloads, "EnableAllDownloads", (orig) => (enable: any, ...rest: any[]) => {
      // "Pausar" has no appid: it is ours only while the overview shows our download (and
      // then Steam has none of its own to pause)
      // `shadowShown` is only refreshed when something reads the overview: check that the
      // game is still downloading, or a later pause of Steam's own would be swallowed
      if (enable !== false || !shadowShown || downloading()?.[0] !== shadowShown) return orig(enable, ...rest);
      handlers?.pause(shadowShown);
      return undefined;
    }),
  );
  part("overview", () => {
    const store = downloadsStore;
    const proto = store && Object.getPrototypeOf(store);
    for (const name of OVERVIEWS) {
      const getter = proto && Object.getOwnPropertyDescriptor(proto, name)?.get;
      if (!getter) throw new Error(`downloadsStore.${name} missing`);
      Object.defineProperty(store, name, { configurable: true, get: () => shadow(getter.call(store)) });
      restore.push(() => void delete store[name]);
    }
  });
  part("overviewChanges", () => {
    const hook = overviewHook();
    // Steam replaced per_client_data: put ours back after Steam's own handler ran
    hook.handler = () => void setTimeout(apply, 0);
    restore.push(() => {
      hook.handler = () => undefined;
    });
  });
  timer = setInterval(apply, REAPPLY_MS);
  return failed;
}

/** New set of library shortcuts and what each one shows. */
export function setRemoteDisplay(libraryApps: Iterable<number>, display: Map<number, RemoteDisplay>): void {
  ours = new Set(libraryApps);
  desired = new Map(display);
  apply();
  redraw();
}

/** Plugin unload: Steam's functions and values come back. */
export function stopRemoteSteam(): void {
  if (timer) clearInterval(timer);
  timer = null;
  desired = new Map();
  ours = new Set();
  apply();
  for (const undo of restore.splice(0).reverse()) {
    try {
      undo();
    } catch {
      // already gone
    }
  }
  handlers = null;
  box = null;
  shadowCache = null;
  shadowShown = 0;
}

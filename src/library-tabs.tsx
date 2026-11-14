// Plugin shortcuts in the Game Mode library tabs "Todos os jogos" and "Instalados" (issue #52).
//
// Fourth place that touches Steam internals, kept out of steam.ts like achievements.ts and
// remote-steam.ts because it patches a render instead of calling methods. Measured on the Deck
// (SteamOS 3.9.5, Decky 3.3.1, 2026-11-14; docs/steam-api-notes.md, "Library tabs"):
// - Steam drops every shortcut from those tabs in its own collections
//   (`collectionStore.allGamesCollection` / `localGamesCollection`, mobx computeds that are
//   non-writable own properties): the store cannot be patched, and the Filter menu (X) only
//   narrows. So the `/library` render is wrapped and, in those two tabs only, the collection
//   the grid and the count read is swapped for one of ours: an object of Steam's own class,
//   with Steam's id (sort and filter keep working), filled with Steam's apps plus ours;
// - every component type is found by its SHAPE (exports are minified); when a shape is not
//   found the tab is left as Steam built it (fail open), and the error is logged once;
// - NEVER write inside a render (lesson of #24): the render only reads, and schedules
//   `sync` in a timer when Steam recomputed its collection. `SetApps` on our object is
//   observable: the grid and the count update without the tabs re-rendering;
// - Decky's element at the top of the route has a NEW type on every router pass: the
//   component put in its place is module-level, and every wrapper is cached by Steam's own
//   type, or the library would remount on each pass;
// - right after a deploy or a Steam restart the collections may not be loaded yet
//   (`BIsHidden` threw "reading 'get'"): `sync` is guarded and retried.
// Nothing in Steam's stores is modified, so unloading only drops the patch and our objects.

import { findInReactTree } from "@decky/ui";
import { cloneElement, createElement, memo } from "react";
import { logAdd } from "./backend";
import { isHidden, shortcutExists } from "./steam";

declare const collectionStore: any;

const collections = (): any => (typeof collectionStore === "undefined" ? null : collectionStore);

/** The plugin's own shortcuts, by tab. `installed` comes from the plugin's state (remote game
 *  installed, card inserted), never from `per_client_data.installed`: at plugin load that is
 *  still Steam's `true` for a remote game in "Instalar" (measured). */
export interface TabApps {
  all: number[];
  installed: number[];
}

let members: TabApps = { all: [], installed: [] };
let active = false;

/** Our collections (one per tab) and the Steam collections they were last built from. */
let mineAll: any = null;
let mineLocal: any = null;
let srcAll: any = null;
let srcLocal: any = null;
/** What was last given to `SetApps`, to skip a write that changes nothing. */
let appliedAll = "";
let appliedLocal = "";

let syncScheduled = false;
let retryTimer: ReturnType<typeof setTimeout> | null = null;
let retries = 0;
/** At 1 s each: covers Steam still loading after a restart (~3 s measured) with room to spare. */
const MAX_RETRIES = 120;
const RETRY_MS = 1000;

const reported = new Set<string>();
function reportOnce(what: string, err: unknown): void {
  const detail = err instanceof Error ? err.message : String(err);
  const key = `${what}: ${detail}`;
  if (reported.has(key)) return;
  reported.add(key);
  void logAdd("error", `library tabs: ${key}`).catch(() => undefined);
}

/** New plugin shortcuts for the tabs; rebuilds our collections now (outside any render). */
export function setTabApps(next: TabApps): void {
  members = next;
  syncTabs();
}

/** Rebuilds our collections from Steam's and `members`. The only writer; never in a render. */
export function syncTabs(): void {
  syncScheduled = false;
  if (!active) return;
  try {
    syncUnsafe();
    retries = 0;
  } catch (err) {
    // collections not loaded yet (plugin load after a deploy or a Steam restart): try again;
    // the render-side check also resyncs on the first library render
    if (retries++ < MAX_RETRIES) {
      if (!retryTimer) {
        retryTimer = setTimeout(() => {
          retryTimer = null;
          syncTabs();
        }, RETRY_MS);
      }
    } else {
      reportOnce("sync", err);
      // the next event or library render starts a new bounded cycle instead of never retrying
      retries = 0;
    }
  }
}

function scheduleSync(): void {
  if (syncScheduled || !active) return;
  syncScheduled = true;
  setTimeout(syncTabs, 0);
}

/** Steam's ids first (its order is irrelevant: the grid sorts), then ours not already in. */
function merged(steam: any, extra: number[]): number[] {
  const ids: number[] = Array.from(steam.m_rgApps as number[]);
  const has = steam.apps instanceof Set ? (a: number) => steam.apps.has(a) : (a: number) => ids.includes(a);
  for (const appid of extra) if (!has(appid)) ids.push(appid);
  return ids;
}

function syncUnsafe(): void {
  const store = collections();
  if (!store) throw new Error("collectionStore missing");
  // measured: the stored collections load after appStore; until then BIsHidden throws
  if ("collectionsFromStorage" in store && store.collectionsFromStorage == null) {
    throw new Error("collections not loaded yet");
  }
  const all = store.allGamesCollection;
  const local = store.localGamesCollection;
  if (!Array.isArray(all?.m_rgApps) || !Array.isArray(local?.m_rgApps)) throw new Error("Steam collections not found");
  // built in locals and published only after both `SetApps` succeeded: a sync that throws
  // midway (isHidden while the collections load) leaves Steam's own collections in the tabs
  // instead of an empty one of ours
  let a = mineAll;
  let l = mineLocal;
  const fresh = !a;
  if (fresh) {
    const Collection = Object.getPrototypeOf(all)?.constructor;
    if (typeof Collection !== "function") throw new Error("collection class not found");
    a = new Collection(all.id, all.displayName);
    l = new Collection(local.id, local.displayName);
    if (typeof a.SetApps !== "function") throw new Error("SetApps missing");
  }
  // hidden apps are dropped by `visibleApps` anyway; leaving them out keeps the count right
  // too. isHidden throws while the collections load: caught by syncTabs, which retries.
  // A shortcut deleted in Steam drops out here, on the sync Steam's own recompute triggers,
  // without waiting for the next plugin event.
  const shown = (ids: number[]) => ids.filter((appid) => shortcutExists(appid) && !isHidden(appid));
  const installed = shown(members.installed);
  const ids = merged(all, [...shown(members.all), ...installed]);
  const localIds = merged(local, installed);
  const keyAll = ids.join(",");
  const keyLocal = localIds.join(",");
  if (fresh || keyAll !== appliedAll) a.SetApps(ids);
  if (fresh || keyLocal !== appliedLocal) l.SetApps(localIds);
  mineAll = a;
  mineLocal = l;
  appliedAll = keyAll;
  appliedLocal = keyLocal;
  srcAll = all;
  srcLocal = local;
}

/** Inside the render of the tabs component: READS only. Swaps the collection of the two tabs. */
function patchTabs(out: any): void {
  if (!active) return;
  const store = collections();
  if (!mineAll || store?.allGamesCollection !== srcAll || store?.localGamesCollection !== srcLocal) scheduleSync();
  // set together, only by a sync that fully succeeded
  if (!mineAll || !srcAll) return;
  const el = findInReactTree(out, (x: any) => Array.isArray(x?.props?.tabs));
  if (!el) throw new Error("tabs element not found");
  const swap: Record<string, any> = { AllGames: mineAll, Installed: mineLocal };
  el.props.tabs = el.props.tabs.map((tab: any) => {
    const mine = tab && swap[tab.id];
    const inner = tab?.content?.props?.children;
    const theirs = inner?.props?.collection;
    // only when the tab still shows the collection ours was built from
    if (!mine || !theirs || theirs.id !== mine.id) return tab;
    const addon = tab.renderTabAddon;
    return {
      ...tab,
      content: cloneElement(tab.content, {}, cloneElement(inner, { collection: mine })),
      renderTabAddon:
        typeof addon === "function"
          ? () => {
              const a = addon();
              return a?.props?.collection ? cloneElement(a, { collection: mine }) : a;
            }
          : addon,
    };
  });
}

// ---- The wrappers. Each one is cached by the Steam type it wraps: same type in, same wrapper out.

const ours = new WeakSet<object>();
const memoCache = new Map<any, any>();
const routeCache = new Map<any, any>();

function wrapMemo(m: any): any {
  let wrapped = memoCache.get(m);
  if (!wrapped) {
    const inner = m.type;
    wrapped = memo((props: any) => {
      const out = inner(props);
      try {
        patchTabs(out);
      } catch (err) {
        reportOnce("tabs", err);
      }
      return out;
    }, m.compare);
    ours.add(wrapped);
    memoCache.set(m, wrapped);
  }
  return wrapped;
}

/** Steam's library route component: its child is the memo that builds `rgTabs`. */
function wrapRoute(route: any): any {
  let wrapped = routeCache.get(route);
  if (!wrapped) {
    wrapped = (props: any) => {
      const out = route(props);
      try {
        const type = out?.type;
        if (
          active &&
          type?.$$typeof &&
          !ours.has(type) &&
          typeof type.type === "function" &&
          String(type.type).includes("rgTabs")
        ) {
          out.type = wrapMemo(type);
        }
      } catch (err) {
        reportOnce("route", err);
      }
      return out;
    };
    ours.add(wrapped);
    routeCache.set(route, wrapped);
  }
  return wrapped;
}

/** Stable component type in place of Decky's wrapper (a new function on every router pass). */
function LibraryRoute({ __insertplayInner, ...props }: any) {
  const out = __insertplayInner(props);
  try {
    const type = out?.type;
    // Only Steam's library route component (a plain function, measured by this marker): another
    // plugin's patch or a Steam update may put something else here, e.g. a class component that
    // cannot be called as a function.
    if (
      active &&
      typeof type === "function" &&
      !ours.has(type) &&
      !type.prototype?.isReactComponent &&
      String(type).includes("GetLastLibraryTab")
    ) {
      out.type = wrapRoute(type);
    } else if (active && typeof type === "function" && !ours.has(type)) {
      reportOnce("library route", new Error("unexpected library route component"));
    }
  } catch (err) {
    reportOnce("library route", err);
  }
  return out;
}

/** For `routerHook.addPatch("/library", …)`: never throws, returns the tree as it was when the
 *  shape is not the measured one. */
export function patchLibraryTabs(tree: any): any {
  try {
    const top = tree?.children;
    if (!active || typeof top?.type !== "function" || top.type === LibraryRoute) return tree;
    tree.children = createElement(LibraryRoute, { ...top.props, __insertplayInner: top.type });
  } catch (err) {
    reportOnce("patch", err);
  }
  return tree;
}

/** Plugin load. The first `sync` comes with the first `setTabApps` (after the library loads)
 *  or from the first library render. */
export function installLibraryTabs(): void {
  active = true;
}

/** Plugin unload. `routerHook.removePatch` takes the patch out of the next router render;
 *  until then the wrappers pass Steam's render through untouched. */
export function stopLibraryTabs(): void {
  active = false;
  if (retryTimer) clearTimeout(retryTimer);
  retryTimer = null;
  mineAll = mineLocal = srcAll = srcLocal = null;
  appliedAll = appliedLocal = "";
  members = { all: [], installed: [] };
  memoCache.clear();
  routeCache.clear();
}

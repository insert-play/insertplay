// RetroAchievements progress shown in Steam's own screens (issue #24): the "Conquistas"
// section of the game page and the "Minhas conquistas / Conquistas globais" list.
//
// Third place that touches Steam internals, kept out of steam.ts because it patches
// Steam's stores instead of calling methods. Measured on the Deck and the Steam Machine
// (SteamOS 3.9.5, 2026-11-01; docs/steam-api-notes.md):
// - the export names are minified, so every object is found by its SHAPE;
// - each patch is independent: one that is not found disables only its part;
// - NEVER write a mobx observable inside a getter Steam calls while rendering
//   (`GetAchievements`, `GetSections`). Doing so in a first test made a render loop that
//   froze the steamwebhelper of the Deck until a watchdog killed it. Writes happen here
//   only in `publish` / `forget` (card flow) and in timers, and only when the value changed;
// - `details.achievements` is not written at all: the section reads it through
//   `GetAchievements`, and the patched getter alone was enough (measured after a Steam
//   restart, with `details.achievements` still at Steam's `0/0`).

import { Patch, afterPatch, callOriginal, findModuleExport, replacePatch } from "@decky/ui";
import { RaGame, SteamAchievement } from "./backend";

declare const appDetailsStore: any;

/** What the Steam screens read for one shortcut, built only when the progress changes. The
 *  objects keep their identity until then: comparing by identity is what tells "changed". */
interface Shown {
  gameId: number;
  /** Which achievements are unlocked and when: what a new fetch is compared with. */
  signature: string;
  /** What `appDetailsStore.GetAchievements` returns (game page section). */
  details: {
    nAchieved: number;
    nTotal: number;
    vecHighlight: SteamAchievement[];
    vecUnachieved: SteamAchievement[];
    vecAchievedHidden: SteamAchievement[];
  };
  /** Achievements store, list page. */
  my: { data: { achieved: Record<string, SteamAchievement>; unachieved: Record<string, SteamAchievement>; hidden: {} } };
  global: { data: Record<string, number> };
}

const shown = new Map<number, Shown>();
const patches = new Map<string, Patch[]>();
/** Store with `m_mapMyAchievements` (list page). `null` = not found: no list page. */
let store: any = null;
let stopped = false;

/** Measured on a Steam game (AoE DE): the section shows the 12 most common locked ones. */
const UNACHIEVED_SHOWN = 12;

/** Opening a game page or its list asks for a fresh fetch at most this often per game:
 *  an unlock made with the game open shows up in the Steam menu without closing the game. */
const VIEW_REFRESH_MS = 60_000;

/** The global % is left out: it moves as other players unlock, and redrawing the page for
 *  that is not worth it. It is updated together with the user's own progress. */
const signatureOf = (game: RaGame) =>
  `${game.gameId}|${game.achievements.map((a) => `${a.strID}:${a.rtUnlocked}`).join(",")}`;

function build(game: RaGame): Shown {
  const achieved = game.achievements.filter((a) => a.bAchieved).sort((a, b) => b.rtUnlocked - a.rtUnlocked);
  const locked = game.achievements.filter((a) => !a.bAchieved);
  const byId = (list: SteamAchievement[]) => Object.fromEntries(list.map((a) => [a.strID, a]));
  return {
    gameId: game.gameId,
    signature: signatureOf(game),
    details: {
      nAchieved: achieved.length,
      nTotal: game.achievements.length,
      // measured: achieved ones from the most recent, locked ones from the most common
      vecHighlight: achieved,
      vecUnachieved: [...locked].sort((a, b) => b.flAchieved - a.flAchieved).slice(0, UNACHIEVED_SHOWN),
      vecAchievedHidden: [],
    },
    my: { data: { achieved: byId(achieved), unachieved: byId(locked), hidden: {} } },
    global: { data: Object.fromEntries(game.achievements.map((a) => [a.strID, a.flAchieved])) },
  };
}

/** Writes the list page's store for one appid, only what differs. Never call it from a getter. */
function write(appid: number): void {
  const s = shown.get(appid);
  if (!s || stopped || !store) return;
  try {
    if (store.m_mapMyAchievements.get(appid) !== s.my) store.m_mapMyAchievements.set(appid, s.my);
    if (store.m_mapGlobalAchievements.get(appid) !== s.global) store.m_mapGlobalAchievements.set(appid, s.global);
  } catch {
    // the store changed shape: only the list page is lost, the section still works
  }
}

/** Writes after the current task: whoever triggered it (Steam's own `LoadMyAchievements`)
 *  may be in the middle of a render. */
function writeLater(appid: number): void {
  setTimeout(() => write(appid), 0);
}

let onView: ((appid: number, gameId: number) => Promise<void>) | null = null;
const lastView = new Map<number, number>();

/** A Steam screen is reading the achievements of `appid`. Called from getters while Steam
 *  renders: it only schedules, at most once per VIEW_REFRESH_MS per game. The fetch runs
 *  outside the render, and `publishAchievements` writes only if the progress changed. */
function viewed(appid: number): void {
  const s = shown.get(appid);
  const now = Date.now();
  if (!s || !onView || now - (lastView.get(appid) ?? 0) < VIEW_REFRESH_MS) return;
  lastView.set(appid, now);
  const handler = onView;
  setTimeout(() => void handler(appid, s.gameId).catch(() => undefined), 0);
}

/** Who fetches when a screen shows the achievements (flow.ts, which talks to the backend). */
export function onAchievementsViewed(handler: (appid: number, gameId: number) => Promise<void>): void {
  onView = handler;
}

/** Adds "achievements" to the sections (after "nonsteam"; the page lays them out in its own
 *  order, measured: first in "Suas coisas"). Kept per Steam Set: Steam memoizes the Set
 *  (`m_setSectionsMemo`), and a new one per call would change identity on every render. */
const withSection = new WeakMap<Set<string>, Set<string>>();
function addSection(sections: Set<string>): Set<string> {
  let out = withSection.get(sections);
  if (!out) {
    const list = [...sections];
    const at = list.indexOf("nonsteam");
    list.splice(at < 0 ? list.length : at + 1, 0, "achievements");
    out = new Set(list);
    withSection.set(sections, out);
  }
  return out;
}

/** Each part of the patch, found by shape. Throws when its Steam object is not found. */
const PARTS: Record<string, () => Patch[]> = {
  sections() {
    const Sections = findModuleExport(
      (e: any) => typeof e === "function" && typeof e.prototype?.GetSections === "function",
    );
    if (!Sections) throw new Error("class with prototype.GetSections not found");
    return [
      afterPatch(Sections.prototype, "GetSections", (args: any[], ret: any) => {
        const appid = Number(args[0]?.appid);
        if (!shown.has(appid) || !(ret instanceof Set) || ret.has("achievements")) return ret;
        return addSection(ret);
      }),
    ];
  },
  details() {
    if (typeof appDetailsStore?.GetAchievements !== "function") throw new Error("appDetailsStore.GetAchievements missing");
    // Only returns (and schedules): Steam calls this while rendering (see the header)
    return [
      replacePatch(appDetailsStore, "GetAchievements", (args: any[]) => {
        const appid = Number(args[0]);
        const s = shown.get(appid);
        if (!s) return callOriginal;
        viewed(appid);
        return s.details;
      }),
    ];
  },
  list() {
    const found = findModuleExport(
      (e: any) => e && typeof e === "object" && "m_mapMyAchievements" in e && typeof e.LoadMyAchievements === "function",
    );
    if (!found) throw new Error("store with m_mapMyAchievements not found");
    store = found;
    return [
      // Without this, Steam asks for the shortcut's achievements and stores `{error: 8}`.
      // `GetMyAchievements` calls it during render after storing `{loading: true}`; the
      // original also writes only after an await, so ours writes on a timer.
      replacePatch(found, "LoadMyAchievements", (args: any[]) => {
        const appid = Number(args[0]);
        if (!shown.has(appid)) return callOriginal;
        writeLater(appid);
        return Promise.resolve();
      }),
      // The list page reads through here, also during render: only schedules
      afterPatch(found, "GetMyAchievements", (args: any[], ret: any) => {
        viewed(Number(args[0]));
        return ret;
      }),
    ];
  },
};

/** Installs the parts not installed yet. Returns the error of each part that failed. */
export function installAchievements(): string[] {
  if (stopped) return [];
  const failed: string[] = [];
  for (const [name, install] of Object.entries(PARTS)) {
    if (patches.has(name)) continue;
    try {
      patches.set(name, install());
    } catch (err) {
      failed.push(`${name}: ${err instanceof Error ? err.message : err}`);
    }
  }
  return failed;
}

/** Shows `game` on the shortcut `appid`. Writes only when the progress changed: a fetch
 *  with nothing new keeps every object, so Steam has nothing to redraw. Returns whether it changed. */
export function publishAchievements(appid: number, game: RaGame): boolean {
  if (stopped) return false;
  if (shown.get(appid)?.signature === signatureOf(game)) return false;
  shown.set(appid, build(game));
  writeLater(appid);
  return true;
}

/** Stops showing the achievements of `appid` (logout, card without gameId). */
export function forgetAchievements(appid: number): void {
  const s = shown.get(appid);
  if (!s) return;
  shown.delete(appid);
  lastView.delete(appid);
  try {
    if (store?.m_mapMyAchievements.get(appid) === s.my) store.m_mapMyAchievements.delete(appid);
    if (store?.m_mapGlobalAchievements.get(appid) === s.global) store.m_mapGlobalAchievements.delete(appid);
  } catch {
    // nothing else to undo
  }
}

export function shownAppids(): number[] {
  return [...shown.keys()];
}

/** Plugin unload: undoes every patch and what was written. */
export function stopAchievements(): void {
  for (const appid of [...shown.keys()]) forgetAchievements(appid);
  stopped = true;
  onView = null;
  for (const patch of [...patches.values()].flat()) {
    try {
      patch.unpatch();
    } catch {
      // already gone
    }
  }
  patches.clear();
  store = null;
}

// RetroAchievements progress shown in Steam's own screens (issues #24 and #30): the
// "Conquistas" section of the game page, the achievements page (Achievement Sets) and the
// in-game overlay.
//
// Third place that touches Steam internals, kept out of steam.ts because it patches
// Steam's objects instead of calling methods. Measured on the Deck and the Steam Machine
// (docs/steam-api-notes.md, "Achievement Sets page"):
// - the export names are minified, so every object is found by its SHAPE;
// - each patch is independent: one that is not found disables only its part;
// - since the Steam client of 2026-11-13 the achievements page and the game page section
//   read three TanStack queries (`["achievements", appid, "schema" | "globalpercentages" |
//   "user_achievements", steamid]`). For a card shortcut the plugin swaps their `queryFn`
//   in `QueryClient.defaultQueryOptions`, so Steam fetches from the plugin instead of its
//   service (which answers "no achievements" for a shortcut);
// - NEVER write an observable or the query cache inside something Steam calls while
//   rendering (`defaultQueryOptions`, `GetAchievements`, `GetSections`). A first test that
//   did it froze the steamwebhelper of the Deck. Writes (query invalidation) happen only on
//   timers, after a change of progress;
// - `appDetailsStore.GetAchievements` still feeds the in-game overlay;
//   `details.achievements` itself is never written.

import { Patch, afterPatch, callOriginal, findModuleExport, replacePatch } from "@decky/ui";
import { RaGame, SteamAchievement } from "./backend";

declare const appDetailsStore: any;

/** Steam's base-game set: the page names it "Jogo base". */
const BASE_SET = 0;
const QUERY_KINDS = new Set(["schema", "globalpercentages", "user_achievements"]);

interface SetAchievement {
  internal_key: number;
  api_name: string;
  name: string;
  description: string;
  hidden: boolean;
  archived: boolean;
  icon_achieved: string;
  icon_unachieved: string;
  groupid: number;
}

/** What the Steam screens read for one shortcut, built only when the progress changes. The
 *  objects keep their identity until then: comparing by identity is what tells "changed". */
interface Shown {
  gameId: number;
  /** Which achievements are unlocked and when: what a new fetch is compared with. */
  signature: string;
  /** What `appDetailsStore.GetAchievements` returns (in-game overlay). */
  details: {
    nAchieved: number;
    nTotal: number;
    vecHighlight: SteamAchievement[];
    vecUnachieved: SteamAchievement[];
    vecAchievedHidden: SteamAchievement[];
  };
  /** The three queries of the achievements page, in the shape of Steam's fetchers. */
  schema: object;
  percentages: { percentages: Record<number, number> };
  /** The user query without `steamid`, added per steamid in `userData`. */
  user: { achievements: Record<number, object>; groups: Record<number, object> };
  userBySteamid: Map<string, object>;
}

const shown = new Map<number, Shown>();
/** appid → GameID of every card shortcut with achievements, with or without fetched data:
 *  a card first loaded offline still retries when its page is opened (PR #28 review). */
const known = new Map<number, number>();
/** Bumped on logout and login: a fetch started in another session is never shown. */
let session = 0;
const patches = new Map<string, Patch[]>();
/** Steam's global QueryClient. `null` = not found: no achievements page nor section. */
let queries: any = null;
let stopped = false;

/** Measured on a Steam game (AoE DE): the overlay shows the 12 most common locked ones. */
const UNACHIEVED_SHOWN = 12;

/** Opening a game page or its list asks for a fresh fetch at most this often per game:
 *  an unlock made with the game open shows up in the Steam menu without closing the game. */
const VIEW_REFRESH_MS = 60_000;

/** The global % is left out: it moves as other players unlock, and redrawing the page for
 *  that is not worth it. It is updated together with the user's own progress. */
const signatureOf = (game: RaGame) =>
  `${game.gameId}|${game.achievements.map((a) => `${a.strID}:${a.rtUnlocked}`).join(",")}`;

const keyOf = (a: SteamAchievement) => Number(a.strID);

function build(appid: number, game: RaGame): Shown {
  const achieved = game.achievements.filter((a) => a.bAchieved).sort((a, b) => b.rtUnlocked - a.rtUnlocked);
  const locked = game.achievements.filter((a) => !a.bAchieved);
  const total = game.achievements.length;
  const setAchievements: SetAchievement[] = game.achievements.map((a) => ({
    internal_key: keyOf(a),
    api_name: a.strID,
    name: a.strName,
    description: a.strDescription,
    hidden: false,
    archived: false,
    // RA badge URLs: the backend already picks the locked image for a locked one
    icon_achieved: a.strImage,
    icon_unachieved: a.strImage,
    groupid: BASE_SET,
  }));
  return {
    gameId: game.gameId,
    signature: signatureOf(game),
    details: {
      nAchieved: achieved.length,
      nTotal: total,
      // measured: achieved ones from the most recent, locked ones from the most common
      vecHighlight: achieved,
      vecUnachieved: [...locked].sort((a, b) => b.flAchieved - a.flAchieved).slice(0, UNACHIEVED_SHOWN),
      vecAchievedHidden: [],
    },
    schema: {
      appid,
      language: "",
      schema_hash: game.gameId,
      schema_version: 1,
      // Steam leaves out sets without achievements (an RA game with none: no section)
      groups: total
        ? [
            {
              id: BASE_SET,
              name: "",
              archived: false,
              ispublic: true,
              developeronly: false,
              dlcappid: 0,
              order: 0,
              achievements: setAchievements,
            },
          ]
        : [],
    },
    percentages: { percentages: Object.fromEntries(game.achievements.map((a) => [keyOf(a), a.flAchieved])) },
    user: {
      // Steam's user query only lists what is unlocked (or has progress)
      achievements: Object.fromEntries(
        achieved.map((a) => [keyOf(a), { internal_key: keyOf(a), unlocked: true, unlock_time: a.rtUnlocked }]),
      ),
      groups: {
        [BASE_SET]: {
          groupid: BASE_SET,
          is_achievable: true,
          completed_achievements: achieved.length,
          is_completed: total > 0 && achieved.length === total,
          time_completed: total > 0 && achieved.length === total ? achieved[0].rtUnlocked : undefined,
        },
      },
    },
    userBySteamid: new Map(),
  };
}

/** Data of one query of a card shortcut, same identity while the progress is the same. */
function queryData(appid: number, s: Shown, kind: string, steamid: string): object | undefined {
  if (kind === "schema") return s.schema;
  if (kind === "globalpercentages") return s.percentages;
  // another user (friend comparison): Steam's answer for a shortcut, which is empty
  const me = (window as any).App?.m_CurrentUser?.strSteamID;
  if (me && steamid !== me) return undefined;
  let user = s.userBySteamid.get(steamid);
  if (!user) {
    user = { appid, steamid, schema_hash: s.gameId, ...s.user };
    s.userBySteamid.set(steamid, user);
  }
  return user;
}

/** Steam's query of `appid` refetches (from our `queryFn` or, for a forgotten card, from
 *  Steam's). Never call it from something Steam calls while rendering. */
function refetchLater(appid: number, all = false): void {
  const client = queries;
  if (!client) return;
  setTimeout(() => {
    try {
      void Promise.resolve(
        client.invalidateQueries({ queryKey: ["achievements", appid], refetchType: all ? "all" : "active" }),
      ).catch(() => undefined);
    } catch {
      // the client changed shape: the page keeps what it shows
    }
  }, 0);
}

let onView: ((appid: number, gameId: number) => Promise<void>) | null = null;
const lastView = new Map<number, number>();

/** A Steam screen is reading the achievements of `appid`. Called from getters while Steam
 *  renders: it only schedules, at most once per VIEW_REFRESH_MS per game. The fetch runs
 *  outside the render, and `publishAchievements` writes only if the progress changed. */
function viewed(appid: number): void {
  const gameId = known.get(appid);
  const now = Date.now();
  if (!gameId || !onView || now - (lastView.get(appid) ?? 0) < VIEW_REFRESH_MS) return;
  lastView.set(appid, now);
  const handler = onView;
  setTimeout(() => void handler(appid, gameId).catch(() => undefined), 0);
}

/** Who fetches when a screen shows the achievements (flow.ts, which talks to the backend). */
export function onAchievementsViewed(handler: (appid: number, gameId: number) => Promise<void>): void {
  onView = handler;
}

/** Our `queryFn`s, one per query hash, so the options Steam builds on every render keep
 *  the same function. */
const queryFns = new Map<string, () => Promise<object>>();

function queryFnFor(appid: number, kind: string, steamid: string, original: unknown): (() => Promise<object>) | null {
  if (typeof original !== "function") return null;
  const id = `${appid}|${kind}|${steamid}`;
  let fn = queryFns.get(id);
  if (!fn) {
    fn = async function (this: unknown, ...args: unknown[]) {
      // runs inside Query.fetch, outside the render
      viewed(appid);
      const s = shown.get(appid);
      const data = s && !stopped ? queryData(appid, s, kind, steamid) : undefined;
      return data ?? (original as (...a: unknown[]) => Promise<object>).apply(this, args);
    };
    queryFns.set(id, fn);
  }
  return fn;
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
        // the game page is open: without data yet there is no section to read the queries,
        // so this is where a card loaded offline gets its retry
        if (!shown.has(appid)) viewed(appid);
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
        viewed(appid);
        return shown.get(appid)?.details ?? callOriginal;
      }),
    ];
  },
  queries() {
    const client = findModuleExport(
      (e: any) =>
        e &&
        typeof e === "object" &&
        typeof e.getQueryCache === "function" &&
        typeof e.defaultQueryOptions === "function" &&
        typeof e.invalidateQueries === "function",
    );
    if (!client) throw new Error("QueryClient not found");
    queries = client;
    return [
      // Called for every query, also while rendering: only computes, never writes
      afterPatch(client, "defaultQueryOptions", (_args: any[], ret: any) => {
        const key = ret?.queryKey;
        if (!Array.isArray(key) || key[0] !== "achievements" || !QUERY_KINDS.has(key[2])) return ret;
        const appid = Number(key[1]);
        if (!known.has(appid)) return ret;
        if (queryFns.get(`${appid}|${key[2]}|${String(key[3] ?? "")}`) === ret.queryFn) return ret;
        const fn = queryFnFor(appid, key[2], String(key[3] ?? ""), ret.queryFn);
        return fn ? { ...ret, queryFn: fn } : ret;
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

/** The card shortcut `appid` has achievements of RA game `gameId`, fetched or not yet. */
export function trackAchievements(appid: number, gameId: number): void {
  if (known.get(appid) !== gameId) lastView.delete(appid);
  known.set(appid, gameId);
}

/** Current session, captured before a fetch and handed back to `publishAchievements`. */
export const achievementsSession = (): number => session;

/** Shows `game` on the shortcut `appid`. Writes only when the progress changed: a fetch
 *  with nothing new keeps every object, so Steam has nothing to redraw. A fetch started
 *  before a logout or login (`fromSession` ≠ the current one) is dropped. Returns whether it changed. */
export function publishAchievements(appid: number, game: RaGame, fromSession: number): boolean {
  if (stopped || fromSession !== session) return false;
  if (shown.get(appid)?.signature === signatureOf(game)) return false;
  shown.set(appid, build(appid, game));
  refetchLater(appid);
  return true;
}

function unpublish(appid: number): void {
  lastView.delete(appid);
  if (!shown.delete(appid)) return;
  refetchLater(appid);
}

/** The card of `appid` no longer declares a GameID: nothing to show or fetch. */
export function forgetAchievements(appid: number): void {
  known.delete(appid);
  unpublish(appid);
}

/** Logout or login: Steam's screens go back to "no achievements", fetches already on their
 *  way are dropped, and the appid → GameID links stay for the next session. */
export function newAchievementsSession(): void {
  session += 1;
  for (const appid of [...shown.keys()]) unpublish(appid);
}

export function shownAppids(): number[] {
  return [...shown.keys()];
}

/** Plugin unload: undoes every patch, and Steam refetches the cards' queries from its own
 *  service (also the inactive ones, so its cache keeps nothing of ours). */
export function stopAchievements(): void {
  const appids = [...new Set([...known.keys(), ...shown.keys()])];
  stopped = true;
  shown.clear();
  known.clear();
  session += 1;
  onView = null;
  for (const patch of [...patches.values()].flat()) {
    try {
      patch.unpatch();
    } catch {
      // already gone
    }
  }
  patches.clear();
  for (const appid of appids) refetchLater(appid, true);
  queryFns.clear();
  queries = null;
}

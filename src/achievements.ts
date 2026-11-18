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
// - the card's RA game is Steam's base-game set, and each RA subset it lists is one more set,
//   named after the subset ("[Subset - Multiplayer]" → "Multiplayer");
// - NEVER write an observable or the query cache inside something Steam calls while
//   rendering (`defaultQueryOptions`, `GetAchievements`, `GetSections`). A first test that
//   did it froze the steamwebhelper of the Deck. Writes (query invalidation) happen only on
//   timers, after a change of progress;
// - `appDetailsStore.GetAchievements` still feeds the in-game overlay's achievement
//   widgets; `GetAppDetails(...).achievements` decides whether the overlay menu includes
//   the "Conquistas" action and supplies the page data;
//   `details.achievements` itself is never written.

import { Patch, afterPatch, callOriginal, findModuleExport, replacePatch } from "@decky/ui";
import { RaGame, SteamAchievement } from "./backend";
import { currentSteamId } from "./steam";

declare const appDetailsStore: any;

/** Steam's base-game set: the page names it "Jogo base". The RA subsets follow, 1..n. */
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
  /** Which achievements are unlocked and when, per set: what a new fetch is compared with. */
  signature: string;
  /** The same, per RA GameID: tells which games changed (for the event log). */
  gameSignatures: Map<number, string>;
  /** Steam's `schema_hash`, from the card's GameIDs: a card with other sets has another. */
  schemaHash: number;
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
/** appid → GameIDs (game, then subsets) of every card shortcut with achievements, with or
 *  without fetched data: a card first loaded offline still retries when its page is opened
 *  (PR #28 review). */
const known = new Map<number, number[]>();
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
const gameSignature = (g: RaGame) => `${g.gameId}|${g.achievements.map((a) => `${a.strID}:${a.rtUnlocked}`).join(",")}`;
const signatureOf = (gameIds: number[], games: RaGame[]) =>
  `${gameIds.join(",")}#${games.map(gameSignature).join(";")}`;

/** 32-bit FNV-1a of the card's GameIDs. */
function schemaHashOf(gameIds: number[]): number {
  let hash = 0x811c9dc5;
  for (const ch of gameIds.join(",")) hash = Math.imul(hash ^ ch.charCodeAt(0), 0x01000193);
  return hash >>> 0;
}

const keyOf = (a: SteamAchievement) => Number(a.strID);

/** Name of a subset's set: RA titles a subset "<game> [Subset - <name>]". */
export function subsetSetName(title: string): string {
  const match = /\[Subset - (.+)\]\s*$/.exec(title);
  return (match ? match[1] : title).trim();
}

/** `gameIds[0]` is the card's RA game, the others its subsets: set `i` is `gameIds[i]`, so
 *  a set keeps its id when another one could not be read. `games` = the ones that could.
 *  Like Steam with sets that need no DLC (ETS2: "10/42 … em 3 conjuntos"), the section
 *  and the overlay count every set. */
function build(appid: number, gameIds: number[], games: RaGame[]): Shown {
  const all = games.flatMap((g) => g.achievements);
  const achieved = all.filter((a) => a.bAchieved).sort((a, b) => b.rtUnlocked - a.rtUnlocked);
  const locked = all.filter((a) => !a.bAchieved);
  // Steam leaves out sets without achievements (an RA game with none: no section)
  const sets = games
    .map((game) => ({ game, id: gameIds.indexOf(game.gameId) }))
    .filter(({ game, id }) => id >= 0 && game.achievements.length > 0);
  const schemaHash = schemaHashOf(gameIds);
  return {
    signature: signatureOf(gameIds, games),
    gameSignatures: new Map(games.map((g) => [g.gameId, gameSignature(g)])),
    schemaHash,
    details: {
      nAchieved: achieved.length,
      nTotal: all.length,
      // measured: achieved ones from the most recent, locked ones from the most common
      vecHighlight: achieved,
      vecUnachieved: [...locked].sort((a, b) => b.flAchieved - a.flAchieved).slice(0, UNACHIEVED_SHOWN),
      vecAchievedHidden: [],
    },
    schema: {
      appid,
      language: "",
      schema_hash: schemaHash,
      schema_version: 1,
      groups: sets.map(({ game, id }) => ({
        id,
        // the base set's name comes from Steam ("Jogo base")
        name: id === BASE_SET ? "" : subsetSetName(game.title || String(game.gameId)),
        archived: false,
        ispublic: true,
        developeronly: false,
        dlcappid: 0,
        order: id,
        achievements: game.achievements.map(
          (a): SetAchievement => ({
            internal_key: keyOf(a),
            api_name: a.strID,
            name: a.strName,
            description: a.strDescription,
            hidden: false,
            archived: false,
            // RA badge URLs: the backend already picks the locked image for a locked one
            icon_achieved: a.strImage,
            icon_unachieved: a.strImage,
            groupid: id,
          }),
        ),
      })),
    },
    percentages: { percentages: Object.fromEntries(all.map((a) => [keyOf(a), a.flAchieved])) },
    user: {
      // Steam's user query only lists what is unlocked (or has progress)
      achievements: Object.fromEntries(
        achieved.map((a) => [keyOf(a), { internal_key: keyOf(a), unlocked: true, unlock_time: a.rtUnlocked }]),
      ),
      groups: Object.fromEntries(
        sets.map(({ game, id }) => {
          const done = game.achievements.filter((a) => a.bAchieved);
          const complete = done.length === game.achievements.length;
          return [
            id,
            {
              groupid: id,
              // a set with dlcappid 0: always achievable, like ETS2's sets without DLC
              is_achievable: true,
              completed_achievements: done.length,
              is_completed: complete,
              time_completed: complete ? Math.max(...done.map((a) => a.rtUnlocked)) : undefined,
            },
          ];
        }),
      ),
    },
    userBySteamid: new Map(),
  };
}

/** Data of one query of a card shortcut, same identity while the progress is the same. */
function queryData(appid: number, s: Shown, kind: string, steamid: string): object | undefined {
  if (kind === "schema") return s.schema;
  if (kind === "globalpercentages") return s.percentages;
  // another user (friend comparison), or the account is not known yet: Steam's answer
  // for a shortcut, which is empty
  if (steamid !== currentSteamId()) return undefined;
  let user = s.userBySteamid.get(steamid);
  if (!user) {
    user = { appid, steamid, schema_hash: s.schemaHash, ...s.user };
    s.userBySteamid.set(steamid, user);
  }
  return user;
}

/** Runs `action` on Steam's QueryClient after the current task. Never call the client from
 *  something Steam calls while rendering. */
function onQueriesLater(action: (client: any) => unknown): void {
  const client = queries;
  if (!client) return;
  setTimeout(() => {
    try {
      void Promise.resolve(action(client)).catch(() => undefined);
    } catch {
      // the client changed shape: the page keeps what it shows
    }
  }, 0);
}

/** New progress: the open screens refetch (through our `queryFn`), the others on view. */
function refetchLater(appid: number): void {
  onQueriesLater((client) => client.invalidateQueries({ queryKey: ["achievements", appid] }));
}

/** Nothing of ours may stay: inactive queries are dropped (they also hold our `queryFn`)
 *  and open ones start over from whatever `queryFn` the options give now. */
function dropLater(appid: number): void {
  onQueriesLater((client) => {
    client.removeQueries({ queryKey: ["achievements", appid], type: "inactive" });
    return client.resetQueries({ queryKey: ["achievements", appid], type: "active" });
  });
}

let onView: ((appid: number, gameIds: number[]) => Promise<void>) | null = null;
const lastView = new Map<number, number>();

/** A Steam screen is reading the achievements of `appid`. Called from getters while Steam
 *  renders: it only schedules, at most once per VIEW_REFRESH_MS per game. The fetch runs
 *  outside the render, and `publishAchievements` writes only if the progress changed. */
function viewed(appid: number): void {
  const gameIds = known.get(appid);
  const now = Date.now();
  if (!gameIds?.length || !onView || now - (lastView.get(appid) ?? 0) < VIEW_REFRESH_MS) return;
  lastView.set(appid, now);
  const handler = onView;
  setTimeout(() => void handler(appid, gameIds).catch(() => undefined), 0);
}

/** Who fetches when a screen shows the achievements (flow.ts, which talks to the backend). */
export function onAchievementsViewed(handler: (appid: number, gameIds: number[]) => Promise<void>): void {
  onView = handler;
}

/** Our `queryFn`s, one per query, so the options Steam builds on every render keep the
 *  same function; and the latest `queryFn` Steam built for it, used while there is no data. */
const queryFns = new Map<string, () => Promise<object>>();
const steamQueryFns = new Map<string, unknown>();

function queryFnFor(id: string, appid: number, kind: string, steamid: string): () => Promise<object> {
  let fn = queryFns.get(id);
  if (!fn) {
    fn = async function (this: unknown, ...args: unknown[]) {
      // runs inside Query.fetch, outside the render
      const s = shown.get(appid);
      const data = s && !stopped ? queryData(appid, s, kind, steamid) : undefined;
      if (data) return data;
      const original = steamQueryFns.get(id);
      if (typeof original !== "function") throw new Error(`no queryFn for ${id}`);
      return original.apply(this, args);
    };
    queryFns.set(id, fn);
  }
  return fn;
}

const appDetailsCache = new WeakMap<object, { details: object; result: object }>();

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
    if (typeof appDetailsStore?.GetAppDetails !== "function") throw new Error("appDetailsStore.GetAppDetails missing");
    // Only returns (and schedules): Steam calls this while rendering (see the header)
    return [
      replacePatch(appDetailsStore, "GetAchievements", (args: any[]) => {
        const appid = Number(args[0]);
        viewed(appid);
        return shown.get(appid)?.details ?? callOriginal;
      }),
      // The in-game overlay's action list and content read `GetAppDetails`, whereas the
      // achievements widgets still use `GetAchievements`. Return a stable, derived object:
      // never write into Steam's observable details during a render.
      afterPatch(appDetailsStore, "GetAppDetails", (args: any[], ret: any) => {
        const appid = Number(args[0]);
        const data = shown.get(appid)?.details;
        if (!data || !ret || typeof ret !== "object") return ret;
        const cached = appDetailsCache.get(ret);
        if (cached?.details === data) return cached.result;
        const result = { ...ret, achievements: data };
        appDetailsCache.set(ret, { details: data, result });
        return result;
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
        // a card's achievements page or section is on screen: at most one fetch a minute
        viewed(appid);
        const kind: string = key[2];
        const steamid = String(key[3] ?? "");
        const id = `${appid}|${kind}|${steamid}`;
        const fn = queryFnFor(id, appid, kind, steamid);
        if (ret.queryFn === fn) return ret;
        if (typeof ret.queryFn !== "function") return ret;
        steamQueryFns.set(id, ret.queryFn);
        return { ...ret, queryFn: fn };
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

/** The card shortcut `appid` has achievements of these RA games (the game, then its
 *  subsets), fetched or not yet. */
export function trackAchievements(appid: number, gameIds: number[]): void {
  if (known.get(appid)?.join() !== gameIds.join()) lastView.delete(appid);
  known.set(appid, gameIds);
}

/** GameIDs tracked for `appid` (game, then subsets), joined; `undefined` = none. */
export const trackedAchievements = (appid: number): string | undefined => known.get(appid)?.join();

/** Current session, captured before a fetch and handed back to `publishAchievements`. */
export const achievementsSession = (): number => session;

/** Shows on the shortcut `appid` the card's RA games `gameIds` (the game, then its subsets)
 *  from `games`, the ones that could be read. Writes only when the progress changed: a fetch
 *  with nothing new keeps every object, so Steam has nothing to redraw. A fetch started
 *  before a logout or login (`fromSession` ≠ the current one) is dropped. Returns the games
 *  whose progress changed (empty = nothing changed). */
export function publishAchievements(appid: number, gameIds: number[], games: RaGame[], fromSession: number): RaGame[] {
  if (stopped || fromSession !== session || !games.length) return [];
  const before = shown.get(appid);
  if (before?.signature === signatureOf(gameIds, games)) return [];
  const now = build(appid, gameIds, games);
  shown.set(appid, now);
  refetchLater(appid);
  return games.filter((g) => before?.gameSignatures.get(g.gameId) !== now.gameSignatures.get(g.gameId));
}

function unpublish(appid: number): void {
  lastView.delete(appid);
  if (!shown.delete(appid)) return;
  dropLater(appid);
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

/** Plugin unload: undoes every patch, then Steam's cache drops the cards' queries, so it
 *  keeps neither our data nor our `queryFn`. */
export function stopAchievements(): void {
  const appids = [...known.keys()];
  newAchievementsSession();
  for (const patch of [...patches.values()].flat()) {
    try {
      patch.unpatch();
    } catch {
      // already gone
    }
  }
  patches.clear();
  // after the unpatch: the open screens start over with Steam's own queryFn
  for (const appid of appids) dropLater(appid);
  stopped = true;
  known.clear();
  lastView.clear();
  onView = null;
  // `steamQueryFns` stays: an open screen may still fetch through our queryFn until it
  // renders again, and that falls back to Steam's
  queries = null;
}

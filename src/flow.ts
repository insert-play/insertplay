import { toaster } from "@decky/api";
import { createElement } from "react";
import {
  Card,
  PortableMode,
  PortableResults,
  PortableStatus,
  RegistryRecord,
  RemoteGame,
  SaveRecord,
  artworkBase64,
  currentCards,
  getSettings,
  libraryCard,
  logAdd,
  portableSet,
  portableSync,
  portableUnlinkDangling,
  raGame,
  raStatus,
  registryAll,
  registryPut,
  remoteArtworkBase64,
  remoteCancel,
  remoteGames,
  remoteGetConfig,
  remoteInstall,
  remotePause,
  remoteRefresh,
  remoteResume,
  remoteUninstall,
  savesPush,
  savesStatus,
  shortcutIcon,
} from "./backend";
import { RemoteDisplay, setRemoteDisplay } from "./remote-steam";
import {
  ShortcutSpec,
  addShortcut,
  appIcon,
  appName,
  applyArtwork,
  currentSteamId,
  gameRunning,
  isHidden,
  launchOptions,
  mountedLibraries,
  openGamePage,
  runningAppids,
  steamLibraries,
  runGame,
  runSteamGame,
  setHidden,
  setShortcutIcon,
  shortcutExists,
  updateShortcut,
  waitForApp,
  waitForLibrary,
} from "./steam";
import { showBadges } from "./steam-badges";
import { setTabApps } from "./library-tabs";
import {
  achievementsSession,
  forgetAchievements,
  newAchievementsSession,
  onAchievementsViewed,
  publishAchievements,
  trackAchievements,
} from "./achievements";

const TITLE = "InsertPlay";

/** Game icon for the toast's `logo` slot (the square left of the title). Without an icon,
 *  or if the image fails to load (Steam game offline), the toast stays text only. */
function gameLogo(appid: number | undefined) {
  const src = appid === undefined ? null : appIcon(appid);
  if (!src) return undefined;
  return createElement("img", {
    src,
    alt: "",
    style: { width: "100%", height: "100%", objectFit: "cover" },
    onError: (e: { currentTarget: HTMLImageElement }) => {
      e.currentTarget.style.visibility = "hidden";
    },
  });
}

/** Shortcut appid of each mounted InsertPlay card: `card_removed` only carries mount and
 *  label. Filled by ensureCard, so the load-time reconciliation also fills it. */
const cardApps = new Map<string, number>();

/** Single queue: insertion, removal and reconciliation touch the same shortcut and the
 *  same registry. In parallel, a removal could hide the shortcut an insertion had
 *  just shown. */
let chain: Promise<unknown> = Promise.resolve();
export function enqueue<T>(work: () => Promise<T>): Promise<T> {
  // every shortcut decision depends on the loaded library (see waitForLibrary)
  const guarded = async () => {
    await waitForLibrary();
    return work();
  };
  const next = chain.then(guarded, guarded);
  chain = next.catch(() => undefined);
  return next;
}

/** Where a shortcut comes from: a card or a remote library game. */
interface ShortcutSource {
  guid: string;
  spec: ShortcutSpec;
  artwork?: Record<string, string>;
  toBase64: (path: string) => Promise<string>;
  icon?: string | null;
  raGameId: number | null;
  /** Extra fields saved in the registry record (e.g. `source: "library"`). */
  record?: Partial<RegistryRecord>;
}

/** Creates or updates the shortcut of `src.guid` and saves the registry record. Shared by
 *  cards and the remote library, so the same guid is always the same shortcut. */
async function ensureShortcut(src: ShortcutSource, update = true): Promise<{ appid: number; created: boolean }> {
  const { guid, spec } = src;
  // Only the registry ties a shortcut to a card, always by guid. Adopting by name
  // overwrote other people's shortcuts with the same name and made two cards with the
  // same name share a shortcut (PR #2 review). An appid another guid also claims
  // is not trustworthy: we create our own shortcut.
  const registry = await loadRegistry();
  const known = registry[guid];
  const claimedByOther =
    known !== undefined &&
    Object.entries(registry).some(([other, record]) => other !== guid && record.appid === known.appid);
  let appid = known && !claimedByOther && shortcutExists(known.appid) ? known.appid : null;
  let created = false;

  if (appid === null) {
    appid = await addShortcut(spec);
    created = true;
    await logAdd("add", `${spec.appName}: shortcut ${appid} created`);
  } else if (update) {
    updateShortcut(appid, spec);
  }

  let artworkApplied = Boolean(known?.artworkApplied) && !created && known?.appid === appid;
  if (!artworkApplied && src.artwork && Object.keys(src.artwork).length) {
    const failed = await applyArtwork(appid, src.artwork, src.toBase64);
    if (failed.length) await logAdd("error", `${spec.appName}: artwork failed: ${failed.join("; ")}`);
    artworkApplied = failed.length === 0;
  }
  if (src.icon) await ensureIcon(spec.appName, guid, src.icon, appid);
  await saveRecord(guid, { appid, appName: spec.appName, artworkApplied, raGameId: src.raGameId, ...src.record });
  return { appid, created };
}

/** Ensures the card's shortcut exists and makes it visible. `launch` is only true on a
 *  real insertion; the load-time reconciliation never opens the page or starts a game. Returns the appid. */
async function ensureCard(card: Card, launch: boolean): Promise<number | null> {
  if (card.error || !card.guid || !card.appName || !card.exe) {
    const why = card.error ?? "incomplete manifest";
    await logAdd("error", `${card.label}: ${why}`);
    toaster.toast({ title: TITLE, body: `Cartão ${card.label} não pôde ser lido: ${why}`, duration: 10000 });
    return null;
  }
  const raGameId = card.game?.retroAchievements?.gameId ?? null;
  const { appid } = await ensureShortcut({
    guid: card.guid,
    spec: {
      appName: card.appName,
      exe: card.exe,
      startDir: card.startDir ?? card.mount,
      launchOptions: card.launchOptions ?? "",
    },
    artwork: card.artwork,
    toBase64: (p) => artworkBase64(p),
    icon: card.icon,
    raGameId,
  });
  // the inserted card wins over a remote library install of the same guid ({SD_ROOT} = the
  // card); the library spec is applied again when the card leaves
  libraryApplied.delete(card.guid);
  setHidden(appid, false);
  cardApps.set(card.mount, appid);
  // outside the queue: the API can take seconds and the game may already be starting
  if (raGameId) void showAchievements(appid, raGameId, "auto");
  else forgetAchievements(appid);

  if (launch) await onInsert(appid, card.appName, runGame);
  else toaster.toast({ title: TITLE, body: `${card.appName} disponível`, logo: gameLogo(appid) });
  return appid;
}

/** The "Ao inserir" setting, for the single game of a card just inserted (issue #38). With a
 *  game already running it only notifies: neither the page nor the start pulls the user out
 *  of the game. */
async function onInsert(appid: number, name: string, run: (appid: number) => void): Promise<void> {
  const logo = gameLogo(appid);
  const { onInsert: setting } = await getSettings();
  const action = gameRunning() ? "none" : setting;
  if (action === "launch") {
    toaster.toast({ title: TITLE, body: `Iniciando ${name}…`, logo });
    await logAdd("launch", `${name} (${appid})`);
    run(appid);
    return;
  }
  toaster.toast({ title: TITLE, body: `${name} disponível`, logo });
  if (action !== "page") return;
  if (!(await waitForApp(appid))) {
    await logAdd("error", `${name}: page not opened, ${appid} missing from the library`);
    return;
  }
  // a game may have started during the wait (PR #39 review)
  if (gameRunning()) return;
  openGamePage(appid);
  await logAdd("page", `${name} (${appid})`);
}

/** Shortcut icon from the copy on the SSD (issue #17). Runs on every insertion, not only
 *  on creation: a new icon on the card becomes a new path. The backend decides whether to
 *  apply it (it does not overwrite an icon the user chose). A failure does not stop the flow. */
async function ensureIcon(name: string, guid: string, source: string, appid: number): Promise<void> {
  try {
    const steamid = currentSteamId();
    if (!steamid) throw new Error("unknown Steam account");
    const icon = await shortcutIcon(steamid, appid, guid, source);
    if (!icon) throw new Error("backend refused (see log)");
    if (!icon.apply) return;
    setShortcutIcon(appid, icon.path);
    await logAdd("icon", `${name}: ${icon.path}`);
  } catch (err) {
    await logAdd("error", `${name}: icon failed: ${err instanceof Error ? err.message : err}`);
  }
}

/** A fetch younger than this is not repeated on insertion or startup. */
const ACHIEVEMENTS_FRESH_SECONDS = 120;
const JUST_FETCHED_SECONDS = 5;
/** RA registers an unlock when it happens, but the emulator may still be sending it as
 *  the game closes. */
const AFTER_GAME_MS = 5000;

const reportedAchievementErrors = new Set<string>();

/** "cache": only what the backend already has (startup, absent cards); "auto": also the API
 *  when the cache is old (insertion); "force": always the API ("Atualizar agora", login). */
type AchievementsLoad = "cache" | "auto" | "force";

/** RetroAchievements progress of a shortcut (issue #24): the cache first, so the page has
 *  something right away, then the API as `mode` says. Never throws: achievements are
 *  cosmetic, and a failure is logged once per session. */
async function showAchievements(appid: number, gameId: number, mode: AchievementsLoad): Promise<void> {
  trackAchievements(appid, gameId);
  const session = achievementsSession();
  try {
    // without a cache, the backend already goes to the API here
    const game = await raGame(gameId, false);
    if (game) publishAchievements(appid, game, session);
    // "force" skips only a fetch the line above has just made (game without a cache)
    const age = game ? Date.now() / 1000 - game.fetchedAt : 0;
    const fetchAgain =
      mode === "force" ? !game || age > JUST_FETCHED_SECONDS : mode === "auto" && game && age > ACHIEVEMENTS_FRESH_SECONDS;
    if (fetchAgain) await refreshAchievements(appid, gameId, mode);
  } catch (err) {
    const detail = `achievements ${gameId}: ${err instanceof Error ? err.message : err}`;
    if (reportedAchievementErrors.has(detail)) return;
    reportedAchievementErrors.add(detail);
    await logAdd("error", detail).catch(() => undefined);
  }
}

/** Every registered shortcut with a GameID, card in or out: the page of a removed card
 *  keeps the last state. `force` goes to the API for all of them ("Atualizar agora", login). */
export async function loadAllAchievements(force: boolean): Promise<void> {
  const registry = await registryAll();
  if (!registry) return;
  const games = new Map<number, number>();
  for (const record of Object.values(registry)) if (record.raGameId) games.set(record.appid, record.raGameId);
  // A present card's manifest wins over the registry: it may have gained the block after
  // its last insertion. Saved, so the game-close refresh and the next startup find it too.
  for (const card of (await currentCards()) ?? []) {
    const record = card.guid ? registry[card.guid] : undefined;
    const gameId = card.game?.retroAchievements?.gameId;
    if (!card.guid || !record || !gameId) continue;
    games.set(record.appid, gameId);
    if (record.raGameId !== gameId) await registryPut(card.guid, { raGameId: gameId });
  }
  for (const [appid, gameId] of games) {
    if (shortcutExists(appid)) await showAchievements(appid, gameId, force ? "force" : "cache");
  }
}

let refusedWarned = false;

/** Logout or login: Steam's screens go back to "no achievements" and fetches still on
 *  their way are dropped. A key refused after a new login warns again. */
export function clearAchievements(): void {
  newAchievementsSession();
  refusedWarned = false;
}

/** RA refused the saved key (issue #34): one toast per session, not one per game or per
 *  refetch of an open page. Called by the backend's `ra_refused` event and at startup (the
 *  mark survives a restart). A lack of internet never gets here. */
export async function warnIfRaRefused(): Promise<void> {
  if (refusedWarned) return;
  const session = achievementsSession();
  const status = await raStatus();
  // the startup check and the event may both be waiting here (PR #36 review), and a login
  // meanwhile ends the session the answer belongs to
  if (refusedWarned || session !== achievementsSession() || !status?.refused) return;
  refusedWarned = true;
  toaster.toast({
    title: TITLE,
    body: "RetroAchievements: a chave foi recusada. Entre de novo no painel do InsertPlay.",
    duration: 10000,
  });
}

/** Fetches again and shows it if the progress changed. Never throws. */
async function refreshAchievements(appid: number, gameId: number, why: string): Promise<void> {
  const session = achievementsSession();
  try {
    const game = await raGame(gameId, true);
    if (game && publishAchievements(appid, game, session)) {
      await logAdd("achievements", `${game.title || gameId}: ${game.unlocked}/${game.total} (${why})`);
    }
  } catch (err) {
    await logAdd("error", `achievements ${gameId}: ${err instanceof Error ? err.message : err}`).catch(() => undefined);
  }
}

/** Steam's game page or achievement list is open (card in or out, game running or not):
 *  an unlock made with the game open shows up there. Throttled in achievements.ts. */
onAchievementsViewed((appid, gameId) => refreshAchievements(appid, gameId, "page opened"));

/** A card game closed: fetch again to show what was unlocked in the session. */
export async function onGameClosed(appid: number): Promise<void> {
  void savesAfterGame(appid).catch((err) => logAdd("error", `saves after game: ${err}`));
  const registry = await registryAll();
  const record = Object.values(registry ?? {}).find((r) => r.appid === appid && r.raGameId);
  if (!record?.raGameId) return;
  await new Promise((r) => setTimeout(r, AFTER_GAME_MS));
  await refreshAchievements(appid, record.raGameId, "game closed");
}

/** Applies the "hide on removal" setting to every registered shortcut whose card is not
 *  mounted now: hides it, or shows it again if the option is off. Asks the backend
 *  instead of remembering mount→guid here: the frontend reloads without the backend
 *  knowing, and the backend has already updated the list before emitting the removal. */
async function syncAbsent(): Promise<void> {
  const present = new Set((await presentCards()).map((c) => c.guid).filter(Boolean));
  const registry = Object.entries(await loadRegistry());
  const hide = (await getSettings()).hideRemoved ?? true;
  // an appid used by a PRESENT card is never hidden, even if another (absent) guid
  // points to it in an old registry entry
  const presentAppids = new Set(registry.filter(([guid]) => present.has(guid)).map(([, r]) => r.appid));
  for (const [guid, record] of registry) {
    if (present.has(guid) || presentAppids.has(record.appid) || !shortcutExists(record.appid)) continue;
    // a remote library game has no card to wait for (#45); while the library list is
    // unknown, none of its shortcuts is hidden
    if (record.source === "library" && (libraryGames === null || libraryGames.some((g) => g.id === guid))) continue;
    if (isHidden(record.appid) !== hide) setHidden(record.appid, hide);
  }
}

/** Cards mounted now. `null` from the backend = it has not finished its first scan
 *  (or failed): carrying on with "no card" would hide the shortcut of the card in the
 *  reader, so we throw and the operation shows up as an error. */
async function presentCards(): Promise<Card[]> {
  const cards = await currentCards();
  if (!Array.isArray(cards)) throw new Error("backend has not finished scanning for cards");
  return cards;
}

/** guid → appid registry. A backend failure throws: treating it as empty would make
 *  every card look new and get a duplicate shortcut. */
async function loadRegistry(): Promise<Record<string, RegistryRecord>> {
  const registry = await registryAll();
  if (!registry || typeof registry !== "object") throw new Error("could not read the shortcut registry");
  return registry;
}

/** A write that fails silently would lose the guid → appid link and the next
 *  insertion would create another shortcut; hence it throws. */
async function saveRecord(guid: string, record: Partial<RegistryRecord>): Promise<void> {
  if (!(await registryPut(guid, record))) throw new Error(`could not save the registry entry for ${guid}`);
}

async function report(what: string, err: unknown): Promise<void> {
  const detail = err instanceof Error ? err.message : String(err);
  // the stack goes only to the log: without it "reading 'get'" does not say where it broke
  const stack = err instanceof Error && err.stack ? `\n${err.stack.split("\n").slice(1, 6).join("\n")}` : "";
  await logAdd("error", `${what}: ${detail}${stack}`).catch(() => undefined);
  toaster.toast({ title: TITLE, body: `${what} falhou: ${detail}`, duration: 10000 });
}

let lastBadgeError = "";

/** Recomputes which games get the card badge: the shortcuts of inserted InsertPlay
 *  cards and the Steam games installed in a library on the card. Only present cards:
 *  an absent card's shortcut is hidden and must not keep a badge.
 *  Never throws; the badge is cosmetic. The error is logged once, not on every trigger. */
export async function refreshBadges(): Promise<void> {
  try {
    const [cards, registry] = await Promise.all([presentCards(), loadRegistry()]);
    const present = new Set(cards.map((c) => c.guid).filter(Boolean));
    // same card criterion as the notifications; before the first snapshot, none
    const steamApps = [...(steamCards?.values() ?? [])].filter((c) => c.card).flatMap((c) => c.apps);
    const shortcuts = Object.entries(registry)
      .filter(([guid]) => present.has(guid))
      .map(([, record]) => record.appid);
    showBadges([...shortcuts, ...steamApps]);
    lastBadgeError = "";
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    if (detail === lastBadgeError) return;
    lastBadgeError = detail;
    await logAdd("error", `badges: ${detail}`).catch(() => undefined);
  }
}

let lastTabsError = "";
let tabsRead: Promise<void> | null = null;
let tabsAgain = false;

/** Which plugin shortcuts go in the library tabs (issue #52): every registry shortcut that
 *  exists in "Todos os jogos"; in "Instalados" also, when its card is inserted or its remote
 *  library game is installed. From the plugin's own state: `per_client_data.installed` is
 *  Steam's `true` for a remote game in "Instalar" until remote-steam.ts re-applies it.
 *  Never throws; one read at a time, plus one more when asked meanwhile. */
export function refreshTabs(): Promise<void> {
  if (tabsRead) {
    tabsAgain = true;
    return tabsRead;
  }
  const run = readTabs().finally(() => {
    tabsRead = null;
    if (tabsAgain) {
      tabsAgain = false;
      void refreshTabs();
    }
  });
  tabsRead = run;
  return run;
}

async function readTabs(): Promise<void> {
  try {
    await waitForLibrary();
    const [cards, registry] = await Promise.all([presentCards(), loadRegistry()]);
    const present = new Set(cards.map((c) => c.guid).filter(Boolean));
    const installedRemote = new Set((libraryGames ?? []).filter((g) => g.state === "installed").map((g) => g.id));
    const all = new Set<number>();
    const installed = new Set<number>();
    for (const [guid, record] of Object.entries(registry)) {
      // only shortcuts of this account (the registry is per device, issue #8)
      if (!record?.appid || !shortcutExists(record.appid)) continue;
      all.add(record.appid);
      if (present.has(guid) || installedRemote.has(guid)) installed.add(record.appid);
    }
    setTabApps({ all: [...all], installed: [...installed] });
    lastTabsError = "";
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    if (detail === lastTabsError) return;
    lastTabsError = detail;
    await logAdd("error", `library tabs: ${detail}`).catch(() => undefined);
  }
}

export const onInserted = (card: Card) =>
  enqueue(() => ensureCard(card, true))
    .catch((err) => report(`Inserção de ${card.label}`, err))
    .finally(() => {
      void refreshBadges();
      void refreshTabs();
      // a library game with this guid: the card's "Jogar" wins while it is in
      if (card.guid && libraryGames?.some((g) => g.id === card.guid)) void refreshLibrary(true);
    });

export const onRemoved = (card: { mount: string; label: string }) =>
  enqueue(async () => {
    await syncAbsent();
    await logAdd("hide", `${card.label} removed`);
    const appid = cardApps.get(card.mount);
    cardApps.delete(card.mount);
    toaster.toast({ title: TITLE, body: `Cartão ${card.label} removido`, logo: gameLogo(appid) });
  })
    .catch((err) => report(`Remoção de ${card.label}`, err))
    .finally(() => {
      void refreshBadges();
      void refreshTabs();
      // the shortcut of a library game goes back to the install folder (or "Instalar")
      if (libraryGames?.length) void refreshLibrary(true);
    });

/** Mounted Steam library seen in Steam's last notification, per library folder. */
interface SteamCard {
  mount: string;
  label: string;
  /** It is on a card: Steam says it is not fixed (Deck) or the kernel says the disk is
   *  removable (Steam Machine USB reader). A second internal SSD is left out. */
  card: boolean;
  /** Also an InsertPlay card (or the backend could not tell): the other flow notifies. */
  silent: boolean;
  /** The backend found the mount point (media under /run/media). Without it, portable
   *  saves do not apply: the backend refuses paths outside mounted media. */
  onMedia: boolean;
  apps: number[];
}

/** `null` = no snapshot yet. The first notification (Steam calls the callback right after
 *  registration) and plugin load with the card inserted are initial state, not insertion. */
let steamCards: Map<string, SteamCard> | null = null;

/** Compares the mounted removable libraries with the last snapshot. Per card (mount
 *  point), not per appid: installing a game on an already inserted card changes `apps`,
 *  not the present cards. */
async function checkSteamLibraries(): Promise<void> {
  const libraries = await steamLibraries();
  const previous = steamCards;
  const next = new Map<string, SteamCard>();
  // Measured on the Deck: Steam's notification arrives ~12 s after a move finishes, so the
  // snapshot's `apps` may be stale. On removal, what Steam kept for the unmounted folder
  // wins, even if empty (the last game may have left the card).
  const unmountedApps = new Map(libraries.filter((l) => !l.mounted).map((l) => [l.path, l.apps]));
  if (previous) {
    for (const [path, card] of previous) {
      const apps = unmountedApps.get(path);
      if (apps) previous.set(path, { ...card, apps });
    }
  }
  for (const library of libraries.filter((l) => l.mounted)) {
    const known = previous?.get(library.path);
    if (known) {
      next.set(library.path, { ...known, apps: library.apps });
      continue;
    }
    const card = await libraryCard(library.path);
    next.set(library.path, {
      mount: card?.mount ?? library.path,
      label: card?.label ?? library.path.split("/").pop() ?? library.path,
      card: !library.fixed || Boolean(card?.removable),
      silent: !card || card.insertplay,
      onMedia: Boolean(card),
      apps: library.apps,
    });
  }
  steamCards = next;
  // Before "Abrir jogo": the card's game must open already using the card's prefix.
  // A failure here must not suppress the insertion notification.
  await syncPortable().catch((err) => report("Save portátil", err));
  if (previous === null) {
    // without this line, a card missing from the snapshot only shows up as a "lost removal"
    const cards = [...next.values()]
      .filter((c) => c.card)
      .map((c) => `${c.label}${c.silent ? " (insertplay)" : ""}: ${c.apps.length}`);
    await logAdd("steam", `snapshot: ${cards.join(", ") || "-"}`);
    return;
  }

  // Insertion and removal belong to the card (mount point), not to each library:
  // adding or removing a library on an inserted card is not an event, and a card
  // with two libraries sums the games of both (PR #12 review).
  const byMount = (cards: Map<string, SteamCard>) => {
    const out = new Map<string, SteamCard>();
    for (const card of cards.values()) {
      const seen = out.get(card.mount);
      out.set(
        card.mount,
        seen
          ? {
              ...seen,
              card: seen.card || card.card,
              silent: seen.silent || card.silent,
              apps: [...seen.apps, ...card.apps],
            }
          : card,
      );
    }
    return out;
  };
  const before = byMount(previous);
  const after = byMount(next);
  const gone = (from: Map<string, SteamCard>, other: Map<string, SteamCard>) =>
    [...from.values()].filter(
      (card) => !other.has(card.mount) && card.card && !card.silent && card.apps.length > 0,
    );
  for (const card of gone(before, after)) {
    await logAdd("steam", `${card.label} removed (${card.apps.join(", ")})`);
    const apps = [...new Set(card.apps)];
    const logo = apps.length === 1 ? gameLogo(apps[0]) : undefined;
    toaster.toast({ title: TITLE, body: `Cartão ${card.label} removido`, logo });
  }
  for (const card of gone(after, before)) await steamCardInserted(card);
}

/** "Ao inserir" applies only with exactly one game on the card. */
async function steamCardInserted(card: SteamCard): Promise<void> {
  const apps = [...new Set(card.apps)];
  await logAdd("steam", `${card.label} inserted (${apps.join(", ")})`);
  if (apps.length > 1) {
    toaster.toast({ title: TITLE, body: `Cartão ${card.label}: ${apps.length} jogos disponíveis` });
    return;
  }
  const [appid] = apps;
  await onInsert(appid, appName(appid), runSteamGame);
}

/** Steam notification that the libraries changed (card inserted or removed, game
 *  installed or moved). Also refreshes the badges, as before. */
export const onSteamLibrariesChanged = () =>
  enqueue(checkSteamLibraries)
    .catch((err) => report("Biblioteca Steam no cartão", err))
    .finally(refreshBadges);

/** Card with a Steam library inserted now, merging the libraries of the same card. */
export interface PortableCard {
  mount: string;
  label: string;
  libraries: string[];
  apps: number[];
}

/** Steam cards present in the last snapshot (empty before the first one). */
export function steamCardsPresent(): PortableCard[] {
  const out = new Map<string, PortableCard>();
  for (const [path, card] of steamCards ?? []) {
    if (!card.card || !card.onMedia) continue;
    const seen = out.get(card.mount) ?? { mount: card.mount, label: card.label, libraries: [], apps: [] };
    seen.libraries.push(path);
    seen.apps = [...new Set([...seen.apps, ...card.apps])];
    out.set(card.mount, seen);
  }
  return [...out.values()];
}

const RESULT_TEXT: Record<string, string> = {
  migrated: "save levado para o cartão",
  conflict: "havia outro save; o mais antigo foi guardado como backup",
  restored: "save copiado de volta para o aparelho",
};

const reportedPortableErrors = new Set<string>();

/** Games already warned this session that their launch options change the prefix. */
const warnedCompatPath = new Set<number>();

/** `STEAM_COMPAT_DATA_PATH` (or `WINEPREFIX`) in the launch options makes Proton use
 *  another prefix and ignore the link. Measured on the Steam Machine: with the option from
 *  an old test, Bully opened without the card's saves and wrote ~115 MB of DLLs into the
 *  prefix at the card root. The option is the user's: we warn, we do not delete it. */
async function warnPrefixOverride(card: PortableCard, results: PortableResults): Promise<void> {
  for (const id of Object.keys(results)) {
    const appid = Number(id);
    if (warnedCompatPath.has(appid)) continue;
    if (!/STEAM_COMPAT_DATA_PATH|WINEPREFIX/.test((await launchOptions(appid)) ?? "")) continue;
    warnedCompatPath.add(appid);
    await logAdd("portable", `${card.label} ${appid}: launch options override the prefix`);
    toaster.toast({
      title: TITLE,
      body: `${appName(appid)}: as opções de inicialização mudam o prefixo do Proton. O save portátil não vale para este jogo enquanto elas existirem.`,
      duration: 10000,
    });
  }
}

/** Toast for what changed in a pass. "ok", "none", "leftover" and "unlinked" only go to
 *  the backend log: they happen without the user needing to know. */
async function announcePortable(card: PortableCard, results: PortableResults): Promise<void> {
  await warnPrefixOverride(card, results);
  for (const [id, result] of Object.entries(results)) {
    const name = appName(Number(id));
    if (result.startsWith("error")) {
      // the same error comes back on every pass (30 s): warn and log once per session
      const key = `${card.mount}|${id}|${result}`;
      if (reportedPortableErrors.has(key)) continue;
      reportedPortableErrors.add(key);
      await logAdd("error", `portable ${card.label} ${id}: ${result}`);
      toaster.toast({ title: TITLE, body: `Save portátil de ${name} falhou: ${result.slice(7)}`, duration: 10000 });
    } else if (RESULT_TEXT[result]) {
      toaster.toast({ title: TITLE, body: `${name}: ${RESULT_TEXT[result]}` });
    }
  }
}

/** Games installed in other libraries (SSD or another card): a game that moved from this
 *  card to one of them gets a copy of the prefix on the SSD instead of just losing the link. */
async function elsewhereOf(card: PortableCard): Promise<number[]> {
  return (await mountedLibraries()).filter((l) => !card.libraries.includes(l.path)).flatMap((l) => l.apps);
}

/** One portable saves pass over all present Steam cards (the backend skips the ones that
 *  are off). Without knowing which games are running, it touches nothing. */
async function syncPortable(): Promise<void> {
  const running = runningAppids();
  if (!running) return;
  await portableUnlinkDangling(running);
  for (const card of steamCardsPresent()) {
    const results = await portableSync(card.mount, card.libraries, card.apps, running, await elsewhereOf(card));
    if (results) await announcePortable(card, results);
  }
}

/** Periodic pass: Steam recreates the prefix on the SSD ~20 s after a move, and a game
 *  run for the first time creates a regular prefix that can only go to the card later. */
let portableTicking = false;
export const portableTick = () => {
  // A slow migration (whole prefix on a slow card) takes minutes. Stacking a tick every
  // 30 s on the single queue would delay InsertPlay card insertion behind all of them.
  if (portableTicking) return Promise.resolve();
  portableTicking = true;
  return enqueue(syncPortable)
    .catch((err) => report("Save portátil", err))
    .finally(() => {
      portableTicking = false;
    });
};

/** Mode choice in progress (`done: false`) or already applied and not yet re-read by the
 *  panel (`done: true`). Lives here, outside the component: the selector menu and the
 *  confirm modal open over Quick Access and the panel is mounted again. Kept in the
 *  component, "applying" vanished and the selector reverted to the old mode (seen on the Deck). */
export interface PortableChange {
  mode: PortableMode;
  done: boolean;
}
const portableChanges = new Map<string, PortableChange>();
const portableListeners = new Set<() => void>();

export const portableChange = (mount: string): PortableChange | null => portableChanges.get(mount) ?? null;

/** The panel has read the applied mode: the change is over. */
export function settlePortableChange(mount: string): void {
  if (portableChanges.get(mount)?.done) portableChanges.delete(mount);
}

/** Notifies when a change starts or ends. Returns the unsubscribe function. */
export function onPortableChange(callback: () => void): () => void {
  portableListeners.add(callback);
  return () => void portableListeners.delete(callback);
}

function trackPortable(mount: string, change: PortableChange | null): void {
  if (change) portableChanges.set(mount, change);
  else portableChanges.delete(mount);
  portableListeners.forEach((callback) => callback());
}

/** Panel choice. Returns the new status, or `null` if it failed (already reported). */
export const setPortable = (card: PortableCard, mode: PortableMode) => {
  trackPortable(card.mount, { mode, done: false });
  return runSetPortable(card, mode).then((status) => {
    // unchanged (failure, or the card's game is running): the selector reverts to the old mode
    trackPortable(card.mount, status?.mode === mode ? { mode, done: true } : null);
    return status;
  });
};

const runSetPortable = (card: PortableCard, mode: PortableMode) =>
  enqueue(async (): Promise<PortableStatus> => {
    const running = runningAppids();
    if (!running) throw new Error("não deu para saber quais jogos estão abertos");
    const result = await portableSet(card.mount, mode, card.libraries, card.apps, running, await elsewhereOf(card));
    if (!result) throw new Error("veja o log de eventos");
    if (result.status.mode !== mode && Object.values(result.results).includes("running")) {
      toaster.toast({ title: TITLE, body: `Feche o jogo do cartão ${card.label} antes de mudar o save portátil` });
    }
    await announcePortable(card, result.results);
    return result.status;
  }).catch(async (err) => {
    await report(`Save portátil de ${card.label}`, err);
    return null;
  });

/** Initial state on plugin load: shows what is inserted, applies "hide on removal" to
 *  the rest, starts nothing. */
export const reconcile = () =>
  enqueue(async () => {
    const cards = await presentCards();
    for (const card of cards) await ensureCard(card, false);
    await syncAbsent();
    // without this line, "reconciled correctly" and "never ran" look the same in the log
    await logAdd("reconcile", `${cards.length} card(s): ${cards.map((c) => c.appName ?? c.label).join(", ") || "-"}`);
  })
    .catch((err) => report("Reconciliação", err))
    .finally(() => {
      void refreshBadges();
      void refreshTabs();
      // absent cards too: their shortcut pages keep the last state (cache only)
      void loadAllAchievements(false);
      void warnIfRaRefused().catch(() => undefined);
    });

/** "Hide on removal" toggled in the panel: applies right away to absent cards. */
export const applyHideRemoved = () =>
  enqueue(syncAbsent)
    .catch((err) => report("Ocultar ao remover", err))
    .finally(() => {
      void refreshBadges();
      void refreshTabs();
    });

/** "Reinstalar atalho" button: forces the artwork to be applied again. */
export const reinstall = (card: Card) =>
  enqueue(async () => {
    if (card.guid) await saveRecord(card.guid, { artworkApplied: false });
    await ensureCard(card, false);
  })
    .catch((err) => report(`Reinstalação de ${card.label}`, err))
    .finally(() => {
      void refreshBadges();
      void refreshTabs();
    });

export const launchCard = (card: Card) =>
  enqueue(async () => {
    const appid = await ensureCard(card, false);
    if (appid !== null) runGame(appid);
  }).catch((err) => report(`Início de ${card.label}`, err));

// ---- Remote library ("Biblioteca Remota", #45). The engine is the backend (#47); this part
// keeps one shortcut per library game and tells remote-steam.ts what the game page shows.

/** Games last read from the backend (`remote_games`); `null` = not read yet or it failed. */
let libraryGames: RemoteGame[] | null = null;
/** guid → shortcut appid of every library game. */
const libraryApps = new Map<string, number>();
/** guid → spec last applied to the shortcut this session: `updateShortcut` makes Steam
 *  replace the overview (and our "Instalar"), so it runs only when something changed. */
const libraryApplied = new Map<string, string>();
/** Guids of the InsertPlay cards mounted at the last sync: the card wins over the library. */
let libraryCardGuids = new Set<string>();
/** Last state per guid, to toast "instalado" / errors once, on the transition. */
const lastStates = new Map<string, RemoteGame["state"]>();
const libraryAchievements = new Set<number>();
const libraryListeners = new Set<() => void>();

export const remoteLibraryGames = (): RemoteGame[] | null => libraryGames;

/** Notifies when the library list or a game state changes. Returns the unsubscribe function. */
export function onRemoteLibraryChange(callback: () => void): () => void {
  libraryListeners.add(callback);
  return () => void libraryListeners.delete(callback);
}

function displayOf(game: RemoteGame): RemoteDisplay {
  switch (game.state) {
    case "downloading":
      return { status: 7, percent: game.percent, speed: game.speed };
    case "queued":
      return { status: 23, percent: game.percent, speed: 0 };
    case "paused":
      return { status: 22, percent: game.percent, speed: 0 };
    case "installed":
      return { status: null, percent: 100, speed: 0 };
    default: // not_installed, error
      return { status: 9, percent: 0, speed: 0 };
  }
}

/** What each library shortcut shows. A mounted card with the same guid: Steam's own "Jogar". */
function publishLibrary(): void {
  const display = new Map<number, RemoteDisplay>();
  for (const game of libraryGames ?? []) {
    const appid = libraryApps.get(game.id);
    if (appid === undefined || libraryCardGuids.has(game.id)) continue;
    display.set(appid, displayOf(game));
  }
  setRemoteDisplay(libraryApps.values(), display);
  libraryListeners.forEach((callback) => callback());
}

/** Toasts for transitions seen since the last read (none on the first read: plugin load). */
function announceLibrary(games: RemoteGame[], first: boolean): void {
  for (const game of games) {
    const before = lastStates.get(game.id);
    lastStates.set(game.id, game.state);
    if (first || before === undefined || before === game.state) continue;
    const logo = gameLogo(libraryApps.get(game.id));
    if (game.state === "installed") {
      toaster.toast({ title: TITLE, body: `${game.name} instalado`, logo });
    } else if (game.state === "error" && game.error) {
      toaster.toast({ title: TITLE, body: `${game.name}: ${game.error.message}`, logo, duration: 10000 });
    }
  }
}

/** Shortcut of each library game, created or updated. Never toasts nor runs "Ao inserir": a
 *  game installed from the library is a virtual card, not an insertion. */
async function syncLibraryShortcuts(games: RemoteGame[]): Promise<void> {
  const cards = await presentCards();
  libraryCardGuids = new Set(cards.map((c) => c.guid).filter((g): g is string => Boolean(g)));
  for (const game of games) {
    const sc = game.shortcut;
    if (!sc?.appName || !sc.exe) {
      await logAdd("error", `remote ${game.id}: incomplete manifest`);
      continue;
    }
    const record = (await loadRegistry())[game.id];
    if (libraryCardGuids.has(game.id) && record && shortcutExists(record.appid)) {
      // the inserted card's shortcut is this game's shortcut
      libraryApps.set(game.id, record.appid);
      if (record.source !== "library") await saveRecord(game.id, { source: "library" });
      continue;
    }
    const spec = { appName: sc.appName, exe: sc.exe, startDir: sc.startDir, launchOptions: sc.launchOptions };
    const key = JSON.stringify(spec);
    const fresh = record?.source !== "library";
    const raGameId = sc.game?.retroAchievements?.gameId ?? null;
    const { appid, created } = await ensureShortcut(
      {
        guid: game.id,
        spec,
        artwork: sc.artwork,
        // the cache file name is the kind: `<runtime>/library/artwork/<id>/<kind>.<ext>`
        toBase64: (path) => remoteArtworkBase64(game.id, (path.split("/").pop() ?? "").split(".")[0]),
        icon: sc.icon,
        raGameId,
        record: { source: "library" },
      },
      libraryApplied.get(game.id) !== key,
    );
    libraryApplied.set(game.id, key);
    // a card shortcut hidden while its card was out becomes a library game: show it once
    if (created || fresh) setHidden(appid, false);
    if (created) await logAdd("remote", `${sc.appName}: library shortcut ${appid}`);
    libraryApps.set(game.id, appid);
    if (raGameId && !libraryAchievements.has(appid)) {
      libraryAchievements.add(appid);
      void showAchievements(appid, raGameId, "cache");
    }
  }
}

const MISSING_RETRY_MS = 30_000;
let lastMissingSync = 0;

/** Reads the games from the backend (no network) and updates the game pages. `shortcuts`:
 *  also create/update the shortcuts (plugin load, list refreshed, card in or out). */
async function readLibrary(shortcuts: boolean): Promise<void> {
  const result = await remoteGames();
  if (!result || !result.ok) return;
  const first = libraryGames === null;
  libraryGames = result.games;
  // a game still without a shortcut (its creation failed) is retried, but not on every
  // progress event: a failure would log and toast once a second during a download
  const missing = result.games.some((g) => !libraryApps.has(g.id)) && Date.now() - lastMissingSync > MISSING_RETRY_MS;
  if (shortcuts || missing) {
    lastMissingSync = Date.now();
    await enqueue(() => syncLibraryShortcuts(result.games));
  }
  announceLibrary(result.games, first);
  publishLibrary();
  // the library tabs (#52) change with a shortcut or an install, not with every progress event
  const tabsKey = `${[...libraryApps.values()].join(",")}|${result.games
    .filter((g) => g.state === "installed")
    .map((g) => g.id)
    .join(",")}`;
  if (shortcuts || tabsKey !== lastTabsKey) {
    lastTabsKey = tabsKey;
    void refreshTabs();
  }
}

let lastTabsKey = "";

let libraryRead: Promise<void> | null = null;
let libraryAgain = false;
let libraryShortcutsAgain = false;

/** Coalesces bursts (the backend emits up to once a second while downloading): one read at
 *  a time, and one more after it when something asked meanwhile. */
export function refreshLibrary(shortcuts = false): Promise<void> {
  if (libraryRead) {
    libraryAgain = true;
    if (shortcuts) libraryShortcutsAgain = true;
    return libraryRead;
  }
  const run = readLibrary(shortcuts)
    .catch((err) => report("Biblioteca Remota", err))
    .finally(() => {
      libraryRead = null;
      if (libraryAgain) {
        const again = libraryShortcutsAgain;
        libraryAgain = libraryShortcutsAgain = false;
        void refreshLibrary(again);
      }
    });
  libraryRead = run;
  return run;
}
// ---------------------------------------------------------------- save sync (#63)

/** How long to wait for the launch wrapper's download before giving up on the toast. The
 *  wrapper gives the server ~10 s, plus the download itself. */
const SAVES_START_WAIT_MS = 45_000;
/** After the game closes, the wrapper's own upload (or the detached one after "Sair do
 *  jogo") usually lands within a few seconds. */
const SAVES_AFTER_GAME_MS = 4_000;
/** While the wrapper still holds the lock after the game (uploading a large save). */
const SAVES_BUSY_WAIT_MS = 180_000;
/** guid → `at` of the last record toasted, so the start and the close never toast twice. */
const savesToasted = new Map<string, number>();

/** pt-BR toast text for a sync result; null = nothing worth a toast. */
function saveToastText(name: string, r: SaveRecord): { body: string; long?: boolean } | null {
  switch (r.result) {
    case "downloaded":
      return { body: `${name}: save baixado do servidor${r.fromDevice ? ` (feito em ${r.fromDevice})` : ""}` };
    case "conflict_downloaded":
      return { body: `${name}: o save do servidor era mais recente e foi baixado. O deste aparelho ficou no backup.`, long: true };
    case "conflict_uploaded":
      return { body: `${name}: o save deste aparelho era mais recente e foi enviado. O do servidor ficou no backup.`, long: true };
    case "uploaded":
      return { body: `${name}: save enviado ao servidor` };
    case "offline":
      return r.op === "pull"
        ? { body: `${name}: servidor de saves fora do ar. Jogando com o save deste aparelho.`, long: true }
        : { body: `${name}: servidor de saves fora do ar. O save será enviado depois.`, long: true };
    case "no_strategy":
      return { body: `Não foi possível identificar os saves de ${name}: jogo sem sincronização.`, long: true };
    case "error":
      return { body: `${name}: erro ao sincronizar o save (${r.message})`, long: true };
    default:
      return null;
  }
}

function toastSave(appid: number, guid: string, record: SaveRecord): void {
  if ((savesToasted.get(guid) ?? 0) >= record.at) return;
  const text = saveToastText(appName(appid), record);
  if (!text) return;
  savesToasted.set(guid, record.at);
  toaster.toast({ title: TITLE, body: text.body, logo: gameLogo(appid), duration: text.long ? 10000 : undefined });
}

/** A library game started: the wrapper downloads the save before the game itself; this only
 *  reports what it did, as soon as its record shows up. */
export async function onGameStarted(appid: number): Promise<void> {
  const guid = guidOf(appid);
  if (!guid || !(await getSettings()).savesSync) return;
  // a shortcut still without the wrapper writes no record: nothing to wait for
  if (!(await launchOptions(appid))?.includes("insertplay-run")) return;
  const started = Date.now() / 1000 - 5;
  const deadline = Date.now() + SAVES_START_WAIT_MS;
  while (Date.now() < deadline) {
    const status = await savesStatus(guid);
    const last = status?.last;
    if (last && last.op === "pull" && last.at >= started) {
      toastSave(appid, guid, last);
      return;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
}

/** A library game closed: the safety net upload (idempotent; the wrapper usually did it
 *  already), then a toast for whichever upload happened. */
async function savesAfterGame(appid: number): Promise<void> {
  const guid = guidOf(appid);
  if (!guid || !(await getSettings()).savesSync) return;
  const closed = Date.now() / 1000 - 5;
  await new Promise((r) => setTimeout(r, SAVES_AFTER_GAME_MS));
  let pushed = await savesPush(guid);
  if (pushed && pushed.result !== "unchanged" && pushed.result !== "busy") {
    toastSave(appid, guid, pushed);
    return;
  }
  // the wrapper (or its detached unit) is uploading, or already did: report its record. A
  // large save over Wi-Fi can take a while, so keep asking for up to SAVES_BUSY_WAIT_MS.
  const deadline = Date.now() + SAVES_BUSY_WAIT_MS;
  for (let i = 1; ; i++) {
    const last = (await savesStatus(guid))?.last;
    if (last && last.op === "push" && last.at >= closed) {
      toastSave(appid, guid, last);
      return;
    }
    if (pushed?.result !== "busy" || Date.now() > deadline) return;
    await new Promise((r) => setTimeout(r, 2000));
    if (i % 8 === 0) {
      pushed = await savesPush(guid);
      if (pushed && pushed.result !== "unchanged" && pushed.result !== "busy") {
        toastSave(appid, guid, pushed);
        return;
      }
    }
  }
}

/** Plugin load: the cached list first (works without the server), then the server's. */
export async function startLibrary(): Promise<void> {
  await refreshLibrary(true);
  const config = await remoteGetConfig();
  if (!config?.url || !config.tokenSet) return;
  const fetched = await remoteRefresh();
  if (fetched && !fetched.ok) await logAdd("remote", `refresh at load: ${fetched.code}`);
  await refreshLibrary(true);
}

/** "Atualizar lista" in the panel. Returns the failure message, or null. */
export async function refreshRemoteIndex(): Promise<string | null> {
  const fetched = await remoteRefresh();
  await refreshLibrary(true);
  if (!fetched) return "veja o log de eventos";
  return fetched.ok ? null : fetched.message;
}

const guidOf = (appid: number): string | undefined =>
  [...libraryApps.entries()].find(([, a]) => a === appid)?.[0];

const nameOf = (guid: string) => libraryGames?.find((g) => g.id === guid)?.name ?? guid;

export type RemoteAction = "install" | "pause" | "resume" | "cancel" | "uninstall";

/** Panel and Steam buttons. Each one re-reads the state afterwards. */
export async function remoteAction(guid: string, action: RemoteAction): Promise<void> {
  const name = nameOf(guid);
  try {
    let result: { ok: boolean; message?: string } | null;
    if (action === "install") {
      result = await remoteInstall(guid);
      if (result?.ok) toaster.toast({ title: TITLE, body: `Baixando ${name}…`, logo: gameLogo(libraryApps.get(guid)) });
    } else if (action === "pause") result = await remotePause(guid);
    else if (action === "resume") result = await remoteResume(guid);
    else if (action === "cancel") result = await remoteCancel(guid);
    else {
      const appid = libraryApps.get(guid);
      const running = runningAppids();
      if (!running || (appid !== undefined && running.includes(appid))) {
        toaster.toast({ title: TITLE, body: `Feche ${name} antes de desinstalar` });
        return;
      }
      result = await remoteUninstall(guid);
      if (result?.ok) toaster.toast({ title: TITLE, body: `${name} desinstalado` });
    }
    if (!result?.ok) {
      const message = result?.message ?? "veja o log de eventos";
      await logAdd("remote", `${action} ${guid}: ${message}`);
      toaster.toast({ title: TITLE, body: `${name}: ${message}`, duration: 10000 });
    }
  } catch (err) {
    await report(`Biblioteca Remota (${name})`, err);
  } finally {
    await refreshLibrary();
  }
}

/** Steam's own buttons on a library shortcut's page (remote-steam.ts). */
export const remoteSteamHandlers = {
  install(appid: number) {
    const guid = guidOf(appid);
    if (guid) void remoteAction(guid, "install");
  },
  resume(appid: number) {
    const guid = guidOf(appid);
    const state = libraryGames?.find((g) => g.id === guid)?.state;
    // "Baixar" on a queued game: it is already waiting for its turn
    if (guid && (state === "paused" || state === "error")) void remoteAction(guid, "resume");
  },
  pause(appid: number) {
    const guid = guidOf(appid);
    if (guid) void remoteAction(guid, "pause");
  },
};

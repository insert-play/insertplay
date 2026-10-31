import { toaster } from "@decky/api";
import { createElement } from "react";
import {
  Card,
  PortableMode,
  PortableResults,
  PortableStatus,
  RegistryRecord,
  artworkBase64,
  currentCards,
  getSettings,
  libraryCard,
  logAdd,
  portableSet,
  portableSync,
  portableUnlinkDangling,
  registryAll,
  registryPut,
  shortcutIcon,
} from "./backend";
import {
  addShortcut,
  appIcon,
  appName,
  applyArtwork,
  currentSteamId,
  gameRunning,
  isHidden,
  launchOptions,
  mountedLibraries,
  runningAppids,
  steamLibraries,
  runGame,
  runSteamGame,
  setHidden,
  setShortcutIcon,
  shortcutExists,
  updateShortcut,
  waitForLibrary,
} from "./steam";
import { showBadges } from "./steam-badges";

const TITLE = "InsertPlay";

/** Game icon for the toast's `logo` slot (the square left of the title). Without an icon,
 *  or if the image fails to load (Steam game offline), the toast stays text only. */
function gameLogo(appid: number | undefined) {
  const src = appid === undefined ? null : appIcon(appid);
  if (!src) return undefined;
  return createElement("img", {
    src,
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

/** Ensures the card's shortcut exists and makes it visible. `launch` is only true on a
 *  real insertion; the load-time reconciliation never starts a game. Returns the appid. */
async function ensureCard(card: Card, launch: boolean): Promise<number | null> {
  if (card.error || !card.guid || !card.appName || !card.exe) {
    const why = card.error ?? "incomplete manifest";
    await logAdd("error", `${card.label}: ${why}`);
    toaster.toast({ title: TITLE, body: `Cartão ${card.label} não pôde ser lido: ${why}`, duration: 10000 });
    return null;
  }
  const spec = {
    appName: card.appName,
    exe: card.exe,
    startDir: card.startDir ?? card.mount,
    launchOptions: card.launchOptions ?? "",
  };
  // Only the registry ties a shortcut to a card, always by guid. Adopting by name
  // overwrote other people's shortcuts with the same name and made two cards with the
  // same name share a shortcut (PR #2 review). An appid another guid also claims
  // is not trustworthy: we create our own shortcut.
  const registry = await loadRegistry();
  const known = registry[card.guid];
  const claimedByOther =
    known !== undefined &&
    Object.entries(registry).some(([guid, record]) => guid !== card.guid && record.appid === known.appid);
  let appid = known && !claimedByOther && shortcutExists(known.appid) ? known.appid : null;
  let created = false;

  if (appid === null) {
    appid = await addShortcut(spec);
    created = true;
    await logAdd("add", `${card.appName}: shortcut ${appid} created`);
  } else {
    updateShortcut(appid, spec);
  }

  let artworkApplied = Boolean(known?.artworkApplied) && !created && known?.appid === appid;
  if (!artworkApplied && card.artwork && Object.keys(card.artwork).length) {
    const failed = await applyArtwork(appid, card.artwork, (p) => artworkBase64(p));
    if (failed.length) await logAdd("error", `${card.appName}: artwork failed: ${failed.join("; ")}`);
    artworkApplied = failed.length === 0;
  }
  if (card.icon) await ensureIcon(card, appid);
  await saveRecord(card.guid, { appid, appName: card.appName, artworkApplied });
  setHidden(appid, false);
  cardApps.set(card.mount, appid);

  const logo = gameLogo(appid);
  if (launch && (await getSettings()).autoLaunch) {
    toaster.toast({ title: TITLE, body: `Iniciando ${card.appName}…`, logo });
    await logAdd("launch", `${card.appName} (${appid})`);
    runGame(appid);
  } else {
    toaster.toast({ title: TITLE, body: `${card.appName} disponível`, logo });
  }
  return appid;
}

/** Shortcut icon from the copy on the SSD (issue #17). Runs on every insertion, not only
 *  on creation: a new icon on the card becomes a new path. The backend decides whether to
 *  apply it (it does not overwrite an icon the user chose). A failure does not stop the flow. */
async function ensureIcon(card: Card, appid: number): Promise<void> {
  try {
    const steamid = currentSteamId();
    if (!steamid) throw new Error("unknown Steam account");
    const icon = await shortcutIcon(steamid, appid, card.guid!, card.icon!);
    if (!icon) throw new Error("backend refused (see log)");
    if (!icon.apply) return;
    setShortcutIcon(appid, icon.path);
    await logAdd("icon", `${card.appName}: ${icon.path}`);
  } catch (err) {
    await logAdd("error", `${card.appName}: icon failed: ${err instanceof Error ? err.message : err}`);
  }
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

export const onInserted = (card: Card) =>
  enqueue(() => ensureCard(card, true))
    .catch((err) => report(`Inserção de ${card.label}`, err))
    .finally(refreshBadges);

export const onRemoved = (card: { mount: string; label: string }) =>
  enqueue(async () => {
    await syncAbsent();
    await logAdd("hide", `${card.label} removed`);
    const appid = cardApps.get(card.mount);
    cardApps.delete(card.mount);
    toaster.toast({ title: TITLE, body: `Cartão ${card.label} removido`, logo: gameLogo(appid) });
  })
    .catch((err) => report(`Remoção de ${card.label}`, err))
    .finally(refreshBadges);

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
  // Before auto-launch: the card's game must open already using the card's prefix.
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

/** Auto-starts only with exactly one game on the card and no game running. */
async function steamCardInserted(card: SteamCard): Promise<void> {
  const apps = [...new Set(card.apps)];
  await logAdd("steam", `${card.label} inserted (${apps.join(", ")})`);
  if (apps.length > 1) {
    toaster.toast({ title: TITLE, body: `Cartão ${card.label}: ${apps.length} jogos disponíveis` });
    return;
  }
  const [appid] = apps;
  const name = appName(appid);
  const logo = gameLogo(appid);
  if ((await getSettings()).autoLaunch && !gameRunning()) {
    toaster.toast({ title: TITLE, body: `Iniciando ${name}…`, logo });
    await logAdd("launch", `${name} (${appid})`);
    runSteamGame(appid);
  } else {
    toaster.toast({ title: TITLE, body: `${name} disponível`, logo });
  }
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
    .finally(refreshBadges);

/** "Hide on removal" toggled in the panel: applies right away to absent cards. */
export const applyHideRemoved = () =>
  enqueue(syncAbsent)
    .catch((err) => report("Ocultar ao remover", err))
    .finally(refreshBadges);

/** "Reinstalar atalho" button: forces the artwork to be applied again. */
export const reinstall = (card: Card) =>
  enqueue(async () => {
    if (card.guid) await saveRecord(card.guid, { artworkApplied: false });
    await ensureCard(card, false);
  })
    .catch((err) => report(`Reinstalação de ${card.label}`, err))
    .finally(refreshBadges);

export const launchCard = (card: Card) =>
  enqueue(async () => {
    const appid = await ensureCard(card, false);
    if (appid !== null) runGame(appid);
  }).catch((err) => report(`Início de ${card.label}`, err));

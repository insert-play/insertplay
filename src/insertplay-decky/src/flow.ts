import { toaster } from "@decky/api";
import {
  Card,
  artworkBase64,
  currentCards,
  getSettings,
  logAdd,
  registryAll,
  registryPut,
} from "./backend";
import {
  addShortcut,
  applyArtwork,
  findShortcutByName,
  runGame,
  setHidden,
  shortcutExists,
  updateShortcut,
  waitForLibrary,
} from "./steam";

const TITLE = "InsertPlay";

/** Fila única: inserção, remoção e reconciliação mexem no mesmo atalho e no mesmo
 *  registro. Em paralelo, uma remoção podia ocultar o atalho que uma inserção
 *  acabou de mostrar. */
let chain: Promise<unknown> = Promise.resolve();
export function enqueue<T>(work: () => Promise<T>): Promise<T> {
  // toda decisão sobre atalhos depende da biblioteca carregada (ver waitForLibrary)
  const guarded = async () => {
    await waitForLibrary();
    return work();
  };
  const next = chain.then(guarded, guarded);
  chain = next.catch(() => undefined);
  return next;
}

/** Garante o atalho do cartão e o deixa visível. `launch` só é verdadeiro numa
 *  inserção real; a reconciliação de carga nunca inicia jogo. Devolve o appid. */
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
  const known = (await registryAll())[card.guid];
  let appid = known && shortcutExists(known.appid) ? known.appid : null;
  let created = false;

  if (appid === null) {
    appid = findShortcutByName(card.appName);
    if (appid !== null) await logAdd("adopt", `${card.appName}: adopted existing shortcut ${appid}`);
  }
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
  await registryPut(card.guid, { appid, appName: card.appName, artworkApplied });
  setHidden(appid, false);

  if (launch && (await getSettings()).autoLaunch) {
    toaster.toast({ title: TITLE, body: `Iniciando ${card.appName}…` });
    await logAdd("launch", `${card.appName} (${appid})`);
    runGame(appid);
  } else {
    toaster.toast({ title: TITLE, body: `${card.appName} disponível` });
  }
  return appid;
}

/** Oculta todo atalho registrado cujo cartão não está montado agora. Pergunta ao
 *  backend em vez de lembrar mount→guid aqui: o frontend recarrega sem o backend
 *  saber, e o backend já atualizou a lista antes de emitir a remoção. */
async function hideAbsent(): Promise<void> {
  const present = new Set((await currentCards()).map((c) => c.guid).filter(Boolean));
  for (const [guid, record] of Object.entries(await registryAll())) {
    if (present.has(guid) || !shortcutExists(record.appid)) continue;
    setHidden(record.appid, true);
  }
}

async function report(what: string, err: unknown): Promise<void> {
  const detail = err instanceof Error ? err.message : String(err);
  // o stack vai só para o log: sem ele "reading 'get'" não diz onde quebrou
  const stack = err instanceof Error && err.stack ? `\n${err.stack.split("\n").slice(1, 6).join("\n")}` : "";
  await logAdd("error", `${what}: ${detail}${stack}`).catch(() => undefined);
  toaster.toast({ title: TITLE, body: `${what} falhou: ${detail}`, duration: 10000 });
}

export const onInserted = (card: Card) =>
  enqueue(() => ensureCard(card, true)).catch((err) => report(`Inserção de ${card.label}`, err));

export const onRemoved = (card: { mount: string; label: string }) =>
  enqueue(async () => {
    await hideAbsent();
    await logAdd("hide", `${card.label} removed`);
    toaster.toast({ title: TITLE, body: `Cartão ${card.label} removido` });
  }).catch((err) => report(`Remoção de ${card.label}`, err));

/** Estado inicial ao carregar o plugin: mostra o que está inserido, oculta o resto,
 *  não inicia nada. */
export const reconcile = () =>
  enqueue(async () => {
    const cards = await currentCards();
    for (const card of cards) await ensureCard(card, false);
    await hideAbsent();
    // sem esta linha, "reconciliou certo" e "nem rodou" ficam iguais no log
    await logAdd("reconcile", `${cards.length} card(s): ${cards.map((c) => c.appName ?? c.label).join(", ") || "-"}`);
  }).catch((err) => report("Reconciliação", err));

/** Botão "Reinstalar atalho": força nova aplicação das artes. */
export const reinstall = (card: Card) =>
  enqueue(async () => {
    if (card.guid) await registryPut(card.guid, { artworkApplied: false });
    await ensureCard(card, false);
  }).catch((err) => report(`Reinstalação de ${card.label}`, err));

export const launchCard = (card: Card) =>
  enqueue(async () => {
    const appid = await ensureCard(card, false);
    if (appid !== null) runGame(appid);
  }).catch((err) => report(`Início de ${card.label}`, err));

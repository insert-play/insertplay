import { toaster } from "@decky/api";
import {
  Card,
  RegistryRecord,
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
  isHidden,
  removableLibraryApps,
  runGame,
  setHidden,
  shortcutExists,
  updateShortcut,
  waitForLibrary,
} from "./steam";
import { showBadges } from "./steam-badges";

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
  // Só o registro associa um atalho a um cartão, sempre pelo guid. Adotar por nome
  // sobrescrevia atalhos alheios com o mesmo nome e fazia dois cartões homônimos
  // dividirem um atalho (review do PR #2). Um appid que outro guid também reivindica
  // não é confiável: criamos um atalho próprio.
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
  await saveRecord(card.guid, { appid, appName: card.appName, artworkApplied });
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

/** Aplica a configuração "ocultar ao remover" a todo atalho registrado cujo cartão não
 *  está montado agora: oculta, ou mostra de volta se a opção estiver desligada. Pergunta
 *  ao backend em vez de lembrar mount→guid aqui: o frontend recarrega sem o backend
 *  saber, e o backend já atualizou a lista antes de emitir a remoção. */
async function syncAbsent(): Promise<void> {
  const present = new Set((await presentCards()).map((c) => c.guid).filter(Boolean));
  const registry = Object.entries(await loadRegistry());
  const hide = (await getSettings()).hideRemoved ?? true;
  // um appid que um cartão PRESENTE usa nunca é ocultado, mesmo que outro guid
  // (ausente) aponte para ele num registro antigo
  const presentAppids = new Set(registry.filter(([guid]) => present.has(guid)).map(([, r]) => r.appid));
  for (const [guid, record] of registry) {
    if (present.has(guid) || presentAppids.has(record.appid) || !shortcutExists(record.appid)) continue;
    if (isHidden(record.appid) !== hide) setHidden(record.appid, hide);
  }
}

/** Cartões montados agora. `null` do backend = ele ainda não terminou a primeira
 *  varredura (ou falhou): seguir com "nenhum cartão" ocultaria o atalho do cartão
 *  que está no leitor, então lançamos e a operação aparece como erro. */
async function presentCards(): Promise<Card[]> {
  const cards = await currentCards();
  if (!Array.isArray(cards)) throw new Error("backend has not finished scanning for cards");
  return cards;
}

/** Registro guid → appid. Falha do backend lança: tratar como vazio faria cada
 *  cartão parecer novo e ganhar um atalho duplicado. */
async function loadRegistry(): Promise<Record<string, RegistryRecord>> {
  const registry = await registryAll();
  if (!registry || typeof registry !== "object") throw new Error("could not read the shortcut registry");
  return registry;
}

/** Gravação que falha em silêncio perderia o vínculo guid → appid e a próxima
 *  inserção criaria outro atalho; por isso lança. */
async function saveRecord(guid: string, record: Partial<RegistryRecord>): Promise<void> {
  if (!(await registryPut(guid, record))) throw new Error(`could not save the registry entry for ${guid}`);
}

async function report(what: string, err: unknown): Promise<void> {
  const detail = err instanceof Error ? err.message : String(err);
  // o stack vai só para o log: sem ele "reading 'get'" não diz onde quebrou
  const stack = err instanceof Error && err.stack ? `\n${err.stack.split("\n").slice(1, 6).join("\n")}` : "";
  await logAdd("error", `${what}: ${detail}${stack}`).catch(() => undefined);
  toaster.toast({ title: TITLE, body: `${what} falhou: ${detail}`, duration: 10000 });
}

let lastBadgeError = "";

/** Recalcula quais jogos levam o badge de cartão: os atalhos dos cartões InsertPlay
 *  inseridos e os jogos Steam instalados numa biblioteca do cartão. Só cartões
 *  presentes: o atalho de um cartão ausente fica oculto e não deve ficar com badge.
 *  Nunca lança; o badge é enfeite. O erro vai para o log uma vez, não a cada gatilho. */
export async function refreshBadges(): Promise<void> {
  try {
    const [cards, registry, steamApps] = await Promise.all([presentCards(), loadRegistry(), removableLibraryApps()]);
    const present = new Set(cards.map((c) => c.guid).filter(Boolean));
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
    toaster.toast({ title: TITLE, body: `Cartão ${card.label} removido` });
  })
    .catch((err) => report(`Remoção de ${card.label}`, err))
    .finally(refreshBadges);

/** Estado inicial ao carregar o plugin: mostra o que está inserido, aplica "ocultar ao
 *  remover" ao resto, não inicia nada. */
export const reconcile = () =>
  enqueue(async () => {
    const cards = await presentCards();
    for (const card of cards) await ensureCard(card, false);
    await syncAbsent();
    // sem esta linha, "reconciliou certo" e "nem rodou" ficam iguais no log
    await logAdd("reconcile", `${cards.length} card(s): ${cards.map((c) => c.appName ?? c.label).join(", ") || "-"}`);
  })
    .catch((err) => report("Reconciliação", err))
    .finally(refreshBadges);

/** Troca de "ocultar ao remover" no painel: aplica na hora aos cartões ausentes. */
export const applyHideRemoved = () =>
  enqueue(syncAbsent)
    .catch((err) => report("Ocultar ao remover", err))
    .finally(refreshBadges);

/** Botão "Reinstalar atalho": força nova aplicação das artes. */
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

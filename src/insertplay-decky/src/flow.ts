import { toaster } from "@decky/api";
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
} from "./backend";
import {
  addShortcut,
  appName,
  applyArtwork,
  gameRunning,
  isHidden,
  launchOptions,
  mountedLibraries,
  runningAppids,
  steamLibraries,
  runGame,
  runSteamGame,
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
    const [cards, registry] = await Promise.all([presentCards(), loadRegistry()]);
    const present = new Set(cards.map((c) => c.guid).filter(Boolean));
    // o mesmo critério de cartão das notificações; antes do primeiro retrato, nenhum
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
    toaster.toast({ title: TITLE, body: `Cartão ${card.label} removido` });
  })
    .catch((err) => report(`Remoção de ${card.label}`, err))
    .finally(refreshBadges);

/** Biblioteca Steam montada vista no último aviso do Steam, por pasta da biblioteca. */
interface SteamCard {
  mount: string;
  label: string;
  /** Está num cartão: o Steam diz que não é fixa (Deck) ou o kernel diz que o disco é
   *  removível (leitor USB da Steam Machine). Um segundo SSD interno fica de fora. */
  card: boolean;
  /** Também é cartão InsertPlay (ou o backend não soube dizer): o outro fluxo notifica. */
  silent: boolean;
  /** O backend achou o ponto de montagem (mídia em /run/media). Sem isso, o save portátil
   *  não vale: o backend recusa caminhos fora de mídia montada. */
  onMedia: boolean;
  apps: number[];
}

/** `null` = ainda sem retrato. O primeiro aviso (o Steam chama o callback logo após o
 *  registro) e a carga do plugin com o cartão inserido são estado inicial, não inserção. */
let steamCards: Map<string, SteamCard> | null = null;

/** Compara as bibliotecas removíveis montadas com o último retrato. Por cartão (ponto de
 *  montagem), não por appid: instalar um jogo num cartão já inserido muda `apps`, não
 *  os cartões presentes. */
async function checkSteamLibraries(): Promise<void> {
  const libraries = await steamLibraries();
  const previous = steamCards;
  const next = new Map<string, SteamCard>();
  // Medido no Deck: o aviso do Steam chega ~12 s depois de um move terminar, então `apps`
  // do retrato pode estar velho. Na remoção vale o que o Steam guardou da pasta
  // desmontada, mesmo vazio (o último jogo pode ter saído do cartão).
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
  // Antes do auto-launch: o jogo do cartão tem que abrir já com o prefixo do cartão.
  // Falha aqui não pode tirar a notificação da inserção.
  await syncPortable().catch((err) => report("Save portátil", err));
  if (previous === null) {
    // sem esta linha, um cartão que faltou no retrato só aparece como "remoção perdida"
    const cards = [...next.values()]
      .filter((c) => c.card)
      .map((c) => `${c.label}${c.silent ? " (insertplay)" : ""}: ${c.apps.length}`);
    await logAdd("steam", `snapshot: ${cards.join(", ") || "-"}`);
    return;
  }

  // Inserção e remoção são do cartão (ponto de montagem), não de cada biblioteca:
  // acrescentar ou tirar uma biblioteca num cartão inserido não é evento, e um cartão
  // com duas bibliotecas soma os jogos das duas (review do PR #12).
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
    toaster.toast({ title: TITLE, body: `Cartão ${card.label} removido` });
  }
  for (const card of gone(after, before)) await steamCardInserted(card);
}

/** Inicia sozinho só com exatamente um jogo no cartão e nenhum jogo aberto. */
async function steamCardInserted(card: SteamCard): Promise<void> {
  const apps = [...new Set(card.apps)];
  await logAdd("steam", `${card.label} inserted (${apps.join(", ")})`);
  if (apps.length > 1) {
    toaster.toast({ title: TITLE, body: `Cartão ${card.label}: ${apps.length} jogos disponíveis` });
    return;
  }
  const [appid] = apps;
  const name = appName(appid);
  if ((await getSettings()).autoLaunch && !gameRunning()) {
    toaster.toast({ title: TITLE, body: `Iniciando ${name}…` });
    await logAdd("launch", `${name} (${appid})`);
    runSteamGame(appid);
  } else {
    toaster.toast({ title: TITLE, body: `${name} disponível` });
  }
}

/** Aviso do Steam de que as bibliotecas mudaram (cartão inserido ou removido, jogo
 *  instalado ou movido). Também atualiza os badges, como antes. */
export const onSteamLibrariesChanged = () =>
  enqueue(checkSteamLibraries)
    .catch((err) => report("Biblioteca Steam no cartão", err))
    .finally(refreshBadges);

/** Cartão com biblioteca Steam inserido agora, juntando as bibliotecas do mesmo cartão. */
export interface PortableCard {
  mount: string;
  label: string;
  libraries: string[];
  apps: number[];
}

/** Cartões Steam presentes no último retrato (vazio antes do primeiro). */
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

/** Jogos já avisados nesta sessão de que as opções de inicialização trocam o prefixo. */
const warnedCompatPath = new Set<number>();

/** `STEAM_COMPAT_DATA_PATH` (ou `WINEPREFIX`) nas opções de inicialização faz o Proton
 *  usar outro prefixo e ignorar o link. Medido na Steam Machine: com a opção de um teste
 *  antigo, o Bully abriu sem os saves do cartão e gravou ~115 MB de DLLs no prefixo da
 *  raiz do cartão. A opção é do usuário: avisamos, não apagamos. */
async function warnPrefixOverride(card: PortableCard, results: PortableResults): Promise<void> {
  for (const id of Object.keys(results)) {
    const appid = Number(id);
    if (warnedCompatPath.has(appid) || !/STEAM_COMPAT_DATA_PATH|WINEPREFIX/.test(launchOptions(appid) ?? "")) continue;
    warnedCompatPath.add(appid);
    await logAdd("portable", `${card.label} ${appid}: launch options override the prefix`);
    toaster.toast({
      title: TITLE,
      body: `${appName(appid)}: as opções de inicialização mudam o prefixo do Proton. O save portátil não vale para este jogo enquanto elas existirem.`,
      duration: 10000,
    });
  }
}

/** Toast do que mudou numa passada. "ok", "none", "leftover" e "unlinked" vão só para o
 *  log do backend: acontecem sem o usuário precisar saber. */
async function announcePortable(card: PortableCard, results: PortableResults): Promise<void> {
  await warnPrefixOverride(card, results);
  for (const [id, result] of Object.entries(results)) {
    const name = appName(Number(id));
    if (result.startsWith("error")) {
      // o mesmo erro volta a cada passada (30 s): avisa e registra uma vez por sessão
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

/** Jogos instalados em outras bibliotecas (SSD ou outro cartão): um jogo que saiu deste
 *  cartão para lá ganha cópia do prefixo no SSD em vez de só perder o link. */
async function elsewhereOf(card: PortableCard): Promise<number[]> {
  return (await mountedLibraries()).filter((l) => !card.libraries.includes(l.path)).flatMap((l) => l.apps);
}

/** Uma passada do save portátil em todos os cartões Steam presentes (o backend ignora os
 *  desligados). Sem saber quais jogos estão abertos, não mexe em nada. */
async function syncPortable(): Promise<void> {
  const running = runningAppids();
  if (!running) return;
  await portableUnlinkDangling();
  for (const card of steamCardsPresent()) {
    const results = await portableSync(card.mount, card.libraries, card.apps, running, await elsewhereOf(card));
    if (results) await announcePortable(card, results);
  }
}

/** Passada periódica: o Steam recria o prefixo no SSD ~20 s depois de um move, e o jogo
 *  que roda pela primeira vez cria um prefixo comum que só depois pode ir para o cartão. */
let portableTicking = false;
export const portableTick = () => {
  // Uma migração lenta (prefixo inteiro num cartão lento) leva minutos. Empilhar um tick a
  // cada 30 s na fila única atrasaria a inserção de cartões InsertPlay por todos eles.
  if (portableTicking) return Promise.resolve();
  portableTicking = true;
  return enqueue(syncPortable)
    .catch((err) => report("Save portátil", err))
    .finally(() => {
      portableTicking = false;
    });
};

/** Escolha de modo em andamento (`done: false`) ou já aplicada e ainda não relida pelo
 *  painel (`done: true`). Fica aqui, fora do componente: o menu do seletor e o modal de
 *  confirmação abrem por cima do Quick Access e o painel é montado de novo. Guardado no
 *  componente, o "aplicando" sumia e o seletor voltava ao modo antigo (visto no Deck). */
export interface PortableChange {
  mode: PortableMode;
  done: boolean;
}
const portableChanges = new Map<string, PortableChange>();
const portableListeners = new Set<() => void>();

export const portableChange = (mount: string): PortableChange | null => portableChanges.get(mount) ?? null;

/** O painel já leu o modo aplicado: a mudança acabou. */
export function settlePortableChange(mount: string): void {
  if (portableChanges.get(mount)?.done) portableChanges.delete(mount);
}

/** Avisa quando uma mudança começa ou termina. Devolve o cancelamento. */
export function onPortableChange(callback: () => void): () => void {
  portableListeners.add(callback);
  return () => void portableListeners.delete(callback);
}

function trackPortable(mount: string, change: PortableChange | null): void {
  if (change) portableChanges.set(mount, change);
  else portableChanges.delete(mount);
  portableListeners.forEach((callback) => callback());
}

/** Escolha do painel. Devolve o status novo, ou `null` se falhou (já avisado). */
export const setPortable = (card: PortableCard, mode: PortableMode) => {
  trackPortable(card.mount, { mode, done: false });
  return runSetPortable(card, mode).then((status) => {
    // não mudou (falha, ou jogo do cartão aberto): o seletor volta ao modo de antes
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

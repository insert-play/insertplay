// O único arquivo que toca a API não documentada do Steam. Quando a Valve mudar um
// método, o conserto fica aqui.
//
// Partes adaptadas de decky-nonsteam-sync (src/steam.ts),
// Copyright (c) 2026, JoseArkadio — BSD 3-Clause.
//
// Medido no Deck (SteamOS 3.8.28, 2026-10-24) com scripts/spike.mjs: todas as chamadas
// abaixo funcionam como usadas aqui. Resultados em docs/steam-api-notes.md; rode o
// spike de novo depois de uma atualização do Steam.

declare const appStore: any;
declare const appDetailsStore: any;
declare const collectionStore: any;
declare const SteamUIStore: any;
// Tipado como `any` de propósito: nem todas as versões de @decky/ui declaram o global,
// e as assinaturas reais são as que o passo 0 medir no Deck.
declare const SteamClient: any;

const apps = (): any => (typeof appStore === "undefined" ? null : appStore);
const collections = (): any => (typeof collectionStore === "undefined" ? null : collectionStore);
const client = (): any => (typeof SteamClient === "undefined" ? null : (SteamClient as any));

function method(owner: any, name: string, label: string): (...args: any[]) => any {
  const fn = owner?.[name];
  if (typeof fn !== "function") throw new Error(`Steam method missing: ${label}`);
  return fn.bind(owner);
}

export interface ShortcutSpec {
  appName: string;
  exe: string;
  startDir: string;
  launchOptions: string;
}

/** Espera o Steam terminar de carregar a biblioteca e as coleções.
 *
 *  Medido no Deck ao reiniciar o Steam:
 *  - o Decky carrega o plugin ~0,5 s ANTES de `appStore.m_bIsInitialized`, e os atalhos
 *    só aparecem no `appStore` nesse instante. Perguntar antes respondia "o atalho não
 *    existe" e o plugin criava um duplicado;
 *  - as coleções carregam DEPOIS: `SetAppsAsHidden` → `GetCollection` lançava
 *    "reading 'get'". Testamos a própria coleção "hidden", que é o que usamos.
 *  Sem isso pronto não decidimos nada: lança em vez de seguir às cegas. */
function libraryReady(): boolean {
  if (!apps()?.m_bIsInitialized) return false;
  try {
    return Boolean(collections()?.GetCollection?.("hidden"));
  } catch {
    return false;
  }
}

export async function waitForLibrary(timeoutMs = 60_000): Promise<void> {
  const start = Date.now();
  while (!libraryReady()) {
    if (Date.now() - start > timeoutMs) {
      throw new Error("Steam library did not finish loading; not touching shortcuts");
    }
    await new Promise((r) => setTimeout(r, 250));
  }
}

export function shortcutExists(appid: number): boolean {
  return Boolean(apps()?.GetAppOverviewByAppID?.(appid));
}

export async function addShortcut(spec: ShortcutSpec): Promise<number> {
  const apps = client()?.Apps;
  const appid: number = await method(apps, "AddShortcut", "SteamClient.Apps.AddShortcut")(
    spec.appName,
    spec.exe,
    startDirOf(spec.startDir),
    spec.launchOptions,
  );
  if (!appid) throw new Error("AddShortcut returned no appid");
  // Medido no nonsteam-sync: AddShortcut ignora o nome e usa o do executável. Aqui o
  // exe é /bin/bash, então sem isso todo cartão viraria um tile chamado "bash".
  method(apps, "SetShortcutName", "SteamClient.Apps.SetShortcutName")(appid, spec.appName);
  method(apps, "SetShortcutLaunchOptions", "SteamClient.Apps.SetShortcutLaunchOptions")(appid, spec.launchOptions);
  return appid;
}

/** Reaplica nome, exe, pasta e opções. O ponto de montagem do cartão muda entre
 *  aparelhos e rótulos, e o atalho guarda o caminho fixo. */
export function updateShortcut(appid: number, spec: ShortcutSpec): void {
  const apps = client()?.Apps;
  method(apps, "SetShortcutName", "SteamClient.Apps.SetShortcutName")(appid, spec.appName);
  // Medido no nonsteam-sync: AddShortcut põe aspas no exe, SetShortcutExe grava literal.
  const quoted = spec.exe.startsWith('"') ? spec.exe : `"${spec.exe}"`;
  method(apps, "SetShortcutExe", "SteamClient.Apps.SetShortcutExe")(appid, quoted);
  apps?.SetShortcutStartDir?.(appid, startDirOf(spec.startDir));
  method(apps, "SetShortcutLaunchOptions", "SteamClient.Apps.SetShortcutLaunchOptions")(appid, spec.launchOptions);
}

function startDirOf(dir: string): string {
  return dir.endsWith("/") ? dir : `${dir}/`;
}

export function setHidden(appid: number, hidden: boolean): void {
  method(collections(), "SetAppsAsHidden", "collectionStore.SetAppsAsHidden")([appid], hidden);
}

export function isHidden(appid: number): boolean {
  return Boolean(collections()?.BIsHidden?.(appid));
}

// 0 = capa vertical, 1 = hero, 2 = logo, 3 = capa horizontal (medido no nonsteam-sync).
const ASSET_TYPE: Record<string, number> = { capsule: 0, hero: 1, logo: 2, horizontal: 3 };

/** Aplica as artes do cartão. Uma arte que falha não impede as outras;
 *  devolve a lista do que falhou. */
export async function applyArtwork(
  appid: number,
  artwork: Record<string, string>,
  toBase64: (path: string) => Promise<string>,
): Promise<string[]> {
  const set = method(client()?.Apps, "SetCustomArtworkForApp", "SteamClient.Apps.SetCustomArtworkForApp");
  const failed: string[] = [];
  for (const [kind, path] of Object.entries(artwork)) {
    const assetType = ASSET_TYPE[kind];
    if (assetType === undefined) continue;
    try {
      const data = await toBase64(path);
      if (!data) throw new Error("empty image");
      const ext = (path.split(".").pop() ?? "").toLowerCase();
      await set(appid, data, ext === "jpg" || ext === "jpeg" ? "jpg" : "png", assetType);
    } catch (err) {
      failed.push(`${kind}: ${err}`);
    }
  }
  return failed;
}

export interface SteamLibrary {
  path: string;
  mounted: boolean;
  /** `bIsFixed` do Steam. Só no Deck ele distingue o cartão: na Steam Machine, o cartão no
   *  leitor USB vem como fixo (medido em 2026-10-24). Quem decide é `libraryCard`. */
  fixed: boolean;
  apps: number[];
}

/** Lista de bibliotecas já carregada. Medido no Deck ao reiniciar o Steam: ela vem
 *  vazia no começo e, ~3 s depois, aparece inteira de uma vez e sem aviso do Steam.
 *  A pasta padrão sempre existe, então lista sem ela = ainda carregando. Usar a lista
 *  vazia como retrato faria o cartão já inserido parecer inserção no próximo aviso. */
async function installFolders(timeoutMs = 60_000): Promise<any[]> {
  const get = method(client()?.InstallFolder, "GetInstallFolders", "SteamClient.InstallFolder.GetInstallFolders");
  const start = Date.now();
  for (;;) {
    const folders: any[] = await get();
    if (Array.isArray(folders) && folders.some((f) => f?.bIsDefaultFolder)) return folders;
    if (Date.now() - start > timeoutMs) throw new Error("Steam library folders did not finish loading");
    await new Promise((r) => setTimeout(r, 250));
  }
}

/** Bibliotecas Steam além da pasta padrão, montadas ou não, com os appids instalados.
 *
 *  Medido no Deck (2026-10-24, SteamOS 3.9.5): cada biblioteca que o Steam já viu vira
 *  uma pasta em `GetInstallFolders()`, montada ou não. A do cartão no leitor tem
 *  `bIsMounted: true`. As de cartões ausentes continuam na lista com `bIsMounted: false`
 *  e os appids que tinham: quem quer só o que está no leitor filtra por `mounted`. */
export async function steamLibraries(): Promise<SteamLibrary[]> {
  const folders = await installFolders();
  return folders
    .filter((f) => f && !f.bIsDefaultFolder && f.strFolderPath)
    .map((f) => ({
      path: String(f.strFolderPath),
      mounted: Boolean(f.bIsMounted),
      fixed: Boolean(f.bIsFixed),
      apps: (f.vecApps ?? []).map((a: any) => Number(a?.nAppID)).filter((appid: number) => appid > 0),
    }));
}

/** Appids instalados em cada biblioteca montada, inclusive a pasta padrão (SSD). */
export async function mountedLibraries(): Promise<{ path: string; apps: number[] }[]> {
  const folders = await installFolders();
  return folders
    .filter((f) => f?.strFolderPath && (f.bIsMounted || f.bIsDefaultFolder))
    .map((f) => ({
      path: String(f.strFolderPath),
      apps: (f.vecApps ?? []).map((a: any) => Number(a?.nAppID)).filter((appid: number) => appid > 0),
    }));
}

/** Avisa quando bibliotecas entram, saem ou mudam de conteúdo. Devolve o cancelamento.
 *  Medido no Deck: o aviso chega menos de 1 s depois de o cartão montar ou desmontar. */
export function onInstallFoldersChanged(callback: () => void): () => void {
  const registration = client()?.InstallFolder?.RegisterForInstallFolderChanges?.(callback);
  return () => registration?.unregister?.();
}

/** gameid de atalho = (appid << 32) | 0x02000000. Prefere o valor que o Steam expõe. */
function gameIdOf(appid: number): string {
  const fromStore = apps()?.GetAppOverviewByAppID?.(appid)?.m_gameid;
  if (fromStore) return String(fromStore);
  return ((BigInt(appid >>> 0) << 32n) | 0x02000000n).toString();
}

export function runGame(appid: number): void {
  method(client()?.Apps, "RunGame", "SteamClient.Apps.RunGame")(gameIdOf(appid), "", -1, 100);
}

/** Jogo Steam: o overview não tem `m_gameid` (medido no Deck) e o gameid é o próprio
 *  appid, sem os bits de atalho. */
export function runSteamGame(appid: number): void {
  method(client()?.Apps, "RunGame", "SteamClient.Apps.RunGame")(String(appid), "", -1, 100);
}

export function appName(appid: number): string {
  return apps()?.GetAppOverviewByAppID?.(appid)?.display_name || `app ${appid}`;
}

/** Algum jogo aberto agora. Na dúvida (store ausente), responde que sim: o pior caso é
 *  não iniciar sozinho. */
export function gameRunning(): boolean {
  const running = typeof SteamUIStore === "undefined" ? undefined : SteamUIStore?.RunningApps;
  return !Array.isArray(running) || running.length > 0;
}

/** Appids dos jogos abertos. `null` = não sei (store ausente ou item sem appid): o save
 *  portátil não mexe em prefixo nenhum na dúvida. */
export function runningAppids(): number[] | null {
  const running = typeof SteamUIStore === "undefined" ? undefined : SteamUIStore?.RunningApps;
  if (!Array.isArray(running)) return null;
  const ids = running.map((app: any) => Number(app?.appid));
  return ids.every((id) => id > 0) ? ids : null;
}

/** Opções de inicialização do jogo, se o Steam já as carregou (`undefined` = não sei).
 *  Medido na Steam Machine (2026-10-25): `appDetailsStore.GetAppDetails(12200)
 *  .strLaunchOptions` devolve o texto do campo, que é guardado por aparelho. */
export function launchOptions(appid: number): string | undefined {
  try {
    return typeof appDetailsStore === "undefined"
      ? undefined
      : appDetailsStore?.GetAppDetails?.(appid)?.strLaunchOptions;
  } catch {
    return undefined;
  }
}

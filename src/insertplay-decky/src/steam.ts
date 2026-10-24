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
declare const collectionStore: any;
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

/** gameid de atalho = (appid << 32) | 0x02000000. Prefere o valor que o Steam expõe. */
function gameIdOf(appid: number): string {
  const fromStore = apps()?.GetAppOverviewByAppID?.(appid)?.m_gameid;
  if (fromStore) return String(fromStore);
  return ((BigInt(appid >>> 0) << 32n) | 0x02000000n).toString();
}

export function runGame(appid: number): void {
  method(client()?.Apps, "RunGame", "SteamClient.Apps.RunGame")(gameIdOf(appid), "", -1, 100);
}

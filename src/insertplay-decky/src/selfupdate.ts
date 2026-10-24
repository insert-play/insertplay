import { toaster } from "@decky/api";
import { CardPackage, logAdd, packageStatus } from "./backend";

// Global do frontend do Decky (o mesmo que o deck.mjs usa no `rpc`). Não faz parte
// de @decky/api: a rota é a mesma que a loja e o "Install Plugin from ZIP File" usam.
declare const DeckyBackend: any;

/** InstallType do Decky (frontend/src/plugin.ts). Só muda o texto do aviso; o
 *  Decky sempre remove a versão instalada e extrai a nova. */
export enum InstallType {
  INSTALL,
  REINSTALL,
  UPDATE,
  DOWNGRADE,
  OVERWRITE,
}

/** Pede ao Decky para instalar o zip do cartão. O Decky mostra o próprio aviso de
 *  confirmação; ao confirmar, ele descarrega este plugin e carrega a versão nova, então
 *  nada aqui roda depois disso. O sha256 lido pelo backend faz o Decky recusar o zip
 *  se ele mudar entre a leitura e a instalação. */
export async function installFromCard(pkg: CardPackage, type: InstallType): Promise<void> {
  if (typeof DeckyBackend?.call !== "function") throw new Error("Decky backend is not available");
  if (!pkg.version || !pkg.sha256) throw new Error(`no valid package on ${pkg.label}`);
  await logAdd("package", `${pkg.label}: install v${pkg.version} requested`);
  await DeckyBackend.call("utilities/install_plugin", `file://${pkg.zipPath}`, "InsertPlay", pkg.version, pkg.sha256, type);
}

/** Aviso ao inserir um cartão que traz versão mais nova do plugin. Nunca instala sozinho. */
export async function notifyNewerPackage(mount: string): Promise<void> {
  const status = await packageStatus();
  const pkg = status?.cards.find((c) => c.mount === mount);
  if (pkg?.relation !== "newer") return;
  toaster.toast({
    title: "InsertPlay",
    body: `Cartão ${pkg.label} traz o plugin v${pkg.version} (instalado: v${status!.installed}). Atualize pelo painel.`,
    duration: 10000,
  });
}

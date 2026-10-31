import { toaster } from "@decky/api";
import { CardPackage, logAdd, packageStatus } from "./backend";

// Decky frontend global (the same one deck.mjs uses in `rpc`). Not part of
// @decky/api: the route is the same one the store and "Install Plugin from ZIP File" use.
declare const DeckyBackend: any;

/** Decky's InstallType (frontend/src/plugin.ts). Only changes the prompt text;
 *  Decky always removes the installed version and extracts the new one. */
export enum InstallType {
  INSTALL,
  REINSTALL,
  UPDATE,
  DOWNGRADE,
  OVERWRITE,
}

/** Asks Decky to install the zip from the card. Decky shows its own confirmation
 *  prompt; on confirm, it unloads this plugin and loads the new version, so nothing
 *  here runs after that. The sha256 read by the backend makes Decky reject the zip
 *  if it changes between the read and the install. */
export async function installFromCard(pkg: CardPackage, type: InstallType): Promise<void> {
  if (typeof DeckyBackend?.call !== "function") throw new Error("Decky backend is not available");
  if (!pkg.version || !pkg.sha256) throw new Error(`no valid package on ${pkg.label}`);
  await logAdd("package", `${pkg.label}: install v${pkg.version} requested`);
  await DeckyBackend.call("utilities/install_plugin", `file://${pkg.zipPath}`, "InsertPlay", pkg.version, pkg.sha256, type);
}

/** Notice when a card carrying a newer plugin version is inserted. Never installs on its own. */
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

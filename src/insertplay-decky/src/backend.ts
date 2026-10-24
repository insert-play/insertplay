import { callable } from "@decky/api";

export interface Card {
  mount: string;
  label: string;
  error: string | null;
  guid?: string;
  appName?: string;
  exe?: string;
  startDir?: string;
  launchOptions?: string;
  artwork?: Record<string, string>;
}

export interface RegistryRecord {
  appid: number;
  appName: string;
  artworkApplied?: boolean;
  updatedAt?: number;
}

export interface Settings {
  autoLaunch: boolean;
}

export interface LogEntry {
  ts: number;
  kind: string;
  message: string;
}

/** `null` = o backend ainda não terminou a primeira varredura; não é "nenhum cartão". */
export const currentCards = callable<[], Card[] | null>("current_cards");
export const artworkBase64 = callable<[path: string], string>("artwork_base64");
/** `null` = falha no backend; nunca tratar como registro vazio (geraria duplicados). */
export const registryAll = callable<[], Record<string, RegistryRecord> | null>("registry_all");
export const registryPut = callable<[guid: string, record: Partial<RegistryRecord>], RegistryRecord | null>(
  "registry_put",
);
export const getSettings = callable<[], Settings>("get_settings");
export const setSetting = callable<[key: keyof Settings, value: boolean], Settings>("set_setting");
export const logAdd = callable<[kind: string, message: string], void>("log_add");
export const logTail = callable<[count: number], LogEntry[]>("log_tail");

/** Pacote do plugin no cartão comparado com a versão instalada. */
export type PackageRelation = "missing" | "invalid" | "newer" | "older" | "same";

export interface CardPackage {
  mount: string;
  label: string;
  zipPath: string;
  version: string | null;
  sha256: string | null;
  relation: PackageRelation;
  error: string | null;
}

export interface PackageStatus {
  installed: string;
  cards: CardPackage[];
}

/** `null` = falha no backend (detalhe no log de eventos). */
export const packageStatus = callable<[], PackageStatus | null>("package_status");
export const writePackageToCard = callable<[mount: string], CardPackage | null>("write_package_to_card");

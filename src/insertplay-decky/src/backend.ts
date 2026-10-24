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

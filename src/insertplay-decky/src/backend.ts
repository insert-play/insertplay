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

export const currentCards = callable<[], Card[]>("current_cards");
export const artworkBase64 = callable<[path: string], string>("artwork_base64");
export const registryAll = callable<[], Record<string, RegistryRecord>>("registry_all");
export const registryPut = callable<[guid: string, record: Partial<RegistryRecord>], RegistryRecord>("registry_put");
export const getSettings = callable<[], Settings>("get_settings");
export const setSetting = callable<[key: keyof Settings, value: boolean], Settings>("set_setting");
export const logAdd = callable<[kind: string, message: string], void>("log_add");
export const logTail = callable<[count: number], LogEntry[]>("log_tail");

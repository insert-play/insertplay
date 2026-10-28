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
  /** Ícone do atalho, no cartão. */
  icon?: string | null;
}

export interface RegistryRecord {
  appid: number;
  appName: string;
  artworkApplied?: boolean;
  updatedAt?: number;
}

export interface Settings {
  autoLaunch: boolean;
  /** Oculta o atalho quando o cartão sai. Desligado, o atalho fica visível sem badge. */
  hideRemoved: boolean;
}

export interface LogEntry {
  ts: number;
  kind: string;
  message: string;
}

/** `null` = o backend ainda não terminou a primeira varredura; não é "nenhum cartão". */
export const currentCards = callable<[], Card[] | null>("current_cards");
export const artworkBase64 = callable<[path: string], string>("artwork_base64");

export interface ShortcutIcon {
  /** Cópia no SSD (`~/homebrew/data/InsertPlay/icons/<guid>-<hash>.<ext>`). */
  path: string;
  /** Aplicar com `SetShortcutIcon`. Falso quando já está aplicado ou o ícone é do usuário. */
  apply: boolean;
  /** Ícone atual no `shortcuts.vdf`; `null` = atalho ainda fora do arquivo. */
  current: string | null;
}

/** `null` = falha no backend (detalhe no log): não mexe no ícone. */
export const shortcutIcon = callable<[steamid: string, appid: number, guid: string, source: string], ShortcutIcon | null>(
  "shortcut_icon",
);
/** `null` = falha no backend; nunca tratar como registro vazio (geraria duplicados). */
export const registryAll = callable<[], Record<string, RegistryRecord> | null>("registry_all");
export const registryPut = callable<[guid: string, record: Partial<RegistryRecord>], RegistryRecord | null>(
  "registry_put",
);
export interface LibraryCard {
  mount: string;
  label: string;
  /** Tem manifesto InsertPlay: o fluxo do cartão InsertPlay cuida dele. */
  insertplay: boolean;
  /** O kernel declara o disco removível (`/sys/.../removable`), como o leitor USB. */
  removable: boolean;
}

/** Cartão de uma biblioteca Steam. `null` = fora de mídia montada ou falha no backend. */
export const libraryCard = callable<[path: string], LibraryCard | null>("library_card");
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

/** Desligado; só a pasta do usuário Windows (`steamuser`, onde ficam os saves); ou o
 *  prefixo inteiro (lento em cartão lento: o Proton recopia ~115 MB a cada abertura). */
export type PortableMode = "off" | "userdata" | "prefix";

export interface PortableStatus {
  /** Gravado no próprio cartão (`.insertplay/portable-saves.json`): vale em qualquer aparelho. */
  mode: PortableMode;
  /** Cartão ext4/btrfs/xfs. exFAT/NTFS não guardam um prefixo do Proton. */
  fsOk: boolean;
  fsType: string | null;
  reason: string | null;
}

/** Resultado por appid (a chave vem como string do JSON): ok, none, running, linked,
 *  migrated, conflict, leftover, restored, unlinked, foreign ou `error: …`. */
export type PortableResults = Record<string, string>;

/** `null` = falha no backend ou caminho recusado. */
export const portableStatus = callable<[mount: string], PortableStatus | null>("portable_status");
export const portableSet = callable<
  [mount: string, mode: PortableMode, libraries: string[], appids: number[], running: number[], elsewhere: number[]],
  { status: PortableStatus; results: PortableResults } | null
>("portable_set");
/** Cartão desligado devolve `{}`. */
export const portableSync = callable<
  [mount: string, libraries: string[], appids: number[], running: number[], elsewhere: number[]],
  PortableResults | null
>("portable_sync");
/** Jogo aberto fica com o link: o cartão pode voltar no meio da partida. */
export const portableUnlinkDangling = callable<[running: number[]], number[] | null>("portable_unlink_dangling");

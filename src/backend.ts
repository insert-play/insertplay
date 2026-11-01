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
  /** Shortcut icon, on the card. */
  icon?: string | null;
  /** Optional `game` block of the manifest (issue #24). `null` = card without it. */
  game?: CardGame | null;
}

export interface CardGame {
  platform: string | null;
  emulator: string | null;
  retroAchievements: { gameId: number } | null;
}

export interface RegistryRecord {
  appid: number;
  appName: string;
  artworkApplied?: boolean;
  /** RetroAchievements GameID of the card, kept so the shortcut page still shows the
   *  achievements with the card out (and after a Steam restart). `null` = none. */
  raGameId?: number | null;
  updatedAt?: number;
}

export interface Settings {
  autoLaunch: boolean;
  /** Hides the shortcut when the card leaves. Off, the shortcut stays visible without a badge. */
  hideRemoved: boolean;
}

export interface LogEntry {
  ts: number;
  kind: string;
  message: string;
}

/** `null` = the backend has not finished its first scan yet; it does not mean "no card". */
export const currentCards = callable<[], Card[] | null>("current_cards");
export const artworkBase64 = callable<[path: string], string>("artwork_base64");

export interface ShortcutIcon {
  /** Copy on the SSD (`~/homebrew/data/InsertPlay/icons/<guid>-<hash>-<generation>.<ext>`). */
  path: string;
  /** Apply with `SetShortcutIcon`. False when already applied or the icon is the user's. */
  apply: boolean;
  /** Current icon in `shortcuts.vdf`; `null` = shortcut not in the file yet. */
  current: string | null;
}

/** `null` = backend failure (details in the log): leave the icon alone. */
export const shortcutIcon = callable<[steamid: string, appid: number, guid: string, source: string], ShortcutIcon | null>(
  "shortcut_icon",
);
/** `null` = backend failure; never treat it as an empty registry (it would create duplicates). */
export const registryAll = callable<[], Record<string, RegistryRecord> | null>("registry_all");
export const registryPut = callable<[guid: string, record: Partial<RegistryRecord>], RegistryRecord | null>(
  "registry_put",
);
export interface LibraryCard {
  mount: string;
  label: string;
  /** Has an InsertPlay manifest: the InsertPlay card flow handles it. */
  insertplay: boolean;
  /** The kernel reports the disk as removable (`/sys/.../removable`), like the USB reader. */
  removable: boolean;
}

/** Card of a Steam library. `null` = outside mounted media or backend failure. */
export const libraryCard = callable<[path: string], LibraryCard | null>("library_card");
export const getSettings = callable<[], Settings>("get_settings");
export const setSetting = callable<[key: keyof Settings, value: boolean], Settings>("set_setting");
export const logAdd = callable<[kind: string, message: string], void>("log_add");
export const logTail = callable<[count: number], LogEntry[]>("log_tail");

/** Plugin package on the card compared with the installed version. */
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

/** `null` = backend failure (details in the event log). */
export const packageStatus = callable<[], PackageStatus | null>("package_status");
export const writePackageToCard = callable<[mount: string], CardPackage | null>("write_package_to_card");

/** Steam's achievement object (`details.achievements` of a real Steam game, measured on the Deck). */
export interface SteamAchievement {
  strID: string;
  strName: string;
  strDescription: string;
  bAchieved: boolean;
  /** Unix seconds; 0 = locked. */
  rtUnlocked: number;
  strImage: string;
  bHidden: boolean;
  flMinProgress: number;
  flCurrentProgress: number;
  flMaxProgress: number;
  /** % of RA players who have it. */
  flAchieved: number;
}

export interface RaGame {
  gameId: number;
  title: string;
  total: number;
  unlocked: number;
  achievements: SteamAchievement[];
  /** Unix seconds of the fetch. */
  fetchedAt: number;
  /** The API failed and this is the cache. */
  stale: boolean;
}

/** "invalid": wrong format; "refused": RA rejected the user/key; "network": no answer. */
export type RaLoginError = "invalid" | "refused" | "network" | "failed";

/** `user: null` = not logged in. `null` = backend failure. `refused`: RA refused the saved
 *  key (HTTP 401/403); the API stays paused until a new login (issue #34). */
export const raStatus = callable<[], { user: string | null; refused: boolean } | null>("ra_status");
export const raLogin = callable<[user: string, key: string], { user: string | null; error: RaLoginError | null }>(
  "ra_login",
);
export const raLogout = callable<[], { user: null } | null>("ra_logout");
/** `null` = nothing to show (not logged in, or no cache and the API failed). */
export const raGame = callable<[gameId: number, refresh: boolean], RaGame | null>("ra_game");

/** Off; only the Windows user folder (`steamuser`, where the saves live); or the
 *  whole prefix (slow on a slow card: Proton re-copies ~115 MB on every launch). */
export type PortableMode = "off" | "userdata" | "prefix";

export interface PortableStatus {
  /** Stored on the card itself (`.insertplay/portable-saves.json`): applies on any device. */
  mode: PortableMode;
  /** ext4/btrfs/xfs card. exFAT/NTFS cannot hold a Proton prefix. */
  fsOk: boolean;
  fsType: string | null;
  reason: string | null;
}

/** Result per appid (the key comes as a string from JSON): ok, none, running, linked,
 *  migrated, conflict, leftover, restored, unlinked, foreign or `error: …`. */
export type PortableResults = Record<string, string>;

/** `null` = backend failure or path refused. */
export const portableStatus = callable<[mount: string], PortableStatus | null>("portable_status");
export const portableSet = callable<
  [mount: string, mode: PortableMode, libraries: string[], appids: number[], running: number[], elsewhere: number[]],
  { status: PortableStatus; results: PortableResults } | null
>("portable_set");
/** A card with the mode off returns `{}`. */
export const portableSync = callable<
  [mount: string, libraries: string[], appids: number[], running: number[], elsewhere: number[]],
  PortableResults | null
>("portable_sync");
/** A running game keeps its link: the card may come back mid-session. */
export const portableUnlinkDangling = callable<[running: number[]], number[] | null>("portable_unlink_dangling");

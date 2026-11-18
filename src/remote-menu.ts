export type RemoteShortcutRemoval = (appid: number) => void;

function appidValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isInteger(value) && value > 0) return value;
  if (!value || typeof value !== "object") return null;
  const candidate = (value as { appid?: unknown; m_unAppID?: unknown }).appid
    ?? (value as { m_unAppID?: unknown }).m_unAppID;
  return typeof candidate === "number" && Number.isInteger(candidate) && candidate > 0 ? candidate : null;
}

/** Appid from a Steam tile component's props (`app`, `appid` or `overview`). */
export function remoteTileAppid(props: unknown): number | null {
  if (!props || typeof props !== "object") return null;
  const value = props as { app?: unknown; appid?: unknown; overview?: unknown };
  return appidValue(value.app) ?? appidValue(value.appid) ?? appidValue(value.overview);
}

/** Appid carried by Steam's context-menu instance/parent chain. The library grid stays on
 * `/library`, so route lookup alone cannot identify which tile opened the menu. */
export function remoteMenuAppid(instance: unknown): number | null {
  const seen = new Set<unknown>();
  let current = instance;
  for (let depth = 0; current && depth < 8 && !seen.has(current); depth++) {
    seen.add(current);
    if (typeof current !== "object") break;
    const menu = current as {
      m_rctElement?: { props?: { overview?: unknown } };
      m_parentInstance?: unknown;
    };
    const appid = appidValue(menu.m_rctElement?.props?.overview);
    if (appid !== null) return appid;
    current = menu.m_parentInstance;
  }
  return null;
}

export function remoteUninstallableAppids(
  games: ReadonlyArray<{ id: string; state: string }>,
  appids: ReadonlyMap<string, number>,
  cardGuids: ReadonlySet<string>,
): number[] {
  const out: number[] = [];
  for (const game of games) {
    const appid = appids.get(game.id);
    if (game.state === "installed" && appid !== undefined && !cardGuids.has(game.id)) out.push(appid);
  }
  return out;
}

/** Whether this exact shortcut is currently an installed remote-library game. */
export function isRemoteUninstallable(appid: number, uninstallable: ReadonlySet<number>): boolean {
  return Number.isInteger(appid) && uninstallable.has(appid);
}

/** Replaces Steam's action for our installed shortcuts only; all other appids keep the
 * original Steam action. Returns true when the interception was used. */
export function interceptRemoteShortcutRemoval(
  appid: number,
  uninstallable: ReadonlySet<number>,
  onUninstall: RemoteShortcutRemoval | undefined,
): boolean {
  if (!onUninstall || !isRemoteUninstallable(appid, uninstallable)) return false;
  onUninstall(appid);
  return true;
}

/** Steam localizes this token for both normal and non-Steam shortcuts. Replace it only
 * while the current game page belongs to an eligible remote shortcut. */
export function remoteUninstallMenuLabel(
  token: unknown,
  appid: number | null,
  uninstallable: ReadonlySet<number>,
): "Desinstalar" | null {
  if (token !== "#GameAction_RemoveShortcut" || appid === null || !isRemoteUninstallable(appid, uninstallable)) {
    return null;
  }
  return "Desinstalar";
}

/** Applies an action result to the frontend snapshot without mutating it. A failed or
 * missing result is deliberately ignored, including when Steam's shortcut was unchanged. */
export function applyRemoteGameResult<T extends { id: string }>(
  games: T[],
  result: ({ ok: true; game: T } | { ok: false; message?: string } | null | undefined),
): T[] {
  if (!result?.ok) return games;
  const index = games.findIndex((game) => game.id === result.game.id);
  if (index < 0) return games;
  const next = [...games];
  next[index] = result.game;
  return next;
}

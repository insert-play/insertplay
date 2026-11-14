import { routerHook } from "@decky/api";
import { Navigation, SidebarNavigation } from "@decky/ui";
import { currentPath, windowHistory } from "../steam";
import { CardsPage } from "./cards";
import { GeneralPage } from "./general";
import { RemotePage } from "./remote";

/** Full-screen settings route (issue #53). Measured on the Deck (2026-11-14): with
 *  `SidebarNavigation` the focus lands on the open page's sidebar item, a deep link selects
 *  that page, B in the content goes back to the sidebar and B in the sidebar goes back one
 *  history entry (D-pad on the sidebar replaces the entry; a Navigate to another category pushes). Confirm modals open over the page and keep it open. The path must
 *  not collide with Steam's or other plugins' routes. */
export const ROUTE = "/insertplay";

export type Category = "cartoes" | "biblioteca" | "geral";

function SettingsPage() {
  return (
    <SidebarNavigation
      title="InsertPlay"
      showTitle
      pages={[
        { title: "Cartões", route: `${ROUTE}/cartoes`, content: <CardsPage /> },
        { title: "Biblioteca Remota", route: `${ROUTE}/biblioteca`, content: <RemotePage /> },
        { title: "Geral", route: `${ROUTE}/geral`, content: <GeneralPage /> },
      ]}
    />
  );
}

const isOurs = (path: string | undefined | null) => path === ROUTE || Boolean(path?.startsWith(`${ROUTE}/`));

/** Our own model of the window's history from the moment the route is registered: Steam's
 *  history exposes no entries or index (see `windowHistory`). Each entry is `[key, pathname]`;
 *  a POP truncates to the entry with the same key. Used on unload to leave every route entry
 *  at once: switching pages in the sidebar or opening a category again pushes entries. */
let stack: Array<[string | undefined, string]> = [];
let unlisten: (() => void) | null = null;

function track(location: { pathname: string; key?: string }, action: string) {
  const entry: [string | undefined, string] = [location.key, location.pathname];
  if (action === "PUSH") stack.push(entry);
  else if (action === "REPLACE" && stack.length) stack[stack.length - 1] = entry;
  else {
    const at = location.key ? stack.findIndex(([key]) => key === location.key) : -1;
    stack = at >= 0 ? stack.slice(0, at + 1) : [entry];
  }
}

/** Opens the route on `category`, closing the Quick Access. With the route already open it
 *  replaces the current entry instead of stacking another one. */
export function openCategory(category: Category) {
  const path = `${ROUTE}/${category}`;
  const history = windowHistory();
  if (history && isOurs(history.location?.pathname)) history.replace(path);
  else Navigation.Navigate(path);
  Navigation.CloseSideMenus();
}

/** Registers the route; no `exact`, so the category sub-paths match. */
export function addSettingsRoute() {
  routerHook.addRoute(ROUTE, SettingsPage);
  const history = windowHistory();
  if (history) {
    stack = [[history.location?.key, history.location?.pathname]];
    unlisten = history.listen(track);
  }
}

/** Leaves the route if it is open, then removes it. Measured: removing it while open keeps the
 *  page until the next navigation, and then the path renders an empty screen; so does going
 *  back later into an entry of the removed route. Goes back past every route entry on top of
 *  the history in one step. */
export function removeSettingsRoute() {
  unlisten?.();
  unlisten = null;
  const history = windowHistory();
  if (isOurs(history?.location?.pathname ?? currentPath())) {
    let k = 0;
    while (k < stack.length && isOurs(stack[stack.length - 1 - k][1])) k++;
    try {
      // k === stack.length: the model does not reach below the route; go to the library.
      if (history && k > 0 && k < stack.length) history.go(-k);
      else Navigation.Navigate("/library/home");
    } catch {
      Navigation.Navigate("/library/home");
    }
  }
  stack = [];
  routerHook.removeRoute(ROUTE);
}

import { routerHook } from "@decky/api";
import { Navigation, SidebarNavigation } from "@decky/ui";
import { currentPath } from "../steam";
import { CardsPage } from "./cards";
import { GeneralPage } from "./general";
import { RemotePage } from "./remote";

/** Full-screen settings route (issue #53). Measured on the Deck (2026-11-14): with
 *  `SidebarNavigation` the focus lands on the open page's sidebar item, a deep link selects
 *  that page, B in the content goes back to the sidebar and B in the sidebar goes back to the
 *  previous Steam screen. Confirm modals open over the page and keep it open. The path must
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

/** Opens the route on `category`, closing the Quick Access. */
export function openCategory(category: Category) {
  Navigation.Navigate(`${ROUTE}/${category}`);
  Navigation.CloseSideMenus();
}

/** Registers the route; no `exact`, so the category sub-paths match. */
export function addSettingsRoute() {
  routerHook.addRoute(ROUTE, SettingsPage);
}

/** Leaves the route if it is open, then removes it. Measured: removing it while open keeps the
 *  page until the next navigation, and then the path renders an empty screen. */
export function removeSettingsRoute() {
  const path = currentPath();
  if (path === ROUTE || path?.startsWith(`${ROUTE}/`)) {
    try {
      Navigation.NavigateBack();
    } catch {
      Navigation.Navigate("/library/home");
    }
  }
  routerHook.removeRoute(ROUTE);
}

import { addEventListener, definePlugin, removeEventListener, routerHook } from "@decky/api";
import { staticClasses } from "@decky/ui";
import { FaSdCard } from "react-icons/fa";
import { Card, logAdd } from "./backend";
import { installAchievements, stopAchievements } from "./achievements";
import {
  onGameClosed,
  onGameStarted,
  onInserted,
  onRemoved,
  onSteamLibrariesChanged,
  portableTick,
  reconcile,
  refreshLibrary,
  remoteSteamHandlers,
  startLibrary,
  warnIfRaRefused,
} from "./flow";
import { installLibraryTabs, patchLibraryTabs, stopLibraryTabs } from "./library-tabs";
import { addSettingsRoute, removeSettingsRoute } from "./panel/route";
import { Panel } from "./panel/summary";
import { installRemoteSteam, stopRemoteSteam } from "./remote-steam";
import { notifyNewerPackage } from "./selfupdate";
import { onAppClosed, onAppStarted, onInstallFoldersChanged } from "./steam";
import { showBadges, stopBadges } from "./steam-badges";

const PORTABLE_TICK_MS = 30_000;

export default definePlugin(() => {
  const inserted = addEventListener<[Card]>("card_inserted", (card) => {
    void onInserted(card);
    void notifyNewerPackage(card.mount).catch(() => undefined);
  });
  const removed = addEventListener<[{ mount: string; label: string }]>("card_removed", (card) => void onRemoved(card));
  const raRefused = addEventListener("ra_refused", () => void warnIfRaRefused().catch(() => undefined));
  // A card with a Steam library and no InsertPlay manifest raises no backend event:
  // Steam is what notifies, for the badge and for the notification (issue #10).
  const stopFolders = onInstallFoldersChanged(() => void onSteamLibrariesChanged());
  // The library window may not exist when the plugin loads (game running, Steam
  // starting). Entering the library is the trigger to attach the badges to it.
  // Also puts the plugin's shortcuts in the "Todos os jogos" and "Instalados" tabs (#52):
  // their contents come from refreshTabs (flow.ts), after reconcile and on every event.
  installLibraryTabs();
  const libraryPatch = routerHook.addPatch("/library", (tree: unknown) => {
    showBadges();
    return patchLibraryTabs(tree);
  });
  const portableTimer = setInterval(() => void portableTick(), PORTABLE_TICK_MS);
  // Before reconcile, which publishes the achievements. A part not found is logged and the
  // rest works (issue #24). Measured: all three are found at plugin load, also right after
  // a Steam restart.
  const achievementsFailed = installAchievements();
  if (achievementsFailed.length) void logAdd("error", `achievements patch: ${achievementsFailed.join("; ")}`);
  const stopAppClosed = onAppClosed((appid) => void onGameClosed(appid));
  // save sync (#63): reports what the launch wrapper downloaded
  const stopAppStarted = onAppStarted((appid) => void onGameStarted(appid));
  // Remote library (#45): Steam's buttons on its shortcuts, and the backend's progress
  const remoteFailed = installRemoteSteam(remoteSteamHandlers);
  if (remoteFailed.length) void logAdd("error", `remote library patch: ${remoteFailed.join("; ")}`);
  const remoteState = addEventListener("remote_state", () => void refreshLibrary());
  // Full-screen settings, opened from the panel's category buttons (issue #53)
  addSettingsRoute();
  void reconcile();
  // after reconcile in the queue: a card with a library guid wins
  void startLibrary().catch(() => undefined);
  // initial library snapshot, even if Steam stops calling the callback on registration
  void onSteamLibrariesChanged();
  return {
    name: "InsertPlay",
    titleView: <div className={staticClasses.Title}>InsertPlay</div>,
    content: <Panel />,
    icon: <FaSdCard />,
    onDismount() {
      removeEventListener("card_inserted", inserted);
      removeEventListener("card_removed", removed);
      removeEventListener("ra_refused", raRefused);
      stopFolders();
      clearInterval(portableTimer);
      routerHook.removePatch("/library", libraryPatch);
      stopLibraryTabs();
      stopAppClosed();
      stopAppStarted();
      removeEventListener("remote_state", remoteState);
      stopRemoteSteam();
      stopBadges();
      stopAchievements();
      removeSettingsRoute();
    },
  };
});

import assert from "node:assert/strict";
import test from "node:test";
import {
  applyRemoteGameResult,
  interceptRemoteShortcutRemoval,
  remoteMenuAppid,
  remoteTileAppid,
  remoteUninstallMenuLabel,
  remoteUninstallableAppids,
} from "../src/remote-menu.ts";

const INSTALLED = 2691030819;
const OTHER = 1234567890;

test("focused library tile resolves the selected game's appid", () => {
  assert.equal(remoteTileAppid({ app: { appid: INSTALLED } }), INSTALLED);
  assert.equal(remoteTileAppid({ overview: { appid: INSTALLED } }), INSTALLED);
  assert.equal(remoteTileAppid({}), null);
});

test("context menu on the library grid resolves the parent game's appid", () => {
  const mainMenu = { m_rctElement: { props: { overview: { appid: INSTALLED } } } };
  const submenu = { m_parentInstance: mainMenu };
  assert.equal(remoteMenuAppid(submenu), INSTALLED);
  assert.equal(remoteMenuAppid({}), null);
});

test("menu identifies only an installed remote shortcut eligible for uninstall", () => {
  const uninstallable = new Set(
    remoteUninstallableAppids(
      [
        { id: "installed", state: "installed" },
        { id: "card", state: "installed" },
        { id: "missing", state: "not_installed" },
      ],
      new Map([
        ["installed", INSTALLED],
        ["card", OTHER],
        ["missing", 987654321],
      ]),
      new Set(["card"]),
    ),
  );
  assert.deepEqual([...uninstallable], [INSTALLED]);
  assert.equal(remoteUninstallMenuLabel("#GameAction_RemoveShortcut", INSTALLED, uninstallable), "Desinstalar");
  assert.equal(remoteUninstallMenuLabel("#GameAction_RemoveShortcut", OTHER, uninstallable), null);
  assert.equal(remoteUninstallMenuLabel("#GameAction_RemoveShortcut", 987654321, uninstallable), null);
  assert.equal(remoteUninstallMenuLabel("#GameAction_RemoveShortcut", null, uninstallable), null);
  assert.equal(remoteUninstallMenuLabel("#GameAction_RemoveGameLicense", INSTALLED, uninstallable), null);
});

test("Steam RemoveShortcut is intercepted only for an eligible remote shortcut", () => {
  const uninstallable = new Set([INSTALLED]);
  const calls = [];
  const uninstall = (appid) => calls.push(appid);

  assert.equal(interceptRemoteShortcutRemoval(INSTALLED, uninstallable, uninstall), true);
  assert.equal(interceptRemoteShortcutRemoval(OTHER, uninstallable, uninstall), false);
  assert.deepEqual(calls, [INSTALLED]);
});

test("an ok uninstall result changes the remote shortcut to not installed", () => {
  const installed = { id: "remote", state: "installed", name: "Teste" };
  const notInstalled = { ...installed, state: "not_installed" };

  const next = applyRemoteGameResult([installed], { ok: true, game: notInstalled });
  assert.notEqual(next[0], installed);
  assert.equal(next[0].state, "not_installed");
  assert.equal(installed.state, "installed", "the previous snapshot is not mutated");
});

test("a failed or missing uninstall result keeps the shortcut state unchanged", () => {
  const installed = { id: "remote", state: "installed", name: "Teste" };
  const games = [installed];

  assert.equal(applyRemoteGameResult(games, { ok: false, message: "erro" }), games);
  assert.equal(applyRemoteGameResult(games, null), games);
  assert.equal(games[0].state, "installed");
});

// Automated spike: confirms on the Deck the Steam calls that src/steam.ts uses.
// Creates an "IP Teste" shortcut, measures each call, takes screenshots for the visual part and
// always deletes the shortcut at the end. Asks no questions: runs with nobody watching.
// Usage: node scripts/spike.mjs [ip|device]   (without an argument, the default device from deck.config.json)
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { connect } from "./cdp.mjs";
import { loadConfig } from "./deck.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const arg = process.argv[2];
const host = arg && /^[\d.]+$/.test(arg) ? arg : loadConfig(arg).host;
const OPTS = "-c 'sleep 15' --guid 00000000-0000-0000-0000-000000000000";
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// Runs inside Steam. Removes leftovers from a previous run before creating another.
const CREATE = `(async () => {
  const A = SteamClient.Apps, wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const ov = (id) => appStore.GetAppOverviewByAppID(id);
  if (window.ipTeste && ov(window.ipTeste)) { A.RemoveShortcut(window.ipTeste); await wait(1000); }
  const opts = ${JSON.stringify(OPTS)};
  const r = {};
  const a = await A.AddShortcut("IP Teste", "/bin/bash", "/tmp/", opts);
  window.ipTeste = a;
  await wait(1000);
  r.appid = a;
  r.nameAfterAdd = ov(a)?.display_name ?? null;
  A.SetShortcutName(a, "IP Teste");
  A.SetShortcutLaunchOptions(a, opts);
  await wait(1000);
  r.nameAfterSet = ov(a)?.display_name ?? null;
  const png = (w, h, cor, txt) => {
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    const g = c.getContext("2d"); g.fillStyle = cor; g.fillRect(0, 0, w, h);
    g.fillStyle = "white"; g.font = Math.round(h / 6) + "px sans-serif"; g.fillText(txt, 20, h / 2);
    return c.toDataURL("image/png").split(",")[1];
  };
  r.artwork = {};
  for (const [tipo, w, h, cor, txt] of [[0,600,900,"red","COVER"],[1,1920,620,"green","HERO"],[2,640,360,"blue","LOGO"],[3,920,430,"orange","HORIZ"]]) {
    try { await A.SetCustomArtworkForApp(a, png(w, h, cor, txt), "png", tipo); r.artwork[tipo] = "ok"; }
    catch (e) { r.artwork[tipo] = "ERROR " + e; }
  }
  collectionStore.SetAppsAsHidden([a], true);  await wait(500); r.hiddenAfterTrue = collectionStore.BIsHidden(a);
  collectionStore.SetAppsAsHidden([a], false); await wait(500); r.hiddenAfterFalse = collectionStore.BIsHidden(a);
  r.gameidSteam = String(ov(a)?.m_gameid ?? "");
  r.gameidCalc = ((BigInt(a) << 32n) | 0x02000000n).toString();
  return r;
})()`;

// Starts the game and waits for Steam to report that it opened and closed (up to 40 s).
const RUN = `(async () => {
  const a = window.ipTeste, events = [];
  const sub = SteamClient.GameSessions.RegisterForAppLifetimeNotifications((e) => {
    if (e.unAppID === a) events.push(e.bRunning);
  });
  const t0 = Date.now();
  try {
    SteamClient.Apps.RunGame(String(appStore.GetAppOverviewByAppID(a).m_gameid), "", -1, 100);
    while (Date.now() - t0 < 40000 && !(events.includes(true) && events.includes(false))) {
      await new Promise((r) => setTimeout(r, 500));
    }
  } finally { sub?.unregister?.(); }
  return { started: events.includes(true), stopped: events.includes(false), seconds: Math.round((Date.now() - t0) / 1000) };
})()`;

const SET_EXE = `(() => { SteamClient.Apps.SetShortcutExe(window.ipTeste, '"/bin/bash"'); return true; })()`;

const CLEANUP = `(async () => {
  if (!window.ipTeste) return true;
  SteamClient.Apps.RemoveShortcut(window.ipTeste);
  await new Promise((r) => setTimeout(r, 1000));
  const gone = !appStore.GetAppOverviewByAppID(window.ipTeste);
  delete window.ipTeste;
  return gone;
})()`;

const navigate = (path) => `(() => { SteamUIStore.Navigate(${JSON.stringify(path)}); return true; })()`;

const isMainWindow = (t) =>
  t.type === "page" && (/big picture/i.test(t.title) || (/createflags=/.test(t.url) && !/browserviewpopup/.test(t.url)));

const screensDir = join(ROOT, "out", "screens");
async function screenshot(name) {
  const screen = await connect(host, isMainWindow);
  try {
    const { data } = await screen.send("Page.captureScreenshot", { format: "png" });
    mkdirSync(screensDir, { recursive: true });
    const file = join(screensDir, `${name}.png`);
    writeFileSync(file, Buffer.from(data, "base64"));
    return file;
  } finally {
    screen.close();
  }
}

const checks = [];
const check = (item, expected, measured, ok) =>
  checks.push({ item, expected: String(expected), measured: String(measured), ok: ok ? "OK" : "FAIL" });
const info = (item, measured) => checks.push({ item, expected: "(informational)", measured: String(measured), ok: "INFO" });
const screens = [];

let steam;
try {
  steam = await connect(host);
} catch (err) {
  console.error(`Error: ${err.message}`);
  process.exit(1);
}
console.log(`Connected to Steam at ${host}. Creating the "IP Teste" shortcut...`);
try {
  const r = await steam.evaluate(CREATE);
  check("AddShortcut returns appid", "number", r.appid, Number.isInteger(r.appid) && r.appid !== 0);
  info("name right after AddShortcut", r.nameAfterAdd);
  check("SetShortcutName", "IP Teste", r.nameAfterSet, r.nameAfterSet === "IP Teste");
  for (const [tipo, nome] of [[0, "cover"], [1, "hero"], [2, "logo"], [3, "horizontal"]]) {
    check(`SetCustomArtworkForApp type ${tipo} (${nome})`, "ok", r.artwork[tipo], r.artwork[tipo] === "ok");
  }
  check("SetAppsAsHidden(true)", true, r.hiddenAfterTrue, r.hiddenAfterTrue === true);
  check("SetAppsAsHidden(false)", false, r.hiddenAfterFalse, r.hiddenAfterFalse === false);
  check("Steam gameid = calculated", r.gameidCalc, r.gameidSteam, r.gameidSteam === r.gameidCalc);

  await steam.evaluate(navigate(`/library/app/${r.appid}`));
  await wait(3000);
  screens.push(await screenshot("spike-1-game-page"));

  console.log("Starting the game through RunGame (should stay running ~15 s)...");
  const run = await steam.evaluate(RUN);
  check("RunGame starts the game", true, run.started, run.started);
  check("game exits on its own", true, `${run.stopped} (${run.seconds}s)`, run.stopped);
  // There is no reliable read of a shortcut's options string (GetLaunchOptionsForApp
  // returns the options menu of Steam games: [] for a shortcut). The proof is the effect: only with
  // the `-c 'sleep 15'` options does /bin/bash stay running ~15 s; without them it closes at once.
  check("SetShortcutLaunchOptions applied", "game ran >= 12 s (sleep 15)", `${run.seconds}s`, run.stopped && run.seconds >= 12);

  await steam.evaluate(navigate("/library/home"));
  await wait(3000);
  screens.push(await screenshot("spike-2-home-recent"));

  check("SetShortcutExe does not throw", true, await steam.evaluate(SET_EXE), true);
} catch (err) {
  check("spike run", "no error", err.message, false);
} finally {
  const gone = await steam.evaluate(CLEANUP).catch((e) => `ERROR ${e.message}`);
  check("RemoveShortcut deletes the shortcut", true, gone, gone === true);
  steam.close();
}

console.table(checks);
for (const file of screens) console.log(`screenshot: ${file}`);
const out = join(ROOT, "out", "spike-result.json");
// the report matters precisely when the spike fails before any screenshot
// (which was what created out/)
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, JSON.stringify({ host, date: new Date().toISOString(), checks, screens }, null, 2));
console.log(`Result saved to ${out}`);
process.exit(checks.every((c) => c.ok !== "FAIL") ? 0 : 1);

// Spike automatizado: confirma no Deck as chamadas do Steam que src/steam.ts usa.
// Cria um atalho "IP Teste", mede cada chamada, tira screenshots para a parte visual e
// sempre apaga o atalho no fim. Não faz perguntas: roda sem ninguém olhando.
// Uso: node scripts/spike.mjs [ip|aparelho]   (sem argumento, o aparelho padrão do deck.config.json)
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

// Roda dentro do Steam. Remove sobra de uma execução anterior antes de criar outro.
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
  for (const [tipo, w, h, cor, txt] of [[0,600,900,"red","CAPA"],[1,1920,620,"green","HERO"],[2,640,360,"blue","LOGO"],[3,920,430,"orange","HORIZ"]]) {
    try { await A.SetCustomArtworkForApp(a, png(w, h, cor, txt), "png", tipo); r.artwork[tipo] = "ok"; }
    catch (e) { r.artwork[tipo] = "ERRO " + e; }
  }
  collectionStore.SetAppsAsHidden([a], true);  await wait(500); r.hiddenAfterTrue = collectionStore.BIsHidden(a);
  collectionStore.SetAppsAsHidden([a], false); await wait(500); r.hiddenAfterFalse = collectionStore.BIsHidden(a);
  r.gameidSteam = String(ov(a)?.m_gameid ?? "");
  r.gameidCalc = ((BigInt(a) << 32n) | 0x02000000n).toString();
  return r;
})()`;

// Inicia o jogo e espera o Steam avisar que ele abriu e fechou (até 40 s).
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
  checks.push({ item, expected: String(expected), measured: String(measured), ok: ok ? "OK" : "FALHOU" });
const info = (item, measured) => checks.push({ item, expected: "(informativo)", measured: String(measured), ok: "INFO" });
const screens = [];

let steam;
try {
  steam = await connect(host);
} catch (err) {
  console.error(`Erro: ${err.message}`);
  process.exit(1);
}
console.log(`Conectado ao Steam em ${host}. Criando o atalho "IP Teste"...`);
try {
  const r = await steam.evaluate(CREATE);
  check("AddShortcut devolve appid", "número", r.appid, Number.isInteger(r.appid) && r.appid !== 0);
  info("nome logo após AddShortcut", r.nameAfterAdd);
  check("SetShortcutName", "IP Teste", r.nameAfterSet, r.nameAfterSet === "IP Teste");
  for (const [tipo, nome] of [[0, "capa"], [1, "hero"], [2, "logo"], [3, "horizontal"]]) {
    check(`SetCustomArtworkForApp tipo ${tipo} (${nome})`, "ok", r.artwork[tipo], r.artwork[tipo] === "ok");
  }
  check("SetAppsAsHidden(true)", true, r.hiddenAfterTrue, r.hiddenAfterTrue === true);
  check("SetAppsAsHidden(false)", false, r.hiddenAfterFalse, r.hiddenAfterFalse === false);
  check("gameid Steam = calculado", r.gameidCalc, r.gameidSteam, r.gameidSteam === r.gameidCalc);

  await steam.evaluate(navigate(`/library/app/${r.appid}`));
  await wait(3000);
  screens.push(await screenshot("spike-1-pagina-do-jogo"));

  console.log("Iniciando o jogo pelo RunGame (deve ficar em execução ~15 s)...");
  const run = await steam.evaluate(RUN);
  check("RunGame inicia o jogo", true, run.started, run.started);
  check("jogo encerra sozinho", true, `${run.stopped} (${run.seconds}s)`, run.stopped);
  // Não há leitura confiável da string de opções de um atalho (GetLaunchOptionsForApp
  // devolve o menu de opções de jogos Steam: [] para atalho). A prova é o efeito: só com
  // as opções `-c 'sleep 15'` o /bin/bash fica ~15 s em execução; sem elas fecha na hora.
  check("SetShortcutLaunchOptions aplicada", "jogo rodou >= 12 s (sleep 15)", `${run.seconds}s`, run.stopped && run.seconds >= 12);

  await steam.evaluate(navigate("/library/home"));
  await wait(3000);
  screens.push(await screenshot("spike-2-inicio-recentes"));

  check("SetShortcutExe não lança erro", true, await steam.evaluate(SET_EXE), true);
} catch (err) {
  check("execução do spike", "sem erro", err.message, false);
} finally {
  const gone = await steam.evaluate(CLEANUP).catch((e) => `ERRO ${e.message}`);
  check("RemoveShortcut apaga o atalho", true, gone, gone === true);
  steam.close();
}

console.table(checks);
for (const file of screens) console.log(`screenshot: ${file}`);
const out = join(ROOT, "out", "spike-result.json");
// o relatório importa justamente quando o spike falha antes de qualquer screenshot
// (que era quem criava out/)
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, JSON.stringify({ host, date: new Date().toISOString(), checks, screens }, null, 2));
console.log(`Resultado salvo em ${out}`);
process.exit(checks.every((c) => c.ok !== "FALHOU") ? 0 : 1);

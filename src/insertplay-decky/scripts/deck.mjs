// Ferramenta de acesso ao Steam Deck para desenvolvimento (usada pela IA e por humanos).
// Lê o IP de deck.config.json. Veja CLAUDE.md para quando usar cada comando.
//
//   node scripts/deck.mjs check              diagnóstico: ssh, Steam (CEF), Decky, acesso de deploy
//   node scripts/deck.mjs eval "<js>"        roda JS dentro do Steam (SharedJSContext)
//   node scripts/deck.mjs ssh "<comando>"    roda um comando no Deck como usuário deck
//   node scripts/deck.mjs deploy [--skip-build]   build + instala o plugin + reinicia o Decky
//   node scripts/deck.mjs restart            reinicia o Decky (plugin_loader)
//   node scripts/deck.mjs remove             desinstala o plugin InsertPlay
//   node scripts/deck.mjs logs [linhas]      log do Decky filtrado pelo InsertPlay
//   node scripts/deck.mjs screenshot [nome]  captura a tela do Steam em out/screens/<nome>.png
//   node scripts/deck.mjs panel [nome]       abre o painel do InsertPlay no Quick Access e captura
//   node scripts/deck.mjs rpc <método> [args JSON...]  chama o backend do plugin pelo Decky
//   node scripts/deck.mjs setup-access       envia o instalador do acesso sem senha (ver README)
//   node scripts/deck.mjs stage-card <rótulo> [--version X]   build + grava o zip do plugin no
//                                            cartão montado (insertplay/decky/InsertPlay.zip)
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { connect } from "./cdp.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CONFIG_PATH = join(ROOT, "deck.config.json");
const STAGE = "/tmp/insertplay-decky";
const DEPLOY = "/etc/insertplay-dev/deploy-plugin.sh";

export function loadConfig() {
  if (!existsSync(CONFIG_PATH)) {
    throw new Error(`crie ${CONFIG_PATH} a partir de deck.config.example.json com o IP do Deck`);
  }
  const cfg = JSON.parse(readFileSync(CONFIG_PATH, "utf8"));
  if (!cfg.host) throw new Error(`"host" ausente em ${CONFIG_PATH}`);
  return { user: "deck", ...cfg };
}

/** ssh/scp devolvem 255 quando a conexão falha. O Wi-Fi do Deck às vezes dorme e o
 *  primeiro pacote dá timeout, então repetimos uma vez antes de desistir. */
function run(cmd, args, { capture = false, input } = {}) {
  for (let attempt = 1; ; attempt++) {
    const r = spawnSync(cmd, args, {
      cwd: ROOT,
      encoding: "utf8",
      input,
      stdio: capture || input !== undefined ? ["pipe", "pipe", "pipe"] : "inherit",
      shell: false,
    });
    if (r.error) throw r.error;
    const result = { code: r.status ?? 1, out: (r.stdout ?? "").trim(), err: (r.stderr ?? "").trim() };
    if (result.code === 255 && attempt === 1) {
      spawnSync(process.execPath, ["-e", "setTimeout(() => {}, 2000)"]);
      continue;
    }
    if (input !== undefined && !capture) {
      if (result.out) console.log(result.out);
      if (result.err) console.error(result.err);
    }
    return result;
  }
}

const sshArgs = (cfg, extra = []) => ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8", ...extra, `${cfg.user}@${cfg.host}`];

/** Comando remoto sem TTY e sem senha: falha em vez de travar esperando input. */
function ssh(cfg, script, opts = {}) {
  return run("ssh", [...sshArgs(cfg), "bash -s"], { ...opts, input: script });
}

function npx(args) {
  const r = spawnSync("npx", ["-y", "pnpm@9", ...args], { cwd: ROOT, encoding: "utf8", shell: process.platform === "win32" });
  return { code: r.status ?? 1, out: `${r.stdout ?? ""}${r.stderr ?? ""}` };
}

async function check(cfg) {
  const rows = [];
  const add = (item, ok, detail) => rows.push({ item, status: ok ? "OK" : "FALHOU", detail });
  const s = ssh(cfg, `
    echo "os=$(grep ^VERSION_ID= /etc/os-release | cut -d= -f2) device=$(cat /sys/devices/virtual/dmi/id/product_name)"
    echo "loader=$(systemctl is-active plugin_loader)"
    echo "plugin=$( [ -f ~/homebrew/plugins/InsertPlay/plugin.json ] && echo instalado || echo ausente)"
    echo "deploy=$(sudo -n ${DEPLOY} >/dev/null 2>&1; [ $? -eq 2 ] && echo liberado || echo sem-acesso)"
    echo "old_service=$(systemctl is-enabled insertplay.service 2>/dev/null || echo ausente)"
  `, { capture: true });
  const facts = Object.fromEntries(s.out.split(/\s+/).filter((x) => x.includes("=")).map((x) => x.split("=")));
  add("ssh sem senha", s.code === 0, s.code === 0 ? `SteamOS ${facts.os} (${facts.device})` : s.err);
  if (s.code === 0) {
    add("Decky (plugin_loader)", facts.loader === "active", facts.loader);
    add("deploy sem senha", facts.deploy === "liberado", facts.deploy === "liberado" ? DEPLOY : "rode o setup-access (README)");
    add("plugin InsertPlay", true, facts.plugin);
    add("serviço antigo desligado", facts.old_service !== "enabled", facts.old_service);
  }
  try {
    const steam = await connect(cfg.host);
    const info = await steam.evaluate(
      "({ steam: typeof SteamClient, decky: typeof DeckyPluginLoader, apps: appStore.allApps.length })",
    );
    steam.close();
    add("Steam via CEF :8081", info.steam === "object", `${info.apps} apps, Decky frontend ${info.decky}`);
  } catch (err) {
    add("Steam via CEF :8081", false, err.message);
  }
  console.table(rows);
  return rows.every((r) => r.status === "OK") ? 0 : 1;
}

/** Build (opcional) e envio dos arquivos do plugin para o stage no Deck. */
function upload(cfg, skipBuild) {
  if (!skipBuild) {
    const b = npx(["build"]);
    if (b.code !== 0 || b.out.includes("plugin typescript")) {
      console.error(b.out);
      console.error("Build falhou ou tem erros de TypeScript; nada foi enviado.");
      return 1;
    }
    console.log("build ok");
  }
  if (ssh(cfg, `rm -rf ${STAGE} && mkdir -p ${STAGE}/dist`).code !== 0) return 1;
  // mesma lista de PACKAGE_ENTRIES (py_modules/insertplay_decky/plugin_package.py); dist vai abaixo
  const files = ["plugin.json", "package.json", "main.py", "NOTICE", "py_modules"];
  const target = `${cfg.user}@${cfg.host}`;
  if (run("scp", ["-q", "-r", "-o", "BatchMode=yes", ...files, `${target}:${STAGE}/`]).code !== 0) return 1;
  if (run("scp", ["-q", "-o", "BatchMode=yes", "dist/index.js", `${target}:${STAGE}/dist/`]).code !== 0) return 1;
  ssh(cfg, `find ${STAGE} -name __pycache__ -type d -prune -exec rm -rf {} +`);
  return 0;
}

function deploy(cfg, skipBuild) {
  if (upload(cfg, skipBuild) !== 0) return 1;
  const target = `${cfg.user}@${cfg.host}`;
  const viaSudo = ssh(cfg, `sudo -n ${DEPLOY} deploy`);
  if (viaSudo.code === 0) return 0;
  // Sem o acesso liberado: pede a senha num terminal interativo (uso humano).
  if (!process.stdin.isTTY) {
    console.error("deploy sem senha não configurado; rode o setup-access (README) ou faça o deploy num terminal.");
    return 1;
  }
  return run("ssh", ["-t", target,
    `sudo rsync -a --delete ${STAGE}/ ~/homebrew/plugins/InsertPlay/ && sudo systemctl restart plugin_loader`]).code;
}

/** Grava o zip do plugin no cartão, como o build-sd-card.sh faria, usando o mesmo
 *  write_card_package do plugin (valida o zip antes de substituir o anterior).
 *  `--version` troca a versão só no zip: serve para testar "cartão mais novo". */
function stageCard(cfg, label, version) {
  if (!label || label.startsWith("--")) throw new Error("uso: deck.mjs stage-card <rótulo do cartão> [--version X]");
  if (upload(cfg, false) !== 0) return 1;
  return ssh(cfg, `python3 - <<'PY'
import json, os, sys
sys.path.insert(0, ${JSON.stringify(STAGE + "/py_modules")})
from insertplay_decky.plugin_package import card_package_path, write_card_package
mount = os.path.join("/run/media/deck", ${JSON.stringify(label)})
if not os.path.isdir(mount):
    sys.exit(f"cartão {mount} não está montado")
version = ${JSON.stringify(version ?? "")}
if version:
    path = ${JSON.stringify(STAGE + "/package.json")}
    data = json.load(open(path))
    data["version"] = version
    json.dump(data, open(path, "w"), indent=2)
info = write_card_package(${JSON.stringify(STAGE)}, mount)
print(f"{card_package_path(mount)}: v{info['version']} sha256={info['sha256'][:12]} {info['size']} bytes")
PY`).code;
}

function setupAccess(cfg) {
  const target = `${cfg.user}@${cfg.host}`;
  if (ssh(cfg, "rm -rf /tmp/insertplay-deck-setup").code !== 0) return 1;
  if (run("scp", ["-q", "-r", "-o", "BatchMode=yes", "scripts/deck-setup", `${target}:/tmp/insertplay-deck-setup`]).code !== 0) return 1;
  console.log("Instalador enviado. Agora rode (vai pedir a senha do deck):");
  console.log(`  ssh -t ${target} "sudo bash /tmp/insertplay-deck-setup/install-dev-access.sh"`);
  return 0;
}

/** A janela principal do Steam. O título é traduzido ("Steam — Modo Big Picture"),
 *  então procuramos por "Big Picture" ou pela janela que não é popup. */
const isMainWindow = (t) =>
  t.type === "page" && (/big picture/i.test(t.title) || (/createflags=/.test(t.url) && !/browserviewpopup/.test(t.url)));

const MAIN_WINDOW = "(SteamUIStore.m_WindowStore ?? SteamUIStore.WindowStore).SteamUIWindows[0]";
const DECKY_TAB = 999;

/** O Quick Access é outra janela do CEF (QuickAccess_uid2); a captura da janela
 *  principal não o inclui. */
async function panel(cfg, name) {
  const steam = await connect(cfg.host);
  try {
    await steam.evaluate(`(async () => {
      ${MAIN_WINDOW}.MenuStore.OpenQuickAccessMenu(${DECKY_TAB});
      await new Promise((r) => setTimeout(r, 800));
      DeckyPluginLoader.deckyState.setActivePlugin("InsertPlay");
      await new Promise((r) => setTimeout(r, 1500));
    })()`);
  } finally {
    steam.close();
  }
  return screenshot(cfg, name ?? "painel", (t) => t.title.startsWith("QuickAccess"));
}

async function screenshot(cfg, name, target = isMainWindow) {
  const steam = await connect(cfg.host, target);
  try {
    const { data } = await steam.send("Page.captureScreenshot", { format: "png" });
    const file = join(ROOT, "out", "screens", `${name ?? new Date().toISOString().replace(/[:.]/g, "-")}.png`);
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, Buffer.from(data, "base64"));
    console.log(file);
  } finally {
    steam.close();
  }
  return 0;
}

async function main() {
  const [command, ...rest] = process.argv.slice(2);
  const cfg = loadConfig();
  switch (command) {
    case "check":
      return check(cfg);
    case "eval": {
      if (!rest[0]) throw new Error('uso: deck.mjs eval "<js>"');
      const steam = await connect(cfg.host);
      try {
        console.log(JSON.stringify(await steam.evaluate(rest[0]), null, 2));
      } finally {
        steam.close();
      }
      return 0;
    }
    case "ssh":
      if (!rest[0]) throw new Error('uso: deck.mjs ssh "<comando>"');
      return ssh(cfg, rest.join(" ")).code;
    case "deploy":
      return deploy(cfg, rest.includes("--skip-build"));
    case "restart":
    case "remove":
      return ssh(cfg, `sudo -n ${DEPLOY} ${command}`).code;
    case "logs": {
      const n = Number(rest[0] ?? 80);
      return ssh(cfg, `journalctl -u plugin_loader -n 4000 --no-pager -o short-iso | grep -i insertplay | tail -n ${n}`).code;
    }
    case "setup-access":
      return setupAccess(cfg);
    case "stage-card": {
      const i = rest.indexOf("--version");
      if (i >= 0 && (!rest[i + 1] || rest[i + 1].startsWith("--"))) throw new Error("--version precisa de um valor, ex.: --version 0.3.0");
      return stageCard(cfg, rest[0], i >= 0 ? rest[i + 1] : undefined);
    }
    case "screenshot":
      return screenshot(cfg, rest[0]);
    case "panel":
      return panel(cfg, rest[0]);
    case "rpc": {
      if (!rest[0]) throw new Error("uso: deck.mjs rpc <método> [args JSON...]");
      const args = rest.slice(1).map((a) => JSON.parse(a));
      const steam = await connect(cfg.host);
      try {
        // argumentos ESPALHADOS: passar uma lista vira um único argumento a mais (medido)
        const call = `window.DeckyBackend.call("loader/call_plugin_method", "InsertPlay", ${JSON.stringify(rest[0])}, ...${JSON.stringify(args)})`;
        console.log(JSON.stringify(await steam.evaluate(call), null, 2));
      } finally {
        steam.close();
      }
      return 0;
    }
    default:
      console.error("uso: node scripts/deck.mjs check|eval|ssh|deploy|restart|remove|logs|setup-access|stage-card|screenshot|panel|rpc");
      return 2;
  }
}

main().then(
  (code) => process.exit(code ?? 0),
  (err) => {
    console.error(`Erro: ${err.message}`);
    process.exit(1);
  },
);

// Ferramenta de acesso aos aparelhos de teste (usada pela IA e por humanos).
// Lê os aparelhos de deck.config.json. Veja CLAUDE.md para quando usar cada comando.
//
// Aparelho: `--device <nome>` (em qualquer posição) ou INSERTPLAY_DEVICE; sem isso, o
// "default" do config (o Steam Deck de dev). Em aparelho com "role": "prod" os comandos
// que mudam o aparelho (deploy, remove, restart, stage-card, setup-access, legacy-uninstall) exigem
// `--confirm-prod`.
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
//   node scripts/deck.mjs legacy-uninstall   envia o desinstalador do InsertPlay legado (CLI + serviço)
//   node scripts/deck.mjs stage-card <rótulo> [--version X]   build + grava o zip do plugin no
//                                            cartão montado (insertplay/decky/InsertPlay.zip)
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { connect } from "./cdp.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CONFIG_PATH = join(ROOT, "deck.config.json");
const STAGE = "/tmp/insertplay-decky";
const DEPLOY = "/etc/insertplay-dev/deploy-plugin.sh";

const MUTATING = new Set(["deploy", "remove", "restart", "stage-card", "setup-access", "legacy-uninstall"]);

/** Config de um aparelho: `{ name, host, user, role }`. Aceita o formato antigo
 *  (`{ host, user }` na raiz), tratado como o aparelho "deck". */
export function loadConfig(device = process.env.INSERTPLAY_DEVICE) {
  if (!existsSync(CONFIG_PATH)) {
    throw new Error(`crie ${CONFIG_PATH} a partir de deck.config.example.json com o IP do Deck`);
  }
  const raw = JSON.parse(readFileSync(CONFIG_PATH, "utf8"));
  const devices = raw.devices ?? { deck: { host: raw.host, user: raw.user, role: "dev" } };
  const name = device ?? raw.default ?? Object.keys(devices)[0];
  const cfg = devices[name];
  if (!cfg) throw new Error(`aparelho "${name}" não existe em ${CONFIG_PATH} (há: ${Object.keys(devices).join(", ")})`);
  if (!cfg.host) throw new Error(`"host" ausente para o aparelho "${name}" em ${CONFIG_PATH}`);
  // padrões depois do spread: no formato antigo `user` vem como undefined explícito
  // e, antes, sobrescrevia o "deck" (review do PR #7)
  return { ...cfg, user: cfg.user ?? "deck", role: cfg.role ?? "dev", name };
}

/** Tira `--device X` e `--confirm-prod` dos argumentos. */
function takeGlobalFlags(argv) {
  const args = [...argv];
  let device;
  const i = args.indexOf("--device");
  if (i >= 0) {
    device = args[i + 1];
    if (!device || device.startsWith("--")) throw new Error("--device precisa de um nome, ex.: --device machine");
    args.splice(i, 2);
  }
  const j = args.indexOf("--confirm-prod");
  const confirmProd = j >= 0;
  if (confirmProd) args.splice(j, 1);
  return { args, device, confirmProd };
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

/** Atalhos do InsertPlay em cada conta Steam do aparelho, lidos (só leitura) do shortcuts.vdf.
 *  O plugin cria atalhos pelo Steam, que grava o exe entre aspas, com opções terminando em
 *  `--guid <uuid>`; o CLI legado gravava o vdf direto, com o exe sem aspas. Legado é o que
 *  menciona InsertPlay ou `--guid` fora desse formato, ou aponta para ~/.local/share/insertplay.
 *  Atalhos no formato do plugin cujo appid não está no registro (ex.: criados em outra conta
 *  Steam do mesmo aparelho) saem à parte. Imprime `<legado> <fora-do-registro>`, cada um
 *  como `<userdata>:<qtd>` separados por vírgula, ou `-`. */
const LEGACY_SHORTCUTS_PY = String.raw`
import glob, json, os, re, struct
home = os.path.expanduser("~")
PLUGIN_OPTS = re.compile(r"--guid [0-9a-f-]{36}$")
try:
    owned = {r.get("appid") for r in json.load(open(home + "/homebrew/settings/InsertPlay/registry.json")).values()}
except (OSError, ValueError):
    owned = set()
def parse(buf):
    i = buf.index(b"\0", 1) + 1
    def cstr():
        nonlocal i
        j = buf.index(b"\0", i); s = buf[i:j].decode("utf-8", "replace"); i = j + 1; return s
    def obj():
        nonlocal i
        d = {}
        while True:
            t = buf[i]; i += 1
            if t == 8: return d
            k = cstr().lower()
            if t == 0: d[k] = obj()
            elif t == 1: d[k] = cstr()
            elif t == 2: d[k] = struct.unpack_from("<I", buf, i)[0]; i += 4
            else: raise ValueError(t)
    return obj()
legacy, orphans = [], []
for path in sorted(glob.glob(home + "/.local/share/Steam/userdata/*/config/shortcuts.vdf")):
    uid = path.split("/")[-3]
    try:
        shortcuts = parse(open(path, "rb").read()).values()
    except Exception:
        legacy.append(uid + ":ilegivel"); continue
    n_legacy = n_orphan = 0
    for s in shortcuts:
        exe, opts = s.get("exe", ""), s.get("launchoptions", "").strip()
        blob = (exe + " " + opts).lower()
        if "insertplay" not in blob and "--guid" not in opts:
            continue
        if exe.startswith('"') and PLUGIN_OPTS.search(opts) and "/.local/share/insertplay/" not in blob:
            n_orphan += s.get("appid") not in owned
        else:
            n_legacy += 1
    if n_legacy: legacy.append(f"{uid}:{n_legacy}")
    if n_orphan: orphans.append(f"{uid}:{n_orphan}")
print(",".join(legacy) or "-", ",".join(orphans) or "-")`;

async function check(cfg) {
  const rows = [];
  const add = (item, ok, detail) => rows.push({ item, status: ok ? "OK" : "FALHOU", detail });
  const s = ssh(cfg, `
    echo "os=$(grep ^VERSION_ID= /etc/os-release | cut -d= -f2) device=$(cat /sys/devices/virtual/dmi/id/product_name)"
    echo "loader=$(systemctl is-active plugin_loader)"
    echo "plugin=$( [ -f ~/homebrew/plugins/InsertPlay/plugin.json ] && echo instalado || echo ausente)"
    echo "deploy=$(sudo -n ${DEPLOY} >/dev/null 2>&1; [ $? -eq 2 ] && echo liberado || echo sem-acesso)"
    legacy=""
    # o mesmo inventário do uninstall.sh; -L pega link quebrado
    for p in /etc/systemd/system/insertplay.service /etc/systemd/system/multi-user.target.wants/insertplay.service \\
        /tmp/sd-monitor.lock ~/.local/share/insertplay ~/.local/share/insertplay.update.lock \\
        ~/.local/bin/insertplay ~/.config/insertplay ~/.local/state/insertplay; do
      { [ -e "$p" ] || [ -L "$p" ]; } && legacy="$legacy,\${p/#$HOME/\\~}"
    done
    state=$(systemctl show -p ActiveState --value insertplay.service 2>/dev/null)
    case "$state" in ""|inactive) ;; *) legacy="$legacy,insertplay.service($state)" ;; esac
    # /etc/polkit-1/rules.d só abre para o root: sem acesso, a regra não é verificável daqui
    polkit=/etc/polkit-1/rules.d/49-insertplay-controller-sleep.rules
    if [ -x /etc/polkit-1/rules.d ]; then
      [ -e "$polkit" ] && legacy="$legacy,$polkit"
      echo "legacy_polkit=verificado"
    else
      echo "legacy_polkit=sem-acesso"
    fi
    echo "legacy_files=\${legacy#,}"
    read -r legacy_shortcuts orphan_shortcuts <<EOF
$(python3 - <<'PY'
${LEGACY_SHORTCUTS_PY}
PY
)
EOF
    echo "legacy_shortcuts=$legacy_shortcuts orphan_shortcuts=$orphan_shortcuts"
  `, { capture: true });
  const facts = Object.fromEntries(s.out.split(/\s+/).filter((x) => x.includes("=")).map((x) => x.split("=")));
  add("ssh sem senha", s.code === 0, s.code === 0 ? `SteamOS ${facts.os} (${facts.device})` : s.err);
  if (s.code === 0) {
    add("Decky (plugin_loader)", facts.loader === "active", facts.loader);
    if (facts.deploy === "liberado") add("deploy sem senha", true, DEPLOY);
    // em prod o plugin chega pelo fluxo real (zip do cartão); sem a liberação é o esperado
    else if (cfg.role === "prod") add("deploy sem senha", true, "não usado em prod");
    else add("deploy sem senha", false, "rode o setup-access (README)");
    add("plugin InsertPlay", true, facts.plugin);
    const polkitNote = facts.legacy_polkit === "verificado" ? "" : " (regra de polkit só se vê como root)";
    add("InsertPlay legado ausente", !facts.legacy_files, (facts.legacy_files || "nenhum arquivo do legado") + polkitNote);
    add("atalhos legados ausentes", facts.legacy_shortcuts === "-", facts.legacy_shortcuts === "-" ? "nenhum (todas as contas)" : facts.legacy_shortcuts);
    // informativo: atalho do plugin que o registro não conhece (outra conta Steam, issue aberta)
    if (facts.orphan_shortcuts && facts.orphan_shortcuts !== "-") {
      add("atalhos do plugin fora do registro", true, facts.orphan_shortcuts);
    }
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

/** Envia o uninstall.sh do CLI legado; quem roda com sudo é o usuário (em prod não há
 *  liberação de sudo). Os atalhos legados saem pelo Steam, não por este script. */
function legacyUninstall(cfg) {
  const target = `${cfg.user}@${cfg.host}`;
  const remote = "/tmp/insertplay-legacy-uninstall.sh";
  const script = join(ROOT, "..", "insertplay-cli", "assets", "installer", "uninstall.sh");
  if (run("scp", ["-q", "-o", "BatchMode=yes", script, `${target}:${remote}`]).code !== 0) return 1;
  console.log("Desinstalador enviado. Agora rode (vai pedir a senha do deck):");
  console.log(`  ssh -t ${target} "sudo bash ${remote} --purge"`);
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
  const { args, device, confirmProd } = takeGlobalFlags(process.argv.slice(2));
  const [command, ...rest] = args;
  const cfg = loadConfig(device);
  // stderr: não atrapalha quem lê a saída JSON do eval/rpc
  console.error(`[${cfg.name} · ${cfg.role} · ${cfg.host}]`);
  if (cfg.role === "prod" && MUTATING.has(command) && !confirmProd) {
    console.error(`"${command}" muda o aparelho de produção "${cfg.name}". Combine com o usuário e repita com --confirm-prod.`);
    return 3;
  }
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
    case "legacy-uninstall":
      return legacyUninstall(cfg);
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
      console.error("uso: node scripts/deck.mjs check|eval|ssh|deploy|restart|remove|logs|setup-access|legacy-uninstall|stage-card|screenshot|panel|rpc");
      return 2;
  }
}

// só como comando: o spike.mjs importa o loadConfig daqui
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().then(
    (code) => process.exit(code ?? 0),
    (err) => {
      console.error(`Erro: ${err.message}`);
      process.exit(1);
    },
  );
}

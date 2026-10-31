// Access tool for the test devices (used by the AI and by humans).
// Reads the devices from deck.config.json. See CLAUDE.md for when to use each command.
//
// Device: `--device <name>` (in any position) or INSERTPLAY_DEVICE; without either, the
// config "default" (the dev Steam Deck). On a device with "role": "prod" the commands
// that change the device (deploy, remove, restart, stage-card, setup-access) require
// `--confirm-prod`.
//
//   node scripts/deck.mjs check              diagnostics: ssh, Steam (CEF), Decky, deploy access
//   node scripts/deck.mjs eval "<js>"        runs JS inside Steam (SharedJSContext)
//   node scripts/deck.mjs ssh "<command>"    runs a command on the Deck as the deck user
//   node scripts/deck.mjs deploy [--skip-build]   build + install the plugin + restart Decky
//   node scripts/deck.mjs restart            restarts Decky (plugin_loader)
//   node scripts/deck.mjs remove             uninstalls the InsertPlay plugin
//   node scripts/deck.mjs logs [lines]       Decky log filtered by InsertPlay
//   node scripts/deck.mjs screenshot [name]  captures the Steam screen to out/screens/<name>.png
//   node scripts/deck.mjs panel [name]       opens the InsertPlay panel in Quick Access and captures it
//   node scripts/deck.mjs toasts [name]      captures the toast popup window (notificationtoasts)
//   node scripts/deck.mjs rpc <method> [JSON args...]  calls the plugin backend through Decky
//   node scripts/deck.mjs setup-access       sends the passwordless access installer (see docs/development.md)
//   node scripts/deck.mjs stage-card <label> [--version X]   build + write the plugin zip to the
//                                            mounted card (insertplay/decky/InsertPlay.zip)
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { connect } from "./cdp.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CONFIG_PATH = join(ROOT, "deck.config.json");
const STAGE = "/tmp/insertplay-decky";
const DEPLOY = "/etc/insertplay-dev/deploy-plugin.sh";

const MUTATING = new Set(["deploy", "remove", "restart", "stage-card", "setup-access"]);

/** Config of a device: `{ name, host, user, role }`. Accepts the old format
 *  (`{ host, user }` at the root), treated as the "deck" device. */
export function loadConfig(device = process.env.INSERTPLAY_DEVICE) {
  if (!existsSync(CONFIG_PATH)) {
    throw new Error(`create ${CONFIG_PATH} from deck.config.example.json with the Deck's IP`);
  }
  const raw = JSON.parse(readFileSync(CONFIG_PATH, "utf8"));
  const devices = raw.devices ?? { deck: { host: raw.host, user: raw.user, role: "dev" } };
  const name = device ?? raw.default ?? Object.keys(devices)[0];
  const cfg = devices[name];
  if (!cfg) throw new Error(`device "${name}" does not exist in ${CONFIG_PATH} (available: ${Object.keys(devices).join(", ")})`);
  if (!cfg.host) throw new Error(`"host" missing for device "${name}" in ${CONFIG_PATH}`);
  // defaults after the spread: in the old format `user` comes as an explicit undefined
  // and, before, overwrote the "deck" (PR #7 review)
  return { ...cfg, user: cfg.user ?? "deck", role: cfg.role ?? "dev", name };
}

/** Strips `--device X` and `--confirm-prod` from the arguments. */
function takeGlobalFlags(argv) {
  const args = [...argv];
  let device;
  const i = args.indexOf("--device");
  if (i >= 0) {
    device = args[i + 1];
    if (!device || device.startsWith("--")) throw new Error("--device needs a name, e.g.: --device machine");
    args.splice(i, 2);
  }
  const j = args.indexOf("--confirm-prod");
  const confirmProd = j >= 0;
  if (confirmProd) args.splice(j, 1);
  return { args, device, confirmProd };
}

/** ssh/scp return 255 when the connection fails. The Deck's Wi-Fi sometimes sleeps and the
 *  first packet times out, so we retry once before giving up. */
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

/** Remote command without TTY and without password: fails instead of hanging waiting for input. */
function ssh(cfg, script, opts = {}) {
  return run("ssh", [...sshArgs(cfg), "bash -s"], { ...opts, input: script });
}

function npx(args) {
  const r = spawnSync("npx", ["-y", "pnpm@9", ...args], { cwd: ROOT, encoding: "utf8", shell: process.platform === "win32" });
  return { code: r.status ?? 1, out: `${r.stdout ?? ""}${r.stderr ?? ""}` };
}

/** Plugin shortcuts the registry doesn't know, per Steam account on the device, read
 *  (read-only) from shortcuts.vdf. The plugin creates shortcuts through Steam, which stores the
 *  exe in quotes, with launch options ending in `--guid <uuid>`. A shortcut in that format whose
 *  appid is missing from the registry was usually created under another Steam account of the
 *  same device (issue #8). Prints `<userdata>:<count>` entries separated by commas, or `-`. */
const ORPHAN_SHORTCUTS_PY = String.raw`
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
orphans = []
for path in sorted(glob.glob(home + "/.local/share/Steam/userdata/*/config/shortcuts.vdf")):
    uid = path.split("/")[-3]
    try:
        shortcuts = parse(open(path, "rb").read()).values()
    except Exception:
        orphans.append(uid + ":unreadable"); continue
    n_orphan = 0
    for s in shortcuts:
        exe, opts = s.get("exe", ""), s.get("launchoptions", "").strip()
        if exe.startswith('"') and PLUGIN_OPTS.search(opts):
            n_orphan += s.get("appid") not in owned
    if n_orphan: orphans.append(f"{uid}:{n_orphan}")
print(",".join(orphans) or "-")`;

async function check(cfg) {
  const rows = [];
  const add = (item, ok, detail) => rows.push({ item, status: ok ? "OK" : "FAIL", detail });
  const s = ssh(cfg, `
    echo "os=$(grep ^VERSION_ID= /etc/os-release | cut -d= -f2) device=$(cat /sys/devices/virtual/dmi/id/product_name)"
    echo "loader=$(systemctl is-active plugin_loader)"
    echo "plugin=$( [ -f ~/homebrew/plugins/InsertPlay/plugin.json ] && echo installed || echo missing)"
    echo "deploy=$(sudo -n ${DEPLOY} >/dev/null 2>&1; [ $? -eq 2 ] && echo granted || echo no-access)"
    echo "orphan_shortcuts=$(python3 - <<'PY'
${ORPHAN_SHORTCUTS_PY}
PY
)"
  `, { capture: true });
  const facts = Object.fromEntries(s.out.split(/\s+/).filter((x) => x.includes("=")).map((x) => x.split("=")));
  add("passwordless ssh", s.code === 0, s.code === 0 ? `SteamOS ${facts.os} (${facts.device})` : s.err);
  if (s.code === 0) {
    add("Decky (plugin_loader)", facts.loader === "active", facts.loader);
    if (facts.deploy === "granted") add("passwordless deploy", true, DEPLOY);
    // in prod the plugin arrives through the real flow (card zip); no grant is expected
    else if (cfg.role === "prod") add("passwordless deploy", true, "not used in prod");
    else add("passwordless deploy", false, "run setup-access (docs/development.md)");
    add("InsertPlay plugin", true, facts.plugin);
    // informational: plugin shortcut the registry doesn't know (another Steam account, open issue)
    if (facts.orphan_shortcuts && facts.orphan_shortcuts !== "-") {
      add("plugin shortcuts missing from the registry", true, facts.orphan_shortcuts);
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

/** Build (optional) and upload of the plugin files to the stage on the Deck. */
function upload(cfg, skipBuild) {
  if (!skipBuild) {
    const b = npx(["build"]);
    if (b.code !== 0 || b.out.includes("plugin typescript")) {
      console.error(b.out);
      console.error("Build failed or has TypeScript errors; nothing was uploaded.");
      return 1;
    }
    console.log("build ok");
  }
  if (ssh(cfg, `rm -rf ${STAGE} && mkdir -p ${STAGE}/dist`).code !== 0) return 1;
  // same list as PACKAGE_ENTRIES (py_modules/insertplay_decky/plugin_package.py); dist goes below
  const files = ["plugin.json", "package.json", "main.py", "LICENSE", "NOTICE", "py_modules"];
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
  // Without the access granted: asks for the password in an interactive terminal (human use).
  if (!process.stdin.isTTY) {
    console.error("passwordless deploy not configured; run setup-access (docs/development.md) or deploy from a terminal.");
    return 1;
  }
  return run("ssh", ["-t", target,
    `sudo rsync -a --delete ${STAGE}/ ~/homebrew/plugins/InsertPlay/ && sudo systemctl restart plugin_loader`]).code;
}

/** Writes the plugin zip to the card using the plugin's same
 *  write_card_package (validates the zip before replacing the previous one).
 *  `--version` changes the version only in the zip: used to test "newer card". */
function stageCard(cfg, label, version) {
  if (!label || label.startsWith("--")) throw new Error("usage: deck.mjs stage-card <card label> [--version X]");
  if (upload(cfg, false) !== 0) return 1;
  return ssh(cfg, `python3 - <<'PY'
import json, os, sys
sys.path.insert(0, ${JSON.stringify(STAGE + "/py_modules")})
from insertplay_decky.plugin_package import card_package_path, write_card_package
mount = os.path.join("/run/media/deck", ${JSON.stringify(label)})
if not os.path.isdir(mount):
    sys.exit(f"card {mount} is not mounted")
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
  console.log("Installer sent. Now run (it will ask for the deck password):");
  console.log(`  ssh -t ${target} "sudo bash /tmp/insertplay-deck-setup/install-dev-access.sh"`);
  return 0;
}

/** The main Steam window. The title is localized ("Steam — Modo Big Picture"),
 *  so we look for "Big Picture" or for the window that is not a popup. */
const isMainWindow = (t) =>
  t.type === "page" && (/big picture/i.test(t.title) || (/createflags=/.test(t.url) && !/browserviewpopup/.test(t.url)));

const MAIN_WINDOW = "(SteamUIStore.m_WindowStore ?? SteamUIStore.WindowStore).SteamUIWindows[0]";
const DECKY_TAB = 999;

/** Quick Access is another CEF window (QuickAccess_uid2); the capture of the main
 *  window does not include it. */
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
  return screenshot(cfg, name ?? "panel", (t) => t.title.startsWith("QuickAccess"));
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
  // stderr: doesn't get in the way of whoever reads the JSON output of eval/rpc
  console.error(`[${cfg.name} · ${cfg.role} · ${cfg.host}]`);
  if (cfg.role === "prod" && MUTATING.has(command) && !confirmProd) {
    console.error(`"${command}" changes the production device "${cfg.name}". Agree with the user and repeat with --confirm-prod.`);
    return 3;
  }
  switch (command) {
    case "check":
      return check(cfg);
    case "eval": {
      if (!rest[0]) throw new Error('usage: deck.mjs eval "<js>"');
      const steam = await connect(cfg.host);
      try {
        console.log(JSON.stringify(await steam.evaluate(rest[0]), null, 2));
      } finally {
        steam.close();
      }
      return 0;
    }
    case "ssh":
      if (!rest[0]) throw new Error('usage: deck.mjs ssh "<command>"');
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
      if (i >= 0 && (!rest[i + 1] || rest[i + 1].startsWith("--"))) throw new Error("--version needs a value, e.g.: --version 0.3.0");
      return stageCard(cfg, rest[0], i >= 0 ? rest[i + 1] : undefined);
    }
    case "screenshot":
      return screenshot(cfg, rest[0]);
    case "panel":
      return panel(cfg, rest[0]);
    case "toasts":
      // toasts are drawn in their own CEF window; the plain screenshot misses them
      return screenshot(cfg, rest[0] ?? "toasts", (t) => t.title.startsWith("notificationtoasts"));
    case "rpc": {
      if (!rest[0]) throw new Error("usage: deck.mjs rpc <method> [JSON args...]");
      const args = rest.slice(1).map((a) => JSON.parse(a));
      const steam = await connect(cfg.host);
      try {
        // SPREAD arguments: passing a list becomes a single extra argument (measured)
        const call = `window.DeckyBackend.call("loader/call_plugin_method", "InsertPlay", ${JSON.stringify(rest[0])}, ...${JSON.stringify(args)})`;
        console.log(JSON.stringify(await steam.evaluate(call), null, 2));
      } finally {
        steam.close();
      }
      return 0;
    }
    default:
      console.error("usage: node scripts/deck.mjs check|eval|ssh|deploy|restart|remove|logs|setup-access|stage-card|screenshot|panel|toasts|rpc");
      return 2;
  }
}

// only as a command: spike.mjs imports loadConfig from here
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().then(
    (code) => process.exit(code ?? 0),
    (err) => {
      console.error(`Error: ${err.message}`);
      process.exit(1);
    },
  );
}

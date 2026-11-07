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
//   node scripts/deck.mjs wake               wake-on-LAN magic packet, then waits for SSH (needs "mac")
//   node scripts/deck.mjs suspend            suspends the device through Steam (to test `wake`)
import { spawnSync } from "node:child_process";
import { createSocket } from "node:dgram";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { Socket } from "node:net";
import { lookup } from "node:dns/promises";
import { networkInterfaces } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { connect } from "./cdp.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CONFIG_PATH = join(ROOT, "deck.config.json");
const STAGE = "/tmp/insertplay-decky";
const DEPLOY = "/etc/insertplay-dev/deploy-plugin.sh";

const MUTATING = new Set(["deploy", "remove", "restart", "stage-card", "setup-access", "suspend"]);

/** Config of a device: `{ name, host, user, role }`. Accepts the old format
 *  (`{ host, user }` at the root), treated as the "deck" device. */
export function loadConfig(device = process.env.INSERTPLAY_DEVICE) {
  if (!existsSync(CONFIG_PATH)) {
    throw new Error(`create ${CONFIG_PATH} from deck.config.example.json with the Deck's IP`);
  }
  const raw = JSON.parse(readFileSync(CONFIG_PATH, "utf8"));
  // old format: also carries the `mac`/`sleep` that saveFacts writes at the root (PR #42 review)
  const devices = raw.devices ?? { deck: { host: raw.host, user: raw.user, role: "dev", mac: raw.mac, sleep: raw.sleep } };
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

/** Writes measured facts (`mac`, `sleep`) to the device in deck.config.json
 *  (`devices.<name>`, or the root in the old format). `check` calls it so `wake` works
 *  without editing the file by hand, and knows a device that sleeps in S3 cannot be woken. */
function saveFacts(cfg, facts) {
  const raw = JSON.parse(readFileSync(CONFIG_PATH, "utf8"));
  const entry = raw.devices ? raw.devices[cfg.name] : raw;
  const changed = Object.entries(facts).filter(([k, v]) => v && entry && entry[k] !== v);
  if (!changed.length) return;
  for (const [k, v] of changed) entry[k] = cfg[k] = v;
  writeFileSync(CONFIG_PATH, `${JSON.stringify(raw, null, 2)}\n`);
}

const DEEP_SLEEP = "it sleeps in S3 (deep), where the Wi-Fi cannot wake it: press the power button";

const normMac = (mac) => mac.toLowerCase().replace(/-/g, ":");

/** Magic packet: 6 x 0xFF followed by the MAC 16 times. */
export function magicPacket(mac) {
  const hex = mac.replace(/[:-]/g, "");
  if (!/^[0-9a-f]{12}$/i.test(hex)) throw new Error(`invalid MAC "${mac}"`);
  const m = Buffer.from(hex, "hex");
  return Buffer.concat([Buffer.alloc(6, 0xff), ...Array(16).fill(m)]);
}

/** Broadcast addresses to send the packet to: the directed broadcast of every local IPv4
 *  interface on the device's subnet, plus the limited broadcast. On Windows the limited
 *  broadcast leaves through a single interface, so the directed one is the reliable one.
 *  `ip` must be an IPv4 address (resolve hostnames first). */
function broadcastTargets(ip) {
  const toInt = (a) => a.split(".").reduce((n, o) => (n << 8) + Number(o), 0) >>> 0;
  const toIp = (n) => [24, 16, 8, 0].map((s) => (n >>> s) & 255).join(".");
  const directed = new Set();
  for (const addrs of Object.values(networkInterfaces())) {
    for (const a of addrs ?? []) {
      if (a.family !== "IPv4" || a.internal) continue;
      const mask = toInt(a.netmask);
      if (((toInt(a.address) ^ toInt(ip)) & mask) === 0) directed.add(toIp((toInt(a.address) | ~mask) >>> 0));
    }
  }
  return [...directed, "255.255.255.255"];
}

function sendMagic(mac, targets) {
  const packet = magicPacket(mac);
  return new Promise((resolve, reject) => {
    const sock = createSocket("udp4");
    sock.once("error", reject);
    sock.bind(() => {
      sock.setBroadcast(true);
      let pending = targets.length * 2;
      for (const t of targets) {
        for (const port of [9, 7]) {
          sock.send(packet, port, t, () => {
            if (--pending === 0) {
              sock.close();
              resolve();
            }
          });
        }
      }
    });
  });
}

/** Whether the TCP port answers within `ms`. */
function tcpOpen(host, port, ms) {
  return new Promise((resolve) => {
    const s = new Socket();
    const done = (ok) => {
      s.destroy();
      resolve(ok);
    };
    s.setTimeout(ms);
    s.once("connect", () => done(true));
    s.once("timeout", () => done(false));
    s.once("error", () => done(false));
    s.connect(port, host);
  });
}

const sleepMs = (ms) => new Promise((r) => setTimeout(r, ms));
const hostIp = async (host) => (await lookup(host, { family: 4 })).address;
const deviceFlag = (cfg) => `--device ${cfg.name}`;

/** Sends the magic packet and resends it every few seconds until SSH (port 22) answers.
 *  Wi-Fi in WoWLAN may miss a packet, so a single one is not enough. */
async function wake(cfg, timeoutS = 60) {
  if (!cfg.mac) {
    console.error(`"mac" missing for device "${cfg.name}" in ${CONFIG_PATH}. Run check once while it is awake: it saves the MAC.`);
    return 1;
  }
  if (await tcpOpen(cfg.host, 22, 1500)) {
    console.log("already awake (ssh answers)");
    return 0;
  }
  if (cfg.sleep === "deep") {
    console.error(`no answer, and wake-on-LAN does not work on "${cfg.name}": ${DEEP_SLEEP}`);
    return 1;
  }
  const ip = await hostIp(cfg.host);
  const targets = broadcastTargets(ip);
  // only the limited broadcast left: no interface of this machine is on the device's subnet
  // (PC on another network, or WSL2's NAT), and the broadcast does not cross a router (measured)
  if (targets.length === 1) {
    console.error(`no local network interface on ${ip}'s subnet: the magic packet would not reach it. ` +
      "Connect this PC to the device's network (or run from Windows instead of WSL2).");
    return 1;
  }
  const start = Date.now();
  console.log(`magic packet for ${cfg.mac} to ${targets.join(", ")}; waiting for ssh...`);
  while (Date.now() - start < timeoutS * 1000) {
    const round = Date.now();
    await sendMagic(cfg.mac, targets);
    if (await tcpOpen(cfg.host, 22, 3000)) {
      console.log(`awake: ssh answered after ${Math.round((Date.now() - start) / 1000)} s`);
      return 0;
    }
    // a refused connection returns at once: keep a resend every ~3 s, never a tight loop
    await sleepMs(Math.max(0, 3000 - (Date.now() - round)));
  }
  console.error(`no answer after ${timeoutS} s: wake-on-LAN may be disabled on the device (check shows it), or the IP changed`);
  return 1;
}

/** IPs where this PC's ARP table has seen `mac`. A sleeping device does not answer ARP;
 *  a device that changed IP shows up under the new one (after any traffic with it).
 *  On WSL the Linux table only sees WSL's own network: read Windows' (arp.exe). */
function arpLookup(mac) {
  const r = process.platform === "win32" ? run("arp", ["-a"], { capture: true })
    : process.env.WSL_DISTRO_NAME ? run("arp.exe", ["-a"], { capture: true })
    : run("ip", ["neigh"], { capture: true });
  const want = normMac(mac);
  const ips = [];
  for (const line of r.out.split(/\r?\n/)) {
    const ip = line.match(/\b(\d+\.\d+\.\d+\.\d+)\b/);
    const m = line.match(/\b([0-9a-f]{2}(?:[:-][0-9a-f]{2}){5})\b/i);
    if (ip && m && normMac(m[1]) === want) ips.push(ip[1]);
  }
  return ips;
}

/** Why the device does not answer: asleep or another IP. */
async function unreachableHint(cfg) {
  if (!cfg.mac) return "asleep or the IP changed; run check once while awake to save the MAC and tell them apart";
  let ip = cfg.host;
  try {
    ip = await hostIp(cfg.host);
  } catch {
    return `could not resolve ${cfg.host}`;
  }
  const other = arpLookup(cfg.mac).filter((a) => a !== ip);
  if (other.length) return `the MAC ${cfg.mac} is now at ${other.join(", ")}: the IP changed, update ${CONFIG_PATH}`;
  if (cfg.sleep === "deep") return `no answer: it may be asleep; ${DEEP_SLEEP}`;
  return `no answer: it may be asleep, run "node scripts/deck.mjs wake ${deviceFlag(cfg)}"`;
}

async function check(cfg) {
  const rows = [];
  const add = (item, ok, detail) => rows.push({ item, status: ok === "warn" ? "WARN" : ok ? "OK" : "FAIL", detail });
  const s = ssh(cfg, `
    echo "os=$(grep ^VERSION_ID= /etc/os-release | cut -d= -f2) device=$(cat /sys/devices/virtual/dmi/id/product_name)"
    dev=$(ip route | awk '/^default/{print $5; exit}')
    echo "mac=$(cat /sys/class/net/$dev/address 2>/dev/null) netif=$dev"
    echo "sleep=$(grep -o '\\[[a-z0-9]*\\]' /sys/power/mem_sleep | tr -d '[]')"
    phy=$(cat /sys/class/net/$dev/phy80211/name 2>/dev/null)
    if [ -n "$phy" ]; then
      echo "wol=$(iw phy "$phy" wowlan show 2>/dev/null | grep -qi 'magic packet' && echo magic || echo off)"
    else
      echo "wol=wired"
    fi
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
  // only when the network did not answer: ssh also returns 255 on auth/host-key errors,
  // and calling those "asleep" would hide the real cause (PR #42 review)
  if (s.code === 255 && /timed out|no route to host|host is unreachable|network is unreachable/i.test(s.err)) {
    add("diagnosis", false, await unreachableHint(cfg));
  }
  if (s.code === 0) {
    saveFacts(cfg, { mac: facts.mac, sleep: facts.sleep });
    // WARN, not FAIL: the device works without it; only `wake` depends on it
    // S3 ("deep", the Deck): the Wi-Fi root port wakes the system only from S0 (measured)
    if (facts.sleep === "deep") add("wake-on-LAN", true, `not available: sleeps in S3 (deep); ${facts.netif} ${facts.mac}`);
    else if (facts.wol === "magic") add("wake-on-LAN", true, `${facts.netif} ${facts.mac}, magic packet`);
    else if (facts.wol === "wired") add("wake-on-LAN", "warn", `${facts.netif} ${facts.mac}: Ethernet, not measured`);
    else add("wake-on-LAN", "warn", `off on ${facts.netif} ${facts.mac}: run setup-access again (docs/development.md)`);
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
  return rows.every((r) => r.status !== "FAIL") ? 0 : 1;
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
    case "wake":
      return wake(cfg);
    case "suspend": {
      // S3 (the Deck): `wake` cannot bring it back, every remote tool stays down until
      // someone presses the power button (PR #42 review)
      if (cfg.sleep === "deep" && !rest.includes("--deep-ok")) {
        console.error(`not suspending "${cfg.name}": ${DEEP_SLEEP}. Repeat with --deep-ok if someone is at the device.`);
        return 1;
      }
      const steam = await connect(cfg.host);
      try {
        const running = await steam.evaluate("SteamUIStore.RunningApps.map((a) => a.display_name)");
        if (running.length) {
          console.error(`not suspending: running ${running.join(", ")}`);
          return 1;
        }
        // delayed: the CEF answer must come back before the device sleeps
        await steam.evaluate("(setTimeout(() => SteamClient.System.SuspendPC(), 1000), true)");
      } finally {
        steam.close();
      }
      console.log("suspending");
      return 0;
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
      console.error("usage: node scripts/deck.mjs check|eval|ssh|deploy|restart|remove|logs|setup-access|stage-card|screenshot|panel|toasts|rpc|wake|suspend");
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

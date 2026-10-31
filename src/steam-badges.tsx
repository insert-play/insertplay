// SD card badge on the capsule of games that are on the inserted card: shortcuts created
// by InsertPlay and Steam games installed in a library on the card.
//
// Second place that touches Steam internals. Kept out of steam.ts because it breaks for
// a different reason: here we depend on the library DOM, not on methods. If Valve changes
// the DOM, the badges disappear and nothing else is affected; hence everything is in try/catch.
//
// Adapted from decky-nonsteam-sync (src/steam-badges.tsx),
// Copyright (c) 2026, JoseArkadio — BSD 3-Clause. Measurements from there that hold here:
// - the visible library lives in a CEF window other than SharedJSContext, where the
//   plugin runs; we find the window via DFL.getGamepadNavigationTrees();
// - Steam remounts the cards when the list scrolls, so a MutationObserver puts the
//   badges back;
// - on the Steam Machine (3840×2160), redrawing on every mutation froze the UI, which
//   restarted in a loop (SIGTRAP every ~80 s). Fix: one pass per frame and
//   geometry read only after all writes.

declare const DFL: any;

const CLASS = "insertplay-badge";
const STYLE_ID = "insertplay-badge-style";
const CAPSULES = 'div[role="gridcell"], div[role="listitem"]';

let appids = new Set<string>();
let doc: Document | null = null;
let observer: MutationObserver | null = null;
let frame = 0;
/** After stopBadges, nothing draws again. A refresh already on its way (RPC in
 *  flight, flow.ts queue) would recreate the observer and badges next to those of the
 *  next plugin load. Each load brings a fresh module, so the flag never resets. */
let stopped = false;

/** Drawn here, not copied from Font Awesome (which requires attribution): body with the
 *  top-left corner cut off and four contacts. */
const ICON =
  '<svg viewBox="0 0 24 24" aria-hidden="true">' +
  '<path fill="currentColor" d="M9 2h8.5A2.5 2.5 0 0 1 20 4.5v15a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 19.5V7z"/>' +
  '<path fill="rgba(0,0,0,.55)" d="M9.5 4.5h1.5v4H9.5zM12 4.5h1.5v4H12zM14.5 4.5H16v4h-1.5zM17 4.5h1.2v4H17z"/>' +
  "</svg>";

function bigPictureDocument(): Document | null {
  try {
    for (const tree of DFL?.getGamepadNavigationTrees?.() ?? []) {
      const candidate: Document | undefined = tree?.m_window?.document;
      if (candidate?.querySelector(CAPSULES)) return candidate;
    }
  } catch {
    // no DFL or window closed: no badge until the next trigger
  }
  return null;
}

/** appid of the card: `data-id` (measured on the Deck: the home screen exposes it on every card),
 *  then the React props, finally the capsule URL. The URL comes last because store
 *  capsules use a hash in the path, without the appid. */
function appidOf(capsule: Element): string | null {
  const dataId = capsule.getAttribute("data-id");
  if (dataId && !dataId.startsWith("placeholder")) return dataId;
  try {
    for (const el of [capsule, ...Array.from(capsule.querySelectorAll("*"))]) {
      const key = Object.keys(el).find((k) => k.startsWith("__reactProps$"));
      const props = key ? (el as any)[key] : null;
      const found = props?.appid ?? props?.app?.appid ?? props?.overview?.appid ?? props?.children?.props?.app?.appid;
      if (found) return String(found);
    }
  } catch {
    // the props shape changed: the image is what is left
  }
  const src = capsule.querySelector("img")?.getAttribute("src") ?? "";
  return src.match(/\/(?:assets|customimages)\/(\d+)/)?.[1] ?? null;
}

function insertStyle(d: Document): void {
  if (d.getElementById(STYLE_ID)) return;
  const style = d.createElement("style");
  style.id = STYLE_ID;
  // Same look as decky-nonsteam-badges (nearly transparent background with blur): if both
  // plugins are installed, the badges do not clash. The blur was not the cause of the
  // freeze on the Steam Machine; the cause was redrawing on every mutation.
  // LEFT corner: measured on the Deck, on the focused card Steam draws the status icons
  // in the bottom-right corner, over the badge. The left one stays free, and it is also not
  // the default corner of decky-nonsteam-badges (bottom right).
  style.textContent = `.${CLASS}{position:absolute;left:6px;display:flex;padding:4px;border-radius:5px;
    background:#0000002e;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
    color:#e9e9e9;z-index:9999;pointer-events:none;line-height:0}
    .${CLASS}[data-pos="bottom"]{bottom:6px}
    .${CLASS}[data-pos="top"]{top:6px}
    .${CLASS} svg{width:clamp(20px,1.6vw,32px);height:clamp(20px,1.6vw,32px)}`;
  d.head?.appendChild(style);
}

/** One pass over the visible cards. Returns how many badges are in the document. */
function markAll(d: Document): number {
  const badges: HTMLElement[] = [];
  for (const capsule of Array.from(d.querySelectorAll(CAPSULES))) {
    const appid = appidOf(capsule);
    const existing = capsule.querySelector<HTMLElement>(`.${CLASS}`);
    if (!appid || !appids.has(appid)) {
      existing?.remove();
      continue;
    }
    // The target is the container of the capsule IMAGE, not the card. Measured against the
    // reference on the Deck home screen: the card includes the title space and extends ~27 px
    // past the visible area, while the capsule stays fully in view.
    const img = capsule.querySelector("img");
    const target = (img?.parentElement ?? capsule) as HTMLElement;
    if (existing) {
      // Steam reuses cards when scrolling: the badge may be in the wrong place
      if (existing.parentElement === target && existing.dataset.appid === appid) {
        badges.push(existing);
        continue;
      }
      existing.remove();
    }
    if (getComputedStyle(target).position === "static") target.style.position = "relative";
    const badge = d.createElement("div");
    badge.className = CLASS;
    badge.dataset.appid = appid;
    badge.dataset.pos = "bottom";
    badge.innerHTML = ICON;
    target.appendChild(badge);
    badges.push(badge);
  }
  // Geometry only after all writes: interleaving "insert badge / read rect" forces
  // a layout recalculation per badge. That is what froze the Steam Machine.
  // Measures the CAPSULE, not the badge: that way the right corner comes out of the same
  // math for new and reused badges, whether currently at the bottom or the top.
  const clips = new Map<Element, DOMRect>();
  const positions: Array<[HTMLElement, string]> = [];
  for (const badge of badges) {
    const clip = badge.closest("[class*=ReactVirtualized]");
    let pos = "bottom";
    if (clip && badge.parentElement) {
      let rect = clips.get(clip);
      if (!rect) {
        rect = clip.getBoundingClientRect();
        clips.set(clip, rect);
      }
      // 6 px = badge distance from the edge (CSS above)
      if (badge.parentElement.getBoundingClientRect().bottom - 6 > rect.bottom) pos = "top";
    }
    positions.push([badge, pos]);
  }
  // bottom corner clipped (home screen): the badge moves up to the top corner
  for (const [badge, pos] of positions) if (badge.dataset.pos !== pos) badge.dataset.pos = pos;
  return badges.length;
}

function attach(d: Document): void {
  if (doc === d && observer) return;
  detach();
  doc = d;
  // Steam mutates the DOM in bursts: the observer only schedules, and the pass runs once
  // per frame. Nobody would see the badge before the next frame anyway.
  observer = new MutationObserver(() => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      try {
        markAll(d);
      } catch {
        // a failing pass must not take down the observer
      }
    });
  });
  observer.observe(d.body, { childList: true, subtree: true });
}

function detach(): void {
  observer?.disconnect();
  observer = null;
  if (frame) cancelAnimationFrame(frame);
  frame = 0;
  try {
    doc?.querySelectorAll(`.${CLASS}`).forEach((b) => b.remove());
    doc?.getElementById(STYLE_ID)?.remove();
  } catch {
    // the window may already be closed
  }
  doc = null;
}

/** Without a library window (Steam starting, or the route trigger ran before the
 *  cards existed), retries every second for up to a minute. Each new trigger
 *  restarts the count. */
const RETRY_MS = 1000;
const RETRY_LIMIT = 60;
let retry: ReturnType<typeof setTimeout> | null = null;
let retries = 0;

function scheduleRetry(): void {
  if (retry || retries >= RETRY_LIMIT) return;
  retries += 1;
  retry = setTimeout(() => {
    retry = null;
    draw();
  }, RETRY_MS);
}

function draw(): number {
  if (stopped) return 0;
  try {
    const d = bigPictureDocument();
    if (!d) {
      scheduleRetry();
      return 0;
    }
    retries = 0;
    attach(d);
    insertStyle(d);
    return markAll(d);
  } catch {
    return 0;
  }
}

/** Replaces the set of badged games and redraws. Without an argument, just redraws with the
 *  current set (navigation trigger). Returns how many badges are on screen, or 0 if the
 *  library window is not open. */
export function showBadges(ids?: Iterable<number>): number {
  if (stopped) return 0;
  if (ids) appids = new Set(Array.from(ids, String));
  retries = 0;
  return draw();
}

/** Removes badges, style and observer. Without this, the observer and style survive a
 *  plugin reload in the library document. */
export function stopBadges(): void {
  stopped = true;
  if (retry) clearTimeout(retry);
  retry = null;
  detach();
  appids = new Set();
}

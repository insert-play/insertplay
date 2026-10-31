// Badge de cartão SD na capa dos jogos que estão no cartão inserido: atalhos criados
// pelo InsertPlay e jogos Steam instalados numa biblioteca do cartão.
//
// Segundo ponto que toca internals do Steam. Fica fora de steam.ts porque quebra por
// outro motivo: aqui dependemos do DOM da biblioteca, não de métodos. Se a Valve mudar
// o DOM, os badges somem e nada mais é afetado; por isso tudo fica em try/catch.
//
// Adaptado de decky-nonsteam-sync (src/steam-badges.tsx),
// Copyright (c) 2026, JoseArkadio — BSD 3-Clause. Medições de lá que valem aqui:
// - a biblioteca visível vive numa janela CEF diferente da SharedJSContext, onde o
//   plugin roda; achamos a janela por DFL.getGamepadNavigationTrees();
// - o Steam remonta os cards ao rolar a lista, então um MutationObserver recoloca os
//   badges;
// - no Steam Machine (3840×2160), redesenhar a cada mutação travava a interface, que
//   reiniciava em loop (SIGTRAP a cada ~80 s). Correção: uma passada por quadro e
//   geometria lida só depois de todas as escritas.

declare const DFL: any;

const CLASS = "insertplay-badge";
const STYLE_ID = "insertplay-badge-style";
const CAPSULES = 'div[role="gridcell"], div[role="listitem"]';

let appids = new Set<string>();
let doc: Document | null = null;
let observer: MutationObserver | null = null;
let frame = 0;
/** Depois do stopBadges, nada desenha de novo. Um refresh que já estava a caminho
 *  (RPC em andamento, fila do flow.ts) recriaria observer e badges ao lado dos da
 *  próxima carga do plugin. Cada carga traz este módulo novo, então a flag não volta. */
let stopped = false;

/** Desenhado aqui, não copiado do Font Awesome (que exige atribuição): corpo com o
 *  canto superior esquerdo cortado e quatro contatos. */
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
    // sem DFL ou janela fechada: fica sem badge até o próximo gatilho
  }
  return null;
}

/** appid do card: `data-id` (medido no Deck: a tela inicial o expõe em todo card),
 *  depois as props do React, por fim a URL da capa. A URL vem por último porque capas
 *  da loja usam um hash no caminho, sem o appid. */
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
    // o formato das props mudou: sobra a imagem
  }
  const src = capsule.querySelector("img")?.getAttribute("src") ?? "";
  return src.match(/\/(?:assets|customimages)\/(\d+)/)?.[1] ?? null;
}

function insertStyle(d: Document): void {
  if (d.getElementById(STYLE_ID)) return;
  const style = d.createElement("style");
  style.id = STYLE_ID;
  // Mesmo visual do decky-nonsteam-badges (fundo quase transparente com blur): se os
  // dois plugins estiverem instalados, os badges não brigam. O blur não foi a causa do
  // travamento no Steam Machine; a causa era redesenhar a cada mutação.
  // Canto ESQUERDO: medido no Deck, no card em foco o Steam desenha os ícones de status
  // no canto inferior direito, por cima do badge. O esquerdo fica livre, e também não
  // é o canto padrão do decky-nonsteam-badges (inferior direito).
  style.textContent = `.${CLASS}{position:absolute;left:6px;display:flex;padding:4px;border-radius:5px;
    background:#0000002e;backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);
    color:#e9e9e9;z-index:9999;pointer-events:none;line-height:0}
    .${CLASS}[data-pos="bottom"]{bottom:6px}
    .${CLASS}[data-pos="top"]{top:6px}
    .${CLASS} svg{width:clamp(20px,1.6vw,32px);height:clamp(20px,1.6vw,32px)}`;
  d.head?.appendChild(style);
}

/** Uma passada sobre os cards visíveis. Devolve quantos badges há no documento. */
function markAll(d: Document): number {
  const badges: HTMLElement[] = [];
  for (const capsule of Array.from(d.querySelectorAll(CAPSULES))) {
    const appid = appidOf(capsule);
    const existing = capsule.querySelector<HTMLElement>(`.${CLASS}`);
    if (!appid || !appids.has(appid)) {
      existing?.remove();
      continue;
    }
    // O alvo é o contêiner da IMAGEM da capa, não o card. Medido pela referência na
    // tela inicial do Deck: o card inclui o espaço do título e passa ~27 px da área
    // visível, enquanto a capa fica inteira à vista.
    const img = capsule.querySelector("img");
    const target = (img?.parentElement ?? capsule) as HTMLElement;
    if (existing) {
      // o Steam reaproveita cards ao rolar: o badge pode estar no lugar errado
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
  // Geometria só depois de todas as escritas: intercalar "insere badge / lê retângulo"
  // força um recálculo de layout por badge. Foi isso que travou o Steam Machine.
  // Mede a CAPA, não o badge: assim o canto certo sai da mesma conta para badge novo e
  // reaproveitado, esteja ele embaixo ou em cima agora.
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
      // 6 px = distância do badge à borda (CSS acima)
      if (badge.parentElement.getBoundingClientRect().bottom - 6 > rect.bottom) pos = "top";
    }
    positions.push([badge, pos]);
  }
  // canto de baixo cortado (tela inicial): o badge sobe para o canto de cima
  for (const [badge, pos] of positions) if (badge.dataset.pos !== pos) badge.dataset.pos = pos;
  return badges.length;
}

function attach(d: Document): void {
  if (doc === d && observer) return;
  detach();
  doc = d;
  // O Steam muta o DOM em rajadas: o observer só agenda, e a passada roda uma vez por
  // quadro. Antes do próximo quadro ninguém veria o badge mesmo.
  observer = new MutationObserver(() => {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      try {
        markAll(d);
      } catch {
        // uma passada que falha não pode derrubar o observer
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
    // a janela pode já ter fechado
  }
  doc = null;
}

/** Sem janela da biblioteca (Steam iniciando, ou o gatilho da rota rodou antes de os
 *  cards existirem), tenta de novo a cada segundo por até um minuto. Cada gatilho novo
 *  recomeça a contagem. */
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

/** Troca o conjunto de jogos com badge e redesenha. Sem argumento, só redesenha com o
 *  conjunto atual (gatilho de navegação). Devolve quantos badges há na tela, ou 0 se a
 *  janela da biblioteca não estiver aberta. */
export function showBadges(ids?: Iterable<number>): number {
  if (stopped) return 0;
  if (ids) appids = new Set(Array.from(ids, String));
  retries = 0;
  return draw();
}

/** Remove badges, estilo e observer. Sem isto, observer e estilo sobrevivem ao reload
 *  do plugin no documento da biblioteca. */
export function stopBadges(): void {
  stopped = true;
  if (retry) clearTimeout(retry);
  retry = null;
  detach();
  appids = new Set();
}

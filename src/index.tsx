import { addEventListener, definePlugin, removeEventListener, routerHook, toaster } from "@decky/api";
import {
  ButtonItem,
  ConfirmModal,
  DropdownItem,
  Navigation,
  PanelSection,
  PanelSectionRow,
  Spinner,
  TextField,
  ToggleField,
  showModal,
  staticClasses,
} from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { FaSdCard } from "react-icons/fa";
import {
  Card,
  CardPackage,
  LogEntry,
  OnInsert,
  PackageStatus,
  PortableMode,
  PortableStatus,
  RaLoginError,
  RemoteGame,
  Settings,
  currentCards,
  getSettings,
  logAdd,
  logTail,
  packageStatus,
  portableStatus,
  raLogin,
  raLogout,
  raStatus,
  remoteGetConfig,
  remoteSetConfig,
  setSetting,
  writePackageToCard,
} from "./backend";
import { installAchievements, shownAppids, stopAchievements } from "./achievements";
import {
  PortableCard,
  RemoteAction,
  applyHideRemoved,
  clearAchievements,
  launchCard,
  loadAllAchievements,
  onGameClosed,
  onPortableChange,
  onInserted,
  onRemoteLibraryChange,
  onRemoved,
  onSteamLibrariesChanged,
  portableChange,
  portableTick,
  reconcile,
  refreshLibrary,
  refreshRemoteIndex,
  reinstall,
  remoteAction,
  remoteLibraryGames,
  remoteSteamHandlers,
  setPortable,
  settlePortableChange,
  startLibrary,
  steamCardsPresent,
  warnIfRaRefused,
} from "./flow";
import { installRemoteSteam, stopRemoteSteam } from "./remote-steam";
import { InstallType, installFromCard, notifyNewerPackage } from "./selfupdate";
import { onAppClosed, onInstallFoldersChanged } from "./steam";
import { showBadges, stopBadges } from "./steam-badges";

const REFRESH_EVENTS = ["card_inserted", "card_removed"] as const;

const RELATION_TEXT: Record<CardPackage["relation"], string> = {
  missing: "sem pacote do plugin",
  invalid: "pacote inválido",
  newer: "versão mais nova",
  older: "versão mais antiga",
  same: "mesma versão",
};

function failed(what: string, err: unknown) {
  const detail = err instanceof Error ? err.message : String(err);
  void logAdd("error", `${what}: ${detail}`).catch(() => undefined);
  toaster.toast({ title: "InsertPlay", body: `${what} falhou: ${detail}`, duration: 10000 });
}

/** Install/update the plugin from the card and write the installed version to the card. */
function PackageRow({ pkg, installed, onChange }: { pkg: CardPackage; installed: string; onChange: () => void }) {
  const [busy, setBusy] = useState(false);

  const write = async () => {
    setBusy(true);
    try {
      const result = await writePackageToCard(pkg.mount);
      if (!result) throw new Error("veja o log de eventos");
      toaster.toast({ title: "InsertPlay", body: `Plugin v${result.version} gravado em ${pkg.label}` });
    } catch (err) {
      failed(`Gravação no cartão ${pkg.label}`, err);
    } finally {
      setBusy(false);
      onChange();
    }
  };
  const confirmWrite = () =>
    showModal(
      <ConfirmModal
        strTitle="Gravar plugin no cartão"
        strDescription={`Substituir ${pkg.version ? `o plugin v${pkg.version}` : "o pacote inválido"} do cartão ${pkg.label} pela versão instalada v${installed}?`}
        strOKButtonText="Gravar"
        onOK={() => void write()}
      />,
    );
  const install = (type: InstallType) =>
    installFromCard(pkg, type).catch((err) => failed(`Instalação pelo cartão ${pkg.label}`, err));

  return (
    <PanelSectionRow>
      <div style={{ fontWeight: "bold" }}>{pkg.label}</div>
      <div style={{ fontSize: "0.8em", opacity: 0.7, wordBreak: "break-all" }}>
        {pkg.version ? `v${pkg.version} · ` : ""}
        {RELATION_TEXT[pkg.relation]}
        {pkg.error ? `: ${pkg.error}` : ""}
      </div>
      {pkg.relation === "newer" && (
        <ButtonItem layout="below" disabled={busy} onClick={() => void install(InstallType.UPDATE)}>
          Atualizar plugin para v{pkg.version}
        </ButtonItem>
      )}
      {pkg.relation === "same" && (
        <ButtonItem layout="below" disabled={busy} onClick={() => void install(InstallType.REINSTALL)}>
          Reinstalar plugin do cartão
        </ButtonItem>
      )}
      {pkg.relation === "missing" && (
        <ButtonItem layout="below" disabled={busy} onClick={() => void write()}>
          Gravar plugin v{installed} no cartão
        </ButtonItem>
      )}
      {(pkg.relation === "older" || pkg.relation === "invalid") && (
        <ButtonItem layout="below" disabled={busy} onClick={confirmWrite}>
          Atualizar cartão para v{installed}
        </ButtonItem>
      )}
    </PanelSectionRow>
  );
}

const PORTABLE_TICK_MS = 30_000;

const ON_INSERT_OPTIONS: { data: OnInsert; label: string }[] = [
  { data: "none", label: "Nenhum" },
  { data: "page", label: "Abrir página do jogo" },
  { data: "launch", label: "Abrir jogo" },
];

const PORTABLE_MODES: { data: PortableMode; label: string; description: string }[] = [
  { data: "off", label: "Desligado", description: "O save fica no aparelho." },
  {
    data: "userdata",
    label: "Saves",
    description: "Leva a pasta do usuário (Documents, AppData). Rápido em qualquer cartão.",
  },
  {
    data: "prefix",
    label: "Prefixo completo",
    description: "Leva também o registro do Windows. Lento para abrir em cartão lento.",
  },
];

/** Portable saves of a card with Steam games. Applies to the whole card and is stored
 *  on it. Leaving a mode copies what is on the card back to the device. */
function PortableRow({
  card,
  status,
  onChange,
}: {
  card: PortableCard;
  status: PortableStatus | undefined;
  onChange: () => void;
}) {
  // While the choice runs (the copy takes seconds), the selector already shows the chosen mode,
  // disabled and with a spinner. The change lives in flow.ts and survives the panel remounting.
  const change = portableChange(card.mount);
  useEffect(() => {
    if (change?.done && status?.mode === change.mode) settlePortableChange(card.mount);
  }, [change, status?.mode]);
  const pending = change && !change.done ? change.mode : null;
  const settled = change?.done ? change.mode : status?.mode ?? "off";
  const current = pending ?? settled;
  // failure: the selector reverts to the previous mode; the toast says why
  const apply = (mode: PortableMode) => void setPortable(card, mode).finally(onChange);
  const choose = (mode: PortableMode) => {
    if (pending || mode === settled) return;
    if (settled === "off") return apply(mode);
    const target = PORTABLE_MODES.find((m) => m.data === mode)?.label ?? mode;
    showModal(
      <ConfirmModal
        strTitle="Mudar save portátil"
        strDescription={`O save dos jogos do cartão ${card.label} será copiado para este aparelho antes de mudar para "${target}". A cópia do cartão continua lá.`}
        strOKButtonText="Mudar"
        onOK={() => apply(mode)}
        onCancel={onChange}
      />,
    );
  };
  const games = `${card.apps.length} jogo${card.apps.length === 1 ? "" : "s"} Steam`;
  const option = PORTABLE_MODES.find((m) => m.data === current);
  const description = pending ? (
    <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
      <Spinner style={{ width: "1em", height: "1em" }} />
      Aplicando "{option?.label}"…
    </span>
  ) : status && !status.fsOk ? (
    status.reason ?? ""
  ) : (
    `${games}. ${option?.description ?? ""}`
  );
  return (
    <PanelSectionRow>
      <DropdownItem
        label={card.label}
        description={description}
        rgOptions={PORTABLE_MODES.map(({ data, label }) => ({ data, label }))}
        selectedOption={current}
        disabled={pending !== null || !status || (!status.fsOk && settled === "off")}
        onChange={(option) => choose(option.data as PortableMode)}
      />
    </PanelSectionRow>
  );
}

const RA_KEYS_URL = "https://retroachievements.org/settings";

const LOGIN_ERROR: Record<RaLoginError, string> = {
  invalid: "Usuário ou chave em formato inválido.",
  refused: "O RetroAchievements recusou o usuário ou a chave.",
  network: "Sem resposta do RetroAchievements. Confira a rede.",
  failed: "Falha no login (veja o log de eventos).",
};

/** Plugin's own RetroAchievements login (issue #24): user + web API key, used only to
 *  read progress. The emulators keep their own login. */
function AchievementsSection() {
  // undefined = still asking the backend
  const [user, setUser] = useState<string | null | undefined>(undefined);
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // RA refused the saved key (issue #34): the login form comes back, with the user filled in
  const [refused, setRefused] = useState(false);

  useEffect(() => {
    const load = () =>
      void raStatus().then((status) => {
        setUser(status ? status.user : null);
        setRefused(!!status?.refused);
        if (status?.refused && status.user) setName((current) => current || status.user!);
      });
    load();
    // Decky keeps the panel mounted: a refusal while it is open must show up too
    const sub = addEventListener("ra_refused", load);
    return () => void removeEventListener("ra_refused", sub);
  }, []);

  const login = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await raLogin(name, key);
      if (!result.user) {
        setError(LOGIN_ERROR[result.error ?? "failed"]);
        return;
      }
      setUser(result.user);
      setRefused(false);
      setKey("");
      toaster.toast({ title: "InsertPlay", body: `RetroAchievements: conectado como ${result.user}` });
      clearAchievements(); // new session: nothing from before the login is shown
      void loadAllAchievements(true);
    } catch (err) {
      failed("Login no RetroAchievements", err);
    } finally {
      setBusy(false);
    }
  };
  const logout = async () => {
    await raLogout();
    clearAchievements();
    setUser(null);
    setRefused(false);
  };
  const update = async () => {
    setBusy(true);
    try {
      await loadAllAchievements(true);
    } finally {
      setBusy(false);
    }
  };

  if (user === undefined) return null;
  return (
    <PanelSection title="Conquistas">
      {user && !refused ? (
        <>
          <PanelSectionRow>
            <div style={{ fontSize: "0.85em" }}>RetroAchievements: {user}</div>
            <div style={{ fontSize: "0.75em", opacity: 0.7 }}>
              {shownAppids().length} jogo(s) com conquistas na biblioteca
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={() => void update()}>
              Atualizar agora
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={() => void logout()}>
              Sair
            </ButtonItem>
          </PanelSectionRow>
        </>
      ) : (
        <>
          {refused && (
            <PanelSectionRow>
              <div style={{ fontSize: "0.8em", color: "#e57373" }}>
                O RetroAchievements recusou a chave salva de {user}. As conquistas não são atualizadas até
                você entrar de novo com a Web API Key atual.
              </div>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <div style={{ fontSize: "0.75em", opacity: 0.7 }}>
              Mostra as conquistas do RetroAchievements dos cartões na página do jogo. Abra a página da
              chave, copie a Web API Key (seção Keys), volte aqui e cole.
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            {/* Steam's own browser, in Game Mode too. Measured on the Deck (2026-11-01): copying
                the key there and pasting it in the field below works. */}
            <ButtonItem layout="below" onClick={() => Navigation.NavigateToExternalWeb(RA_KEYS_URL)}>
              Abrir página da chave
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <TextField label="Usuário" value={name} disabled={busy} onChange={(e) => setName(e.target.value)} />
          </PanelSectionRow>
          <PanelSectionRow>
            <TextField
              label="Web API Key"
              value={key}
              bIsPassword
              disabled={busy}
              onChange={(e) => setKey(e.target.value)}
            />
          </PanelSectionRow>
          {error && (
            <PanelSectionRow>
              <div style={{ fontSize: "0.8em", color: "#e57373" }}>{error}</div>
            </PanelSectionRow>
          )}
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy || !name.trim() || !key.trim()} onClick={() => void login()}>
              Entrar
            </ButtonItem>
          </PanelSectionRow>
          {refused && (
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={busy} onClick={() => void logout()}>
                Sair
              </ButtonItem>
            </PanelSectionRow>
          )}
        </>
      )}
    </PanelSection>
  );
}

const REMOTE_STATE_TEXT: Record<RemoteGame["state"], string> = {
  not_installed: "Não instalado",
  queued: "Na fila",
  downloading: "Baixando",
  paused: "Pausado",
  installed: "Instalado",
  error: "Erro",
};

function gb(bytes: number): string {
  return `${(bytes / 1024 ** 3).toFixed(bytes >= 10 * 1024 ** 3 ? 0 : 1).replace(".", ",")} GB`;
}

function remoteDetail(game: RemoteGame): string {
  const state = REMOTE_STATE_TEXT[game.state];
  if (game.state === "downloading" || game.state === "paused" || game.state === "queued") {
    const speed = game.state === "downloading" && game.speed ? ` · ${(game.speed / 1024 ** 2).toFixed(1).replace(".", ",")} MB/s` : "";
    return `${state} ${game.percent}% · ${gb(game.bytesDone)} de ${gb(game.bytesTotal)}${speed}`;
  }
  if (game.state === "error") return `${state}: ${game.error?.message ?? "veja o log de eventos"}`;
  if (game.state === "installed") return `${state} · ${gb(game.bytesTotal)}${game.inIndex ? "" : " · fora da biblioteca"}`;
  return `${state} · ${gb(game.bytesTotal)}`;
}

/** One library game: state and the actions that apply to it. */
function RemoteGameRow({ game }: { game: RemoteGame }) {
  const [busy, setBusy] = useState(false);
  const run = (action: RemoteAction) => {
    setBusy(true);
    void remoteAction(game.id, action).finally(() => setBusy(false));
  };
  const confirm = (action: "cancel" | "uninstall") =>
    showModal(
      <ConfirmModal
        strTitle={action === "uninstall" ? `Desinstalar ${game.name}` : `Cancelar ${game.name}`}
        strDescription={
          action === "uninstall"
            ? `Apaga ${game.name} deste aparelho (${gb(game.bytesTotal)}), inclusive saves gravados dentro da pasta do jogo (memory cards, saves do Wine). O atalho volta a "Instalar".`
            : `Para o download de ${game.name} e apaga o que já foi baixado.`
        }
        strOKButtonText={action === "uninstall" ? "Desinstalar" : "Cancelar download"}
        strCancelButtonText="Voltar"
        onOK={() => run(action)}
      />,
    );
  const s = game.state;
  return (
    <PanelSectionRow>
      <div style={{ fontWeight: "bold" }}>{game.name}</div>
      <div style={{ fontSize: "0.8em", opacity: 0.7, color: s === "error" ? "#e57373" : undefined }}>
        {remoteDetail(game)}
      </div>
      {(s === "not_installed" || s === "error") && game.inIndex && (
        <ButtonItem layout="below" disabled={busy} onClick={() => run(s === "error" ? "resume" : "install")}>
          {s === "error" ? "Tentar de novo" : "Instalar"}
        </ButtonItem>
      )}
      {(s === "downloading" || s === "queued") && (
        <ButtonItem layout="below" disabled={busy} onClick={() => run("pause")}>
          Pausar
        </ButtonItem>
      )}
      {s === "paused" && (
        <ButtonItem layout="below" disabled={busy} onClick={() => run("resume")}>
          Retomar
        </ButtonItem>
      )}
      {(s === "downloading" || s === "queued" || s === "paused" || s === "error") && (
        <ButtonItem layout="below" disabled={busy} onClick={() => confirm("cancel")}>
          Cancelar
        </ButtonItem>
      )}
      {s === "installed" && (
        <ButtonItem layout="below" disabled={busy} onClick={() => confirm("uninstall")}>
          Desinstalar
        </ButtonItem>
      )}
    </PanelSectionRow>
  );
}

/** "Biblioteca Remota" (#45): server address and token, and the games of its index. */
function RemoteLibrarySection() {
  // undefined = still asking the backend
  const [config, setConfig] = useState<{ url: string | null; tokenSet: boolean } | null | undefined>(undefined);
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [games, setGames] = useState<RemoteGame[] | null>(remoteLibraryGames());
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    void remoteGetConfig().then((c) => {
      setConfig(c);
      setUrl(c?.url ?? "");
    });
    const stop = onRemoteLibraryChange(() => setGames(remoteLibraryGames()));
    // the event while downloading; the list itself is read by flow.ts
    void refreshLibrary();
    return stop;
  }, []);

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      // an empty (or blank) token field keeps the saved token (write-only field)
      const result = await remoteSetConfig(url.trim(), token.trim() || null);
      if (!result) throw new Error("veja o log de eventos");
      if (!result.ok) {
        setMessage(result.message);
        return;
      }
      setConfig({ url: result.url, tokenSet: result.tokenSet });
      setToken("");
      setEditing(false);
      setMessage(await refreshRemoteIndex());
    } catch (err) {
      failed("Biblioteca Remota", err);
    } finally {
      setBusy(false);
    }
  };
  const reload = async () => {
    setBusy(true);
    setMessage(await refreshRemoteIndex());
    setBusy(false);
  };

  if (config === undefined) return null;
  const configured = Boolean(config?.url && config.tokenSet);
  return (
    <PanelSection title="Biblioteca Remota">
      {(!configured || editing) && (
        <>
          <PanelSectionRow>
            <div style={{ fontSize: "0.75em", opacity: 0.7 }}>
              Instala jogos de um servidor na rede de casa. Informe o endereço (ex.: http://192.168.1.83:8080/) e o
              token do servidor.
            </div>
          </PanelSectionRow>
          <PanelSectionRow>
            <TextField label="Endereço" value={url} disabled={busy} onChange={(e) => setUrl(e.target.value)} />
          </PanelSectionRow>
          <PanelSectionRow>
            <TextField
              label="Token"
              description={config?.tokenSet ? "Token salvo. Deixe vazio para manter." : undefined}
              value={token}
              bIsPassword
              disabled={busy}
              onChange={(e) => setToken(e.target.value)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem
              layout="below"
              disabled={busy || !url.trim() || (!token.trim() && !config?.tokenSet)}
              onClick={() => void save()}
            >
              Salvar
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
      {message && (
        <PanelSectionRow>
          <div style={{ fontSize: "0.8em", color: "#e57373" }}>{message}</div>
        </PanelSectionRow>
      )}
      {configured && !editing && (
        <PanelSectionRow>
          <div style={{ fontSize: "0.75em", opacity: 0.7, wordBreak: "break-all" }}>{config?.url}</div>
        </PanelSectionRow>
      )}
      {configured && (games ?? []).map((game) => <RemoteGameRow key={game.id} game={game} />)}
      {configured && games?.length === 0 && <PanelSectionRow>Nenhum jogo na biblioteca.</PanelSectionRow>}
      {configured && !editing && (
        <>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={() => void reload()}>
              Atualizar lista
            </ButtonItem>
          </PanelSectionRow>
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={busy} onClick={() => setEditing(true)}>
              Alterar servidor
            </ButtonItem>
          </PanelSectionRow>
        </>
      )}
    </PanelSection>
  );
}

function Panel() {
  const [cards, setCards] = useState<Card[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [pkg, setPkg] = useState<PackageStatus | null>(null);
  const [portable, setPortableCards] = useState<{ card: PortableCard; status?: PortableStatus }[]>([]);

  const refresh = useCallback(async () => {
    const [c, s, l, p] = await Promise.all([currentCards(), getSettings(), logTail(8), packageStatus()]);
    if (Array.isArray(c)) setCards(c); // null = backend still scanning; keep what was there
    setSettings(s);
    setLog(l);
    if (p) setPkg(p);
    const present = await Promise.all(
      steamCardsPresent().map(async (card) => ({ card, status: (await portableStatus(card.mount)) ?? undefined })),
    );
    // only cards with Steam games; an enabled card left with no games stays, to turn it off
    setPortableCards(present.filter(({ card, status }) => card.apps.length > 0 || (status && status.mode !== "off")));
  }, []);

  useEffect(() => {
    void refresh();
    // wait for the flow.ts queue to handle the event before re-reading the state
    const later = () => setTimeout(() => void refresh(), 1500);
    const subs = REFRESH_EVENTS.map((name) => addEventListener(name, later));
    // start and end of a portable saves change: redraw now, with the new status
    const stopPortable = onPortableChange(() => void refresh());
    // Decky keeps the panel mounted between Quick Access openings; without this it
    // would show a stale state until someone clicks "Atualizar" (measured on the Deck).
    const timer = setInterval(() => void refresh(), 5000);
    return () => {
      clearInterval(timer);
      stopPortable();
      REFRESH_EVENTS.forEach((name, i) => removeEventListener(name, subs[i]));
    };
  }, [refresh]);

  return (
    <>
      <PanelSection title="Cartões">
        {cards.length === 0 && <PanelSectionRow>Nenhum cartão InsertPlay inserido.</PanelSectionRow>}
        {cards.map((card) => (
          <PanelSectionRow key={card.mount}>
            <div style={{ fontWeight: "bold" }}>{card.appName ?? card.label}</div>
            <div style={{ fontSize: "0.8em", opacity: 0.7, wordBreak: "break-all" }}>
              {card.error ? `Erro: ${card.error}` : card.mount}
            </div>
            {!card.error && (
              <>
                <ButtonItem layout="below" onClick={() => void launchCard(card)}>
                  Jogar
                </ButtonItem>
                <ButtonItem layout="below" onClick={() => void reinstall(card).then(refresh)}>
                  Reinstalar atalho
                </ButtonItem>
              </>
            )}
          </PanelSectionRow>
        ))}
      </PanelSection>
      {portable.length > 0 && (
        <PanelSection title="Save portátil">
          {portable.map(({ card, status }) => (
            <PortableRow key={card.mount} card={card} status={status} onChange={() => void refresh()} />
          ))}
        </PanelSection>
      )}
      <RemoteLibrarySection />
      <AchievementsSection />
      <PanelSection title="Plugin">
        <PanelSectionRow>
          <div style={{ fontSize: "0.85em" }}>Instalado: {pkg ? `v${pkg.installed}` : "…"}</div>
        </PanelSectionRow>
        {pkg?.cards.map((p) => (
          <PackageRow key={p.mount} pkg={p} installed={pkg.installed} onChange={() => void refresh()} />
        ))}
      </PanelSection>
      <PanelSection title="Configurações">
        <PanelSectionRow>
          <DropdownItem
            label="Ao inserir"
            description="Com um jogo aberto, só avisa."
            rgOptions={ON_INSERT_OPTIONS}
            selectedOption={settings?.onInsert ?? "launch"}
            disabled={!settings}
            onChange={(option) => void setSetting("onInsert", option.data as OnInsert).then(setSettings)}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Ocultar ao remover o cartão"
            description="Desligado, o jogo fica na biblioteca sem o badge do cartão."
            checked={settings?.hideRemoved ?? true}
            disabled={!settings}
            onChange={(value) =>
              void setSetting("hideRemoved", value)
                .then(setSettings)
                .then(applyHideRemoved)
            }
          />
        </PanelSectionRow>
      </PanelSection>
      <PanelSection title="Eventos">
        {log.length === 0 && <PanelSectionRow>Nenhum evento ainda.</PanelSectionRow>}
        {log.map((entry, i) => (
          <PanelSectionRow key={i}>
            <div style={{ fontSize: "0.75em", color: entry.kind === "error" ? "#e57373" : undefined }}>
              {new Date(entry.ts * 1000).toLocaleTimeString()} · {entry.kind} · {entry.message}
            </div>
          </PanelSectionRow>
        ))}
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => void refresh()}>
            Atualizar
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

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
  const libraryPatch = routerHook.addPatch("/library", (tree: unknown) => {
    showBadges();
    return tree;
  });
  const portableTimer = setInterval(() => void portableTick(), PORTABLE_TICK_MS);
  // Before reconcile, which publishes the achievements. A part not found is logged and the
  // rest works (issue #24). Measured: all three are found at plugin load, also right after
  // a Steam restart.
  const achievementsFailed = installAchievements();
  if (achievementsFailed.length) void logAdd("error", `achievements patch: ${achievementsFailed.join("; ")}`);
  const stopAppClosed = onAppClosed((appid) => void onGameClosed(appid));
  // Remote library (#45): Steam's buttons on its shortcuts, and the backend's progress
  const remoteFailed = installRemoteSteam(remoteSteamHandlers);
  if (remoteFailed.length) void logAdd("error", `remote library patch: ${remoteFailed.join("; ")}`);
  const remoteState = addEventListener("remote_state", () => void refreshLibrary());
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
      stopAppClosed();
      removeEventListener("remote_state", remoteState);
      stopRemoteSteam();
      stopBadges();
      stopAchievements();
    },
  };
});

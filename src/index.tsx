import { addEventListener, definePlugin, removeEventListener, routerHook, toaster } from "@decky/api";
import {
  ButtonItem,
  ConfirmModal,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  Spinner,
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
  PackageStatus,
  PortableMode,
  PortableStatus,
  Settings,
  currentCards,
  getSettings,
  logAdd,
  logTail,
  packageStatus,
  portableStatus,
  setSetting,
  writePackageToCard,
} from "./backend";
import {
  PortableCard,
  applyHideRemoved,
  launchCard,
  onPortableChange,
  onInserted,
  onRemoved,
  onSteamLibrariesChanged,
  portableChange,
  portableTick,
  reconcile,
  reinstall,
  setPortable,
  settlePortableChange,
  steamCardsPresent,
} from "./flow";
import { InstallType, installFromCard, notifyNewerPackage } from "./selfupdate";
import { onInstallFoldersChanged } from "./steam";
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
          <ToggleField
            label="Iniciar ao inserir"
            checked={settings?.autoLaunch ?? true}
            disabled={!settings}
            onChange={(value) => void setSetting("autoLaunch", value).then(setSettings)}
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
  void reconcile();
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
      stopFolders();
      clearInterval(portableTimer);
      routerHook.removePatch("/library", libraryPatch);
      stopBadges();
    },
  };
});

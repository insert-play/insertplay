import { addEventListener, definePlugin, removeEventListener, routerHook, toaster } from "@decky/api";
import {
  ButtonItem,
  ConfirmModal,
  PanelSection,
  PanelSectionRow,
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
  Settings,
  currentCards,
  getSettings,
  logAdd,
  logTail,
  packageStatus,
  setSetting,
  writePackageToCard,
} from "./backend";
import { applyHideRemoved, launchCard, onInserted, onRemoved, onSteamLibrariesChanged, reconcile, reinstall } from "./flow";
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

/** Instalar/atualizar o plugin pelo cartão e gravar a versão instalada no cartão. */
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

function Panel() {
  const [cards, setCards] = useState<Card[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);
  const [pkg, setPkg] = useState<PackageStatus | null>(null);

  const refresh = useCallback(async () => {
    const [c, s, l, p] = await Promise.all([currentCards(), getSettings(), logTail(8), packageStatus()]);
    if (Array.isArray(c)) setCards(c); // null = backend ainda varrendo; mantém o que havia
    setSettings(s);
    setLog(l);
    if (p) setPkg(p);
  }, []);

  useEffect(() => {
    void refresh();
    // espera a fila do flow.ts tratar o evento antes de reler o estado
    const later = () => setTimeout(() => void refresh(), 1500);
    const subs = REFRESH_EVENTS.map((name) => addEventListener(name, later));
    // O Decky mantém o painel montado entre aberturas do Quick Access; sem isto ele
    // mostraria um estado velho até alguém clicar em "Atualizar" (medido no Deck).
    const timer = setInterval(() => void refresh(), 5000);
    return () => {
      clearInterval(timer);
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
  // Cartão com biblioteca Steam e sem manifesto InsertPlay não gera evento do backend:
  // quem avisa é o Steam, para o badge e para a notificação (issue #10).
  const stopFolders = onInstallFoldersChanged(() => void onSteamLibrariesChanged());
  // A janela da biblioteca pode não existir quando o plugin carrega (jogo aberto, Steam
  // iniciando). Entrar na biblioteca é o gatilho para prender os badges nela.
  const libraryPatch = routerHook.addPatch("/library", (tree: unknown) => {
    showBadges();
    return tree;
  });
  void reconcile();
  // retrato inicial das bibliotecas, mesmo que o Steam deixe de chamar o callback no registro
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
      routerHook.removePatch("/library", libraryPatch);
      stopBadges();
    },
  };
});

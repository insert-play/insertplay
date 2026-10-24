import { addEventListener, definePlugin, removeEventListener } from "@decky/api";
import { ButtonItem, PanelSection, PanelSectionRow, ToggleField, staticClasses } from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { FaSdCard } from "react-icons/fa";
import { Card, LogEntry, Settings, currentCards, getSettings, logTail, setSetting } from "./backend";
import { launchCard, onInserted, onRemoved, reconcile, reinstall } from "./flow";

const REFRESH_EVENTS = ["card_inserted", "card_removed"] as const;

function Panel() {
  const [cards, setCards] = useState<Card[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [log, setLog] = useState<LogEntry[]>([]);

  const refresh = useCallback(async () => {
    const [c, s, l] = await Promise.all([currentCards(), getSettings(), logTail(8)]);
    if (Array.isArray(c)) setCards(c); // null = backend ainda varrendo; mantém o que havia
    setSettings(s);
    setLog(l);
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
      <PanelSection title="Configurações">
        <PanelSectionRow>
          <ToggleField
            label="Iniciar ao inserir"
            checked={settings?.autoLaunch ?? true}
            disabled={!settings}
            onChange={(value) => void setSetting("autoLaunch", value).then(setSettings)}
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
  const inserted = addEventListener<[Card]>("card_inserted", (card) => void onInserted(card));
  const removed = addEventListener<[{ mount: string; label: string }]>("card_removed", (card) => void onRemoved(card));
  void reconcile();
  return {
    name: "InsertPlay",
    titleView: <div className={staticClasses.Title}>InsertPlay</div>,
    content: <Panel />,
    icon: <FaSdCard />,
    onDismount() {
      removeEventListener("card_inserted", inserted);
      removeEventListener("card_removed", removed);
    },
  };
});

import {
  ButtonItem,
  ConfirmModal,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  Spinner,
  ToggleField,
  showModal,
} from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import {
  Card,
  OnInsert,
  PortableMode,
  PortableStatus,
  Settings,
  currentCards,
  getSettings,
  portableStatus,
  setSetting,
} from "../backend";
import {
  PortableCard,
  applyHideRemoved,
  launchCard,
  portableChange,
  reinstall,
  setPortable,
  settlePortableChange,
  steamCardsPresent,
} from "../flow";
import { AchievementsSection } from "./achievements";
import { useRefresh } from "./common";

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
  // disabled and with a spinner. The change lives in flow.ts and survives the page remounting.
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

/** "Cartões": inserted cards, portable saves, what happens on insert and on removal, and
 *  RetroAchievements (the cards' achievements). */
export function CardsPage() {
  const [cards, setCards] = useState<Card[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [portable, setPortableCards] = useState<{ card: PortableCard; status?: PortableStatus }[]>([]);

  const load = useCallback(async () => {
    const [c, s] = await Promise.all([currentCards(), getSettings()]);
    if (Array.isArray(c)) setCards(c); // null = backend still scanning; keep what was there
    setSettings(s);
    const present = await Promise.all(
      steamCardsPresent().map(async (card) => ({ card, status: (await portableStatus(card.mount)) ?? undefined })),
    );
    // only cards with Steam games; an enabled card left with no games stays, to turn it off
    setPortableCards(present.filter(({ card, status }) => card.apps.length > 0 || (status && status.mode !== "off")));
  }, []);
  const refresh = useCallback(() => void load(), [load]);
  useRefresh(refresh);

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
                <ButtonItem layout="below" onClick={() => void reinstall(card).then(load)}>
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
            <PortableRow key={card.mount} card={card} status={status} onChange={refresh} />
          ))}
        </PanelSection>
      )}
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
      <AchievementsSection />
    </>
  );
}

import { ButtonItem, PanelSection, PanelSectionRow } from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import { Card, RemoteGame, currentCards } from "../backend";
import { onRemoteLibraryChange, refreshLibrary, remoteLibraryGames } from "../flow";
import { remoteDetail, useRefresh } from "./common";
import { Category, openCategory } from "./route";

const IN_PROGRESS: RemoteGame["state"][] = ["downloading", "queued", "paused"];

const CATEGORIES: { category: Category; label: string }[] = [
  { category: "cartoes", label: "Cartões" },
  { category: "biblioteca", label: "Biblioteca Remota" },
  { category: "geral", label: "Geral" },
];

/** Quick Access panel (issue #53): a short summary and one button per category. The
 *  settings live in the full-screen route. */
export function Panel() {
  const [cards, setCards] = useState<Card[]>([]);
  const [games, setGames] = useState<RemoteGame[] | null>(remoteLibraryGames());

  const refresh = useCallback(() => {
    void currentCards().then((c) => {
      if (Array.isArray(c)) setCards(c); // null = backend still scanning; keep what was there
    });
  }, []);
  useRefresh(refresh);
  useEffect(() => {
    const stop = onRemoteLibraryChange(() => setGames(remoteLibraryGames()));
    void refreshLibrary();
    return stop;
  }, []);

  const downloads = (games ?? []).filter((game) => IN_PROGRESS.includes(game.state));
  return (
    <>
      <PanelSection title="Cartões">
        {cards.length === 0 && <PanelSectionRow>Nenhum cartão InsertPlay inserido.</PanelSectionRow>}
        {cards.map((card) => (
          <PanelSectionRow key={card.mount}>
            <div style={{ fontWeight: "bold" }}>{card.appName ?? card.label}</div>
            {card.error && (
              <div style={{ fontSize: "0.8em", color: "#e57373", wordBreak: "break-all" }}>Erro: {card.error}</div>
            )}
          </PanelSectionRow>
        ))}
      </PanelSection>
      {downloads.length > 0 && (
        <PanelSection title="Biblioteca Remota">
          {downloads.map((game) => (
            <PanelSectionRow key={game.id}>
              <div style={{ fontWeight: "bold" }}>{game.name}</div>
              <div style={{ fontSize: "0.8em", opacity: 0.7 }}>{remoteDetail(game)}</div>
            </PanelSectionRow>
          ))}
        </PanelSection>
      )}
      <PanelSection>
        {CATEGORIES.map(({ category, label }) => (
          <PanelSectionRow key={category}>
            <ButtonItem layout="below" onClick={() => openCategory(category)}>
              {label}
            </ButtonItem>
          </PanelSectionRow>
        ))}
      </PanelSection>
    </>
  );
}

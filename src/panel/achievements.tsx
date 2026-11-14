import { addEventListener, removeEventListener, toaster } from "@decky/api";
import { ButtonItem, Navigation, PanelSection, PanelSectionRow, TextField } from "@decky/ui";
import { useEffect, useState } from "react";
import { RaLoginError, raLogin, raLogout, raStatus } from "../backend";
import { shownAppids } from "../achievements";
import { clearAchievements, loadAllAchievements } from "../flow";
import { SecretField, failed } from "./common";

const RA_KEYS_URL = "https://retroachievements.org/settings";

const LOGIN_ERROR: Record<RaLoginError, string> = {
  invalid: "Usuário ou chave em formato inválido.",
  refused: "O RetroAchievements recusou o usuário ou a chave.",
  network: "Sem resposta do RetroAchievements. Confira a rede.",
  failed: "Falha no login (veja o log de eventos).",
};

/** Plugin's own RetroAchievements login (issue #24): user + web API key, used only to
 *  read progress. The emulators keep their own login. */
export function AchievementsSection() {
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
    // a refusal while the page is open must show up too
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
            <SecretField label="Web API Key" value={key} disabled={busy} onChange={setKey} />
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

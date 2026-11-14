import { addEventListener, removeEventListener, toaster } from "@decky/api";
import {
  ButtonItem,
  ConfirmModal,
  Navigation,
  PanelSection,
  PanelSectionRow,
  TextField,
  ToggleField,
  showModal,
} from "@decky/ui";
import { useCallback, useEffect, useState } from "react";
import {
  CardPackage,
  LogEntry,
  PackageStatus,
  RaLoginError,
  Settings,
  getSettings,
  logTail,
  packageStatus,
  raLogin,
  raLogout,
  raStatus,
  setSetting,
  writePackageToCard,
} from "../backend";
import { shownAppids } from "../achievements";
import { applyHideRemoved, clearAchievements, loadAllAchievements } from "../flow";
import { InstallType, installFromCard } from "../selfupdate";
import { SecretField, failed, useRefresh } from "./common";

const RELATION_TEXT: Record<CardPackage["relation"], string> = {
  missing: "sem pacote do plugin",
  invalid: "pacote inválido",
  newer: "versão mais nova",
  older: "versão mais antiga",
  same: "mesma versão",
};

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

/** "Geral": achievements, the plugin package (version, update, write to card), settings and
 *  the event log. */
export function GeneralPage() {
  const [log, setLog] = useState<LogEntry[]>([]);
  const [pkg, setPkg] = useState<PackageStatus | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);

  const refresh = useCallback(() => {
    void Promise.all([logTail(8), packageStatus(), getSettings()]).then(([l, p, s]) => {
      setLog(l);
      if (p) setPkg(p);
      setSettings(s);
    });
  }, []);
  useRefresh(refresh);

  return (
    <>
      <AchievementsSection />
      <PanelSection title="Plugin">
        <PanelSectionRow>
          <div style={{ fontSize: "0.85em" }}>Instalado: {pkg ? `v${pkg.installed}` : "…"}</div>
        </PanelSectionRow>
        {pkg?.cards.map((p) => (
          <PackageRow key={p.mount} pkg={p} installed={pkg.installed} onChange={refresh} />
        ))}
      </PanelSection>
      <PanelSection title="Configurações">
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
          <ButtonItem layout="below" onClick={refresh}>
            Atualizar
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

import { ButtonItem, ConfirmModal, PanelSection, PanelSectionRow, TextField, ToggleField, showModal } from "@decky/ui";
import { useEffect, useState } from "react";
import {
  RemoteGame,
  SaveStatus,
  Settings,
  getSettings,
  remoteGetConfig,
  remoteSetConfig,
  savesOverview,
  setSetting,
} from "../backend";
import {
  RemoteAction,
  onRemoteLibraryChange,
  refreshLibrary,
  refreshRemoteIndex,
  remoteAction,
  remoteLibraryGames,
} from "../flow";
import { SecretField, failed, gb, remoteDetail } from "./common";

function ago(at: number): string {
  const s = Math.max(0, Date.now() / 1000 - at);
  if (s < 60) return "agora";
  if (s < 3600) return `há ${Math.floor(s / 60)} min`;
  if (s < 86400) return `há ${Math.floor(s / 3600)} h`;
  return `em ${new Date(at * 1000).toLocaleDateString("pt-BR")}`;
}

/** Save sync line of one game (#63); null = nothing to say. */
function saveLine(save: SaveStatus | undefined): { text: string; error?: boolean } | null {
  if (!save) return null;
  if (save.pending) return { text: "Save: envio pendente (servidor fora do ar)" };
  const r = save.last;
  if (!r) return null;
  switch (r.result) {
    case "uploaded":
    case "conflict_uploaded":
      return { text: `Save enviado ${ago(r.at)}` };
    case "downloaded":
    case "conflict_downloaded":
      return { text: `Save baixado ${ago(r.at)}${r.fromDevice ? ` (feito em ${r.fromDevice})` : ""}` };
    case "unchanged":
      return { text: `Save em dia ${ago(r.at)}` };
    case "empty":
      return { text: "Ainda sem save" };
    case "offline":
      return { text: `Servidor de saves fora do ar ${ago(r.at)}` };
    case "no_strategy":
      return { text: "Saves não identificados: jogo sem sincronização", error: true };
    case "error":
      return { text: `Erro no save: ${r.message}`, error: true };
    default:
      return null;
  }
}

/** One library game: state and the actions that apply to it. */
function RemoteGameRow({ game, save, syncOn }: { game: RemoteGame; save?: SaveStatus; syncOn: boolean }) {
  const [busy, setBusy] = useState(false);
  const run = (action: RemoteAction) => {
    setBusy(true);
    void remoteAction(game.id, action).finally(() => setBusy(false));
  };
  // From the full-screen route the modal opens over the page in the Big Picture window, and
  // the page stays open after OK or cancel (measured, issue #53).
  const confirm = (action: "cancel" | "uninstall") =>
    showModal(
      <ConfirmModal
        strTitle={action === "uninstall" ? `Desinstalar ${game.name}` : `Cancelar ${game.name}`}
        strDescription={
          action === "uninstall"
            ? `Apaga ${game.name} deste aparelho (${gb(game.bytesTotal)}), inclusive saves gravados dentro da pasta do jogo (memory cards, saves do Wine). O atalho volta a "Instalar".${
                syncOn ? " Com a sincronização de saves ligada, o último save enviado continua no servidor." : ""
              }`
            : `Para o download de ${game.name} e apaga o que já foi baixado.`
        }
        strOKButtonText={action === "uninstall" ? "Desinstalar" : "Cancelar download"}
        strCancelButtonText="Voltar"
        onOK={() => run(action)}
      />,
    );
  const s = game.state;
  const line = syncOn ? saveLine(save) : null;
  return (
    <PanelSectionRow>
      <div style={{ fontWeight: "bold" }}>{game.name}</div>
      <div style={{ fontSize: "0.8em", opacity: 0.7, color: s === "error" ? "#e57373" : undefined }}>
        {remoteDetail(game)}
      </div>
      {line && (
        <div style={{ fontSize: "0.8em", opacity: 0.7, color: line.error ? "#e57373" : undefined }}>{line.text}</div>
      )}
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
export function RemotePage() {
  // undefined = still asking the backend
  const [config, setConfig] = useState<{ url: string | null; tokenSet: boolean } | null | undefined>(undefined);
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [games, setGames] = useState<RemoteGame[] | null>(remoteLibraryGames());
  const [editing, setEditing] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [saves, setSaves] = useState<Record<string, SaveStatus>>({});

  useEffect(() => {
    void remoteGetConfig().then((c) => {
      setConfig(c);
      setUrl(c?.url ?? "");
    });
    void getSettings().then(setSettings);
    const readSaves = () => void savesOverview().then((o) => o && setSaves(o));
    readSaves();
    const stop = onRemoteLibraryChange(() => setGames(remoteLibraryGames()));
    // the save lines change when a game opens or closes, which raises no library event
    const timer = setInterval(readSaves, 10_000);
    // the event while downloading; the list itself is read by flow.ts
    void refreshLibrary();
    return () => {
      stop();
      clearInterval(timer);
    };
  }, []);
  const toggle = (key: "savesSync" | "savesBackup", value: boolean) =>
    void setSetting(key, value).then((next) => next && setSettings(next));

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
            <SecretField
              label="Token"
              description={config?.tokenSet ? "Token salvo. Deixe vazio para manter." : undefined}
              value={token}
              disabled={busy}
              onChange={setToken}
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
      {configured && (
        <>
          <PanelSectionRow>
            <ToggleField
              label="Sincronizar saves"
              description="Guarda no servidor o save mais recente de cada jogo da biblioteca, para continuar em outro aparelho. Vale também para um cartão do mesmo jogo."
              checked={settings?.savesSync ?? false}
              disabled={!settings}
              onChange={(value) => toggle("savesSync", value)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <ToggleField
              label="Guardar backup dos saves"
              description="Ao trocar o save, o anterior fica guardado no servidor (até 50 MB por jogo; um save maior fica sem backup)."
              checked={settings?.savesBackup ?? true}
              disabled={!settings || !settings.savesSync}
              onChange={(value) => toggle("savesBackup", value)}
            />
          </PanelSectionRow>
        </>
      )}
      {configured &&
        (games ?? []).map((game) => (
          <RemoteGameRow key={game.id} game={game} save={saves[game.id]} syncOn={Boolean(settings?.savesSync)} />
        ))}
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

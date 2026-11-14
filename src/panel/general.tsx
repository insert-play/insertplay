import { toaster } from "@decky/api";
import { ButtonItem, ConfirmModal, PanelSection, PanelSectionRow, showModal } from "@decky/ui";
import { useCallback, useState } from "react";
import { CardPackage, LogEntry, PackageStatus, logTail, packageStatus, writePackageToCard } from "../backend";
import { InstallType, installFromCard } from "../selfupdate";
import { failed, useRefresh } from "./common";

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

/** "Geral": the plugin package (version, update, write to card) and the event log. */
export function GeneralPage() {
  const [log, setLog] = useState<LogEntry[]>([]);
  const [pkg, setPkg] = useState<PackageStatus | null>(null);

  const refresh = useCallback(() => {
    void Promise.all([logTail(8), packageStatus()]).then(([l, p]) => {
      setLog(l);
      if (p) setPkg(p);
    });
  }, []);
  useRefresh(refresh);

  return (
    <>
      <PanelSection title="Plugin">
        <PanelSectionRow>
          <div style={{ fontSize: "0.85em" }}>Instalado: {pkg ? `v${pkg.installed}` : "…"}</div>
        </PanelSectionRow>
        {pkg?.cards.map((p) => (
          <PackageRow key={p.mount} pkg={p} installed={pkg.installed} onChange={refresh} />
        ))}
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

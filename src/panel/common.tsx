import { addEventListener, removeEventListener, toaster } from "@decky/api";
import { TextField } from "@decky/ui";
import { useEffect } from "react";
import { RemoteGame, logAdd } from "../backend";
import { onPortableChange } from "../flow";

const REFRESH_EVENTS = ["card_inserted", "card_removed"] as const;

export function failed(what: string, err: unknown) {
  const detail = err instanceof Error ? err.message : String(err);
  void logAdd("error", `${what}: ${detail}`).catch(() => undefined);
  toaster.toast({ title: "InsertPlay", body: `${what} falhou: ${detail}`, duration: 10000 });
}

/** Calls `refresh` now, after a card is inserted or removed, when portable saves change and
 *  every 5 s, until the component unmounts. Decky keeps the Quick Access panel mounted between
 *  openings; without the timer it would show a stale state (measured on the Deck). The pages
 *  of the full-screen route mount when opened and unmount when left (measured, issue #53). */
export function useRefresh(refresh: () => void) {
  useEffect(() => {
    refresh();
    // wait for the flow.ts queue to handle the event before re-reading the state
    const later = () => setTimeout(refresh, 1500);
    const subs = REFRESH_EVENTS.map((name) => addEventListener(name, later));
    // start and end of a portable saves change: redraw now, with the new status
    const stopPortable = onPortableChange(refresh);
    const timer = setInterval(refresh, 5000);
    return () => {
      clearInterval(timer);
      stopPortable();
      REFRESH_EVENTS.forEach((name, i) => removeEventListener(name, subs[i]));
    };
  }, [refresh]);
}

/** `bIsPassword` does not mask the field in this Steam (measured 2026-11-13: the input stays
 *  `type="text"` and shows what is typed), so the characters are hidden with CSS. */
const SECRET_CLASS = "insertplay-secret";
export function SecretField(props: {
  label: string;
  description?: string;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <div className={SECRET_CLASS}>
      <style>{`.${SECRET_CLASS} input { -webkit-text-security: disc; }`}</style>
      <TextField
        label={props.label}
        description={props.description}
        value={props.value}
        bIsPassword
        disabled={props.disabled}
        onChange={(e) => props.onChange(e.target.value)}
      />
    </div>
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

export function gb(bytes: number): string {
  return `${(bytes / 1024 ** 3).toFixed(bytes >= 10 * 1024 ** 3 ? 0 : 1).replace(".", ",")} GB`;
}

export function remoteDetail(game: RemoteGame): string {
  const state = REMOTE_STATE_TEXT[game.state];
  if (game.state === "downloading" || game.state === "paused" || game.state === "queued") {
    const speed = game.state === "downloading" && game.speed ? ` · ${(game.speed / 1024 ** 2).toFixed(1).replace(".", ",")} MB/s` : "";
    return `${state} ${game.percent}% · ${gb(game.bytesDone)} de ${gb(game.bytesTotal)}${speed}`;
  }
  if (game.state === "error") return `${state}: ${game.error?.message ?? "veja o log de eventos"}`;
  if (game.state === "installed") return `${state} · ${gb(game.bytesTotal)}${game.inIndex ? "" : " · fora da biblioteca"}`;
  return `${state} · ${gb(game.bytesTotal)}`;
}

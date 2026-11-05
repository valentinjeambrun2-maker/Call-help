// État des services du backend (GET /status) : la transcription en direct (ElevenLabs) et les
// conseils IA (Claude) sont-ils prêts, et sinon pourquoi, en clair. Affiché avant de lancer un
// appel ou une démo, pour ne pas découvrir le problème en plein milieu.
import { useCallback, useEffect, useState } from "react";

interface Check {
  ready: boolean;
  message: string;
  source: string | null;
}

export interface ServiceStatus {
  transcription: Check;
  llm: Check;
  env_notes: string[];
}

type Loaded =
  | { kind: "ok"; status: ServiceStatus }
  | { kind: "stale" } // backend lancé avant la mise à jour (pas de /status)
  | { kind: "broken" } // /status en erreur
  | { kind: "down" }
  | { kind: "loading" };

/** `refreshKey` : refait le diagnostic quand il change (fin d'un appel, par exemple). */
export function useServiceStatus(api: string, refreshKey?: unknown) {
  const [state, setState] = useState<Loaded>({ kind: "loading" });
  const load = useCallback(
    (refresh = false) => {
      let cancelled = false;
      setState({ kind: "loading" });
      fetch(`${api}/status${refresh ? "?refresh=true" : ""}`)
        .then(async (r): Promise<Loaded> => {
          if (r.status === 404) return { kind: "stale" }; // backend lancé avant la mise à jour
          if (!r.ok) return { kind: "broken" };
          return { kind: "ok", status: (await r.json()) as ServiceStatus };
        })
        .then((v) => {
          if (!cancelled) setState(v);
        })
        .catch(() => {
          if (!cancelled) setState({ kind: "down" });
        });
      return () => {
        cancelled = true;
      };
    },
    [api],
  );
  useEffect(() => load(), [load, refreshKey]);
  // Retour sur la fenêtre (après avoir corrigé backend/.env, par exemple) : on refait le point.
  useEffect(() => {
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [load]);
  // Backend pas encore lancé : on réessaie jusqu'à ce qu'il réponde.
  useEffect(() => {
    if (state.kind !== "down") return;
    const id = window.setTimeout(() => load(), 5000);
    return () => clearTimeout(id);
  }, [state.kind, load]);
  return { service: state, recheck: () => load(true) };
}

/** Avertissements à montrer : rien quand tout est prêt. `needsTranscription` : faux en mode
 * « Texte de référence », qui n'utilise pas ElevenLabs. */
export function ServiceNotices({ service, recheck, needsTranscription = true, transcriptionFallback }: {
  service: Loaded; recheck: () => void; needsTranscription?: boolean; transcriptionFallback?: string;
}) {
  if (service.kind === "loading" || service.kind === "down") return null; // « backend injoignable » est dit ailleurs
  if (service.kind === "broken") {
    return <p className="notice">Le diagnostic des clés a échoué : voir le terminal du backend.</p>;
  }
  if (service.kind === "stale") {
    return (
      <p className="notice">
        Le backend lancé est une ancienne version : arrêtez-le (Ctrl+C dans son terminal) et relancez-le.
      </p>
    );
  }
  const { transcription, llm, env_notes } = service.status;
  const lines: string[] = [...env_notes];
  if (needsTranscription && !transcription.ready) {
    lines.push(`Transcription en direct indisponible : ${transcription.message}${transcriptionFallback ? ` ${transcriptionFallback}` : ""}`);
  }
  if (!llm.ready) lines.push(`Conseils IA (Claude) indisponibles : ${llm.message}`);
  if (!lines.length) return null;
  return (
    <div className="notice service-notice" role="status">
      {lines.map((l) => <p key={l}>{l}</p>)}
      <button className="link" onClick={recheck}>Revérifier</button>
    </div>
  );
}

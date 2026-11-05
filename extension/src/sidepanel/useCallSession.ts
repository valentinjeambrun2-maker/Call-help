import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AiSummary, CallSummary, ClientMsg, EmotionReading, Hint, Metrics, ServerMsg, StepState,
} from "../shared/protocol";

export type Phase = "idle" | "connecting" | "live" | "ended" | "error";

export interface CallState {
  phase: Phase;
  step: StepState | null;
  metrics: Metrics | null;
  emotions: EmotionReading[];
  analysisPaused: boolean;
  announcementPending: boolean;
  prospectConsent: boolean | null;
  announcement: Hint | null; // message d'information, affiché tant qu'il n'est pas confirmé
  current: Hint | null;
  history: Hint[];
  summary: CallSummary | null;
  aiSummary: AiSummary | null;
  aiSummaryState: "none" | "pending" | "ready" | "failed";
  error: string | null;
  feedback: Record<string, boolean>;
}

const INITIAL: CallState = {
  phase: "idle", step: null, metrics: null, emotions: [], analysisPaused: false, announcementPending: false,
  prospectConsent: null, announcement: null, current: null, history: [], summary: null, aiSummary: null, aiSummaryState: "none", error: null, feedback: {},
};

// Un conseil reste à l'écran au moins ce temps, sauf s'il est plus urgent.
const MIN_DISPLAY_MS = 4000;
// Un conseil mis en attente plus longtemps que ça n'est plus d'actualité : il
// reste dans l'historique mais n'est plus promu à l'écran.
const MAX_QUEUE_AGE_MS = 15000;

interface Queued {
  hint: Hint;
  at: number;
}

export function useCallSession(backendUrl: string, onServerMessage?: (msg: ServerMsg) => void) {
  // Observateur facultatif de tous les messages (page de démo : mesure de latence, revue).
  const observer = useRef(onServerMessage);
  observer.current = onServerMessage;
  const [state, setState] = useState<CallState>(INITIAL);
  const ws = useRef<WebSocket | null>(null);
  const connectedAt = useRef(0);
  const summaryReceived = useRef(false);

  // Affichage des conseils : décidé hors de setState (les fonctions de mise à jour
  // doivent rester pures, React les exécute deux fois en mode strict).
  const current = useRef<Hint | null>(null);
  const shownAt = useRef(0);
  const queue = useRef<Queued[]>([]);
  const timer = useRef<number | null>(null);

  const clearTimer = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  };

  const show = useCallback((hint: Hint) => {
    current.current = hint;
    shownAt.current = Date.now();
    setState((s) => ({ ...s, current: hint }));
  }, []);

  // Promeut le conseil en attente le plus prioritaire dès que l'affiché a eu son temps.
  const schedule = useCallback(() => {
    clearTimer();
    if (queue.current.length === 0) return;
    const wait = Math.max(0, MIN_DISPLAY_MS - (Date.now() - shownAt.current));
    timer.current = window.setTimeout(() => {
      timer.current = null;
      const now = Date.now();
      queue.current = queue.current.filter((q) => now - q.at < MAX_QUEUE_AGE_MS);
      const next = queue.current.shift();
      if (next) show(next.hint);
      schedule();
    }, wait);
  }, [show]);

  const onHint = useCallback(
    (hint: Hint) => {
      if (hint.kind === "announcement") {
        // Carte dédiée : le choix accord / refus ne doit jamais être masqué.
        setState((s) => ({ ...s, announcement: hint }));
        return;
      }

      setState((s) => {
        const history = hint.replaces
          ? s.history.map((h) => (h.id === hint.replaces ? hint : h))
          : [hint, ...s.history].slice(0, 100);
        // Le vote donné au conseil d'origine vaut pour sa version affinée.
        const voted = hint.replaces ? s.feedback[hint.replaces] : undefined;
        const feedback = voted === undefined ? s.feedback : { ...s.feedback, [hint.id]: voted };
        return { ...s, history, feedback };
      });

      if (hint.replaces) {
        // Version affinée par l'IA : remplace l'original là où il se trouve.
        if (current.current?.id === hint.replaces) {
          current.current = hint;
          setState((s) => ({ ...s, current: hint }));
        } else {
          queue.current = queue.current.map((q) => (q.hint.id === hint.replaces ? { ...q, hint } : q));
        }
        return;
      }

      const shown = current.current;
      const canShow =
        !shown ||
        hint.priority > shown.priority ||
        (hint.priority >= 3 && shown.priority >= 3) || // une objection peut en remplacer une autre
        Date.now() - shownAt.current > MIN_DISPLAY_MS;
      if (canShow) {
        show(hint);
      } else {
        queue.current = [...queue.current, { hint, at: Date.now() }].sort((a, b) => b.hint.priority - a.hint.priority);
      }
      schedule();
    },
    [schedule, show],
  );

  const send = useCallback((msg: ClientMsg) => {
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(msg));
  }, []);

  // Audio pour la transcription en direct : 1 octet de canal (0 = vous, 1 = prospect) + PCM 16 kHz.
  const sendAudio = useCallback((frame: Uint8Array) => {
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(frame);
  }, []);

  const resetDisplay = () => {
    clearTimer();
    queue.current = [];
    current.current = null;
    shownAt.current = 0;
  };

  const connect = useCallback(
    (start: Extract<ClientMsg, { type: "start" }>) =>
      new Promise<void>((resolve, reject) => {
        const previous = ws.current;
        ws.current = null; // les événements de l'ancienne connexion sont ignorés
        previous?.close();
        resetDisplay();
        summaryReceived.current = false;
        setState({ ...INITIAL, phase: "connecting" });
        const socket = new WebSocket(backendUrl);
        ws.current = socket;
        const mine = () => ws.current === socket;

        socket.onopen = () => {
          if (!mine()) return;
          connectedAt.current = Date.now();
          socket.send(JSON.stringify(start));
          setState((s) => ({ ...s, phase: "live", error: null }));
          resolve();
        };
        socket.onerror = () => {
          if (!mine()) return;
          setState((s) => ({ ...s, phase: "error", error: `Backend injoignable (${backendUrl})` }));
          reject(new Error("ws error"));
        };
        socket.onclose = () => {
          if (!mine() || summaryReceived.current) return;
          clearTimer();
          setState((s) =>
            s.phase === "error" ? s : { ...s, phase: "error", error: "Connexion au backend perdue. Relancez l'appel." },
          );
        };
        socket.onmessage = (ev) => {
          if (!mine()) return;
          let msg: ServerMsg;
          try {
            msg = JSON.parse(ev.data) as ServerMsg;
          } catch {
            return;
          }
          observer.current?.(msg);
          if (msg.type === "state") {
            setState((s) => ({
              ...s, step: msg.step, metrics: msg.metrics, emotions: msg.emotions,
              analysisPaused: msg.analysis_paused, announcementPending: msg.announcement_pending,
              prospectConsent: msg.prospect_consent,
            }));
          } else if (msg.type === "hint") {
            onHint(msg.hint);
          } else if (msg.type === "summary") {
            // Le serveur ferme la connexion après le bilan IA éventuel.
            summaryReceived.current = true;
            clearTimer();
            setState((s) => ({ ...s, phase: "ended", summary: msg.summary }));
          } else if (msg.type === "ai_summary_pending") {
            setState((s) => ({ ...s, aiSummaryState: "pending" }));
          } else if (msg.type === "ai_summary") {
            setState((s) => ({ ...s, aiSummary: msg.summary, aiSummaryState: msg.summary ? "ready" : "failed" }));
            socket.close();
          } else if (msg.type === "error") {
            setState((s) => ({ ...s, error: msg.message }));
          }
        };
      }),
    [backendUrl, onHint],
  );

  const giveFeedback = useCallback(
    (hintId: string, useful: boolean) => {
      send({ type: "feedback", hint_id: hintId, useful });
      setState((s) => ({ ...s, feedback: { ...s.feedback, [hintId]: useful } }));
    },
    [send],
  );

  // Secondes écoulées depuis le début de l'appel (horloge du protocole).
  const elapsed = useCallback(() => (Date.now() - connectedAt.current) / 1000, []);

  const answerAnnouncement = useCallback(
    (consent: boolean) => send({ type: "announcement_done", ts: elapsed(), consent }),
    [send, elapsed],
  );

  const fail = useCallback((message: string) => {
    setState((s) => ({ ...s, phase: "error", error: message }));
  }, []);

  useEffect(
    () => () => {
      clearTimer();
      const socket = ws.current;
      ws.current = null;
      socket?.close();
    },
    [],
  );

  return { state, connect, send, sendAudio, giveFeedback, answerAnnouncement, elapsed, fail };
}

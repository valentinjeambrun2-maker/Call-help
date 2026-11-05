import { useCallback, useEffect, useRef, useState } from "react";
import type {
  CallSummary, ClientMsg, EmotionReading, Hint, Metrics, ServerMsg, StepState,
} from "../shared/protocol";

export type Phase = "idle" | "connecting" | "live" | "ended" | "error";

export interface CallState {
  phase: Phase;
  step: StepState | null;
  metrics: Metrics | null;
  emotions: EmotionReading[];
  analysisPaused: boolean;
  announcementPending: boolean;
  announcement: Hint | null; // message d'information, affiché tant qu'il n'est pas confirmé
  current: Hint | null;
  history: Hint[];
  summary: CallSummary | null;
  error: string | null;
  feedback: Record<string, boolean>;
}

const INITIAL: CallState = {
  phase: "idle", step: null, metrics: null, emotions: [], analysisPaused: false, announcementPending: false,
  announcement: null, current: null, history: [], summary: null, error: null, feedback: {},
};

// Un conseil reste à l'écran au moins ce temps, sauf s'il est plus urgent.
const MIN_DISPLAY_MS = 4000;

export function useCallSession(backendUrl: string) {
  const [state, setState] = useState<CallState>(INITIAL);
  const ws = useRef<WebSocket | null>(null);
  const shownAt = useRef(0);
  const connectedAt = useRef(0);

  const current = useRef<Hint | null>(null);

  const onHint = useCallback((hint: Hint) => {
    if (hint.kind === "announcement") {
      // Carte dédiée, indépendante du conseil courant : le bouton de confirmation
      // ne doit jamais être masqué par un autre conseil.
      setState((s) => ({ ...s, announcement: hint }));
      return;
    }
    // La décision d'affichage est prise hors de setState : les fonctions de mise à
    // jour doivent rester pures (React les exécute deux fois en mode strict).
    const shown = current.current;
    const canShow =
      !shown ||
      (hint.replaces !== null && shown.id === hint.replaces) ||
      hint.priority > shown.priority ||
      Date.now() - shownAt.current > MIN_DISPLAY_MS;
    if (canShow) {
      current.current = hint;
      shownAt.current = Date.now();
    }
    setState((s) => ({
      ...s,
      // Le conseil LLM remplace le conseil par règle qu'il affine.
      history: hint.replaces
        ? s.history.map((h) => (h.id === hint.replaces ? hint : h))
        : [hint, ...s.history].slice(0, 30),
      current: canShow ? hint : s.current,
    }));
  }, []);

  const send = useCallback((msg: ClientMsg) => {
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(msg));
  }, []);

  const connect = useCallback(
    (start: Extract<ClientMsg, { type: "start" }>) =>
      new Promise<void>((resolve, reject) => {
        ws.current?.close();
        current.current = null;
        setState({ ...INITIAL, phase: "connecting" });
        const socket = new WebSocket(backendUrl);
        ws.current = socket;
        socket.onopen = () => {
          connectedAt.current = Date.now();
          socket.send(JSON.stringify(start));
          setState((s) => ({ ...s, phase: "live" }));
          resolve();
        };
        socket.onerror = () => {
          setState((s) => ({ ...s, phase: "error", error: `Backend injoignable (${backendUrl})` }));
          reject(new Error("ws error"));
        };
        socket.onmessage = (ev) => {
          const msg = JSON.parse(ev.data) as ServerMsg;
          if (msg.type === "state") {
            setState((s) => ({
              ...s, step: msg.step, metrics: msg.metrics, emotions: msg.emotions,
              analysisPaused: msg.analysis_paused, announcementPending: msg.announcement_pending,
            }));
          } else if (msg.type === "hint") {
            onHint(msg.hint);
          } else if (msg.type === "summary") {
            setState((s) => ({ ...s, phase: "ended", summary: msg.summary }));
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

  const confirmAnnouncement = useCallback(() => {
    send({ type: "announcement_done", ts: elapsed() });
  }, [send, elapsed]);

  useEffect(() => () => ws.current?.close(), []);

  return { state, connect, send, giveFeedback, confirmAnnouncement, elapsed };
}

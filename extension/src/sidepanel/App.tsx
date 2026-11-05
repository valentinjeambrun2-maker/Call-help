import { useEffect, useRef, useState } from "react";
import { DEFAULT_BACKEND, type ClientMsg, type EmotionReading, type ExtensionMsg } from "../shared/protocol";
import { useCallSession } from "./useCallSession";

const EMOTION_LABELS: Record<string, { label: string; tone: "good" | "warn" | "bad" | "neutral" }> = {
  interest: { label: "Intéressé", tone: "good" },
  enthusiasm: { label: "Enthousiaste", tone: "good" },
  hesitation: { label: "Hésitant", tone: "warn" },
  boredom: { label: "Décroche", tone: "warn" },
  annoyance: { label: "Agacé", tone: "bad" },
};

const KIND_LABELS: Record<string, string> = {
  announcement: "Message d'information", objection: "Objection", step: "Étape", pace: "Rythme", emotion: "Ressenti",
  question: "Question", closing: "Closing",
};

function httpBase(wsUrl: string): string {
  return wsUrl.replace(/^ws/, "http").replace(/\/ws\/session$/, "");
}

function eventTime(e: ClientMsg): number {
  if (e.type === "transcript") return e.end;
  if (e.type === "voice" || e.type === "face") return e.ts;
  return Number.POSITIVE_INFINITY;
}

const hasChrome = typeof chrome !== "undefined" && !!chrome.runtime?.id;

export function App() {
  const [backendUrl, setBackendUrl] = useState(DEFAULT_BACKEND);
  const [selfAnalysis, setSelfAnalysis] = useState(true); // Q36 : actif par défaut
  const [capture, setCapture] = useState<string | null>(null);
  const { state, connect, send, giveFeedback, confirmAnnouncement } = useCallSession(backendUrl);
  const timers = useRef<number[]>([]);

  useEffect(() => {
    if (!hasChrome) return;
    chrome.storage.local.get("backendUrl").then((v) => {
      if (typeof v.backendUrl === "string") setBackendUrl(v.backendUrl);
    });
    const listener = (msg: ExtensionMsg) => {
      if (msg.target === "sidepanel" && msg.action === "capture-status") {
        setCapture(msg.status === "error" ? `Erreur de capture : ${msg.detail}` : msg.status === "running" ? "Capture de l'onglet active" : null);
      }
    };
    chrome.runtime.onMessage.addListener(listener);
    return () => chrome.runtime.onMessage.removeListener(listener);
  }, []);

  const clearTimers = () => {
    timers.current.forEach((t) => clearTimeout(t));
    timers.current = [];
  };

  const startDemo = async () => {
    clearTimers();
    const demo = await fetch(`${httpBase(backendUrl)}/demo`).then((r) => r.json());
    await connect({ type: "start", scenario_id: "cold_call_fr", language: "fr", self_analysis: selfAnalysis, context: demo.context });
    // Rejoue l'appel en temps réel, comme s'il arrivait du micro et de l'onglet.
    for (const event of demo.events as ClientMsg[]) {
      const at = event.type === "stop" ? Math.max(...demo.events.map(eventTime).filter(Number.isFinite)) + 3 : eventTime(event);
      timers.current.push(window.setTimeout(() => send(event), at * 1000));
    }
  };

  const startCall = async () => {
    clearTimers();
    await connect({ type: "start", scenario_id: "cold_call_fr", language: "fr", self_analysis: selfAnalysis });
    if (hasChrome) {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab?.id) await chrome.runtime.sendMessage({ target: "background", action: "start-capture", tabId: tab.id });
    }
  };

  const stopCall = () => {
    clearTimers();
    send({ type: "stop" });
    if (hasChrome) void chrome.runtime.sendMessage({ target: "background", action: "stop-capture" });
  };

  const live = state.phase === "live";
  const prospect = state.emotions.find((e) => e.speaker === "prospect");
  const user = state.emotions.find((e) => e.speaker === "user");

  return (
    <main className="panel">
      <header className="top">
        <strong>Call-help</strong>
        <span className={`dot ${state.phase}`} title={state.phase} />
      </header>

      {state.step && (
        <section className="steps" aria-label="Fil directeur">
          <ol>
            {Array.from({ length: state.step.total }, (_, i) => (
              <li key={i} className={i < state.step!.index ? "done" : i === state.step!.index ? "now" : ""} />
            ))}
          </ol>
          <div className="step-title">{state.step.title}</div>
          <div className="muted">{state.step.goal}</div>
        </section>
      )}

      {state.current && (
        <section className={`hint p${state.current.priority}`} aria-live="polite">
          <div className="hint-meta">
            <span>{KIND_LABELS[state.current.kind] ?? state.current.kind}</span>
            {state.current.source === "llm" && <span className="tag">affiné par l'IA</span>}
          </div>
          <p className="hint-text">{state.current.text}</p>
          {state.current.say && <blockquote>« {state.current.say} »</blockquote>}
          {state.current.kind === "announcement" && state.announcementPending && (
            <button className="primary confirm" onClick={confirmAnnouncement}>Message dit, démarrer l'analyse</button>
          )}
          {state.current.kind !== "announcement" && (
            <Feedback id={state.current.id} value={state.feedback[state.current.id]} onFeedback={giveFeedback} />
          )}
        </section>
      )}

      {(live || state.phase === "ended") && (
        <section className="row">
          <EmotionPill who="Prospect" reading={prospect} paused={state.analysisPaused} pending={state.announcementPending} />
          {selfAnalysis && <EmotionPill who="Vous" reading={user} paused={state.analysisPaused} pending={state.announcementPending} />}
        </section>
      )}

      {state.metrics && (
        <section className="metrics">
          <Metric label="Débit" value={`${Math.round(state.metrics.user_wpm)}`} unit="mots/min" warn={state.metrics.user_wpm > 180} />
          <Metric label="Temps de parole" value={`${Math.round(state.metrics.user_talk_ratio * 100)}`} unit="%" warn={state.metrics.user_talk_ratio > 0.7} />
          <Metric label="Monologue" value={`${Math.round(state.metrics.current_monologue)}`} unit="s" warn={state.metrics.current_monologue > 45} />
          <Metric label="Questions" value={`${state.metrics.questions_asked}`} unit="" />
        </section>
      )}

      {state.summary && (
        <section className="summary">
          <h2>Bilan</h2>
          <p>
            {Math.round(state.summary.duration)} s · étapes {state.summary.steps_reached.length} ·
            objections {state.summary.objections.length} · conseils utiles {state.summary.hints_useful}/{state.summary.hints_given}
          </p>
        </section>
      )}

      {state.error && <p className="error">{state.error}</p>}
      {capture && <p className="muted">{capture}</p>}

      <section className="controls">
        {!live ? (
          <>
            <button className="primary" onClick={startCall}>Démarrer l'appel</button>
            <button onClick={startDemo}>Démo cold call</button>
            <label className="check">
              <input type="checkbox" checked={selfAnalysis} onChange={(e) => setSelfAnalysis(e.target.checked)} />
              Analyser aussi mon ton
            </label>
          </>
        ) : (
          <>
            <button onClick={() => send({ type: "pause_analysis" })} disabled={state.analysisPaused}>
              {state.analysisPaused ? "Analyse coupée" : "Couper l'analyse"}
            </button>
            <button className="danger" onClick={stopCall}>Terminer</button>
          </>
        )}
      </section>

      {state.history.length > 1 && (
        <details className="history">
          <summary>Conseils précédents ({state.history.length - 1})</summary>
          <ul>
            {state.history.slice(1).map((h) => (
              <li key={h.id}><span className="muted">{Math.round(h.ts)} s</span> {h.text}</li>
            ))}
          </ul>
        </details>
      )}

      <p className="notice">
        Les images du visage sont analysées dans votre navigateur et ne sont jamais envoyées. Les indices émotionnels sont des
        hypothèses, pas des faits.
      </p>
    </main>
  );
}

function EmotionPill({ who, reading, paused, pending }: { who: string; reading?: EmotionReading; paused: boolean; pending: boolean }) {
  let text = "Calibration…";
  let tone = "neutral";
  if (pending) text = "En attente du message";
  else if (paused) text = "Analyse coupée";
  else if (reading?.calibrated && !reading.state) text = reading.face_visible === false ? "Visage non analysable" : "Neutre";
  else if (reading?.state) {
    const e = EMOTION_LABELS[reading.state];
    text = `${e.label}${reading.label === "probable" ? " (probable)" : ""}`;
    tone = e.tone;
  }
  return (
    <div className={`pill ${tone}`}>
      <span className="muted">{who}</span> {text}
    </div>
  );
}

function Metric({ label, value, unit, warn }: { label: string; value: string; unit: string; warn?: boolean }) {
  return (
    <div className={`metric ${warn ? "warn" : ""}`}>
      <div className="muted">{label}</div>
      <div><strong>{value}</strong> <span className="muted">{unit}</span></div>
    </div>
  );
}

function Feedback({ id, value, onFeedback }: { id: string; value?: boolean; onFeedback: (id: string, useful: boolean) => void }) {
  return (
    <div className="feedback">
      <button aria-label="Conseil utile" className={value === true ? "on" : ""} onClick={() => onFeedback(id, true)}>👍</button>
      <button aria-label="Conseil pas utile" className={value === false ? "on" : ""} onClick={() => onFeedback(id, false)}>👎</button>
    </div>
  );
}

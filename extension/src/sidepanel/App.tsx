import { useEffect, useRef, useState } from "react";
import {
  DEFAULT_BACKEND, type ClientMsg, type EmotionReading, type ExtensionMsg, type Hint, type Metrics,
} from "../shared/protocol";
import { useCallSession } from "./useCallSession";

type Tone = "good" | "warn" | "bad" | "neutral";

const EMOTIONS: Record<string, { label: string; tone: Tone }> = {
  interest: { label: "Intéressé", tone: "good" },
  enthusiasm: { label: "Enthousiaste", tone: "good" },
  hesitation: { label: "Hésitant", tone: "warn" },
  boredom: { label: "Décroche", tone: "warn" },
  annoyance: { label: "Agacé", tone: "bad" },
};

const KINDS: Record<Hint["kind"], { label: string; tone: "accent" | "bad" | "neutral" }> = {
  announcement: { label: "Message d'information", tone: "accent" },
  objection: { label: "Objection", tone: "bad" },
  step: { label: "Étape", tone: "accent" },
  pace: { label: "Rythme", tone: "neutral" },
  emotion: { label: "Ressenti", tone: "neutral" },
  question: { label: "Question", tone: "accent" },
  closing: { label: "Closing", tone: "accent" },
};

const PHASE_LABEL = { idle: "Prêt", connecting: "Connexion…", live: "En direct", ended: "Terminé", error: "Hors ligne" };

function httpBase(wsUrl: string): string {
  return wsUrl.replace(/^ws/, "http").replace(/\/ws\/session$/, "");
}

function eventTime(e: ClientMsg): number {
  if (e.type === "transcript") return e.end;
  if (e.type === "voice" || e.type === "face" || e.type === "announcement_done") return e.ts;
  return Number.POSITIVE_INFINITY;
}

function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// Typographie française : espaces insécables avant ? ! : ; et dans « ».
function fr(text: string): string {
  return text
    .replace(/\s+([?!;:»])/g, "\u202f$1")
    .replace(/«\s+/g, "«\u202f");
}

const hasChrome = typeof chrome !== "undefined" && !!chrome.runtime?.id;

export function App() {
  const [backendUrl, setBackendUrl] = useState(DEFAULT_BACKEND);
  const [selfAnalysis, setSelfAnalysis] = useState(true); // Q36 : actif par défaut
  const [capture, setCapture] = useState<string | null>(null);
  const [now, setNow] = useState(0);
  const { state, connect, send, giveFeedback, confirmAnnouncement, elapsed } = useCallSession(backendUrl);
  const timers = useRef<number[]>([]);
  const live = state.phase === "live";

  useEffect(() => {
    if (!hasChrome) return;
    chrome.storage.local.get("backendUrl").then((v) => {
      if (typeof v.backendUrl === "string") setBackendUrl(v.backendUrl);
    });
    const listener = (msg: ExtensionMsg) => {
      if (msg.target === "sidepanel" && msg.action === "capture-status") {
        setCapture(
          msg.status === "error" ? `Capture impossible : ${msg.detail}` : msg.status === "running" ? "Onglet d'appel capté" : null,
        );
      }
    };
    chrome.runtime.onMessage.addListener(listener);
    return () => chrome.runtime.onMessage.removeListener(listener);
  }, []);

  useEffect(() => {
    if (!live) return;
    setNow(elapsed());
    const id = window.setInterval(() => setNow(elapsed()), 1000);
    return () => clearInterval(id);
  }, [live, elapsed]);

  const clearTimers = () => {
    timers.current.forEach((t) => clearTimeout(t));
    timers.current = [];
  };

  const startDemo = async () => {
    clearTimers();
    const demo = await fetch(`${httpBase(backendUrl)}/demo`).then((r) => r.json());
    await connect({ type: "start", scenario_id: "cold_call_fr", language: "fr", self_analysis: selfAnalysis, context: demo.context });
    // Rejoue l'appel en temps réel, comme s'il arrivait du micro et de l'onglet.
    const events = demo.events as ClientMsg[];
    const last = Math.max(...events.map(eventTime).filter(Number.isFinite));
    for (const event of events) {
      const at = event.type === "stop" ? last + 3 : eventTime(event);
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

  const prospect = state.emotions.find((e) => e.speaker === "prospect");
  const user = state.emotions.find((e) => e.speaker === "user");
  const hint = state.current;

  return (
    <div className="app">
      <header className="bar">
        <div className="brand">
          <span className="mark" aria-hidden />
          Call-help
        </div>
        <div className={`status ${state.phase}`}>
          <span className="pulse" aria-hidden />
          {PHASE_LABEL[state.phase]}
          {live && <span className="time">{clock(now)}</span>}
        </div>
      </header>

      <main className="stack">
        {state.phase === "idle" && !state.summary && (
          <section className="intro">
            <h1>Votre copilote d'appel.</h1>
            <p className="lead">Fil directeur, réponses aux objections et lecture du ton, en temps réel.</p>
          </section>
        )}

        {state.step && (
          <section className="card step" aria-label="Fil directeur">
            <div className="segments">
              {Array.from({ length: state.step.total }, (_, i) => (
                <span key={i} className={i < state.step!.index ? "done" : i === state.step!.index ? "now" : ""} />
              ))}
            </div>
            <div className="eyebrow">
              Étape {state.step.index + 1} sur {state.step.total}
            </div>
            <h2>{state.step.title}</h2>
            <p className="sub">{state.step.goal}</p>
          </section>
        )}

        {hint && (
          <section key={hint.id} className={`card hint ${hint.source === "llm" ? "halo" : ""} p${hint.priority}`} aria-live="polite">
            <div className="hint-head">
              <span className={`eyebrow ${KINDS[hint.kind].tone}`}>{KINDS[hint.kind].label}</span>
              {hint.source === "llm" && <span className="eyebrow halo-text">Affiné par l'IA</span>}
            </div>
            <p className="hint-text">{fr(hint.text)}</p>
            {hint.say && <blockquote>{fr(hint.say)}</blockquote>}
            {hint.kind === "announcement" && state.announcementPending ? (
              <button className="btn primary wide" onClick={confirmAnnouncement}>
                Message dit, démarrer l'analyse
              </button>
            ) : (
              hint.kind !== "announcement" && (
                <Feedback id={hint.id} value={state.feedback[hint.id]} onFeedback={giveFeedback} />
              )
            )}
          </section>
        )}

        {(live || state.phase === "ended") && (
          <section className="tiles two">
            <EmotionTile who="Prospect" reading={prospect} paused={state.analysisPaused} pending={state.announcementPending} />
            {selfAnalysis && (
              <EmotionTile who="Vous" reading={user} paused={state.analysisPaused} pending={state.announcementPending} />
            )}
          </section>
        )}

        {state.metrics && <MetricsGrid m={state.metrics} />}

        {state.summary && (
          <section className="card">
            <div className="eyebrow accent">Bilan</div>
            <h2>{clock(state.summary.duration)} d'appel</h2>
            <dl className="facts">
              <div><dt>Étapes</dt><dd>{state.summary.steps_reached.length}</dd></div>
              <div><dt>Objections</dt><dd>{state.summary.objections.length}</dd></div>
              <div><dt>Conseils utiles</dt><dd>{state.summary.hints_useful}/{state.summary.hints_given}</dd></div>
            </dl>
          </section>
        )}

        {state.error && <p className="alert">{state.error}</p>}
        {capture && <p className="sub center">{capture}</p>}

        <section className="actions">
          {!live ? (
            <>
              <button className="btn primary" onClick={startCall}>Démarrer l'appel</button>
              <button className="btn secondary" onClick={startDemo}>Voir la démo</button>
              <label className="toggle">
                <input type="checkbox" role="switch" checked={selfAnalysis} onChange={(e) => setSelfAnalysis(e.target.checked)} />
                <span className="track" aria-hidden />
                Analyser aussi mon ton
              </label>
            </>
          ) : (
            <>
              <button className="btn secondary" onClick={() => send({ type: "pause_analysis" })} disabled={state.analysisPaused}>
                {state.analysisPaused ? "Analyse coupée" : "Couper l'analyse"}
              </button>
              <button className="btn danger" onClick={stopCall}>Terminer</button>
            </>
          )}
        </section>

        {state.history.length > 1 && (
          <details className="card list">
            <summary>Conseils précédents <span className="count">{state.history.length - 1}</span></summary>
            <ul>
              {state.history.slice(1).map((h) => (
                <li key={h.id}>
                  <span className="time">{clock(h.ts)}</span>
                  <span>{fr(h.text)}</span>
                </li>
              ))}
            </ul>
          </details>
        )}

        <p className="footnote">
          Les images du visage sont analysées dans votre navigateur et ne sont jamais envoyées. Les indices émotionnels
          sont des hypothèses, pas des faits.
        </p>
      </main>
    </div>
  );
}

function EmotionTile({ who, reading, paused, pending }: { who: string; reading?: EmotionReading; paused: boolean; pending: boolean }) {
  let text = "Calibration…";
  let tone: Tone = "neutral";
  let confidence = 0;
  if (pending) text = "En attente du message";
  else if (paused) text = "Analyse coupée";
  else if (reading?.calibrated && !reading.state) text = reading.face_visible === false ? "Visage non analysable" : "Neutre";
  else if (reading?.state) {
    const e = EMOTIONS[reading.state];
    text = e.label;
    tone = e.tone;
    confidence = reading.confidence;
  }
  const pct = Math.round(confidence * 100);
  return (
    <div className={`tile emotion ${tone}`}>
      <div className="label">{who}</div>
      <div className="value-text">
        <span className="dot" aria-hidden />
        {text}
      </div>
      <div className="meter" role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Confiance">
        <span style={{ width: `${pct}%` }} />
      </div>
      <div className="label">{reading?.label === "probable" ? "Probable" : pct ? `Confiance ${pct} %` : " "}</div>
    </div>
  );
}

function MetricsGrid({ m }: { m: Metrics }) {
  const items = [
    { label: "Débit", value: Math.round(m.user_wpm), unit: "mots/min", warn: m.user_wpm > 180 },
    { label: "Temps de parole", value: Math.round(m.user_talk_ratio * 100), unit: "%", warn: m.user_talk_ratio > 0.7 },
    { label: "Monologue", value: Math.round(m.current_monologue), unit: "s", warn: m.current_monologue > 45 },
    { label: "Questions", value: m.questions_asked, unit: "", warn: false },
  ];
  return (
    <section className="tiles four">
      {items.map((it) => (
        <div key={it.label} className={`tile ${it.warn ? "warn" : ""}`}>
          <div className="label">{it.label}</div>
          <div className="value">
            {it.value}
            {it.unit && <small>{it.unit}</small>}
          </div>
        </div>
      ))}
    </section>
  );
}

function Feedback({ id, value, onFeedback }: { id: string; value?: boolean; onFeedback: (id: string, useful: boolean) => void }) {
  return (
    <div className="feedback">
      <span className="sub">Utile ?</span>
      <button aria-label="Conseil utile" aria-pressed={value === true} onClick={() => onFeedback(id, true)}>Oui</button>
      <button aria-label="Conseil pas utile" aria-pressed={value === false} onClick={() => onFeedback(id, false)}>Non</button>
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import { DEFAULT_BACKEND, PANEL_PORT, type ClientMsg, type ExtensionMsg } from "../shared/protocol";
import { clock, httpBase } from "../shared/format";
import { CopilotView } from "./CopilotView";
import { useCallSession } from "./useCallSession";

const PHASE_LABEL = { idle: "Prêt", connecting: "Connexion…", live: "En direct", ended: "Terminé", error: "Hors ligne" };

function eventTime(e: ClientMsg): number {
  if (e.type === "transcript") return e.end;
  if (e.type === "voice" || e.type === "face" || e.type === "announcement_done") return e.ts;
  return Number.POSITIVE_INFINITY;
}

const hasChrome = typeof chrome !== "undefined" && !!chrome.runtime?.id;

// Analyse du ton de l'utilisateur : accord explicite (opt-in), mémorisé d'un appel à l'autre.
const SELF_ANALYSIS_KEY = "callhelp.selfAnalysis";

function loadSelfAnalysis(): boolean {
  try {
    return localStorage.getItem(SELF_ANALYSIS_KEY) === "1";
  } catch {
    return false;
  }
}

function saveSelfAnalysis(on: boolean) {
  try {
    localStorage.setItem(SELF_ANALYSIS_KEY, on ? "1" : "0");
  } catch {
    /* stockage indisponible : le choix vaut pour cette session seulement */
  }
}

export function App() {
  const [backendUrl, setBackendUrl] = useState(DEFAULT_BACKEND);
  const [selfAnalysis, setSelfAnalysisState] = useState(loadSelfAnalysis);
  const [capture, setCapture] = useState<string | null>(null);
  const [now, setNow] = useState(0);
  const { state, connect, send, giveFeedback, answerAnnouncement, elapsed, fail } = useCallSession(backendUrl);
  const timers = useRef<number[]>([]);
  const live = state.phase === "live";
  const busy = state.phase === "connecting";

  const setSelfAnalysis = (on: boolean) => {
    setSelfAnalysisState(on);
    saveSelfAnalysis(on);
  };

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
    // Lien permanent avec le service worker : panneau fermé = capture arrêtée.
    const port = chrome.runtime.connect({ name: PANEL_PORT });
    return () => {
      chrome.runtime.onMessage.removeListener(listener);
      port.disconnect();
    };
  }, []);

  // Fin d'appel ou connexion perdue : plus de rejeu de démo ni de capture en cours.
  useEffect(() => {
    if (state.phase !== "error" && state.phase !== "ended") return;
    clearTimers();
    if (hasChrome) void chrome.runtime.sendMessage({ target: "background", action: "stop-capture" }).catch(() => {});
  }, [state.phase]);

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
    let demo: { context: Record<string, string>; events: ClientMsg[] };
    try {
      const r = await fetch(`${httpBase(backendUrl)}/demo`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      demo = await r.json();
      await connect({ type: "start", scenario_id: "cold_call_fr", language: "fr", self_analysis: selfAnalysis, context: demo.context });
    } catch (err) {
      fail(`Démo indisponible : backend injoignable (${httpBase(backendUrl)})`);
      return;
    }
    // Rejoue l'appel en temps réel, comme s'il arrivait du micro et de l'onglet.
    const events = demo.events;
    const last = Math.max(...events.map(eventTime).filter(Number.isFinite));
    for (const event of events) {
      const at = event.type === "stop" ? last + 3 : eventTime(event);
      timers.current.push(window.setTimeout(() => send(event), at * 1000));
    }
  };

  const startCall = async () => {
    clearTimers();
    setCapture(null);
    try {
      await connect({ type: "start", scenario_id: "cold_call_fr", language: "fr", self_analysis: selfAnalysis });
    } catch {
      return; // l'erreur est déjà affichée par la session
    }
    if (hasChrome) {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tab?.id) await chrome.runtime.sendMessage({ target: "background", action: "start-capture", tabId: tab.id });
    }
  };

  const stopCall = () => {
    clearTimers();
    send({ type: "stop" });
    if (hasChrome) void chrome.runtime.sendMessage({ target: "background", action: "stop-capture" }).catch(() => {});
  };

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

        <CopilotView state={state} selfAnalysis={selfAnalysis} onAnswer={answerAnnouncement} onFeedback={giveFeedback} />

        {state.error && <p className="alert">{state.error}</p>}
        {capture && <p className="sub center">{capture}</p>}

        <section className="actions">
          {!live ? (
            <>
              <button className="btn primary" onClick={startCall} disabled={busy}>Démarrer l'appel</button>
              <button className="btn secondary" onClick={startDemo} disabled={busy}>Voir la démo</button>
              <label className="toggle">
                <input type="checkbox" role="switch" checked={selfAnalysis} onChange={(e) => setSelfAnalysis(e.target.checked)} />
                <span className="track" aria-hidden />
                <span>
                  Analyser aussi mon ton
                  <span className="sub block">Visible par vous seul, jamais par un manager.</span>
                </span>
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

        <p className="footnote">
          Les images du visage sont analysées dans votre navigateur et ne sont jamais envoyées. Les indices émotionnels
          sont des hypothèses, pas des faits.
        </p>
      </main>
    </div>
  );
}

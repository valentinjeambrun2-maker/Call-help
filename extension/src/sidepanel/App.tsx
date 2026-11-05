import { useCallback, useEffect, useRef, useState } from "react";
import { playRecordedCall, type Frame } from "../shared/capture";
import {
  AUDIO_BUS, DEFAULT_BACKEND, PANEL_PORT,
  type AudioBusMsg, type CaptureStatus, type ClientMsg, type ExtensionMsg, type ServerMsg, type Speaker,
} from "../shared/protocol";
import { clock, fr, httpBase } from "../shared/format";
import { CopilotView } from "./CopilotView";
import { loadSettings, saveSettings, scriptContext, type Settings } from "./settings";
import { useCallSession } from "./useCallSession";

const PHASE_LABEL = { idle: "Prêt", connecting: "Connexion…", live: "En direct", ended: "Terminé", error: "Hors ligne" };
const OUTCOME_LABELS: Record<string, string> = {
  meeting_booked: "RDV pris", callback: "Rappel", send_info: "Envoi d'infos", refused: "Refus", hung_up: "Raccroché",
};

const hasChrome = typeof chrome !== "undefined" && !!chrome.runtime?.id;
const FRAME_BYTES = 3200; // 100 ms de PCM 16 bits à 16 kHz
const MAX_PAD_FRAMES = 300; // 30 s au plus de silence de calage
const STALL_MS = 5000; // plus de son depuis ce délai : la captation est perdue

interface DemoCall {
  id: string;
  language: "fr" | "en";
  duration: number;
  outcome: string | null;
  prospect_company: string | null;
  seller_name: string | null;
  prospect_name: string | null;
  has_audio: boolean;
}

interface Replay {
  scenario_id?: string;
  language?: "fr" | "en";
  context: Record<string, string>;
  events: ClientMsg[];
}

interface ActiveCapture {
  id: string;
  kind: "call" | "file"; // vrai appel (offscreen) ou appel enregistré joué dans le panneau
  started: boolean; // premier bloc audio reçu
  lastFrameAt: number;
  levelsShownAt: number;
  stop?: () => void;
  onFirstFrame?: (offset: number) => void;
  onEnded?: () => void;
}

type Tone = "ok" | "warn" | "bad";

function frameBytes(channel: 0 | 1, pcm: ArrayBuffer): Uint8Array {
  const out = new Uint8Array(1 + pcm.byteLength);
  out[0] = channel;
  out.set(new Uint8Array(pcm), 1);
  return out;
}

function eventTime(e: ClientMsg): number {
  if (e.type === "transcript") return e.end;
  if (e.type === "voice" || e.type === "face" || e.type === "announcement_done") return e.ts;
  return Number.POSITIVE_INFINITY;
}

// Décale l'horodatage d'un événement rejoué du silence de calage envoyé avant l'audio.
function shifted(e: ClientMsg, offset: number): ClientMsg {
  if (e.type === "voice" || e.type === "face" || e.type === "announcement_done") return { ...e, ts: e.ts + offset };
  return e;
}

async function micPermission(): Promise<PermissionState | "unknown"> {
  try {
    return (await navigator.permissions.query({ name: "microphone" as PermissionName })).state;
  } catch {
    return "unknown";
  }
}

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
  const [settings, setSettingsState] = useState<Settings>(loadSettings);
  const [calls, setCalls] = useState<DemoCall[]>([]);
  const [demoId, setDemoId] = useState("");
  const [capture, setCapture] = useState<{ tone: Tone; text: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [levels, setLevels] = useState<{ mic: number; tab: number } | null>(null);
  const [heard, setHeard] = useState<Partial<Record<Speaker, { text: string; final: boolean }>>>({});
  const [now, setNow] = useState(0);

  const onServer = useCallback((msg: ServerMsg) => {
    if (msg.type === "transcript") setHeard((h) => ({ ...h, [msg.speaker]: { text: msg.text, final: msg.final } }));
  }, []);
  const { state, connect, disconnect, send, sendAudio, giveFeedback, answerAnnouncement, elapsed, fail } =
    useCallSession(backendUrl, onServer);
  const timers = useRef<number[]>([]);
  const active = useRef<ActiveCapture | null>(null);
  const live = state.phase === "live";
  const busy = state.phase === "connecting";
  const api = httpBase(backendUrl);
  const demoCalls = calls.filter((c) => c.language === settings.language);

  const setSelfAnalysis = (on: boolean) => {
    setSelfAnalysisState(on);
    saveSelfAnalysis(on);
  };
  const setSettings = (patch: Partial<Settings>) => {
    setSettingsState((s) => {
      const next = { ...s, ...patch };
      saveSettings(next);
      return next;
    });
  };

  const clearTimers = () => {
    timers.current.forEach((t) => clearTimeout(t));
    timers.current = [];
  };

  const endCapture = useCallback(() => {
    const cap = active.current;
    active.current = null;
    setLevels(null);
    cap?.stop?.();
    if (hasChrome && cap?.kind !== "file") {
      void chrome.runtime.sendMessage({ target: "background", action: "stop-capture" }).catch(() => {});
    }
  }, []);

  const newCapture = (kind: ActiveCapture["kind"], hooks: Pick<ActiveCapture, "onFirstFrame" | "onEnded"> = {}) => {
    const cap: ActiveCapture = { id: crypto.randomUUID(), kind, started: false, lastFrameAt: 0, levelsShownAt: 0, ...hooks };
    active.current = cap;
    setLevels(null);
    setHeard({});
    setCapture({ tone: "ok", text: kind === "call" ? "Connexion à l'onglet et au micro…" : "Préparation de l'appel de démo…" });
    return cap;
  };

  // Vrai appel : le service worker ouvre le document offscreen, qui capte l'onglet et le micro.
  const captureCall = (tabId: number) => {
    const cap = newCapture("call");
    const msg: ExtensionMsg = { target: "background", action: "start-capture", tabId, captureId: cap.id };
    void chrome.runtime.sendMessage(msg).catch(() => {});
  };

  // Démo : l'appel enregistré est joué et capté ici même, par la même chaîne audio.
  const captureRecording = (url: string, hooks: Pick<ActiveCapture, "onFirstFrame" | "onEnded">) => {
    const cap = newCapture("file", hooks);
    const abort = new AbortController();
    cap.stop = () => abort.abort();
    playRecordedCall(url, (f) => relay.current(cap.id, f), () => {
      if (active.current?.id === cap.id) cap.onEnded?.();
    }, abort.signal)
      .then((stop) => {
        cap.stop = stop;
        if (active.current?.id !== cap.id) stop();
        else onCaptureStatus.current("running");
      })
      .catch((err) => {
        if (!abort.signal.aborted && active.current?.id === cap.id) {
          onCaptureStatus.current("error", err instanceof Error ? err.message : String(err));
        }
      });
  };

  // Bloc audio capté -> backend, sur l'horloge de l'appel.
  const relay = useRef<(captureId: string, frame: Frame) => void>(() => {});
  relay.current = (captureId, frame) => {
    const cap = active.current;
    if (!cap || captureId !== cap.id) return;
    if (!cap.started) {
      cap.started = true;
      // L'audio commence au début de l'appel (connexion), pas au premier bloc capté :
      // on comble l'écart par du silence pour que tous les horodatages concordent.
      const pad = Math.min(MAX_PAD_FRAMES, Math.max(0, Math.round(elapsed() * 10)));
      const silence = new ArrayBuffer(FRAME_BYTES);
      for (let i = 0; i < pad; i++) {
        sendAudio(frameBytes(0, silence));
        sendAudio(frameBytes(1, silence));
      }
      cap.onFirstFrame?.(pad / 10);
    }
    sendAudio(frameBytes(0, frame.mic));
    sendAudio(frameBytes(1, frame.tab));
    const t = performance.now();
    cap.lastFrameAt = t;
    if (t - cap.levelsShownAt > 200) {
      cap.levelsShownAt = t; // vu-mètres rafraîchis 5 fois par seconde
      setLevels(frame.level);
    }
  };

  const stopCall = () => {
    clearTimers();
    send({ type: "stop" });
    endCapture();
  };

  // État de la captation, signalé par le service worker ou le document offscreen.
  const onCaptureStatus = useRef<(status: CaptureStatus, detail?: string) => void>(() => {});
  onCaptureStatus.current = (status, detail) => {
    const cap = active.current;
    if (!cap) return;
    if (status === "running") {
      setCapture({ tone: "ok", text: cap.kind === "call" ? "Onglet d'appel et micro captés" : "Lecture de l'appel de démo" });
    } else if (status === "warning") {
      setCapture({ tone: "warn", text: detail ?? "Captation partielle." });
    } else {
      const started = cap.started;
      clearTimers();
      endCapture(); // libère l'onglet, le micro ou la lecture de la démo
      setCapture({ tone: status === "error" ? "bad" : "warn", text: status === "error" ? `Captation impossible : ${detail}` : detail ?? "Captation arrêtée." });
      // Rien entendu : on abandonne sans bilan. Sinon, bilan de ce qui a été entendu.
      if (started) send({ type: "stop" });
      else disconnect();
    }
  };

  useEffect(() => {
    if (!hasChrome) return;
    chrome.storage.local.get("backendUrl").then((v) => {
      if (typeof v.backendUrl === "string") setBackendUrl(v.backendUrl);
    });
    const listener = (msg: ExtensionMsg) => {
      if (msg.target !== "sidepanel" || msg.action !== "capture-status") return;
      const cap = active.current;
      if (cap?.kind !== "call") return; // la démo est captée dans le panneau
      if (msg.captureId && msg.captureId !== cap.id) return; // captation précédente
      onCaptureStatus.current(msg.status, msg.detail);
    };
    chrome.runtime.onMessage.addListener(listener);
    // Lien permanent avec le service worker : panneau fermé = captation arrêtée.
    const port = chrome.runtime.connect({ name: PANEL_PORT });
    return () => {
      chrome.runtime.onMessage.removeListener(listener);
      port.disconnect();
    };
  }, []);

  // Appels de démo disponibles (générés avec Claude et ElevenLabs).
  useEffect(() => {
    let cancelled = false;
    fetch(`${api}/demo/calls`)
      .then((r) => (r.ok ? r.json() : []))
      .then((list: DemoCall[]) => {
        if (!cancelled) setCalls(list.filter((c) => c.has_audio));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [api]);

  // Audio d'un vrai appel, capté par le document offscreen.
  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const bus = new BroadcastChannel(AUDIO_BUS);
    bus.onmessage = (ev: MessageEvent<AudioBusMsg>) => relay.current(ev.data.captureId, ev.data);
    return () => bus.close();
  }, []);

  // Plus de son depuis quelques secondes (onglet fermé, document de captation arrêté par
  // Chrome, mise en veille) : on clôt l'appel avec ce qui a été entendu.
  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => {
      const cap = active.current;
      if (cap?.started && performance.now() - cap.lastFrameAt > STALL_MS) {
        onCaptureStatus.current("stopped", "Le son de l'appel ne parvient plus à Call-help.");
      }
    }, 1000);
    return () => clearInterval(id);
  }, [live]);

  // Fin d'appel ou connexion perdue : plus de rejeu ni de captation.
  useEffect(() => {
    if (state.phase !== "error" && state.phase !== "ended") return;
    clearTimers();
    if (active.current) endCapture();
  }, [state.phase, endCapture]);

  useEffect(() => {
    if (!live) return;
    setNow(elapsed());
    const id = window.setInterval(() => setNow(elapsed()), 1000);
    return () => clearInterval(id);
  }, [live, elapsed]);

  const startCall = async () => {
    clearTimers();
    setNotice(null);
    setCapture(null);
    if (!hasChrome) {
      fail("Ouvrez Call-help depuis l'icône de l'extension, dans l'onglet de l'appel.");
      return;
    }
    // L'onglet de l'appel est celui où l'icône a été cliquée (seul onglet que Chrome laisse capter).
    const { invokedTabId } = await chrome.storage.session.get("invokedTabId");
    const tab = typeof invokedTabId === "number" ? await chrome.tabs.get(invokedTabId).catch(() => null) : null;
    if (!tab?.id) {
      setNotice("Ouvrez l'onglet de l'appel (Meet, Teams…), cliquez sur l'icône Call-help, puis démarrez.");
      return;
    }
    const mic = await micPermission();
    if (mic !== "granted") {
      await chrome.tabs.create({ url: chrome.runtime.getURL("permission.html") });
      setNotice(
        mic === "denied"
          ? "Le micro est bloqué pour Call-help : rétablissez-le dans l'onglet ouvert, puis relancez l'appel."
          : "Autorisez le micro dans l'onglet qui vient de s'ouvrir, puis revenez ici et relancez l'appel.",
      );
      return;
    }
    try {
      await connect({
        type: "start", scenario_id: "cold_call_fr", language: settings.language, self_analysis: selfAnalysis,
        context: scriptContext(settings), transcription: "server",
        keyterms: [settings.userName, settings.company].map((s) => s.trim()).filter(Boolean),
      });
    } catch {
      return; // l'erreur est déjà affichée par la session
    }
    captureCall(tab.id);
  };

  const startDemo = async () => {
    clearTimers();
    setNotice(null);
    setCapture(null);
    const call = demoCalls.find((c) => c.id === demoId) ?? demoCalls[0];
    if (!call) {
      fail(`Aucun appel de démo : backend injoignable (${api}) ?`);
      return;
    }
    let replay: Replay;
    try {
      const r = await fetch(`${api}/demo/calls/${call.id}/replay`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      replay = await r.json();
    } catch {
      fail(`Démo indisponible : backend injoignable (${api})`);
      return;
    }
    try {
      await connect({
        type: "start", scenario_id: replay.scenario_id ?? "cold_call_fr", language: replay.language ?? call.language,
        self_analysis: selfAnalysis, context: replay.context, transcription: "server",
        keyterms: [call.seller_name, call.prospect_name, call.prospect_company].filter((x): x is string => !!x),
      });
    } catch {
      return;
    }
    // Le texte vient de l'audio ; seuls les autres événements (accord du prospect) sont rejoués.
    const events = replay.events.filter((e) => e.type !== "transcript" && e.type !== "start" && e.type !== "stop");
    captureRecording(
      `${api}/demo/calls/${call.id}/audio`,
      {
        onFirstFrame: (offset) => {
          for (const e of events) {
            const at = eventTime(e);
            if (Number.isFinite(at)) timers.current.push(window.setTimeout(() => send(shifted(e, offset)), at * 1000));
          }
        },
        onEnded: () => {
          timers.current.push(window.setTimeout(stopCall, 1500)); // derniers mots transcrits avant le bilan
        },
      },
    );
  };

  const idle = !live && !busy;

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

        {live && (
          <section className="card listen" aria-label="Écoute">
            <div className="listen-head">
              <span className="eyebrow">Écoute</span>
              {capture && <span className={`listen-state ${capture.tone}`}>{capture.text}</span>}
            </div>
            <Level label="Vous" value={levels?.mic} />
            <Level label="Prospect" value={levels?.tab} />
            {(["prospect", "user"] as const).map((sp) => {
              const line = heard[sp];
              return line ? (
                <p key={sp} className={`heard-line ${line.final ? "" : "partial"}`}>
                  <span className="who">{sp === "user" ? "Vous" : "Prospect"}</span>
                  {fr(line.text)}
                </p>
              ) : null;
            })}
          </section>
        )}

        {state.error && <p className="alert">{state.error}</p>}
        {!live && capture && capture.tone !== "ok" && <p className={capture.tone === "bad" ? "alert" : "notice"}>{capture.text}</p>}
        {notice && <p className="notice">{notice}</p>}

        {idle && (
          <section className="card settings" aria-label="Réglages de l'appel">
            <div className="settings-head">
              <span className="eyebrow">Votre appel</span>
              <div className="segmented" role="radiogroup" aria-label="Langue de l'appel">
                {(["fr", "en"] as const).map((l) => (
                  <button key={l} role="radio" aria-checked={settings.language === l} className={settings.language === l ? "on" : ""}
                    onClick={() => setSettings({ language: l })}>
                    {l === "fr" ? "Français" : "English"}
                  </button>
                ))}
              </div>
            </div>
            <div className="fields">
              <Field label="Votre prénom" placeholder="Léa" value={settings.userName} onChange={(v) => setSettings({ userName: v })} />
              <Field label="Votre société" placeholder="The Herakles" value={settings.company} onChange={(v) => setSettings({ company: v })} />
              <Field label="Qui appelez-vous ?" placeholder="agences immobilières" value={settings.segment}
                onChange={(v) => setSettings({ segment: v })} />
              <Field label="Ce que vous leur apportez" placeholder="gagner deux heures par semaine" value={settings.benefit}
                onChange={(v) => setSettings({ benefit: v })} />
            </div>
          </section>
        )}

        <section className="actions">
          {!live ? (
            <>
              <button className="btn primary" onClick={() => void startCall()} disabled={busy}>Démarrer l'appel</button>
              <div className="demo-pick">
                {demoCalls.length > 0 && (
                  <select value={demoId || demoCalls[0].id} onChange={(e) => setDemoId(e.target.value)} aria-label="Appel de démo" disabled={busy}>
                    {demoCalls.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.prospect_company ?? c.id} · {c.outcome ? OUTCOME_LABELS[c.outcome] ?? c.outcome : ""} · {clock(c.duration)}
                      </option>
                    ))}
                  </select>
                )}
                <button className="btn secondary" onClick={() => void startDemo()} disabled={busy}>Voir la démo</button>
              </div>
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
          Le son de l'appel est transcrit en direct ; Call-help ne l'enregistre pas. Les indices émotionnels sont des
          hypothèses, pas des faits. Un casque évite que votre micro reprenne la voix du prospect.
        </p>
      </main>
    </div>
  );
}

function Level({ label, value }: { label: string; value: number | undefined }) {
  // Niveau en décibels : -60 dB (silence) à 0 dB.
  const pct = value ? Math.max(0, Math.min(100, ((20 * Math.log10(value) + 60) / 60) * 100)) : 0;
  return (
    <div className="level">
      <span className="level-label">{label}</span>
      <div className="meter" role="meter" aria-label={`Niveau ${label}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
        <span style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function Field({ label, value, placeholder, onChange }: { label: string; value: string; placeholder: string; onChange: (v: string) => void }) {
  return (
    <label className="field">
      <span>{label}</span>
      <input type="text" value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

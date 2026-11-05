import { useCallback, useEffect, useRef, useState } from "react";
import { playRecordedCall, type Frame } from "../shared/capture";
import {
  AUDIO_BUS, DEFAULT_BACKEND, PANEL_PORT, invokedKey,
  type AudioBusMsg, type CaptureStatus, type ClientMsg, type ExtensionMsg, type InvokedTab, type ServerMsg, type Speaker,
} from "../shared/protocol";
import { clock, fr, httpBase } from "../shared/format";
import { CopilotView } from "./CopilotView";
import { ServiceNotices, useServiceStatus } from "../shared/serviceStatus";
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
const HEARTBEAT_MS = 1000; // « j'écoute » envoyé au document offscreen
const PORT_PING_MS = 20000; // garde le service worker éveillé (Chrome l'endort après 30 s)
const SILENT_TAB_MS = 20000; // onglet jamais entendu depuis le début : est-ce le bon ?
const HEARD_LEVEL = 0.003; // ~ -50 dB

// Ce panneau (un par fenêtre) : seul lui peut arrêter la captation qu'il a lancée.
const PANEL_ID = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Math.random());

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
  heartbeatAt: number;
  host?: string; // onglet capté (et non le dernier où l'icône a été cliquée)
  startedAt: number;
  tabEverHeard: boolean; // le prospect a déjà été entendu au moins une fois
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

interface AudioDevice {
  id: string;
  label: string;
}

// Périphériques audio, avec leur nom (connu seulement une fois le micro autorisé).
async function audioDevices(): Promise<{ mics: AudioDevice[]; speakers: AudioDevice[] }> {
  try {
    const all = await navigator.mediaDevices.enumerateDevices();
    const pick = (kind: MediaDeviceKind) =>
      all.filter((d) => d.kind === kind && d.deviceId && d.deviceId !== "default" && d.deviceId !== "communications" && d.label)
        .map((d) => ({ id: d.deviceId, label: d.label }));
    return { mics: pick("audioinput"), speakers: pick("audiooutput") };
  } catch {
    return { mics: [], speakers: [] };
  }
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
  const [starting, setStarting] = useState(false); // démarrage en cours : pas de double clic
  const startingRef = useRef(false);
  const [micMuted, setMicMuted] = useState(false);
  const [tabSilent, setTabSilent] = useState(false); // onglet capté jamais entendu depuis 20 s
  // Lecture du ton de l'utilisateur pour l'appel en cours : démos seulement pour l'instant.
  const [callSelfAnalysis, setCallSelfAnalysis] = useState(false);
  const micMutedRef = useRef(false);
  const [invoked, setInvoked] = useState<InvokedTab | null>(null);
  const [devices, setDevices] = useState<{ mics: AudioDevice[]; speakers: AudioDevice[] }>({ mics: [], speakers: [] });
  const bus = useRef<BroadcastChannel | null>(null);

  const onServer = useCallback((msg: ServerMsg) => {
    if (msg.type === "transcript") setHeard((h) => ({ ...h, [msg.speaker]: { text: msg.text, final: msg.final } }));
  }, []);
  const { state, connect, disconnect, send, sendAudio, giveFeedback, answerAnnouncement, elapsed, fail } =
    useCallSession(backendUrl, onServer);
  const timers = useRef<number[]>([]);
  const active = useRef<ActiveCapture | null>(null);
  const live = state.phase === "live";
  const busy = state.phase === "connecting" || starting;
  const api = httpBase(backendUrl);
  const { service, recheck } = useServiceStatus(api);
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
    setTabSilent(false);
    cap?.stop?.();
    if (hasChrome && cap?.kind === "call") {
      const msg: ExtensionMsg = { target: "background", action: "stop-capture", captureId: cap.id };
      void chrome.runtime.sendMessage(msg).catch(() => {});
    }
  }, []);

  const newCapture = (kind: ActiveCapture["kind"], hooks: Pick<ActiveCapture, "onFirstFrame" | "onEnded"> = {}) => {
    if (active.current) endCapture(); // jamais deux captations (ni deux démos) à la fois
    clearTimers();
    const cap: ActiveCapture = {
      id: crypto.randomUUID(), kind, started: false, lastFrameAt: 0, levelsShownAt: 0, heartbeatAt: 0,
      startedAt: 0, tabEverHeard: false, ...hooks,
    };
    active.current = cap;
    setLevels(null);
    setHeard({});
    setMuted(false);
    setTabSilent(false);
    setCapture({ tone: "ok", text: kind === "call" ? "Connexion à l'onglet et au micro…" : "Préparation de l'appel de démo…" });
    return cap;
  };

  const setMuted = (on: boolean) => {
    micMutedRef.current = on;
    setMicMuted(on);
  };

  // Vrai appel : le service worker ouvre le document offscreen, qui capte l'onglet et le micro.
  const captureCall = (tabId: number, host?: string) => {
    const cap = newCapture("call");
    cap.host = host;
    const msg: ExtensionMsg = {
      target: "background", action: "start-capture", tabId, captureId: cap.id, panelId: PANEL_ID,
      windowId: windowId.current ?? undefined,
      devices: { micId: settings.micId || undefined, speakerId: settings.speakerId || undefined },
    };
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
    // Micro coupé par le vendeur : du silence à la place (l'horloge de son canal reste juste).
    const muted = micMutedRef.current;
    sendAudio(frameBytes(0, muted ? new ArrayBuffer(frame.mic.byteLength) : frame.mic));
    sendAudio(frameBytes(1, frame.tab));
    const t = performance.now();
    cap.lastFrameAt = t;
    // Onglet jamais entendu depuis le début (icône cliquée sur un autre onglet, son coupé dans
    // l'outil d'appel…) : on le signale à part, sans masquer l'état du micro. Une fois le
    // prospect entendu, un silence (attente, long exposé) n'est plus suspect.
    if (cap.kind === "call" && !cap.tabEverHeard) {
      if (!cap.startedAt) cap.startedAt = t;
      if (frame.level.tab > HEARD_LEVEL) {
        cap.tabEverHeard = true;
        setTabSilent(false);
      } else if (t - cap.startedAt > SILENT_TAB_MS) {
        setTabSilent(true);
      }
    }
    if (cap.kind === "call" && t - cap.heartbeatAt > HEARTBEAT_MS) {
      // Sur réception d'un bloc (événement), pas sur minuteur : un panneau en arrière-plan
      // continue de répondre, et seul un panneau fermé se tait.
      cap.heartbeatAt = t;
      const beat: AudioBusMsg = { type: "heartbeat", captureId: cap.id };
      bus.current?.postMessage(beat);
    }
    if (t - cap.levelsShownAt > 200) {
      cap.levelsShownAt = t; // vu-mètres rafraîchis 5 fois par seconde
      setLevels(muted ? { ...frame.level, mic: 0 } : frame.level);
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
      setCapture({
        tone: "ok",
        text: cap.kind === "call" ? `Onglet ${cap.host ?? "d'appel"} et micro captés` : "Lecture de l'appel de démo",
      });
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

  const windowId = useRef<number | null>(null);

  useEffect(() => {
    if (!hasChrome) return;
    chrome.storage.local.get("backendUrl").then((v) => {
      if (typeof v.backendUrl === "string") setBackendUrl(v.backendUrl);
    });
    const listener = (msg: ExtensionMsg) => {
      if (msg.target !== "sidepanel" || msg.action !== "capture-status") return;
      const cap = active.current;
      if (cap?.kind !== "call") return; // la démo est captée dans le panneau
      if (msg.captureId !== cap.id) return; // captation précédente, ou d'un autre panneau
      onCaptureStatus.current(msg.status, msg.detail);
    };
    chrome.runtime.onMessage.addListener(listener);

    // Lien avec le service worker : panneau fermé = captation arrêtée. Chrome endort le
    // service worker après 30 s sans événement (le port tombe avec lui) : on le garde éveillé
    // par un « ping » régulier, et on rouvre le port s'il tombe quand même.
    let port: chrome.runtime.Port | null = null;
    let closed = false;
    const open = () => {
      port = chrome.runtime.connect({ name: `${PANEL_PORT}:${PANEL_ID}` });
      port.onDisconnect.addListener(() => {
        port = null;
        if (!closed) window.setTimeout(() => !closed && open(), 250);
      });
    };
    open();
    const ping = window.setInterval(() => port?.postMessage("ping"), PORT_PING_MS);
    // Panneau qui se ferme : on arrête sa captation sans attendre (le document offscreen,
    // qui n'entend plus le panneau, s'arrête de toute façon en quelques secondes).
    const onHide = () => {
      const cap = active.current;
      if (cap?.kind === "call") {
        const msg: ExtensionMsg = { target: "background", action: "stop-capture", captureId: cap.id };
        void chrome.runtime.sendMessage(msg).catch(() => {});
      }
    };
    window.addEventListener("pagehide", onHide);

    // Onglet de l'appel : celui où l'icône a été cliquée dans cette fenêtre.
    let key: string | null = null;
    const onStorage = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
      if (area === "session" && key && key in changes) setInvoked((changes[key].newValue as InvokedTab | undefined) ?? null);
    };
    chrome.storage.onChanged.addListener(onStorage);
    void chrome.windows.getCurrent().then(async (w) => {
      if (w.id === undefined) return;
      windowId.current = w.id;
      key = invokedKey(w.id);
      setInvoked(((await chrome.storage.session.get(key))[key] as InvokedTab | undefined) ?? null);
    });
    return () => {
      closed = true;
      chrome.runtime.onMessage.removeListener(listener);
      chrome.storage.onChanged.removeListener(onStorage);
      window.removeEventListener("pagehide", onHide);
      clearInterval(ping);
      port?.disconnect();
    };
  }, []);

  // Micro et sortie : la liste suit les branchements (casque connecté en cours de route).
  useEffect(() => {
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.enumerateDevices) return;
    const refresh = () => void audioDevices().then(setDevices);
    refresh();
    navigator.mediaDevices.addEventListener?.("devicechange", refresh);
    window.addEventListener("focus", refresh); // retour de la page d'autorisation du micro
    return () => {
      navigator.mediaDevices.removeEventListener?.("devicechange", refresh);
      window.removeEventListener("focus", refresh);
    };
  }, [state.phase]);

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
    const channel = new BroadcastChannel(AUDIO_BUS);
    bus.current = channel;
    channel.onmessage = (ev: MessageEvent<AudioBusMsg>) => {
      if (ev.data.type === "frame" && active.current?.kind === "call") relay.current(ev.data.captureId, ev.data);
    };
    return () => {
      bus.current = null;
      channel.close();
    };
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

  // Un seul démarrage à la fois : un double clic ne lance ni deux appels ni deux démos.
  const once = (run: () => Promise<void>) => async () => {
    if (startingRef.current) return;
    startingRef.current = true;
    setStarting(true);
    try {
      await run();
    } finally {
      startingRef.current = false;
      setStarting(false);
    }
  };

  const startCall = once(async () => {
    clearTimers();
    setNotice(null);
    setCapture(null);
    if (!hasChrome) {
      fail("Ouvrez Call-help depuis l'icône de l'extension, dans l'onglet de l'appel.");
      return;
    }
    setCallSelfAnalysis(false);
    // L'onglet de l'appel est celui où l'icône a été cliquée dans cette fenêtre (seul onglet
    // que Chrome laisse capter).
    const tab = invoked ? await chrome.tabs.get(invoked.tabId).catch(() => null) : null;
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
      // Lecture du ton de l'utilisateur : pas encore branchée sur un vrai appel (démos seulement).
      await connect({
        type: "start", scenario_id: "cold_call_fr", language: settings.language, self_analysis: false,
        context: scriptContext(settings), transcription: "server",
        keyterms: [settings.userName, settings.company].map((s) => s.trim()).filter(Boolean),
      });
    } catch {
      return; // l'erreur est déjà affichée par la session
    }
    captureCall(tab.id, invoked?.host);
  });

  const startDemo = once(async () => {
    setCallSelfAnalysis(selfAnalysis);
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
  });

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

        <CopilotView state={state} selfAnalysis={callSelfAnalysis} onAnswer={answerAnnouncement} onFeedback={giveFeedback} />

        {live && (
          <section className="card listen" aria-label="Écoute">
            <div className="listen-head">
              <span className="eyebrow">Écoute</span>
              {capture && <span className={`listen-state ${capture.tone}`}>{capture.text}</span>}
            </div>
            <Level label={micMuted ? "Vous (coupé)" : "Vous"} value={micMuted ? 0 : levels?.mic} />
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
            {tabSilent && (
              <p className="listen-note warn">
                Rien n'est entendu sur l'onglet {active.current?.host ?? "capté"} depuis le début : est-ce bien celui de
                l'appel ?
              </p>
            )}
            {active.current?.kind === "call" && (
              <button className={`btn secondary small ${micMuted ? "on" : ""}`} aria-pressed={micMuted} onClick={() => setMuted(!micMuted)}>
                {micMuted ? "Réactiver mon micro" : "Couper mon micro"}
              </button>
            )}
          </section>
        )}

        {state.error && <p className="alert">{state.error}</p>}
        {!live && capture && capture.tone !== "ok" && <p className={capture.tone === "bad" ? "alert" : "notice"}>{capture.text}</p>}
        {notice && <p className="notice">{notice}</p>}
        {idle && <ServiceNotices service={service} recheck={recheck} />}

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
            <p className={`call-tab ${invoked ? "" : "missing"}`}>
              {invoked ? (
                <>
                  <span className="who">Onglet de l'appel</span>
                  {invoked.host ?? invoked.title ?? "onglet choisi"}
                  {invoked.host && invoked.title ? <span className="sub"> · {invoked.title}</span> : null}
                </>
              ) : (
                "Ouvrez l'onglet de l'appel (Meet, Teams…) et cliquez sur l'icône Call-help pour le choisir."
              )}
            </p>
            <div className="fields">
              <Field label="Votre prénom" placeholder="Léa" value={settings.userName} onChange={(v) => setSettings({ userName: v })} />
              <Field label="Votre société" placeholder="The Herakles" value={settings.company} onChange={(v) => setSettings({ company: v })} />
              <Field label="Qui appelez-vous ?" placeholder="agences immobilières" value={settings.segment}
                onChange={(v) => setSettings({ segment: v })} />
              <Field label="Ce que vous leur apportez" placeholder="gagner deux heures par semaine" value={settings.benefit}
                onChange={(v) => setSettings({ benefit: v })} />
              {devices.mics.length > 0 && (
                <DeviceField label="Micro (celui de votre casque)" value={settings.micId} options={devices.mics}
                  onChange={(v) => setSettings({ micId: v })} />
              )}
              {devices.speakers.length > 0 && (
                <DeviceField label="Sortie du son de l'appel" value={settings.speakerId} options={devices.speakers}
                  onChange={(v) => setSettings({ speakerId: v })} />
              )}
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
                  <span className="sub block">
                    Visible par vous seul, jamais par un manager. Pour l'instant dans les démos seulement.
                  </span>
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
          Le son de l'appel est transcrit en direct par ElevenLabs ; Call-help ne l'enregistre pas. Les indices
          émotionnels sont des hypothèses, pas des faits. Un casque évite que votre micro reprenne la voix du prospect.
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

function DeviceField({ label, value, options, onChange }: {
  label: string; value: string; options: AudioDevice[]; onChange: (v: string) => void;
}) {
  // Un périphérique retenu mais débranché reste affiché, pour ne pas changer de choix en silence.
  const known = !value || options.some((o) => o.id === value);
  return (
    <label className="field">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Celui du système</option>
        {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        {!known && <option value={value}>Périphérique débranché</option>}
      </select>
    </label>
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

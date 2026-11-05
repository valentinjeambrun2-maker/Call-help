// Miroir des schémas du backend (backend/app/models.py).

export type Speaker = "user" | "prospect";
export type EmotionState = "interest" | "hesitation" | "annoyance" | "boredom" | "enthusiasm";

export interface Hint {
  id: string;
  kind: "announcement" | "objection" | "step" | "pace" | "emotion" | "question" | "closing";
  text: string;
  say: string | null;
  source: "rule" | "llm";
  priority: number;
  replaces: string | null;
  ref: string | null;
  ts: number;
}

export interface EmotionReading {
  speaker: Speaker;
  state: EmotionState | null;
  confidence: number;
  label: "sûr" | "probable" | "incertain";
  face_visible: boolean | null;
  calibrated: boolean;
}

export interface Metrics {
  elapsed: number;
  user_wpm: number;
  prospect_wpm: number;
  user_talk_ratio: number;
  current_monologue: number;
  questions_asked: number;
}

export interface StepState {
  id: string;
  title: string;
  index: number;
  total: number;
  goal: string;
  active_objection: string | null;
}

export interface CallSummary {
  duration: number;
  steps_reached: string[];
  objections: string[];
  metrics: Metrics;
  hints_given: number;
  hints_useful: number;
  hints_not_useful: number;
  announcement_at: number | null;
  prospect_consent: boolean | null;
  emotion_analysis_used: boolean;
  transcription_cost_usd: number | null;
}

export interface AiSummary {
  summary: string;
  outcome: string;
  next_step: string;
  objections: { objection: string; handling: "bien traitée" | "partiellement" | "à retravailler"; comment: string }[];
  strengths: string[];
  improvements: string[];
  follow_up_email: { subject: string; body: string };
}

export type ServerMsg =
  | {
      type: "state";
      step: StepState;
      metrics: Metrics;
      emotions: EmotionReading[];
      analysis_paused: boolean;
      announcement_pending: boolean;
      prospect_consent: boolean | null;
    }
  | { type: "hint"; hint: Hint }
  | { type: "summary"; summary: CallSummary }
  | { type: "transcript"; speaker: Speaker; text: string; start: number; end: number; final: boolean }
  | { type: "ai_summary_pending" }
  | { type: "ai_summary"; summary: AiSummary | null; error: string | null }
  | { type: "error"; message: string };

export type ClientMsg =
  | {
      type: "start";
      scenario_id: string;
      language: "fr" | "en";
      self_analysis: boolean;
      context?: Record<string, string>;
      // "server" : le client envoie l'audio (trames binaires) et le backend transcrit en direct.
      transcription?: "client" | "server";
      keyterms?: string[];
    }
  | { type: "transcript"; speaker: Speaker; text: string; start: number; end: number; final: boolean }
  | { type: "ai_summary_pending" }
  | { type: "ai_summary"; summary: AiSummary | null; error: string | null }
  | { type: "voice"; speaker: Speaker; ts: number; scores: Record<EmotionState, number> }
  | {
      type: "face";
      speaker: Speaker;
      ts: number;
      visible: boolean;
      smile: number;
      frown: number;
      nod: number;
      gaze_away: number;
    }
  | { type: "feedback"; hint_id: string; useful: boolean }
  | { type: "pause_analysis" }
  | { type: "announcement_done"; ts: number; consent: boolean }
  | { type: "stop" };

export type CaptureStatus = "running" | "warning" | "stopped" | "error";

// Périphériques choisis pour l'appel (identifiants de navigator.mediaDevices, communs au
// panneau et au document offscreen, de même origine). Vide : ceux du système.
export interface CaptureDevices {
  micId?: string;
  speakerId?: string;
}

// Messages internes à l'extension (panneau <-> service worker <-> offscreen) pour la
// captation d'un vrai appel. `captureId` identifie une captation : les messages d'une
// captation précédente sont ignorés. `panelId` identifie le panneau qui l'a lancée : seul
// lui peut l'arrêter (un panneau par fenêtre). (La démo, elle, est jouée et captée dans le panneau.)
export type ExtensionMsg =
  | {
      target: "background"; action: "start-capture"; tabId: number; captureId: string; panelId: string;
      windowId?: number; // fenêtre du panneau : l'onglet d'appel retenu est celui de cette fenêtre
      devices: CaptureDevices;
    }
  | { target: "background"; action: "stop-capture"; captureId: string }
  | { target: "offscreen"; action: "start"; streamId: string; captureId: string; devices: CaptureDevices }
  | { target: "offscreen"; action: "stop" }
  | { target: "sidepanel"; action: "capture-status"; status: CaptureStatus; detail?: string; captureId: string };

// Audio du document offscreen vers le panneau (même origine) : blocs de 100 ms, PCM 16 bits
// à 16 kHz, micro (vous) et onglet (prospect) sur la même horloge. En retour, le panneau
// signale qu'il écoute toujours (« heartbeat ») : sans lui, le document offscreen coupe la
// captation de lui-même (panneau fermé, même si le service worker s'est endormi entre-temps).
export const AUDIO_BUS = "callhelp-audio";
export type AudioBusMsg =
  | {
      type: "frame";
      captureId: string;
      seq: number;
      mic: ArrayBuffer;
      tab: ArrayBuffer;
      level: { mic: number; tab: number };
    }
  | { type: "heartbeat"; captureId: string };
export const HEARTBEAT_TIMEOUT_MS = 4000;

export const DEFAULT_BACKEND = "ws://localhost:8000/ws/session";

// Préfixe du port qui relie un panneau au service worker (« callhelp-sidepanel:<panelId> ») :
// sa fermeture arrête la captation lancée par ce panneau.
export const PANEL_PORT = "callhelp-sidepanel";

// Onglet où l'icône a été cliquée, par fenêtre (storage.session « invoked:<windowId> ») :
// seul onglet que Chrome laisse capter. Et captation en cours (« capture »).
export interface InvokedTab {
  tabId: number;
  title?: string;
  host?: string;
}
export interface RunningCapture {
  captureId: string;
  panelId: string;
  tabId: number;
}
export const invokedKey = (windowId: number) => `invoked:${windowId}`;

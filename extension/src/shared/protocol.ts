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

// Messages internes à l'extension (panneau <-> service worker <-> offscreen).
export type ExtensionMsg =
  | { target: "background"; action: "start-capture"; tabId: number }
  | { target: "background"; action: "stop-capture" }
  | { target: "offscreen"; action: "start"; streamId: string; backendUrl: string }
  | { target: "offscreen"; action: "stop" }
  | { target: "sidepanel"; action: "capture-status"; status: "running" | "stopped" | "error"; detail?: string };

export const DEFAULT_BACKEND = "ws://localhost:8000/ws/session";

// Nom du port qui relie le panneau au service worker : sa fermeture arrête la capture.
export const PANEL_PORT = "callhelp-sidepanel";

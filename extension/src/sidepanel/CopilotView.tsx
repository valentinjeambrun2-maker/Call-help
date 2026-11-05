// Affichage du copilote (panneau latéral et page de démo) : fil directeur,
// message d'information, conseil courant, ressentis, métriques, bilan, historique.
import { useState } from "react";
import type { AiSummary, EmotionReading, Hint, Metrics } from "../shared/protocol";
import { clock, fr } from "../shared/format";
import type { CallState } from "./useCallSession";

type Tone = "good" | "warn" | "bad" | "neutral";

export const EMOTIONS: Record<string, { label: string; tone: Tone }> = {
  interest: { label: "Intéressé", tone: "good" },
  enthusiasm: { label: "Enthousiaste", tone: "good" },
  hesitation: { label: "Hésitant", tone: "warn" },
  boredom: { label: "Décroche", tone: "warn" },
  annoyance: { label: "Agacé", tone: "bad" },
};

export const KINDS: Record<Hint["kind"], { label: string; tone: "accent" | "bad" | "neutral" }> = {
  announcement: { label: "Message d'information", tone: "accent" },
  objection: { label: "Objection", tone: "bad" },
  step: { label: "Étape", tone: "accent" },
  pace: { label: "Rythme", tone: "neutral" },
  emotion: { label: "Ressenti", tone: "neutral" },
  question: { label: "Question", tone: "accent" },
  closing: { label: "Closing", tone: "accent" },
};

interface Props {
  state: CallState;
  selfAnalysis: boolean;
  onAnswer: (consent: boolean) => void;
  onFeedback: (id: string, useful: boolean) => void;
}

export function CopilotView({ state, selfAnalysis, onAnswer, onFeedback }: Props) {
  const live = state.phase === "live";
  const prospect = state.emotions.find((e) => e.speaker === "prospect");
  const user = state.emotions.find((e) => e.speaker === "user");
  const hint = state.current;
  const past = state.history.filter((h) => h.id !== hint?.id);

  return (
    <>
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

      {state.announcementPending && state.announcement && (
        <section className="card hint p3 notice" aria-live="polite">
          <div className="hint-head">
            <span className="eyebrow accent">{KINDS.announcement.label}</span>
          </div>
          <p className="hint-text">{fr(state.announcement.text)}</p>
          {state.announcement.say && <blockquote>{fr(state.announcement.say)}</blockquote>}
          <div className="consent">
            <button className="btn primary" onClick={() => onAnswer(true)}>Il est d'accord</button>
            <button className="btn secondary" onClick={() => onAnswer(false)}>Il refuse</button>
          </div>
          <p className="sub">Sans son accord, aucune analyse émotionnelle pendant tout l'appel.</p>
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
          <Feedback id={hint.id} value={state.feedback[hint.id]} onFeedback={onFeedback} />
        </section>
      )}

      {(live || state.phase === "ended") && (
        <section className="tiles two">
          <EmotionTile who="Prospect" reading={prospect} paused={state.analysisPaused} pending={state.announcementPending}
            refused={state.prospectConsent === false} />
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

      {state.aiSummaryState === "pending" && (
        <section className="card ai-summary">
          <div className="eyebrow halo-text">Bilan IA</div>
          <p className="sub">Rédaction du bilan et de l'email de suivi…</p>
        </section>
      )}
      {state.aiSummaryState === "failed" && <p className="sub center">Bilan IA indisponible pour cet appel.</p>}
      {state.aiSummary && <AiSummaryCard s={state.aiSummary} />}

      {past.length > 0 && (
        <details className="card list">
          <summary>Conseils précédents <span className="count">{past.length}</span></summary>
          <ul>
            {past.map((h) => (
              <li key={h.id}>
                <span className="time">{clock(h.ts)}</span>
                <span>{fr(h.text)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

export function EmotionTile({ who, reading, paused, pending, refused = false }: {
  who: string; reading?: EmotionReading; paused: boolean; pending: boolean; refused?: boolean;
}) {
  let text = "Calibration…";
  let tone: Tone = "neutral";
  let confidence = 0;
  if (pending) text = "En attente de son accord";
  else if (refused) text = "Analyse refusée";
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

const HANDLING_TONE: Record<string, string> = { "bien traitée": "good", partiellement: "warn", "à retravailler": "bad" };

function AiSummaryCard({ s }: { s: AiSummary }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`${s.follow_up_email.subject}\n\n${s.follow_up_email.body}`);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* presse-papiers indisponible */
    }
  };
  return (
    <section className="card ai-summary">
      <div className="eyebrow halo-text">Bilan IA</div>
      <h2>{fr(s.outcome)}</h2>
      <p className="sum-text">{fr(s.summary)}</p>
      <div className="sum-block">
        <div className="eyebrow accent">Prochaine étape</div>
        <p>{fr(s.next_step)}</p>
      </div>
      {s.objections.length > 0 && (
        <div className="sum-block">
          <div className="eyebrow">Objections</div>
          <ul className="sum-list">
            {s.objections.map((o, i) => (
              <li key={i}>
                <span className={`pill-mini ${HANDLING_TONE[o.handling] ?? ""}`}>{o.handling}</span>
                <strong>{fr(o.objection)}</strong> — {fr(o.comment)}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="sum-cols">
        <div>
          <div className="eyebrow">Points forts</div>
          <ul className="sum-list">{s.strengths.map((x, i) => <li key={i}>{fr(x)}</li>)}</ul>
        </div>
        <div>
          <div className="eyebrow">À travailler</div>
          <ul className="sum-list">{s.improvements.map((x, i) => <li key={i}>{fr(x)}</li>)}</ul>
        </div>
      </div>
      <details className="sum-email">
        <summary>Email de suivi : {s.follow_up_email.subject}</summary>
        <pre>{s.follow_up_email.body}</pre>
        <button className="btn secondary small" onClick={() => void copy()}>{copied ? "Copié" : "Copier l'email"}</button>
      </details>
    </section>
  );
}

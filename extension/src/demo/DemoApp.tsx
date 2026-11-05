// Page de démonstration : rejoue un appel généré (audio réel) et montre en direct
// ce que fait l'assistant, à côté de ce qu'il aurait dû faire (vérité terrain).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { DEFAULT_BACKEND, type ClientMsg, type Hint, type ServerMsg } from "../shared/protocol";
import { clock, fr, httpBase } from "../shared/format";
import { CopilotView, EMOTIONS } from "../sidepanel/CopilotView";
import { useCallSession } from "../sidepanel/useCallSession";

interface DemoCall {
  id: string;
  language: "fr" | "en";
  duration: number;
  turns: number;
  objections: string[];
  emotions: string[];
  outcome: string | null;
  offer: string | null;
  prospect_company: string | null;
  seller_name: string | null;
  prospect_name: string | null;
  source: "repo" | "local";
  has_audio: boolean;
}

interface TurnLabel {
  index: number;
  speaker: "user" | "prospect";
  text: string;
  start: number;
  end: number;
  emotion: string;
  intensity: number;
  step: string;
  objection: string | null;
}

interface Replay {
  scenario_id?: string;
  language?: "fr" | "en";
  context: Record<string, string>;
  events: ClientMsg[];
}

interface Observed {
  hint: Hint;
  arrival: number; // temps audio à la réception
}

const OBJECTION_LABELS: Record<string, string> = {
  no_time: "Pas le temps", has_provider: "Déjà un prestataire", no_budget: "Pas de budget",
  send_email: "Envoyez un mail", other: "Autre objection",
};
const OUTCOME_LABELS: Record<string, string> = {
  meeting_booked: "RDV pris", callback: "Rappel", send_info: "Envoi d'infos", refused: "Refus", hung_up: "Raccroché",
};
const STEP_LABELS: Record<string, string> = {
  announcement: "Message", hook: "Accroche", qualify: "Qualification", value: "Valeur", close: "RDV", wrap_up: "Conclusion",
};
// Étapes du dialogue -> étapes suivies par le moteur (mêmes règles que datagen/evaluate.py).
const STEP_MAP: Record<string, string> = {
  announcement: "hook", hook: "hook", qualify: "qualify", value: "value", close: "close", wrap_up: "close",
};
const TOLERANCE = 3; // s après la fin du tour pour qu'une fiche d'objection compte

function eventTime(e: ClientMsg): number {
  if (e.type === "transcript") return e.end;
  if (e.type === "voice" || e.type === "face" || e.type === "announcement_done") return e.ts;
  return Number.POSITIVE_INFINITY;
}

export function DemoApp() {
  const backendUrl = DEFAULT_BACKEND;
  const api = httpBase(backendUrl);
  const [calls, setCalls] = useState<DemoCall[]>([]);
  const [lang, setLang] = useState<"all" | "fr" | "en">("all");
  const [selected, setSelected] = useState<DemoCall | null>(null);
  const [labels, setLabels] = useState<TurnLabel[]>([]);
  const [replay, setReplay] = useState<Replay | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [observed, setObserved] = useState<Observed[]>([]);
  const [stepsAfter, setStepsAfter] = useState<Record<number, string>>({});
  const [emotionAfter, setEmotionAfter] = useState<Record<number, string | null>>({});

  const audio = useRef<HTMLAudioElement>(null);
  const nextEvent = useRef(0);
  const ticker = useRef<number | null>(null);
  const lastState = useRef<{ step: string | null; prospect: string | null }>({ step: null, prospect: null });

  const onServer = useCallback((msg: ServerMsg) => {
    const now = audio.current?.currentTime ?? 0;
    if (msg.type === "hint") setObserved((o) => [...o, { hint: msg.hint, arrival: now }]);
    if (msg.type === "state") {
      lastState.current = {
        step: msg.step.id,
        prospect: msg.emotions.find((e) => e.speaker === "prospect")?.state ?? null,
      };
    }
  }, []);
  const { state, connect, send, giveFeedback, answerAnnouncement } = useCallSession(backendUrl, onServer);

  useEffect(() => {
    fetch(`${api}/demo/calls`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((list: DemoCall[]) => setCalls(list.filter((c) => c.has_audio)))
      .catch(() => setLoadError(`Backend injoignable (${api}). Lancez-le, puis rechargez la page.`));
  }, [api]);

  const stopTicker = () => {
    if (ticker.current !== null) window.clearInterval(ticker.current);
    ticker.current = null;
  };
  useEffect(() => stopTicker, []);

  const choose = async (call: DemoCall) => {
    stopTicker();
    audio.current?.pause();
    setPlaying(false);
    setSelected(call);
    setObserved([]);
    setStepsAfter({});
    setEmotionAfter({});
    setT(0);
    const [l, r] = await Promise.all([
      fetch(`${api}/demo/calls/${call.id}/labels`).then((x) => x.json()),
      fetch(`${api}/demo/calls/${call.id}/replay`).then((x) => x.json()),
    ]);
    setLabels(l);
    setReplay(r);
  };

  const start = async () => {
    if (!selected || !replay || !audio.current) return;
    setObserved([]);
    setStepsAfter({});
    setEmotionAfter({});
    nextEvent.current = 0;
    await connect({
      type: "start", scenario_id: replay.scenario_id ?? "cold_call_fr", language: replay.language ?? selected.language,
      self_analysis: false, context: replay.context,
    });
    const el = audio.current;
    el.currentTime = 0;
    await el.play();
    setPlaying(true);
    stopTicker();
    // Pilotage par l'horloge de l'audio : chaque événement part quand le son l'atteint.
    ticker.current = window.setInterval(() => {
      const now = el.currentTime;
      setT(now);
      const events = replay.events;
      while (nextEvent.current < events.length && eventTime(events[nextEvent.current]) <= now) {
        const ev = events[nextEvent.current];
        send(ev);
        if (ev.type === "transcript") {
          const i = labels.findIndex((l) => l.start === ev.start && l.end === ev.end);
          // Instantané de l'état peu après le tour (laisse le temps au moteur de répondre).
          window.setTimeout(() => {
            setStepsAfter((s) => ({ ...s, [i]: lastState.current.step ?? "" }));
            setEmotionAfter((s) => ({ ...s, [i]: lastState.current.prospect }));
          }, 400);
        }
        nextEvent.current += 1;
      }
    }, 50);
  };

  const pause = () => {
    if (!audio.current) return;
    if (audio.current.paused) {
      void audio.current.play();
      setPlaying(true);
    } else {
      audio.current.pause();
      setPlaying(false);
    }
  };

  const finish = () => {
    stopTicker();
    audio.current?.pause();
    setPlaying(false);
    send({ type: "stop" });
  };

  // Revue : pour chaque objection attendue, la fiche du moteur est-elle arrivée à temps ?
  const review = useMemo(() => buildReview(labels, observed, stepsAfter, emotionAfter, t), [labels, observed, stepsAfter, emotionAfter, t]);

  const transcriptEnd = useRef<HTMLDivElement>(null);
  const visible = labels.filter((l) => l.start <= t);
  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [visible.length]);

  const shown = calls.filter((c) => lang === "all" || c.language === lang);

  return (
    <div className="demo">
      <header className="bar">
        <div className="brand">
          <span className="mark" aria-hidden />
          Call-help · Démo
        </div>
        <div className={`status ${state.phase}`}>
          <span className="pulse" aria-hidden />
          {selected ? `${selected.id} · ${clock(t)} / ${clock(selected.duration)}` : "Choisissez un appel"}
        </div>
      </header>

      <div className="demo-grid">
        <section className="demo-left">
          {!selected && (
            <>
              <div className="intro">
                <h1>Appels de démonstration.</h1>
                <p className="lead">
                  Appels générés avec Claude et ElevenLabs. L'assistant réagit en direct ; à gauche, ce qu'il aurait dû faire.
                </p>
              </div>
              {loadError && <p className="alert">{loadError}</p>}
              <div className="filters">
                {(["all", "fr", "en"] as const).map((l) => (
                  <button key={l} className={`chip ${lang === l ? "on" : ""}`} onClick={() => setLang(l)}>
                    {l === "all" ? "Tous" : l === "fr" ? "Français" : "Anglais"}
                  </button>
                ))}
              </div>
              <div className="call-list">
                {shown.map((c) => (
                  <button key={c.id} data-id={c.id} className="card call-card" onClick={() => void choose(c)}>
                    <div className="eyebrow">
                      {c.language === "fr" ? "Français" : "Anglais"} · {clock(c.duration)}
                      {c.outcome ? ` · ${OUTCOME_LABELS[c.outcome] ?? c.outcome}` : ""}
                    </div>
                    <div className="call-title">{c.prospect_company ?? c.id}</div>
                    <div className="sub">{c.offer}</div>
                    <div className="chips">
                      {c.objections.map((o) => <span key={o} className="chip bad">{OBJECTION_LABELS[o] ?? o}</span>)}
                      {c.emotions.map((e) => <span key={e} className="chip">{EMOTIONS[e]?.label ?? e}</span>)}
                    </div>
                  </button>
                ))}
              </div>
            </>
          )}

          {selected && (
            <>
              <div className="card player">
                <div className="player-head">
                  <div>
                    <div className="eyebrow">{selected.seller_name} → {selected.prospect_name}</div>
                    <div className="call-title">{selected.prospect_company}</div>
                    <div className="sub">{selected.offer}</div>
                  </div>
                  <button className="btn secondary small" onClick={() => { finish(); setSelected(null); }}>Changer d'appel</button>
                </div>
                <audio ref={audio} src={`${api}/demo/calls/${selected.id}/audio`} preload="auto"
                  onEnded={() => window.setTimeout(finish, 1500)} />
                <div className="progress"><span style={{ width: `${Math.min(100, (t / selected.duration) * 100)}%` }} /></div>
                <div className="actions">
                  {state.phase !== "live" ? (
                    <button className="btn primary" onClick={() => void start()} disabled={!replay}>Lancer la démo</button>
                  ) : (
                    <>
                      <button className="btn secondary" onClick={pause}>{playing ? "Pause" : "Reprendre"}</button>
                      <button className="btn danger" onClick={finish}>Terminer</button>
                    </>
                  )}
                  <span className="sub">Vendeur à gauche, prospect à droite (casque conseillé).</span>
                </div>
              </div>

              <Scorecard review={review} />

              <div className="card transcript">
                <div className="eyebrow">Transcription · attendu vs assistant</div>
                {visible.map((l, i) => (
                  <TurnRow key={l.index} turn={l} row={review.rows[i]} done={t > l.end + TOLERANCE} />
                ))}
                <div ref={transcriptEnd} />
              </div>
            </>
          )}
        </section>

        <aside className="demo-right">
          <div className="panel-frame">
            <div className="eyebrow">Ce que voit le vendeur</div>
            <CopilotView state={state} selfAnalysis={false} onAnswer={answerAnnouncement} onFeedback={giveFeedback} />
            {state.error && <p className="alert">{state.error}</p>}
          </div>
        </aside>
      </div>
    </div>
  );
}

// --- Revue -------------------------------------------------------------------------

interface Row {
  expectedStep: string;
  trackedStep: string | undefined;
  objection: string | null;
  objectionHit: Observed | null;
  emotion: string;
  engineEmotion: string | null | undefined;
  hints: Observed[]; // fiches émises en réponse à ce tour
  replaced: Set<string>;
}

interface Review {
  rows: Row[];
  objectionsExpected: number;
  objectionsHit: number;
  falseAlerts: number;
  stepsChecked: number;
  stepsCorrect: number;
  latencies: { rule: number[]; llm: number[] };
  hintsPerMinute: number;
}

function buildReview(labels: TurnLabel[], observed: Observed[], stepsAfter: Record<number, string>,
  emotionAfter: Record<number, string | null>, t: number): Review {
  const used = new Set<string>();
  // Fiches remplacées par leur version corrigée par l'IA : ni repérage, ni fausse alerte.
  const replaced = new Set(observed.map((o) => o.hint.replaces).filter((x): x is string => !!x));
  const rows: Row[] = labels.map((l, i) => {
    let hit: Observed | null = null;
    if (l.speaker === "prospect" && l.objection && l.objection !== "other") {
      hit = observed.find((o) => !used.has(o.hint.id) && !replaced.has(o.hint.id) && o.hint.kind === "objection"
        && o.hint.ref === l.objection && o.hint.ts >= l.start && o.hint.ts <= l.end + TOLERANCE) ?? null;
      if (hit) used.add(hit.hint.id);
    }
    const hints = observed.filter((o) => Math.abs(o.hint.ts - l.end) < 0.05 && o.hint.kind !== "announcement");
    return {
      expectedStep: STEP_MAP[l.step] ?? l.step, trackedStep: stepsAfter[i], objection: l.objection,
      objectionHit: hit, emotion: l.emotion, engineEmotion: emotionAfter[i], hints, replaced,
    };
  });
  const passed = labels.map((l) => t > l.end + TOLERANCE);
  const expected = labels.filter((l, i) => passed[i] && l.speaker === "prospect" && l.objection && l.objection !== "other");
  const objectionHints = observed.filter((o) => o.hint.kind === "objection" && o.hint.ref && o.hint.ref !== "other"
    && !replaced.has(o.hint.id));
  const steps = rows.filter((r, i) => passed[i] && r.trackedStep);
  const lat = { rule: [] as number[], llm: [] as number[] };
  for (const o of observed) {
    const l = labels.find((x) => Math.abs(x.end - o.hint.ts) < 0.05);
    if (l && o.hint.kind !== "announcement") lat[o.hint.source].push(Math.max(0, o.arrival - l.end));
  }
  return {
    rows,
    objectionsExpected: expected.length,
    objectionsHit: rows.filter((r, i) => passed[i] && r.objectionHit).length,
    falseAlerts: objectionHints.filter((o) => !used.has(o.hint.id) && o.hint.ts + TOLERANCE < t).length,
    stepsChecked: steps.length,
    stepsCorrect: steps.filter((r) => r.trackedStep === r.expectedStep).length,
    latencies: lat,
    hintsPerMinute: t > 10 ? (observed.filter((o) => o.hint.kind !== "announcement").length / t) * 60 : 0,
  };
}

function mean(xs: number[]): string {
  return xs.length ? `${(xs.reduce((a, b) => a + b, 0) / xs.length).toFixed(1)} s` : "—";
}

function Scorecard({ review }: { review: Review }) {
  const pct = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)} %` : "—");
  return (
    <section className="tiles four score">
      <div className="tile">
        <div className="label">Objections repérées</div>
        <div className="value">{review.objectionsHit}<small>/ {review.objectionsExpected}</small></div>
      </div>
      <div className={`tile ${review.falseAlerts > review.objectionsHit ? "warn" : ""}`}>
        <div className="label">Fausses alertes</div>
        <div className="value">{review.falseAlerts}</div>
      </div>
      <div className="tile">
        <div className="label">Bonne étape</div>
        <div className="value">{pct(review.stepsCorrect, review.stepsChecked)}</div>
      </div>
      <div className="tile">
        <div className="label">Délai règle / IA</div>
        <div className="value small-value">{mean(review.latencies.rule)} · {mean(review.latencies.llm)}</div>
      </div>
    </section>
  );
}

function TurnRow({ turn, row, done }: { turn: TurnLabel; row?: Row; done: boolean }) {
  const seller = turn.speaker === "user";
  const objectionExpected = turn.speaker === "prospect" && turn.objection && turn.objection !== "other";
  return (
    <div className={`turn ${seller ? "seller" : "prospect"}`}>
      <div className="turn-meta">
        <span className="time">{clock(turn.start)}</span>
        <span className="who">{seller ? "Vendeur" : "Prospect"}</span>
        <span className="chip ghost">{STEP_LABELS[turn.step] ?? turn.step}</span>
        {!seller && turn.emotion !== "neutral" && (
          <span className="chip ghost">attendu : {EMOTIONS[turn.emotion]?.label ?? turn.emotion}</span>
        )}
      </div>
      <p className="turn-text">{fr(turn.text)}</p>
      <div className="turn-review">
        {objectionExpected && (
          <span className={`chip ${done ? (row?.objectionHit ? "good" : "bad") : ""}`}>
            {done ? (row?.objectionHit ? "✔" : "✘") : "…"} {OBJECTION_LABELS[turn.objection!] ?? turn.objection}
            {row?.objectionHit && ` (${row.objectionHit.hint.source === "llm" ? "IA" : "mots-clés"})`}
          </span>
        )}
        {done && row?.trackedStep && (
          <span className={`chip ${row.trackedStep === row.expectedStep ? "good" : "warn"}`}>
            étape suivie : {STEP_LABELS[row.trackedStep] ?? row.trackedStep}
          </span>
        )}
        {done && !seller && row?.engineEmotion !== undefined && (
          <span className={`chip ${row.engineEmotion === turn.emotion || (!row.engineEmotion && turn.emotion === "neutral") ? "good" : "ghost"}`}>
            lu : {row.engineEmotion ? EMOTIONS[row.engineEmotion]?.label : "neutre"}
          </span>
        )}
        {row?.hints.map((o) => (
          <span key={o.hint.id} className={`chip hint-chip ${o.hint.source} ${row.replaced.has(o.hint.id) ? "struck" : ""}`}
            title={row.replaced.has(o.hint.id) ? "Remplacée par la version de l'IA" : undefined}>
            {o.hint.source === "llm" ? "IA" : "Règle"} · {fr(o.hint.text).slice(0, 70)}
          </span>
        ))}
      </div>
    </div>
  );
}

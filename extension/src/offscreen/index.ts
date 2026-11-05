// Document offscreen : la captation audio d'un vrai appel.
//
// Le son de l'onglet d'appel (le prospect) et le micro (vous) passent par le même
// AudioWorklet à 16 kHz, puis partent vers le panneau latéral (BroadcastChannel, même
// origine), qui les envoie au backend pour la transcription.
//
// Aucune image n'est captée : l'analyse du visage n'est pas encore branchée, on ne prend
// donc que le son. (Quand elle le sera, elle tournera ici et seuls des scores partiront.)
import { SAMPLE_RATE, captureNode } from "../shared/capture";
import { AUDIO_BUS, type AudioBusMsg, type CaptureStatus, type ExtensionMsg } from "../shared/protocol";

interface Capture {
  id: string;
  stop: () => void;
}

let current: Capture | null = null;
let starting = 0; // jeton : un arrêt ou un nouveau démarrage annule le démarrage en cours

function status(captureId: string, status: CaptureStatus, detail?: string) {
  const msg: ExtensionMsg = { target: "sidepanel", action: "capture-status", status, detail, captureId };
  void chrome.runtime.sendMessage(msg).catch(() => {});
}

function describe(err: unknown): string {
  if (err instanceof DOMException) {
    if (err.name === "NotAllowedError") return "accès refusé";
    if (err.name === "NotFoundError") return "aucun micro trouvé";
    if (err.name === "NotReadableError") return "micro déjà utilisé ou indisponible";
    return `${err.name} : ${err.message}`;
  }
  return err instanceof Error ? err.message : String(err);
}

async function startCall(id: string, streamId: string, token: number): Promise<Capture> {
  const cleanups: Array<() => void> = [];
  const stop = () => cleanups.splice(0).reverse().forEach((f) => f());
  try {
    // Le flux de l'onglet d'abord : son identifiant expire en quelques secondes.
    let tab: MediaStream;
    try {
      tab = await navigator.mediaDevices.getUserMedia({
        audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
      } as unknown as MediaStreamConstraints);
    } catch (err) {
      throw new Error(`son de l'onglet : ${describe(err)}`);
    }
    cleanups.push(() => tab.getTracks().forEach((t) => t.stop()));

    // Capturer un onglet le rend muet : on renvoie son son vers les haut-parleurs, à sa
    // qualité d'origine (contexte séparé de celui de l'analyse, à 16 kHz).
    const playback = new AudioContext();
    cleanups.push(() => void playback.close());
    playback.createMediaStreamSource(tab).connect(playback.destination);

    let mic: MediaStream | null = null;
    let micProblem: string | null = null;
    try {
      mic = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const m = mic;
      cleanups.push(() => m.getTracks().forEach((t) => t.stop()));
    } catch (err) {
      micProblem = describe(err);
    }
    if (token !== starting) throw new DOMException("annulé", "AbortError");

    const ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
    cleanups.push(() => void ctx.close());
    const bus = new BroadcastChannel(AUDIO_BUS);
    cleanups.push(() => bus.close());
    let seq = 0;
    const capture = await captureNode(ctx, (frame) => {
      const msg: AudioBusMsg = { type: "frame", captureId: id, seq: seq++, ...frame };
      bus.postMessage(msg);
    });
    cleanups.push(capture.close);
    if (mic) ctx.createMediaStreamSource(mic).connect(capture.node, 0, 0);
    ctx.createMediaStreamSource(tab).connect(capture.node, 0, 1);
    await ctx.resume();
    await playback.resume();
    if (token !== starting) throw new DOMException("annulé", "AbortError");

    tab.getAudioTracks()[0]?.addEventListener("ended", () => {
      if (current?.id !== id) return;
      stopCurrent();
      status(id, "stopped", "L'onglet d'appel n'est plus capté.");
    });
    if (micProblem) status(id, "warning", `Micro indisponible (${micProblem}) : seul le prospect est entendu.`);
    else status(id, "running");
    return { id, stop };
  } catch (err) {
    stop();
    throw err;
  }
}

function stopCurrent() {
  starting += 1;
  const capture = current;
  current = null;
  capture?.stop();
}

async function start(id: string, streamId: string) {
  stopCurrent();
  const token = starting;
  try {
    const capture = await startCall(id, streamId, token);
    if (token !== starting) {
      capture.stop(); // arrêtée ou remplacée pendant le démarrage
      return;
    }
    current = capture;
  } catch (err) {
    if (token !== starting) return;
    status(id, "error", describe(err));
  }
}

chrome.runtime.onMessage.addListener((msg: ExtensionMsg) => {
  if (msg.target !== "offscreen") return;
  if (msg.action === "start") {
    void start(msg.captureId, msg.streamId);
  } else if (msg.action === "stop") {
    const id = current?.id;
    stopCurrent();
    if (id) status(id, "stopped");
  }
});

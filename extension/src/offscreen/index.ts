// Document offscreen : la captation audio d'un vrai appel.
//
// Le son de l'onglet d'appel (le prospect) et le micro (vous) passent par le même
// AudioWorklet à 16 kHz, puis partent vers le panneau latéral (BroadcastChannel, même
// origine), qui les envoie au backend pour la transcription.
//
// Aucune image n'est captée : l'analyse du visage n'est pas encore branchée, on ne prend
// donc que le son. (Quand elle le sera, elle tournera ici et seuls des scores partiront.)
import { SAMPLE_RATE, captureNode } from "../shared/capture";
import {
  AUDIO_BUS, HEARTBEAT_TIMEOUT_MS,
  type AudioBusMsg, type CaptureDevices, type CaptureStatus, type ExtensionMsg,
} from "../shared/protocol";

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
    if (err.name === "NotFoundError" || err.name === "OverconstrainedError") return "aucun micro trouvé";
    if (err.name === "NotReadableError") return "micro déjà utilisé ou indisponible";
    return `${err.name} : ${err.message}`;
  }
  return err instanceof Error ? err.message : String(err);
}

const MIC_PROCESSING = { echoCancellation: true, noiseSuppression: true, autoGainControl: true };

// Micro choisi dans les réglages (celui du casque, comme dans l'outil d'appel) ; s'il a
// disparu, celui du système, en le disant.
async function openMic(micId: string | undefined): Promise<{ stream: MediaStream; note?: string }> {
  if (micId) {
    try {
      return { stream: await navigator.mediaDevices.getUserMedia({ audio: { ...MIC_PROCESSING, deviceId: { exact: micId } } }) };
    } catch (err) {
      if (!(err instanceof DOMException) || !["NotFoundError", "OverconstrainedError"].includes(err.name)) throw err;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: MIC_PROCESSING });
      return { stream, note: "le micro choisi est introuvable, celui du système est utilisé" };
    }
  }
  return { stream: await navigator.mediaDevices.getUserMedia({ audio: MIC_PROCESSING }) };
}

async function startCall(id: string, streamId: string, devices: CaptureDevices, token: number): Promise<Capture> {
  const cleanups: Array<() => void> = [];
  const stop = () => cleanups.splice(0).reverse().forEach((f) => f());
  const notes: string[] = [];
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

    // Capturer un onglet le rend muet : on renvoie son son vers la sortie choisie (le casque,
    // comme dans l'outil d'appel), à sa qualité d'origine (contexte séparé de l'analyse à 16 kHz).
    const playback = new AudioContext();
    cleanups.push(() => void playback.close());
    if (devices.speakerId) {
      try {
        await (playback as AudioContext & { setSinkId(id: string): Promise<void> }).setSinkId(devices.speakerId);
      } catch {
        notes.push("la sortie choisie est introuvable, celle du système est utilisée");
      }
    }
    playback.createMediaStreamSource(tab).connect(playback.destination);

    let mic: MediaStream | null = null;
    let micProblem: string | null = null;
    try {
      const opened = await openMic(devices.micId);
      mic = opened.stream;
      if (opened.note) notes.push(opened.note);
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

    // Le panneau signale qu'il écoute ; s'il se tait (fermé, rechargé), on coupe tout de
    // suite le micro et l'onglet, sans attendre le service worker (que Chrome endort).
    let heardPanelAt = performance.now();
    bus.onmessage = (ev: MessageEvent<AudioBusMsg>) => {
      if (ev.data.type === "heartbeat" && ev.data.captureId === id) heardPanelAt = performance.now();
    };
    let seq = 0;
    const capture = await captureNode(ctx, (frame) => {
      // Vérifié à chaque bloc (événement audio, jamais ralenti comme un minuteur en arrière-plan).
      if (performance.now() - heardPanelAt > HEARTBEAT_TIMEOUT_MS) {
        orphaned(id);
        return;
      }
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
    // Micro débranché, casque Bluetooth déconnecté, accès retiré : on le dit au lieu d'envoyer
    // du silence comme si le vendeur se taisait.
    mic?.getAudioTracks()[0]?.addEventListener("ended", () => {
      if (current?.id === id) status(id, "warning", "Micro perdu (débranché ?) : seul le prospect est entendu.");
    });
    if (micProblem) status(id, "warning", `Micro indisponible (${micProblem}) : seul le prospect est entendu.`);
    else if (notes.length) status(id, "warning", `Captation en cours, mais ${notes.join(" ; ")}.`);
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

// Plus aucun panneau n'écoute cette captation : on libère le micro et l'onglet, puis on
// demande au service worker de fermer ce document.
function orphaned(id: string) {
  if (current?.id !== id) return;
  stopCurrent();
  const msg: ExtensionMsg = { target: "background", action: "stop-capture", captureId: id };
  void chrome.runtime.sendMessage(msg).catch(() => {});
}

async function start(id: string, streamId: string, devices: CaptureDevices) {
  stopCurrent();
  const token = starting;
  try {
    const capture = await startCall(id, streamId, devices, token);
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
    void start(msg.captureId, msg.streamId, msg.devices ?? {});
  } else if (msg.action === "stop") {
    const id = current?.id;
    stopCurrent();
    if (id) status(id, "stopped");
  }
});

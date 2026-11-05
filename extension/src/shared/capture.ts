// Chaîne de captation audio commune : document offscreen pour un vrai appel, panneau pour
// la démo. Un AudioWorklet à deux entrées (0 = vous, 1 = prospect), dans un contexte à
// 16 kHz, rend des blocs de 100 ms de PCM 16 bits : le format attendu par le backend.

export const SAMPLE_RATE = 16000;

export interface Frame {
  mic: ArrayBuffer;
  tab: ArrayBuffer;
  level: { mic: number; tab: number }; // niveau RMS (0 à 1) du bloc
}

function workletUrl(): string {
  // Dans l'extension : fichier copié à la racine de dist/. En développement : servi par Vite.
  return typeof chrome !== "undefined" && chrome.runtime?.id ? chrome.runtime.getURL("pcm-worklet.js") : "/pcm-worklet.js";
}

export async function captureNode(ctx: AudioContext, onFrame: (f: Frame) => void) {
  await ctx.audioWorklet.addModule(workletUrl());
  const node = new AudioWorkletNode(ctx, "callhelp-capture", {
    numberOfInputs: 2,
    numberOfOutputs: 1,
    outputChannelCount: [1],
  });
  // Sortie muette reliée à la destination : le nœud est ainsi toujours traité.
  const sink = ctx.createGain();
  sink.gain.value = 0;
  node.connect(sink).connect(ctx.destination);
  node.port.onmessage = (e: MessageEvent<Frame>) => onFrame(e.data);
  const close = () => {
    node.port.postMessage("stop");
    node.port.onmessage = null;
    node.disconnect();
    sink.disconnect();
  };
  return { node, close };
}

function abortError(): DOMException {
  return new DOMException("Captation annulée", "AbortError");
}

/**
 * Appel enregistré en stéréo (vendeur à gauche, prospect à droite), joué sur les
 * haut-parleurs et capté comme un vrai appel. Retourne la fonction d'arrêt.
 */
export async function playRecordedCall(
  url: string,
  onFrame: (f: Frame) => void,
  onEnded: () => void,
  signal: AbortSignal,
): Promise<() => void> {
  const ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
  const cleanups: Array<() => void> = [() => void ctx.close()];
  const stop = () => cleanups.splice(0).reverse().forEach((f) => f());
  try {
    const response = await fetch(url, { signal });
    if (!response.ok) throw new Error(`audio de démo introuvable (HTTP ${response.status})`);
    const buffer = await ctx.decodeAudioData(await response.arrayBuffer()); // rééchantillonné à 16 kHz
    if (signal.aborted) throw abortError();
    const capture = await captureNode(ctx, onFrame);
    cleanups.push(capture.close);
    if (signal.aborted) throw abortError();

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    const split = ctx.createChannelSplitter(2);
    source.connect(split);
    split.connect(capture.node, 0, 0); // vendeur = « micro »
    split.connect(capture.node, buffer.numberOfChannels > 1 ? 1 : 0, 1); // prospect = « onglet »
    source.connect(ctx.destination); // on entend l'appel
    let ended = false;
    source.onended = () => {
      if (!ended) {
        ended = true;
        onEnded();
      }
    };
    cleanups.push(() => {
      ended = true; // un arrêt volontaire n'est pas une fin d'appel
      source.onended = null;
      try {
        source.stop();
      } catch {
        /* pas encore démarrée */
      }
    });
    await ctx.resume();
    source.start();
    signal.addEventListener("abort", stop, { once: true });
    return stop;
  } catch (err) {
    stop();
    throw err;
  }
}

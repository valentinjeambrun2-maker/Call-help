// Décodage d'un appel stéréo en deux canaux PCM 16 bits à 16 kHz, pour la transcription
// en direct (même format que la capture d'un vrai appel).

export const SAMPLE_RATE = 16000;
export const FRAME = SAMPLE_RATE / 10; // 100 ms

export async function decodeCall(url: string): Promise<[Int16Array, Int16Array]> {
  const data = await fetch(url).then((r) => {
    if (!r.ok) throw new Error(`audio HTTP ${r.status}`);
    return r.arrayBuffer();
  });
  const ctx = new AudioContext();
  const decoded = await ctx.decodeAudioData(data);
  await ctx.close();
  const frames = Math.ceil(decoded.duration * SAMPLE_RATE);
  const offline = new OfflineAudioContext(2, frames, SAMPLE_RATE);
  const src = offline.createBufferSource();
  src.buffer = decoded;
  // Canaux séparés : vendeur à gauche, prospect à droite.
  const splitter = offline.createChannelSplitter(2);
  const merger = offline.createChannelMerger(2);
  src.connect(splitter);
  splitter.connect(merger, 0, 0);
  splitter.connect(merger, decoded.numberOfChannels > 1 ? 1 : 0, 1);
  merger.connect(offline.destination);
  src.start();
  const rendered = await offline.startRendering();
  return [toInt16(rendered.getChannelData(0)), toInt16(rendered.getChannelData(1))];
}

function toInt16(f: Float32Array): Int16Array {
  const out = new Int16Array(f.length);
  for (let i = 0; i < f.length; i++) {
    const v = Math.max(-1, Math.min(1, f[i]));
    out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return out;
}

export function frameFor(channel: 0 | 1, pcm: Int16Array, from: number, to: number): Uint8Array {
  const slice = pcm.subarray(from, to);
  const out = new Uint8Array(1 + slice.byteLength);
  out[0] = channel;
  out.set(new Uint8Array(slice.buffer, slice.byteOffset, slice.byteLength), 1);
  return out;
}

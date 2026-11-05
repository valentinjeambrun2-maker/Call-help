// Captation audio de Call-help (AudioWorklet).
//
// Deux entrées : 0 = micro (vous), 1 = onglet d'appel (prospect). Chacune est ramenée en
// mono et découpée en blocs de 100 ms de PCM 16 bits. Le contexte audio tourne à 16 kHz :
// le navigateur rééchantillonne lui-même les flux à 44,1 ou 48 kHz. Les deux entrées
// avancent au même rythme (une entrée muette ou absente donne du silence), ce qui garde
// les deux voix sur la même horloge pour la transcription.
const BLOCK = Math.round(sampleRate / 10);

function toInt16(src, dst) {
  let sum = 0;
  for (let i = 0; i < src.length; i++) {
    const v = src[i] > 1 ? 1 : src[i] < -1 ? -1 : src[i];
    dst[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
    sum += v * v;
  }
  return Math.sqrt(sum / src.length);
}

class CallhelpCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.bufs = [new Float32Array(BLOCK), new Float32Array(BLOCK)];
    this.n = 0;
    this.running = true;
    this.port.onmessage = (e) => {
      if (e.data === "stop") this.running = false;
    };
  }

  process(inputs) {
    if (!this.running) return false;
    const quantum = (inputs[0][0] || inputs[1][0] || { length: 128 }).length;
    for (let i = 0; i < quantum; i++) {
      for (let k = 0; k < 2; k++) {
        const channels = inputs[k];
        let v = 0;
        if (channels && channels.length) {
          for (let c = 0; c < channels.length; c++) v += channels[c][i];
          v /= channels.length;
        }
        this.bufs[k][this.n] = v;
      }
      this.n += 1;
      if (this.n === BLOCK) {
        const mic = new Int16Array(BLOCK);
        const tab = new Int16Array(BLOCK);
        const level = { mic: toInt16(this.bufs[0], mic), tab: toInt16(this.bufs[1], tab) };
        this.port.postMessage({ mic: mic.buffer, tab: tab.buffer, level }, [mic.buffer, tab.buffer]);
        this.n = 0;
      }
    }
    return true;
  }
}

registerProcessor("callhelp-capture", CallhelpCapture);

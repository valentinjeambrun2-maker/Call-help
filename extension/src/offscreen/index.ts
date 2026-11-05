// Document offscreen : reçoit le flux de l'onglet d'appel.
//
// État (semaine 1) : capture audio + vidéo de l'onglet et restitution du son à
// l'utilisateur. Prochaines étapes, branchées ici :
//  - semaine 2 : envoi de l'audio (PCM 16 kHz) au backend pour la transcription ;
//  - semaine 4 : analyse du visage avec MediaPipe dans ce document, seuls les
//    scores partent au backend (aucune image ne quitte le navigateur).
import type { ExtensionMsg } from "../shared/protocol";

let stream: MediaStream | null = null;
let audioCtx: AudioContext | null = null;

function status(status: "running" | "stopped" | "error", detail?: string) {
  void chrome.runtime.sendMessage({ target: "sidepanel", action: "capture-status", status, detail });
}

async function start(streamId: string) {
  stop();
  const constraints = {
    audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    video: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
  } as unknown as MediaStreamConstraints;
  stream = await navigator.mediaDevices.getUserMedia(constraints);

  // Capturer un onglet le rend muet : on renvoie le son vers les haut-parleurs.
  audioCtx = new AudioContext();
  audioCtx.createMediaStreamSource(stream).connect(audioCtx.destination);
  status("running");
}

function stop() {
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  void audioCtx?.close();
  audioCtx = null;
}

chrome.runtime.onMessage.addListener((msg: ExtensionMsg) => {
  if (msg.target !== "offscreen") return;
  if (msg.action === "start") {
    start(msg.streamId).catch((err) => status("error", String(err)));
  } else if (msg.action === "stop") {
    stop();
    status("stopped");
  }
});

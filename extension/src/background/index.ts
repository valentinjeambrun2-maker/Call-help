// Service worker : ouvre le panneau latéral et pilote la capture de l'onglet d'appel.
import { DEFAULT_BACKEND, type ExtensionMsg } from "../shared/protocol";

chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(console.error);

async function ensureOffscreen(): Promise<void> {
  const contexts = await chrome.runtime.getContexts({
    contextTypes: [chrome.runtime.ContextType.OFFSCREEN_DOCUMENT],
  });
  if (contexts.length > 0) return;
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: [chrome.offscreen.Reason.USER_MEDIA],
    justification: "Capture du son et de l'image de l'onglet d'appel pour l'analyse en direct.",
  });
}

chrome.runtime.onMessage.addListener((msg: ExtensionMsg) => {
  if (msg.target !== "background") return;
  void (async () => {
    try {
      if (msg.action === "start-capture") {
        const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: msg.tabId });
        await ensureOffscreen();
        const stored = await chrome.storage.local.get("backendUrl");
        const backendUrl = typeof stored.backendUrl === "string" ? stored.backendUrl : DEFAULT_BACKEND;
        await chrome.runtime.sendMessage({ target: "offscreen", action: "start", streamId, backendUrl });
      } else if (msg.action === "stop-capture") {
        await chrome.runtime.sendMessage({ target: "offscreen", action: "stop" });
      }
    } catch (err) {
      await chrome.runtime.sendMessage({
        target: "sidepanel",
        action: "capture-status",
        status: "error",
        detail: String(err),
      });
    }
  })();
});

// Service worker : ouvre le panneau, pilote la capture de l'onglet d'appel et
// garantit qu'aucune capture ne survit au panneau.
import { DEFAULT_BACKEND, PANEL_PORT, type ExtensionMsg } from "../shared/protocol";

// L'API tabCapture n'accepte qu'un onglet où l'extension a été invoquée (activeTab) :
// on ouvre donc le panneau nous-mêmes au clic sur l'icône, et on retient l'onglet.
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false }).catch(console.error);

chrome.action.onClicked.addListener((tab) => {
  // Appel synchrone : sidePanel.open exige le geste de l'utilisateur.
  if (tab.windowId !== undefined) chrome.sidePanel.open({ windowId: tab.windowId }).catch(console.error);
  if (tab.id !== undefined) void chrome.storage.session.set({ invokedTabId: tab.id });
});

function notifyPanel(status: "running" | "stopped" | "error", detail?: string) {
  chrome.runtime.sendMessage({ target: "sidepanel", action: "capture-status", status, detail }).catch(() => {});
}

async function hasOffscreen(): Promise<boolean> {
  const contexts = await chrome.runtime.getContexts({
    contextTypes: [chrome.runtime.ContextType.OFFSCREEN_DOCUMENT],
  });
  return contexts.length > 0;
}

async function stopCapture(): Promise<void> {
  // Fermer le document offscreen libère le flux de l'onglet, même s'il ne répond plus.
  if (await hasOffscreen()) await chrome.offscreen.closeDocument();
  notifyPanel("stopped");
}

async function startCapture(tabId: number): Promise<void> {
  const { invokedTabId } = await chrome.storage.session.get("invokedTabId");
  if (invokedTabId !== tabId) {
    throw new Error("cliquez sur l'icône Call-help depuis l'onglet de l'appel, puis relancez");
  }
  await stopCapture(); // une seule capture à la fois
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: [chrome.offscreen.Reason.USER_MEDIA],
    justification: "Capture du son et de l'image de l'onglet d'appel pour l'analyse en direct.",
  });
  const stored = await chrome.storage.local.get("backendUrl");
  const backendUrl = typeof stored.backendUrl === "string" ? stored.backendUrl : DEFAULT_BACKEND;
  await chrome.runtime.sendMessage({ target: "offscreen", action: "start", streamId, backendUrl });
}

chrome.runtime.onMessage.addListener((msg: ExtensionMsg) => {
  if (msg.target !== "background") return;
  void (async () => {
    try {
      if (msg.action === "start-capture") await startCapture(msg.tabId);
      else if (msg.action === "stop-capture") await stopCapture();
    } catch (err) {
      notifyPanel("error", err instanceof Error ? err.message : String(err));
    }
  })();
});

// Panneau fermé ou rechargé : la capture s'arrête avec lui.
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== PANEL_PORT) return;
  port.onDisconnect.addListener(() => {
    void stopCapture().catch(console.error);
  });
});

// Onglet capturé fermé : on libère aussi la capture.
chrome.tabs.onRemoved.addListener((tabId) => {
  void chrome.storage.session.get("invokedTabId").then(({ invokedTabId }) => {
    if (invokedTabId === tabId) void stopCapture().catch(console.error);
  });
});

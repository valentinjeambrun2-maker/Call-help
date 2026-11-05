// Service worker : ouvre le panneau, pilote la captation (document offscreen) et
// garantit qu'aucune captation ne survit au panneau ni à l'onglet d'appel.
import { PANEL_PORT, type CaptureStatus, type ExtensionMsg } from "../shared/protocol";

// L'API tabCapture n'accepte qu'un onglet où l'extension a été invoquée (activeTab) :
// on ouvre donc le panneau nous-mêmes au clic sur l'icône, et on retient l'onglet.
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false }).catch(console.error);

chrome.action.onClicked.addListener((tab) => {
  // Appel synchrone : sidePanel.open exige le geste de l'utilisateur.
  if (tab.windowId !== undefined) chrome.sidePanel.open({ windowId: tab.windowId }).catch(console.error);
  if (tab.id !== undefined) void chrome.storage.session.set({ invokedTabId: tab.id });
});

function notifyPanel(status: CaptureStatus, detail?: string, captureId?: string) {
  const msg: ExtensionMsg = { target: "sidepanel", action: "capture-status", status, detail, captureId };
  chrome.runtime.sendMessage(msg).catch(() => {});
}

async function hasOffscreen(): Promise<boolean> {
  const contexts = await chrome.runtime.getContexts({
    contextTypes: [chrome.runtime.ContextType.OFFSCREEN_DOCUMENT],
  });
  return contexts.length > 0;
}

async function closeOffscreen(): Promise<boolean> {
  // Fermer le document offscreen libère l'onglet et le micro, même s'il ne répond plus.
  if (!(await hasOffscreen())) return false;
  await chrome.offscreen.closeDocument();
  return true;
}

async function startCapture(tabId: number, captureId: string): Promise<void> {
  await closeOffscreen(); // une seule captation à la fois
  const { invokedTabId } = await chrome.storage.session.get("invokedTabId");
  if (invokedTabId !== tabId) {
    throw new Error("cliquez sur l'icône Call-help depuis l'onglet de l'appel (Meet, Teams…), puis relancez");
  }
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: [chrome.offscreen.Reason.USER_MEDIA],
    justification: "Captation du son de l'appel (onglet et micro) pour la transcription et l'analyse en direct.",
  });
  const msg: ExtensionMsg = { target: "offscreen", action: "start", streamId, captureId };
  await chrome.runtime.sendMessage(msg);
}

async function stopCapture(): Promise<void> {
  if (await closeOffscreen()) notifyPanel("stopped");
}

// Les demandes sont traitées une à une : un démarrage et un arrêt rapprochés ne se
// chevauchent jamais (un seul document offscreen peut exister).
let queue: Promise<void> = Promise.resolve();
function enqueue(task: () => Promise<void>) {
  queue = queue.then(task, task).catch(console.error);
}

chrome.runtime.onMessage.addListener((msg: ExtensionMsg) => {
  if (msg.target !== "background") return;
  if (msg.action === "start-capture") {
    enqueue(async () => {
      try {
        await startCapture(msg.tabId, msg.captureId);
      } catch (err) {
        await closeOffscreen().catch(() => false);
        notifyPanel("error", err instanceof Error ? err.message : String(err), msg.captureId);
      }
    });
  } else if (msg.action === "stop-capture") {
    enqueue(stopCapture);
  }
});

// Panneau fermé ou rechargé : la captation s'arrête avec lui.
chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== PANEL_PORT) return;
  port.onDisconnect.addListener(() => enqueue(stopCapture));
});

// Onglet capté fermé : on libère aussi la captation.
chrome.tabs.onRemoved.addListener((tabId) => {
  void chrome.storage.session.get("invokedTabId").then(({ invokedTabId }) => {
    if (invokedTabId === tabId) enqueue(stopCapture);
  });
});

// Service worker : ouvre le panneau, pilote la captation (document offscreen) et
// garantit qu'aucune captation ne survit au panneau qui l'a lancée ni à l'onglet d'appel.
//
// Chrome endort ce service worker après 30 s sans événement, même avec un port ouvert : rien
// ne doit donc reposer sur sa mémoire. L'état vit dans storage.session, et le document
// offscreen s'arrête aussi de lui-même s'il n'entend plus le panneau (voir offscreen).
import {
  PANEL_PORT, invokedKey,
  type CaptureDevices, type CaptureStatus, type ExtensionMsg, type InvokedTab, type RunningCapture,
} from "../shared/protocol";

// L'API tabCapture n'accepte qu'un onglet où l'extension a été invoquée (activeTab) :
// on ouvre donc le panneau nous-mêmes au clic sur l'icône, et on retient l'onglet, par fenêtre
// (un panneau par fenêtre : cliquer dans une autre fenêtre ne change pas l'appel de celle-ci).
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false }).catch(console.error);

function hostOf(url?: string): string | undefined {
  try {
    return url ? new URL(url).hostname || undefined : undefined;
  } catch {
    return undefined;
  }
}

chrome.action.onClicked.addListener((tab) => {
  // Appel synchrone : sidePanel.open exige le geste de l'utilisateur.
  if (tab.windowId !== undefined) chrome.sidePanel.open({ windowId: tab.windowId }).catch(console.error);
  if (tab.id !== undefined && tab.windowId !== undefined) {
    const invoked: InvokedTab = { tabId: tab.id, title: tab.title, host: hostOf(tab.url) };
    void chrome.storage.session.set({ [invokedKey(tab.windowId)]: invoked });
  }
});

function notifyPanel(status: CaptureStatus, captureId: string, detail?: string) {
  const msg: ExtensionMsg = { target: "sidepanel", action: "capture-status", status, detail, captureId };
  chrome.runtime.sendMessage(msg).catch(() => {});
}

async function running(): Promise<RunningCapture | null> {
  const { capture } = await chrome.storage.session.get("capture");
  return (capture as RunningCapture | undefined) ?? null;
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

async function startCapture(tabId: number, captureId: string, panelId: string, devices: CaptureDevices): Promise<void> {
  // Une seule captation à la fois : celle d'un autre panneau (autre fenêtre) s'arrête, et on le lui dit.
  const previous = await running();
  await chrome.storage.session.remove("capture");
  if ((await closeOffscreen()) && previous && previous.panelId !== panelId) {
    notifyPanel("stopped", previous.captureId, "Un appel a démarré dans une autre fenêtre.");
  }
  const tab = await chrome.tabs.get(tabId);
  const key = invokedKey(tab.windowId);
  const invoked = (await chrome.storage.session.get(key))[key] as InvokedTab | undefined;
  if (invoked?.tabId !== tabId) {
    throw new Error("cliquez sur l'icône Call-help depuis l'onglet de l'appel (Meet, Teams…), puis relancez");
  }
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tabId });
  await chrome.offscreen.createDocument({
    url: "offscreen.html",
    reasons: [chrome.offscreen.Reason.USER_MEDIA],
    justification: "Captation du son de l'appel (onglet et micro) pour la transcription et l'analyse en direct.",
  });
  const capture: RunningCapture = { captureId, panelId, tabId };
  await chrome.storage.session.set({ capture });
  const msg: ExtensionMsg = { target: "offscreen", action: "start", streamId, captureId, devices };
  await chrome.runtime.sendMessage(msg);
}

// Arrête la captation en cours si elle correspond au filtre (sa captation, son panneau, son onglet).
async function stopCapture(match: (c: RunningCapture) => boolean, detail?: string): Promise<void> {
  const capture = await running();
  if (!capture || !match(capture)) return;
  await chrome.storage.session.remove("capture");
  if (await closeOffscreen()) notifyPanel("stopped", capture.captureId, detail);
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
        await startCapture(msg.tabId, msg.captureId, msg.panelId, msg.devices ?? {});
      } catch (err) {
        await chrome.storage.session.remove("capture");
        await closeOffscreen().catch(() => false);
        notifyPanel("error", msg.captureId, err instanceof Error ? err.message : String(err));
      }
    });
  } else if (msg.action === "stop-capture") {
    enqueue(() => stopCapture((c) => c.captureId === msg.captureId));
  }
});

// Panneau fermé ou rechargé : la captation qu'il a lancée s'arrête avec lui (pas celle d'un
// autre panneau). Le panneau garde ce port actif et le rouvre si Chrome endort le service worker.
chrome.runtime.onConnect.addListener((port) => {
  if (!port.name.startsWith(`${PANEL_PORT}:`)) return;
  const panelId = port.name.slice(PANEL_PORT.length + 1);
  port.onMessage.addListener(() => {}); // « ping » : maintient le service worker éveillé
  port.onDisconnect.addListener(() => enqueue(() => stopCapture((c) => c.panelId === panelId)));
});

// Onglet capté fermé : on libère la captation (et pas pour un autre onglet fermé).
chrome.tabs.onRemoved.addListener((tabId, info) => {
  enqueue(() => stopCapture((c) => c.tabId === tabId, "L'onglet de l'appel a été fermé."));
  const key = invokedKey(info.windowId);
  void chrome.storage.session.get(key).then((v) => {
    if ((v[key] as InvokedTab | undefined)?.tabId === tabId) void chrome.storage.session.remove(key);
  });
});

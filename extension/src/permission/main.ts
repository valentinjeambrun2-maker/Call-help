// Page d'autorisation du micro. Le panneau latéral et le document offscreen ne peuvent pas
// afficher la demande de Chrome : on la fait une fois ici, dans un onglet. L'accord vaut
// ensuite pour toute l'extension.
import "@fontsource-variable/inter";
import "../sidepanel/styles.css";

const button = document.getElementById("ask") as HTMLButtonElement;
const result = document.getElementById("result") as HTMLParagraphElement;

function say(text: string, tone: "ok" | "bad" | "" = "") {
  result.textContent = text;
  result.className = `sub ${tone}`;
}

async function ask() {
  button.disabled = true;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    say("C'est fait. Revenez au panneau Call-help et démarrez l'appel. Cet onglet va se fermer.", "ok");
    button.textContent = "Micro autorisé";
    window.setTimeout(() => {
      void chrome.tabs.getCurrent().then((tab) => (tab?.id !== undefined ? chrome.tabs.remove(tab.id) : undefined));
    }, 2500);
  } catch (err) {
    button.disabled = false;
    const denied = err instanceof DOMException && err.name === "NotAllowedError";
    say(
      denied
        ? "Accès refusé. Pour le rétablir : cliquez sur l'icône de réglages à gauche de l'adresse, puis autorisez le micro."
        : `Micro indisponible : ${err instanceof Error ? err.message : String(err)}`,
      "bad",
    );
  }
}

button.addEventListener("click", () => void ask());

void navigator.permissions
  .query({ name: "microphone" as PermissionName })
  .then((p) => {
    if (p.state === "granted") {
      say("Le micro est déjà autorisé. Vous pouvez fermer cet onglet.", "ok");
      button.textContent = "Micro autorisé";
      button.disabled = true;
    }
  })
  .catch(() => {});

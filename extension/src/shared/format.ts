// Petites fonctions d'affichage partagées par le panneau et la page de démo.

export function httpBase(wsUrl: string): string {
  return wsUrl.replace(/^ws/, "http").replace(/\/ws\/session$/, "");
}

export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

// Typographie française : espaces insécables avant ? ! : ; et dans « ».
export function fr(text: string): string {
  return text.replace(/\s+([?!;:»])/g, " $1").replace(/«\s+/g, "« ");
}

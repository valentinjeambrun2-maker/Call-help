// Réglages de l'appel, mémorisés d'un appel à l'autre (navigateur de l'utilisateur seulement).
// Ils remplissent le script (« Bonjour, ici {prénom} de {société}… ») et aident la
// transcription à reconnaître les noms propres.

export interface Settings {
  language: "fr" | "en";
  userName: string;
  company: string;
  segment: string; // qui vous appelez (« agences immobilières »)
  benefit: string; // ce que vous leur apportez (« gagner deux heures par semaine »)
  // Micro et sortie de l'appel : ceux du casque, comme dans l'outil d'appel. Vide : ceux du système.
  micId: string;
  speakerId: string;
}

export const DEFAULT_SETTINGS: Settings = {
  language: "fr", userName: "", company: "", segment: "", benefit: "", micId: "", speakerId: "",
};

const KEY = "callhelp.settings";

export function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULT_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<Settings>;
    return {
      ...DEFAULT_SETTINGS,
      ...Object.fromEntries(Object.entries(parsed).filter(([, v]) => typeof v === "string")),
      language: parsed.language === "en" ? "en" : "fr",
    };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(s: Settings) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* stockage indisponible : les réglages valent pour cette session seulement */
  }
}

// Variables du script (voir backend/scenarios) : seules les valeurs renseignées sont envoyées.
export function scriptContext(s: Settings): Record<string, string> {
  const ctx: Record<string, string> = {
    user_name: s.userName.trim(),
    company: s.company.trim(),
    segment: s.segment.trim(),
    benefit: s.benefit.trim(),
  };
  return Object.fromEntries(Object.entries(ctx).filter(([, v]) => v));
}

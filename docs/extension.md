# Utiliser Call-help sur un vrai appel

L'extension Chrome écoute un appel passé dans le navigateur (Google Meet, Teams, Zoom web, téléphonie web…) :
- le son de l'onglet d'appel, c'est-à-dire le prospect ;
- votre micro, c'est-à-dire vous.

Les deux voix sont transcrites en direct par ElevenLabs. Le panneau latéral affiche le fil directeur, les réponses aux objections et le bilan de fin d'appel.

## Installer

1. Backend, depuis le dossier `Call-help`. Clés dans `backend/.env` (jamais publié, voir le README).

```bash
cd backend && .venv/bin/uvicorn app.main:app --port 8000
```

2. Extension :

```bash
cd extension && npm install && npm run build
```

3. Dans Chrome : `chrome://extensions` → **Mode développeur** → **Charger l'extension non empaquetée** → dossier `extension/dist`. Épinglez l'icône Call-help.

Après chaque `npm run build`, cliquez sur ↻ dans `chrome://extensions` pour recharger l'extension.

## Pendant l'appel

1. Ouvrez l'onglet de l'appel, puis **cliquez sur l'icône Call-help depuis cet onglet**. Chrome ne laisse capter que l'onglet où l'extension a été ouverte.
2. Dans le panneau, réglez une fois pour toutes :
   - la langue de l'appel ;
   - votre prénom et votre société ;
   - qui vous appelez et ce que vous leur apportez.

   Ces réglages remplissent le script (« Bonjour, ici Léa de The Herakles… ») et aident la transcription à reconnaître les noms.
3. **Démarrer l'appel.** La première fois, un onglet demande l'accès au micro. Le panneau latéral ne peut pas afficher cette demande de Chrome. Autorisez, revenez au panneau et cliquez de nouveau.
4. Le panneau affiche d'abord le **message d'information** à lire au prospect. Notez sa réponse : son ressenti n'est analysé qu'avec son accord.
5. La carte **Écoute**, en bas, montre le niveau de chaque voix et la dernière phrase entendue. Si une barre reste vide, ce côté n'est pas capté.
6. **Terminer** : le bilan chiffré s'affiche aussitôt, le bilan IA et l'email de suivi environ 15 s plus tard.

**Un casque est fortement conseillé.** Sans casque, votre micro reprend la voix du prospect qui sort des haut-parleurs, et ses phrases risquent d'être comptées comme les vôtres. L'annulation d'écho de Chrome est activée, mais ne suffit pas toujours.

## Voir la démo dans l'extension

Choisissez un appel de démo sous **Démarrer l'appel**, puis **Voir la démo**. L'appel généré est joué et passe exactement par le même chemin audio qu'un vrai appel : captation, transcription en direct, conseils, bilan. Pour comparer à ce qu'il aurait fallu faire réplique par réplique, utilisez la page de démo (`docs/demos.md`).

## Ce qui est mesuré

Mesures dans Chromium, avec l'extension construite :
- **Appel de démo de 2 min 35** joué de bout en bout dans le panneau :
  - erreurs de mots : vendeur 3,5 %, prospect 8,3 %, identiques à la page de démo ;
  - horloges de l'audio et de l'appel calées à 0,2 s près.
- **Micro** (faux micro Chromium à 48 kHz, voix du vendeur) : 5,1 % d'erreurs de mots, annulation d'écho active.
- **Non testable automatiquement** : la captation de l'onglet elle-même, car Chrome exige un clic sur l'icône. À vérifier à la main sur un vrai Meet.

## Ce qu'il faut savoir

- **Seuls les appels passés dans le navigateur sont captés.** Pas un téléphone posé sur le bureau.
- **Aucune image n'est captée** tant que l'analyse du visage n'est pas branchée.
- **L'audio n'est pas enregistré par Call-help.** Il est transcrit en direct par ElevenLabs. Le texte de l'appel reste en mémoire du backend le temps de l'appel et du bilan, rédigé par Claude.
- **Si le son n'arrive plus pendant 5 s** (onglet fermé, mise en veille), l'appel est clos avec ce qui a été entendu.
- **Si la clé Anthropic est refusée**, le panneau le dit. Les conseils par mots-clés continuent, mais il n'y a pas de bilan IA.

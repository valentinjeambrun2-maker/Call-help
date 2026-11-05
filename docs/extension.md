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

1. Ouvrez l'onglet de l'appel, puis **cliquez sur l'icône Call-help depuis cet onglet**. Chrome ne laisse capter que l'onglet où l'extension a été ouverte. Le panneau affiche l'onglet retenu (« Onglet de l'appel : meet.google.com · … »). Un clic sur l'icône depuis un autre onglet le remplace pour l'appel suivant, pas pour l'appel en cours.
2. Dans le panneau, réglez une fois pour toutes :
   - la langue de l'appel ;
   - votre prénom et votre société ;
   - qui vous appelez et ce que vous leur apportez ;
   - **le micro et la sortie du son** : choisissez ceux de votre casque, comme dans l'outil d'appel. Par défaut, ce sont ceux du système. Si l'outil d'appel utilise le casque et Call-help le micro de l'ordinateur, votre micro reprend la voix du prospect.

   Ces réglages remplissent le script (« Bonjour, ici Léa de The Herakles… ») et aident la transcription à reconnaître les noms, des deux côtés. Les listes de micros et de sorties apparaissent une fois le micro autorisé.
3. **Démarrer l'appel.** La première fois, un onglet demande l'accès au micro. Le panneau latéral ne peut pas afficher cette demande de Chrome. Autorisez, revenez au panneau et cliquez de nouveau.
4. Le panneau affiche d'abord le **message d'information** à lire au prospect. Il dit ce qui se passe vraiment : la transcription en direct, et la lecture de son ressenti à travers ses mots, seulement s'il est d'accord. Notez sa réponse. S'il refuse, aucun ressenti n'est lu ni proposé pendant tout l'appel. La transcription et les conseils continuent.
5. La carte **Écoute**, en bas, montre le niveau de chaque voix et la dernière phrase entendue. Si une barre reste vide, ce côté n'est pas capté. Le panneau prévient si :
   - rien n'est entendu sur l'onglet pendant 20 s (mauvais onglet ?) ;
   - le micro est perdu en cours d'appel (casque débranché).

   **Couper mon micro** : pour parler à un collègue sans que ce soit transcrit. Couper le micro dans Meet ne suffit pas, car Call-help a son propre accès au micro.
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
- **Captation d'un onglet**, avec le clic sur l'icône simulé par Chrome (`Extensions.triggerAction`) et un faux backend, sans service payant :
  - fermeture du panneau, y compris après 90 s d'inactivité ;
  - panneau qui ne répond plus ;
  - autre panneau ou autre onglet fermé ;
  - micro perdu, micro choisi introuvable, sortie choisie ;
  - micro coupé ;
  - double clic sur la démo.
- **À vérifier à la main sur un vrai Meet** : la qualité de la transcription avec de vrais micros et un vrai casque.

## Ce qu'il faut savoir

- **Seuls les appels passés dans le navigateur sont captés.** Pas un téléphone posé sur le bureau.
- **Aucune image n'est captée** tant que l'analyse du visage n'est pas branchée.
- **L'audio n'est pas enregistré par Call-help.** Il est transcrit en direct par ElevenLabs. Le texte de l'appel reste en mémoire du backend le temps de l'appel et du bilan, rédigé par Claude.
- **ElevenLabs conserve par défaut l'audio et le texte** dans l'historique du compte. Le mode sans conservation (`ELEVENLABS_ZERO_RETENTION=1` dans `backend/.env`) est réservé aux comptes entreprise. Un compte qui ne l'autorise pas refuse la session. À trancher avec le juriste avant de vrais appels, avec le texte du message d'information.
- **Fermer le panneau arrête la captation**, même en plein appel. Le micro et l'onglet sont libérés en 4 s au plus.
- **Lecture de votre propre ton** : seulement dans les démos pour l'instant. Elle n'est pas encore branchée sur un vrai appel.
- **Si le son n'arrive plus pendant 5 s** (onglet fermé, mise en veille), l'appel est clos avec ce qui a été entendu.
- **Si la clé Anthropic est refusée**, le panneau le dit. Les conseils par mots-clés continuent, mais il n'y a pas de bilan IA.

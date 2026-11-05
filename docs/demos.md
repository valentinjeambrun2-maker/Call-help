# Démos en direct avec les appels générés

![Page de démo pendant un appel en transcription directe](demo-page.png)

La page de démo rejoue un appel généré par Claude et ElevenLabs, avec le vrai son, et montre en direct ce que fait l'assistant :
- **à droite**, ce que voit le vendeur ;
- **à gauche**, ce qu'il aurait dû faire, réplique par réplique.

## Lancer la page

Deux terminaux, depuis le dossier `Call-help`.

1. Backend. Clés dans `backend/.env` : `ANTHROPIC_API_KEY` pour les conseils et le bilan, `ELEVENLABS_API_KEY` pour la transcription en direct.

```bash
cd backend && .venv/bin/uvicorn app.main:app --port 8000
```

2. Page :

```bash
cd extension && npm run dev
```

Ouvrez **http://localhost:5173/demo.html**. Dix appels sont fournis dans le dépôt (`backend/demo/calls`, 15 Mo) :
- 5 en français, 5 en anglais ;
- les 4 objections, les 5 issues d'appel et les 5 émotions sont toutes représentées.

Les appels générés localement (`python -m datagen pilot`) s'ajoutent à la liste.

**Deux modes :**

| Mode | Ce qui se passe | Pour quoi faire |
|---|---|---|
| **Transcription en direct** (par défaut) | L'audio est découpé en deux canaux 16 kHz dans le navigateur, envoyé au backend au rythme de la lecture, transcrit par ElevenLabs, puis analysé. C'est le chemin d'un vrai appel. | Voir l'assistant dans les conditions réelles (≈ 0,40 $ par heure d'audio, plus Claude) |
| **Texte de référence** | Le texte exact de chaque réplique est envoyé à la fin du tour. | Isoler les erreurs de l'assistant de celles de la transcription |

**Si la transcription ne marche pas.** Le backend vérifie les clés à son démarrage, sans rien facturer, et l'écrit dans son terminal (« Call-help · Transcription en direct (ElevenLabs) : … »). La page de démo affiche la même raison avant le lancement. Les causes courantes :
- **Clé refusée :**
  - clé révoquée ;
  - caractère en trop, guillemet ou espace ;
  - permission « Speech to Text » non accordée à la clé sur elevenlabs.io.
- **Fichier mal enregistré :**
  - `backend/.env` enregistré en texte enrichi par TextEdit, ou sous le nom `.env.txt`. Il est lu quand même, avec un avertissement ;
  - une clé exportée dans le terminal (`export`, `~/.zshrc`), qui l'emporte sur `backend/.env`.
- **Ancien backend encore lancé :** la page le signale. Arrêtez-le (Ctrl+C), puis relancez-le.

`backend/.env` est relu à chaque appel : une clé corrigée est prise en compte sans relancer le backend. Le bouton « Revérifier » refait le diagnostic.

**Ce que montre la colonne de gauche, pour chaque réplique :**
- le texte de référence et, en mode direct, ce que la transcription a **entendu**, avec son délai ;
- l'étape attendue et l'émotion attendue ;
- ✔ ou ✘ sur chaque objection attendue (repérée par les mots-clés ou par l'IA) ;
- l'étape suivie par l'assistant et l'émotion qu'il a lue ;
- les conseils émis. Une fiche barrée a été corrigée par l'IA.

Le **tableau de bord** en haut compte les objections repérées, les fausses alertes, la bonne étape, et le délai des règles et de l'IA après la fin de la parole. En fin d'appel, le **bilan IA** s'affiche à droite.

## Ce qui marche, ce qui ne marche pas encore

Mesuré le 5 novembre 2026 sur les 10 appels de démo, joués en direct : transcription ElevenLabs, conseils Claude Haiku 5.5, bilan Claude Opus 5.5.

| | Résultat | Commentaire |
|---|---|---|
| Transcription | **3,4 % d'erreurs de mots** (4 600 mots), délai médian **1,0 s** | Fiable. Les noms de marque passent mal (« The Herakles » → « Studio des Héraclès »). |
| Objections repérées | **28/30 (93 %)**, 1 fausse alerte | 21/30 et 4 fausses alertes à la première mesure. Un peu variable d'une exécution à l'autre. |
| Étape suivie | **89 %** des tours | 70 % avant : « Valeur » n'était presque jamais reconnue (voir plus bas) |
| Délai des conseils après la fin de la parole | règles **≈ 1,2 s**, IA **≈ 2,1 s** | Dans la cible de 1 à 3 s |
| Issue de l'appel dans le bilan IA | **10/10 justes** | RDV, rappel, envoi d'infos, refus. Bilan prêt en ≈ 15 s, ≈ 0,04 $ |
| Ressenti lu dans les mots | **≈ 32 %** d'accord exact avec l'émotion attendue | Faible. Les émotions attendues des appels synthétiques sont elles-mêmes incertaines. |
| Ressenti lu dans la voix | non branché | Le meilleur modèle libre utilisable (emotion2vec+) ne fait guère mieux que le hasard sur ces voix (41 % contre 35 %), et ses données d'entraînement posent question pour un usage commercial. |

**Contrôle hors démo.** Les consignes de Claude ont été retouchées en regardant ces 10 appels. Pour vérifier que le gain n'est pas propre à eux, on les a mesurées avant et après sur les 90 autres appels du pilote, qui n'ont pas servi au réglage (texte de référence, sans transcription) :

| | Avant | Après |
|---|---|---|
| Objections : F1 | 87 % | **90 %** |
| Précision | 83 % | 87 % |
| Rappel | 91 % | 94 % |
| Bonne étape | 74 % | **87 %** |

L'objection la moins bien reconnue reste « pas le temps » (F1 73 %) : un prospect qui déplace un créneau est parfois pris pour un prospect pressé.

**Les deux objections encore manquées en direct :**
1. **Formulation très indirecte :** « Côté sous, c'est serré, le patron compte chaque euro », dit juste après avoir accepté le rendez-vous.
2. **Cas limite :** « On a déjà un logiciel, l'espace client de notre éditeur comptable ». L'étiquette dit « déjà un prestataire », mais c'est un outil, pas une agence.

**Corrigé grâce aux démos** (les mesures en lot ne le montraient pas) :
- **L'étape « Valeur » n'était presque jamais reconnue** (1 tour sur 59). Claude écrivait d'abord son conseil (« creuse le besoin ») puis choisissait l'étape qui allait avec, au lieu de regarder ce que faisait le vendeur. Les tours du vendeur sont désormais classés à part, étape seule.
- Une demande de mail polie (« can you just email me something? ») était repérée par les mots-clés, puis retirée par Claude.
- En direct, une analyse de l'IA était annulée dès que le prospect reparlait : l'objection qu'elle contenait était perdue.
- Des fausses alertes des mots-clés (« par mail », « on a déjà ») étaient comptées comme objections levées, et bloquaient ensuite la vraie.
- L'étape avançait sur le premier « oui ».
- La transcription coupe une réplique à chaque pause : l'IA jugeait des fragments.
- L'IA écrivait « objection budget » dans son conseil sans cocher l'objection : elle classe désormais après avoir rédigé.
- Les derniers tours d'un appel n'étaient jamais notés dans le tableau de bord.

**Robustesse** (deux vérifications indépendantes du code, chaque défaut reproduit puis couvert par un test) :
- La transcription en direct survit aux coupures réseau. Elle reprend sans perdre l'audio, sans doublon et sans boucle de reconnexion.
- Elle s'arrête proprement sur une clé refusée.
- Elle se ferme en moins de 3 s même sur un réseau figé.
- Le bilan IA répond toujours, même en cas de panne.
- Changer d'appel pendant la préparation ne laisse plus de session fantôme.

## Pistes suivantes

1. **Données réelles.** Quelques vrais appels enregistrés avec accord, pour mesurer ce que valent ces chiffres hors synthèse.
2. **Vraie capture d'appel.** Brancher l'extension (onglet d'appel et micro) sur le même chemin audio que la démo.
3. **Ressenti de la voix.** Demander l'accès à l'API Hume (français, temps réel), puis la comparer au ressenti lu dans les mots dans le mode revue.
4. **« Pas le temps ».** Mieux distinguer un prospect pressé d'un prospect qui déplace un créneau. Proposer au vendeur de corriger une fiche d'un clic, et s'en servir comme signal.

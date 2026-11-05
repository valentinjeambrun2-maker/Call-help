# Call-help — Architecture du MVP

Référence des décisions : `docs/decisions.md`.

## 1. Vue d'ensemble

```
┌──────────────── Navigateur (extension Chrome) ────────────────┐
│                                                               │
│  Onglet visio / softphone ──► tabCapture ──┐                  │
│  Micro de l'utilisateur ─────► getUserMedia ┼─► Offscreen doc │
│                                             │   - audio 16 kHz│
│  Vidéo de l'onglet ──► MediaPipe (local) ───┤   - scores visage│
│                       (aucune image envoyée)│                  │
│                                             ▼                  │
│  Panneau latéral React ◄──────── WebSocket ─────────────┐     │
└─────────────────────────────────────────────────────────┼─────┘
                                                          │
┌──────────────────── Backend FastAPI ────────────────────▼─────┐
│  /ws/session                                                  │
│   ├─ STT streaming (fournisseur remplaçable)  → segments      │
│   ├─ Métriques live (débit, temps de parole, monologue)       │
│   ├─ Fusion émotions (voix + visage, calibration, seuil 70 %) │
│   ├─ Suivi du scénario (étape, branche objection)             │
│   └─ Moteur de conseils                                       │
│        ├─ règles instantanées (< 100 ms)                      │
│        └─ LLM rapide en parallèle (~2 s), sortie JSON :       │
│           conseil + classement (objection, étape)             │
│  Bilan post-appel (modèle plus fort)                          │
│  API admin (structures, membres, droits) — étape suivante     │
└───────────────────────────────────────────────────────────────┘
```

**Séparation des voix sans diarisation** : le micro = l'utilisateur, le son de l'onglet = le(s) interlocuteur(s). C'est fiable pour un cold call à deux.

## 2. « Full duplex » dans Call-help

L'agent ne parle pas pendant l'appel. « Full duplex » signifie ici que **l'écoute et la rédaction du conseil tournent en parallèle** :

- la transcription arrive en continu (résultats partiels puis définitifs) ;
- dès qu'un déclencheur apparaît (objection, question, silence, changement d'émotion), un conseil par **règle** part immédiatement ;
- en parallèle, le LLM prépare un conseil plus fin, qui **remplace** le conseil par règle quand il arrive ;
- une nouvelle phrase du prospect peut **annuler** une génération LLM devenue obsolète.

C'est ce qui permet de tenir 1-3 s de latence perçue même avec un LLM.

## 3. Protocole WebSocket (v0)

Client → serveur (JSON) :

| type | contenu |
|---|---|
| `start` | `scenario_id`, `language`, `self_analysis` (bool, faux par défaut : accord explicite), `context` |
| `announcement_done` | `ts`, `consent` (réponse du prospect au message d'information) |
| `transcript` | `speaker` (`user`/`prospect`), `text`, `start`, `end`, `final` |
| `voice` | `speaker`, `ts`, `scores` {état: probabilité} |
| `face` | `speaker`, `ts`, `expressions` {smile, frown, nod, gaze_away}, `visible` |
| `feedback` | `hint_id`, `useful` (bool) |
| `stop` | — |

Serveur → client :

| type | contenu |
|---|---|
| `state` | étape du scénario, métriques, émotions affichables |
| `hint` | `id`, `kind`, `text` (indication), `say` (phrase), `source` (`rule`/`llm`), `replaces` |
| `summary` | bilan de fin d'appel |

Plus tard, le client enverra l'audio brut (binaire, PCM 16 kHz) et la transcription se fera côté serveur. Le message `transcript` reste utile pour les tests et le rejeu d'appels.

## 4. Modules backend

| Module | Rôle |
|---|---|
| `app/models.py` | Schémas Pydantic (messages, conseils, émotions) |
| `app/scenario.py` | Chargement du scénario JSON, suivi d'étape, détection d'objection |
| `app/metrics.py` | Débit (mots/min), temps de parole, monologue, silences |
| `app/emotion.py` | Fusion voix + visage, calibration 30 s, seuil de confiance |
| `app/coach/` | Moteur de conseils : règles + fournisseur LLM, limitation de fréquence |
| `app/session.py` | Orchestration d'un appel |
| `app/main.py` | API FastAPI + WebSocket |

## 5. Confidentialité dès la conception

- Les images du visage ne quittent jamais le navigateur : seuls des scores sont envoyés.
- Aucun audio stocké par défaut.
- Les émotions sont exclues des données visibles par un admin/manager (Q38).
- L'analyse du ton de l'utilisateur se fait sur son accord explicite (`self_analysis`, mémorisé par le panneau). Elle n'est jamais visible par un manager ou un admin.
- L'analyse émotionnelle du prospect exige son accord (`announcement_done.consent`). En cas de refus, elle est bloquée pour lui seul pendant tout l'appel, et Claude reçoit `emotion_allowed = false`.
- La capture de l'onglet s'arrête dès que le panneau qui l'a lancée se ferme, que l'onglet d'appel se ferme ou que la connexion au backend tombe. Chrome endort le service worker après 30 s sans événement. Deux mécanismes couvrent ce cas :
  - le panneau garde le service worker éveillé (un message toutes les 20 s) et rouvre son port s'il tombe ;
  - le document offscreen coupe le micro et l'onglet de lui-même s'il n'entend plus le panneau pendant 4 s. Le panneau répond à chaque seconde d'audio reçue.
- Un bouton « couper l'analyse » arrête voix et visage pour le reste de l'appel.

## 6. Open source : viable ou pas ? (Q40)

| Brique | Options open source | Verdict MVP |
|---|---|---|
| Analyse du visage | MediaPipe Face Landmarker (blendshapes, tourne dans le navigateur) | **Oui** — gratuit, local, adapté au « aucune image envoyée » |
| Transcription streaming FR/EN | Modèles STT streaming de Kyutai (FR/EN), Whisper / faster-whisper (pas nativement streaming) | **Viable** mais demande un GPU ; API en MVP, bascule possible ensuite |
| Émotion vocale | emotion2vec, modèles SpeechBrain (wav2vec2) | **Viable pour l'expérimentation**, précision à mesurer sur vos appels (Q27) |
| Full duplex parole | Moshi (Kyutai) | Conçu pour un agent qui *parle* ; pas nécessaire ici, mais intéressant pour un futur mode « coach vocal à l'oreille » |
| LLM de conseils | Mistral, Llama, Qwen servis avec vLLM | **Viable techniquement**, mais un GPU dédié dépasse le budget de 100 €/mois ; à reconsidérer à l'échelle |

**Recommandation** : visage en open source local dès la v1 ; STT, émotion vocale et LLM derrière des interfaces remplaçables, via API pendant le MVP. Le code prévoit un fournisseur par brique pour pouvoir basculer sans réécriture.

LLM retenus par défaut : `claude-haiku-5-5` pour les conseils en direct, `claude-opus-5-5` pour le bilan post-appel.

## 7. Étapes

1. **Semaine 1** — squelette backend + extension, boucle transcription → conseils par règles, panneau latéral. *(ce commit)*
2. **Semaine 2** — STT streaming réel, conseils LLM en parallèle, capture tab + micro.
3. **Semaine 3** — émotion vocale, calibration, bilan post-appel, email de suivi.
4. **Semaine 4** — visage (MediaPipe local), tests sur vrais appels internes, mesures.
5. **Ensuite** — dashboard admin, HubSpot, base de connaissances, collecte d'entraînement opt-in.

## 8. Classement par Claude (objection et étape)

Mesure sur le pilote de 100 appels : avec les mots-clés seuls, le moteur ne détecte que 31 % des objections, car elles sont formulées librement. Il ne suit la bonne étape que dans 30 % des tours.

**À chaque tour du prospect**, le fournisseur LLM (`claude-haiku-5-5`, environ 2 s, environ 0,0003 $ par tour) renvoie, dans cet ordre :
1. l'étape où en est l'appel ;
2. le conseil ;
3. l'objection **nouvelle** du tour, parmi celles du scénario ;
4. le ressenti lu dans les mots, seulement avec l'accord du prospect.

L'ordre compte : chaque champ suit ce que le modèle vient d'écrire. Avec l'objection avant le conseil, le conseil disait « objection budget » alors que le champ restait vide.

**À chaque tour du vendeur**, une requête plus courte ne demande que l'étape. Quand elle venait après un conseil, l'étape suivait le conseil (« creuse le besoin », donc qualification) au lieu de ce que faisait le vendeur, et « Valeur » n'était presque jamais choisie.

**Mots-clés et Claude.** Les mots-clés restent le réflexe instantané : une fiche d'objection s'affiche en environ 1 s. Elle est provisoire. Environ 2 s plus tard, Claude la confirme, la corrige ou la retire (fausse alerte). Si Claude ne répond pas (panne, délai de 8 s), les mots-clés décident. Le fil directeur n'avance que vers l'avant.

**Résultat** sur les 90 appels du pilote hors sélection de démo (non utilisés pour régler les consignes) :
- objections : F1 **90 %** (précision 87 %, rappel 94 %), contre 40 % avec les mots-clés seuls ;
- bonne étape : **87 %** des tours, contre 30 %.

Mesures en direct, avec la transcription : `docs/demos.md`. Détails du pilote : `docs/donnees-synthetiques.md`.

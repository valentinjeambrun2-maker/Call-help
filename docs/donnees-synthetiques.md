# Appels synthétiques — générateur

Objectif (Q27) : 1 000 cold calls annotés, en français et en anglais, générés sans enregistrer de vraies personnes. Les dialogues sont écrits par Claude, puis les voix sont produites par ElevenLabs.

> **À lire avant de lancer la génération complète :** voir [Conditions d'utilisation](#conditions-dutilisation-elevenlabs) et [Coût](#coût).

## Chaîne de production

```
plan ──► dialogues (Claude) ──► voix (ElevenLabs, tour par tour) ──► assemblage ──► évaluation
1 000      texte + étiquettes       WAV 16 kHz par tour                 stéréo L/R        rapport
specs      par tour                 + alignement mot à mot              + étiquettes      objections,
                                                                         + rejeu           étapes
```

| Étape | Commande | Sortie par appel |
|---|---|---|
| Plan | `python -m datagen plan --n 1000` | `plan.jsonl` (langue, secteur, persona, objections, arc émotionnel, issue, lot) |
| Dialogues, pilote | `python -m datagen dialogues --limit 50` | `calls/<id>/dialogue.json` |
| Dialogues, 1 000 | `python -m datagen dialogues --batch submit` puis `--batch collect <id>` | idem, à moitié prix (Batches API) |
| Coût voix | `python -m datagen estimate` | caractères à synthétiser |
| Voix | `python -m datagen voices --limit 50` | `turns/000.wav…`, `turns.json`, `provenance.json` |
| Assemblage | `python -m datagen assemble` | `call.wav` (stéréo), `call_telephone.wav`, `labels.json`, `replay.json` |
| Évaluation | `python -m datagen evaluate` | `report.md`, `report.json` |

Les commandes se lancent depuis `backend/`, avec `ANTHROPIC_API_KEY` (dialogues) et `ELEVENLABS_API_KEY` (voix). Les données vont dans `backend/data/synthetic/`, qui est ignoré par git.

## Plan de couverture

Le tirage est déterministe (graine) et équilibré :
- **Langues :** 60 % français, 40 % anglais.
- **Lots :** 800 entraînement, 100 validation, 100 test. Les voix sont séparées par lot : aucune voix du lot test n'est entendue à l'entraînement.
- **Variété des appels :** 12 secteurs, 5 rôles côté prospect (dont « barrage » assistante), 6 personnalités, vendeurs débutants et confirmés.
- **Objections :** 0 à 3 par appel, et chacune des 4 objections apparaît environ 350 fois. Claude les formule librement, sans mots-clés imposés.
- **Arcs émotionnels :** 13 arcs du prospect (début → milieu → fin), cohérents avec l'issue de l'appel (RDV, rappel, envoi d'infos, refus, raccroché).

## Dialogues (Claude)

Chaque tour porte des étiquettes :
- locuteur et texte ;
- émotion (5 états + neutre) et intensité ;
- indications de jeu ;
- étape (dont le **message d'information** en début d'appel) ;
- objection éventuelle ;
- pause, et chevauchement en cas d'interruption.

Un dialogue est rejeté puis réécrit dans ces cas :
- étapes dans le désordre ;
- objection prévue absente ;
- mauvaise issue ;
- arc émotionnel non respecté ;
- longueur hors cible ;
- message d'information absent.

Modèle : `claude-opus-5-5`, modifiable par `CALLHELP_DIALOGUE_MODEL`. En mode direct (pilote), le repli serveur en cas de refus est activé (`fallbacks: "default"`). L'API Batches ne l'accepte pas : les échecs d'un batch se rejouent ensuite en mode direct.

## Voix (ElevenLabs)

**Choix : un appel de synthèse par tour, assemblé par nos soins**, et non l'API *Text to Dialogue*. Deux raisons :
1. *Text to Dialogue* rend **une seule piste mono mélangée**. Or le produit sépare l'utilisateur (micro) et le prospect (onglet) : il faut deux canaux.
2. Ses horodatages dérivent au-delà de 30 à 60 s sur v3 (tickets ouverts côté ElevenLabs : elevenlabs-python #707, #760). En plaçant nous-mêmes chaque tour, la ligne de temps de référence est exacte.

Réglages :
- **Modèle :** `eleven_v4` (recommandé par ElevenLabs depuis septembre 2026), modifiable par `CALLHELP_VOICE_MODEL`.
- **Format :** `pcm_16000`, le format d'entrée de la transcription et du modèle d'émotion, sans conversion.
- **Émotion :** pilotée de deux façons :
  - des balises de jeu en tête de tour (`[hesitant]`, `[annoyed]`, `[sighs]`…) ;
  - la stabilité de la voix : 0,6 pour une réplique neutre, jusqu'à 0,3 pour une émotion forte.
- **Voix :** listées dans `datagen/voices.json` (modèle : `voices.example.json`), au moins deux par langue, genre et lot. Choisir des voix **natives** : avec v4, une voix étrangère parle sans accent dans la langue cible.

Points à vérifier pendant le pilote, faute de documentation :
- les balises non documentées (`[hesitant]`, `[bored]`) et les balises anglaises dans un texte français ;
- l'effet de `previous_text` sur la continuité entre les deux voix ;
- le support de l'alignement (`with-timestamps`) par `eleven_v4`.

## Assemblage

- **Pistes :** vendeur à gauche, prospect à droite, comme dans le produit.
- **Ligne de temps :** chaque tour commence à la fin du précédent plus sa pause, ou avant cette fin en cas d'interruption.
- **Version « téléphone » :** bande 300–3 400 Hz, 8 kHz, passage par le codec G.711 µ-law, pour approcher un vrai softphone.
- **`labels.json` :** vérité terrain par tour (temps, étape, objection, émotion, intensité) et mots horodatés.
- **`replay.json` :** l'appel rejouable dans une session, comme la démo.

## Évaluation

`python -m datagen evaluate` rejoue chaque appel dans une vraie session, puis mesure :
- les objections : précision, rappel et F1 par objection ;
- l'étape suivie, à chaque tour ;
- le nombre de conseils par minute.

Premier constat, sur le dialogue de test : la détection par mots-clés reconnaît « j'ai pas le temps » mais pas « on bosse déjà avec une agence ». Les 1 000 appels mesureront ce manque à grande échelle. C'est l'argument pour passer à une détection par le modèle de langage.

L'émotion ne se mesure pas ici : elle se mesurera sur l'audio, une fois le modèle de voix branché.

## Pilote de 100 appels

Une seule commande, depuis `backend/`, avec les deux clés définies :

```bash
.venv/bin/pip install -e '.[llm,voices]'
```

```bash
.venv/bin/python -m datagen voices-pick
```

```bash
.venv/bin/python -m datagen pilot --n 100
```

- `voices-pick` choisit 6 voix natives par langue et par genre dans la bibliothèque ElevenLabs. Il écarte les voix à tarif majoré ou à préavis, varie les accents, les ajoute au compte et écrit `datagen/voices.json`. Ajoutez `--dry-run` pour voir la sélection sans rien ajouter.
- `pilot` enchaîne dialogues, voix, assemblage et évaluation. Il reprend là où il s'est arrêté, sans rien redemander ni refacturer. Il s'arrête **avant** la synthèse vocale si le volume dépasse `--max-chars`, fixé par défaut à 600 000 caractères.
- Les 100 premiers appels du plan sont équilibrés : 60 en français, 40 en anglais, environ 36 occurrences de chaque objection, tous les arcs émotionnels.

Réglages ElevenLabs à faire avant de lancer :
- désactiver « Data use » dans les paramètres du compte, pour que les scripts et la grille d'étiquettes ne servent pas à entraîner leurs modèles ;
- rester sur un forfait payant pendant toute la génération : le contenu produit hors abonnement payant n'est pas utilisable commercialement.

## Coût

Tarifs relevés sur elevenlabs.io/pricing/api le 5 novembre 2026, hors TVA :
- `eleven_v4` : 0,08 $ pour 1 000 caractères ;
- `eleven_v4_turbo` : 0,04 $, moins expressif ;
- les balises de jeu comptent comme des caractères.

| | Caractères | Voix (v4) | Dialogues (Claude) | Total estimé |
|---|---|---|---|---|
| Pilote, 100 appels (344 min) | ≈ 341 000 | ≈ 27 $ | ≈ 11 $ (Opus 5.5, appels directs) | **≈ 40 à 50 $** avec 20 à 30 % de reprises |
| 1 000 appels (3 495 min) | ≈ 3,46 M | ≈ 277 $ | ≈ 54 $ (Opus 5.5, Batches) | **≈ 350 à 420 $** |

Forfaits (API) :
- **Pilote :** Creator, 22 $/mois avec 275 000 caractères inclus, plus une recharge prépayée d'environ 66 000 caractères (≈ 5 $). Pro, à 99 $, couvre le pilote seul avec de la marge.
- **1 000 appels :** un mois de Scale (299 $, 3,74 M caractères), puis résiliation. Activer la double authentification, sinon les recharges prépayées sont plafonnées.

À vérifier : la **Startup Grant** ElevenLabs (33 M caractères et forfait Scale pendant 12 mois, pour les entreprises de moins de 25 salariés avec un modèle économique). Elle couvrirait la génération complète plusieurs fois.

## Conditions d'utilisation ElevenLabs

**Point bloquant, à régler par écrit avec ElevenLabs avant d'utiliser l'audio comme jeu de données.** Sources : Prohibited Use Policy du 9 octobre 2026, elevenlabs.io/use-policy.

- **§12 :** interdit d'utiliser les sorties « pour développer, entraîner, affiner ou améliorer tout modèle ou service d'IA, ou de les inclure dans un jeu de données utilisé à ces fins », sauf autorisation expresse.
  - Entraîner le modèle d'émotion de Call-help sur cet audio est donc **interdit sans accord écrit**.
  - Même l'usage comme **jeu d'évaluation** pour améliorer le moteur est une **zone grise**.
- **§9 :** interdit d'utiliser leurs services « pour un système classé à haut risque » par l'AI Act. La reconnaissance des émotions figure à l'annexe III (haut risque). Il faut leur faire confirmer que générer des données de test pour un tel produit n'est pas visé.

Ce que je recommande :
1. **Écrire à ElevenLabs** (juridique ou ventes ; une offre Enterprise peut accorder une exception). Demander une autorisation écrite pour :
   - (i) l'usage en jeu d'évaluation interne ;
   - (ii) l'entraînement d'un classifieur d'émotion interne non concurrent ;
   - (iii) la confirmation que le §9 ne s'applique pas.
2. **En attendant**, n'utiliser le pilote que pour l'**écoute qualité** : les voix rendent-elles bien les émotions, les étiquettes sont-elles crédibles ? Chaque appel porte un `provenance.json` qui rappelle cette restriction.
3. **Si ElevenLabs refuse l'usage en entraînement**, produire la partie entraînement avec un fournisseur dont les conditions l'autorisent :
   - Gemini TTS sur l'offre payante : ses conditions n'interdisent que les modèles concurrents ;
   - Kyutai TTS `en_fr` : poids sous licence CC-BY 4.0, en n'utilisant que des voix CC-BY ou CC0.

   Les scripts et étiquettes écrits par Claude restent utilisables : selon les conditions commerciales d'Anthropic, ils appartiennent au client et peuvent servir à entraîner un modèle non concurrent.

## Ce que ces appels peuvent prouver, et ce qu'ils ne peuvent pas prouver

D'après la littérature récente (détails et sources dans l'historique de recherche du projet).

**Ils peuvent prouver :**
- **que la chaîne fonctionne**, avec une vérité terrain exacte : attribution des canaux, suivi d'étapes, détection d'objections, délai des conseils ;
- **des comparaisons relatives** à conditions contrôlées : un fournisseur de transcription contre un autre, des règles contre un modèle de langage, audio propre contre audio téléphone.

**Ils ne peuvent pas prouver :**
- **la précision réelle sur de vrais appels** : ni le taux d'erreur de transcription, ni la façon dont de vrais prospects formulent leurs objections.
- **la qualité du modèle d'émotion sur de vrais prospects.** Un modèle entraîné uniquement sur de la voix de synthèse se transfère mal à la parole naturelle : même les meilleurs systèmes plafonnent vers 0,43 de F1 macro sur de la parole réelle. Sur de vrais appels de service client, environ 95 % des énoncés sont neutres. Les balises ElevenLabs ne produisent pas toujours l'émotion demandée : un banc d'essai mesure 2,67/5 de fidélité émotionnelle pour v3.

**Contrôle des étiquettes avant tout usage :** 300 tours du prospect, échantillonnés par émotion, écoutés à l'aveugle par 2 évaluateurs natifs par langue. On ne garde une émotion que si l'accord entre évaluateurs atteint au moins 0,67 (kappa) **et** si l'émotion perçue correspond à l'émotion demandée dans au moins 70 % des cas. Compter environ 2 à 3 h par évaluateur.

## Améliorations prévues

- **Prospect :** codec Opus et pertes de paquets, bruit de fond.
- **Vendeur :** bruit de bureau et écho du haut-parleur.
- **Contrôle automatique :** transcrire chaque tour et régénérer ceux qui s'écartent du script (mots sautés ou ajoutés).
- **Lot de contraste :** la même phrase dite en neutre et dans 2 émotions, pour séparer le ton des mots.
- **Plus de voix de test :** au moins 5 par langue et par genre, et des métriques par voix.

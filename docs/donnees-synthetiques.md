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

## Conditions d'utilisation ElevenLabs

*(complété après vérification, voir plus bas)*

## Coût

*(complété après vérification, voir plus bas)*

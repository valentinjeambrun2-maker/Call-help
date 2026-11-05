# Call-help — Décisions produit (v1)

Synthèse des réponses aux 50 questions (`docs/questions-projet.md`). Ce fichier fait foi : en cas de doute, on revient ici.

## Produit

| Sujet | Décision |
|---|---|
| Utilisateurs | Commerciaux (cold call), freelances, dirigeants, formateurs, présentateurs |
| Organisation | Comptes individuels **et** structures entreprise : un admin gère les membres, leurs accès et ce que chacun peut voir (dashboard admin dédié) |
| Priorités (Q3) | 1. objections · 2. fil directeur · 3. lecture de l'interlocuteur · 4. débit/longueur · 5. accompagnement · 6. suivi après l'appel |
| Démo « waouh » | Un call complet de 5 min où l'agent conseille en temps réel et appuie ses conseils sur la lecture émotionnelle du prospect |
| Mesure du succès | Taux de RDV obtenus + note de l'utilisateur après chaque appel |
| Langues | Français et anglais |

## Scénario cold call

- 3 à 5 minutes, 4 étapes (accroche, qualification, objection, prise de RDV).
- Scénario = modèle éditable + génération par IA ; étapes avec **branches pour les objections**.
- Objections de départ : « pas le temps », « déjà un prestataire », « pas de budget », « envoyez un mail » (à compléter).
- Connaissance produit : **tout** (pitch, tarifs, cas clients, FAQ, docs importés) → base de connaissances interrogée par l'IA.
- Conseil = **indication courte + phrase prête à dire**.
- Fiche prospect : saisie manuelle en MVP, puis CRM (HubSpot en premier), LinkedIn, site, notes.
- Après l'appel : résumé, email de suivi, mise à jour CRM, points d'amélioration.

## Capture et plateforme

- Cold calls via **softphone web** ; calls diagnostic et meetings via **visio web** (Meet, Teams, Zoom web) ; téléphone en **voix seule**.
- Affichage : panneau latéral Chrome **ou** fenêtre flottante, visible uniquement par l'utilisateur.
- Latence visée : **1 à 3 s** entre une phrase et le conseil.
- Visage traité **en local dans le navigateur** ; transcription et conseils dans le cloud.

## Émotions (voix + visage)

- États : intérêt, hésitation, agacement, ennui, enthousiasme.
- Analysés : **le prospect et l'utilisateur**. L'analyse du ton de l'utilisateur est **active par défaut** (Q36), désactivable par l'utilisateur (case à cocher) et, plus tard, par l'admin de la structure.
- Seuil d'affichage 70 %, libellé « probable » en dessous ; calibration sur les 30 premières secondes.
- Voix : service externe remplaçable au départ, cible **modèle full duplex** (écoute et génération en parallèle).
- Visage : sourire, froncement, hochements, regard ; « visage non analysable » quand la caméra est coupée.
- Objectif de données : **1 000 appels annotés** à la fin du MVP, **générés de façon synthétique avec ElevenLabs** (dialogues écrits par Claude, étiquetés par tour), avec entraînement continu.

## Données, équipe et conformité

- Stockage : ni image ni audio ; transcription et scores 30 jours, effaçables.
- Le manager/admin voit des **bilans simplifiés avec métriques, jamais les émotions**.
- Information des participants **au moment de l'invitation** pour les meetings ; pour les **cold calls**, **message d'information dit en début d'appel** (affiché dans le panneau). L'analyse émotionnelle ne démarre qu'après confirmation « message dit », et l'heure est tracée dans le bilan. L'utilisateur coupe l'analyse en un clic.
- Cadre : France/UE (RGPD + AI Act) ; validation juriste/DPO avant tout appel réel.

## IA

- Conseils en direct : modèle rapide ; bilan : modèle plus fort. **Évaluer les options open source** (voir `docs/architecture.md` §6).
- Jusqu'à **5-6 conseils par fenêtre de 20 s**, plus les phrases de réponse proposées ; silence si confiance faible.
- Évaluation : boutons utile/pas utile + rejeu d'appels.

## Business et exécution

- Abonnement mensuel par utilisateur, avec quota de consommation (tokens/minutes).
- Différenciation : guidage en direct + lecture du ton, **pour tout type de call**.
- MVP en 4 semaines : voix d'abord, visage ensuite. Budget test < 100 €/mois.

---

## Points de vigilance (à trancher avant un appel réel)

1. **Q36 : analyse du ton de l'utilisateur active par défaut (décidé).** Reste le point le plus exposé face à l'AI Act pour des utilisateurs salariés : à faire valider par le juriste avant un déploiement en entreprise. Elle n'est jamais remontée au manager (Q38).
2. **Q32 « bypass » des biais.** Les expressions faciales se lisent mal selon l'éclairage, la peau, un handicap ou la culture. Sans mesure contre les biais, l'outil risque de donner de mauvais conseils sur certaines personnes. Minimum conservé dans le code : seuil de confiance, libellé « probable », et pas d'affichage sans calibration.
3. **Q34 : réglé pour les cold calls** par un message d'information en début d'appel. À valider par le juriste : la formulation du message, et ce qui se passe si le prospect refuse (bouton « Couper l'analyse »).
4. **Q27 : 1 000 appels synthétiques (ElevenLabs).** Règle le conflit avec « aucun audio stocké » (aucune vraie personne enregistrée). Limite : des émotions jouées par une voix de synthèse ne valent pas des émotions réelles. Il faudra un petit lot d'appels réels, avec accord, pour valider le modèle d'émotion avant de lui faire confiance.
5. **Q43 : 5-6 conseils / 20 s.** C'est beaucoup à lire en parlant. Le paramètre est configurable ; je propose de le mesurer en test et d'ajuster.

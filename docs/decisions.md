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
- Analysés : **le prospect et l'utilisateur**.
- Seuil d'affichage 70 %, libellé « probable » en dessous ; calibration sur les 30 premières secondes.
- Voix : service externe remplaçable au départ, cible **modèle full duplex** (écoute et génération en parallèle).
- Visage : sourire, froncement, hochements, regard ; « visage non analysable » quand la caméra est coupée.
- Objectif de données : **1 000 appels annotés** à la fin du MVP, avec entraînement continu.

## Données, équipe et conformité

- Stockage : ni image ni audio ; transcription et scores 30 jours, effaçables.
- Le manager/admin voit des **bilans simplifiés avec métriques, jamais les émotions**.
- Information des participants **au moment de l'invitation** ; l'utilisateur coupe l'analyse en un clic.
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

1. **Q36 sans réponse : statut des utilisateurs.** Pour les salariés d'une structure, l'AI Act interdit la reconnaissance des émotions sur le lieu de travail. Analyser **l'émotion de l'utilisateur salarié** (Q22 « les deux ») est la partie la plus exposée. Choix technique retenu : l'auto-analyse de l'utilisateur est une **option désactivable par l'admin**, désactivée par défaut pour les comptes entreprise, et jamais remontée au manager (cohérent avec Q38).
2. **Q32 « bypass » des biais.** Les expressions faciales se lisent mal selon l'éclairage, la peau, un handicap ou la culture. Sans mesure contre les biais, l'outil risque de donner de mauvais conseils sur certaines personnes. Minimum conservé dans le code : seuil de confiance, libellé « probable », et pas d'affichage sans calibration.
3. **Q34 : information à l'invitation seulement.** Pour un cold call, il n'y a pas d'invitation. Il faudra une autre forme d'information (annonce en début d'appel) pour ce cas.
4. **Q27 : 1 000 appels annotés et entraînement auto.** Utiliser les appels pour entraîner un modèle demande une base légale distincte et l'accord des personnes enregistrées. Cela contredit aussi « aucun audio stocké » (Q37) : il faut un mode « collecte d'entraînement » séparé, opt-in.
5. **Q43 : 5-6 conseils / 20 s.** C'est beaucoup à lire en parlant. Le paramètre est configurable ; je propose de le mesurer en test et d'ajuster.

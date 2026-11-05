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
- Analyse du ton de l'utilisateur (Q36) : **sur accord explicite, mémorisé**. L'utilisateur l'active une fois, et le choix est retenu pour les appels suivants. Elle reste visible par lui seul, jamais par un manager ou un admin. Voir le point de vigilance 1 pour la raison de ce changement.
- Seuil d'affichage 70 %, libellé « probable » en dessous ; calibration sur les 30 premières secondes.
- Voix : service externe remplaçable au départ, cible **modèle full duplex** (écoute et génération en parallèle).
- Visage : sourire, froncement, hochements, regard ; « visage non analysable » quand la caméra est coupée.
- Objectif de données : **1 000 appels annotés** à la fin du MVP, **générés de façon synthétique avec ElevenLabs** (dialogues écrits par Claude, étiquetés par tour), avec entraînement continu.

## Données, équipe et conformité

- Stockage : ni image ni audio ; transcription et scores 30 jours, effaçables.
- Le manager/admin voit des **bilans simplifiés avec métriques, jamais les émotions**.
- Information et **accord** des participants :
  - meetings : au moment de l'invitation ;
  - **cold calls** : message dit en début d'appel, qui **demande l'accord** du prospect. L'utilisateur note la réponse (« Il est d'accord » ou « Il refuse »).
- **Sans accord, aucune analyse émotionnelle du prospect pendant tout l'appel.** Claude reçoit alors la consigne de ne pas déduire son état émotionnel, ni de sa voix ni de ses mots. L'analyse du ton de l'utilisateur, qu'il a lui-même choisie, continue.
- Le bilan trace l'heure du message, la réponse du prospect, et si une analyse a réellement eu lieu. L'utilisateur peut tout couper en un clic.
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

1. **Q36 : passé de « actif par défaut » à « sur accord, mémorisé ».** Deux textes l'imposent :
   - **Politique d'usage d'Anthropic**, effective le 12 novembre 2026 et intégrée aux conditions commerciales, qui encadre Claude, utilisé par le produit. Elle interdit de construire ou de soutenir un système qui déduit les émotions d'une personne, sauf raison médicale ou de sécurité, **à moins que la personne ait donné son accord ou activé l'option** (« opted in »). Un interrupteur activé par défaut ne vaut pas activation par l'utilisateur.
   - **Lignes directrices de la Commission européenne sur l'AI Act** : analyser les émotions d'un salarié est interdit, sauf usage de formation personnelle dont les résultats ne sont jamais partagés avec l'employeur et n'ont aucun effet sur l'évaluation.

   L'activation reste à un clic et elle est mémorisée.
2. **Q32 « bypass » des biais.** Les expressions faciales se lisent mal selon l'éclairage, la peau, un handicap ou la culture. Sans mesure contre les biais, l'outil risque de donner de mauvais conseils sur certaines personnes. Minimum conservé dans le code : seuil de confiance, libellé « probable », et pas d'affichage sans calibration.
3. **Q34 : message d'information devenu une demande d'accord.** Même raison que le point 1 : la politique d'Anthropic exige que la personne analysée ait donné son accord. Une simple information ne suffit pas. À valider par le juriste : la formulation du message et la base légale RGPD. Une information ou un accord oral ne règle pas à lui seul la question du RGPD.
4. **Q27 : 1 000 appels synthétiques (ElevenLabs).** Règle le conflit avec « aucun audio stocké » (aucune vraie personne enregistrée). Limite : des émotions jouées par une voix de synthèse ne valent pas des émotions réelles. Il faudra un petit lot d'appels réels, avec accord, pour valider le modèle d'émotion avant de lui faire confiance.
5. **Q43 : 5-6 conseils / 20 s.** C'est beaucoup à lire en parlant. Le paramètre est configurable ; je propose de le mesurer en test et d'ajuster.

6. **Données d'entraînement.** Entraîner un modèle sur des sorties de Claude (scripts, étiquettes) demande une **autorisation préalable d'Anthropic**. Entraîner un modèle sur l'audio d'ElevenLabs demande la leur. Voir `docs/donnees-synthetiques.md`.
7. **Lecture du ton à partir du texte.** Selon les lignes directrices de la Commission, déduire une émotion d'un texte écrit ne relève pas de la « reconnaissance des émotions » au sens de l'AI Act (pas de donnée biométrique), contrairement à la voix et au visage. C'est aussi le signal qui se transfère le mieux des données synthétiques aux vrais appels. La politique d'Anthropic, elle, couvre toute déduction d'émotion : l'accord du prospect reste nécessaire.

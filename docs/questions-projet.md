# Call-help — 50 questions pour diriger le projet

Décisions déjà prises : **web app + extension Chrome**, **cold call en premier**, **voix + expressions du visage**, **Python (FastAPI) + React/TypeScript**.

Chaque question propose une valeur par défaut (« Par défaut ») que je retiendrai si vous ne tranchez pas. Répondez dans ce fichier ou en message, dans l'ordre que vous voulez.

## À trancher en premier (bloquantes)

Q1, Q7, Q15, Q22, Q29, Q34, Q36, Q37. Sans elles, je ne peux pas figer l'architecture ni le périmètre du MVP.

---

## A. Vision et utilisateurs

**Q1. Qui est l'utilisateur principal ?** Commercial en entreprise (SDR/AE), freelance, dirigeant, formateur, présentateur ? *Par défaut : commercial qui fait du cold call.*

**Q2. Usage personnel ou déployé à une équipe ?** Un manager peut-il voir les données d'un commercial ? *Par défaut : usage personnel, aucune donnée visible par un tiers.*

**Q3. Quel problème résolvez-vous en priorité ?** Classez : (a) perdre le fil, (b) mal lire l'interlocuteur, (c) mal gérer les objections, (d) parler trop vite ou trop longtemps, (e) manque de suivi après l'appel. *Par défaut : a, c, d.*

**Q4. Quel est le moment « waouh » d'une démo de deux minutes ?** Une alerte en direct, un conseil sur une objection, un bilan d'appel ? *Par défaut : conseil sur objection en direct.*

**Q5. Comment mesurer le succès du MVP ?** Taux de rendez-vous obtenus, durée d'appel, satisfaction de l'utilisateur, conversion ? *Par défaut : taux de RDV + note de l'utilisateur après chaque appel.*

**Q6. Quelles langues ?** Français seul, français + anglais, mélange dans un même appel ? *Par défaut : français, anglais en option.*

## B. Scénario du cold call

**Q7. À quoi ressemble votre cold call type ?** Durée, étapes (accroche, qualification, objection, prise de rendez-vous), taux de décroché. *Par défaut : 3 à 5 minutes, 4 étapes.*

**Q8. Qui écrit le scénario ?** L'utilisateur, un modèle prêt à l'emploi, ou généré par l'IA à partir du site et de l'offre ? *Par défaut : modèle éditable + génération IA.*

**Q9. Le scénario est-il linéaire ou à branches ?** Les étapes changent-elles selon les réponses du prospect ? *Par défaut : étapes avec branches pour les objections.*

**Q10. Quelles sont les cinq objections les plus fréquentes ?** (« pas le temps », « déjà un prestataire », « pas de budget », « envoyez un mail »…) *Par défaut : ces quatre-là, à compléter par vous.*

**Q11. Quelle connaissance produit doit connaître l'outil ?** Pitch, tarifs, cas clients, FAQ. Import de documents ? *Par défaut : import de PDF/texte, utilisé par l'IA comme source.*

**Q12. Les conseils sont-ils des phrases prêtes à dire ou des indications ?** (« Dis : … » ou « Demande son budget »). *Par défaut : indication courte + phrase optionnelle.*

**Q13. Quelles informations sur le prospect avant l'appel ?** CRM, LinkedIn, site web, notes. *Par défaut : saisie manuelle d'une fiche en MVP.*

**Q14. Que doit produire l'outil après l'appel ?** Résumé, email de suivi, mise à jour du CRM, points d'amélioration. *Par défaut : résumé + email de suivi à relire.*

## C. Capture et plateforme

**Q15. Où se passent vos cold calls ?** Téléphone via softphone (Aircall, Ringover…), ou visio ? L'extension Chrome ne couvre que ce qui s'ouvre dans le navigateur. *Par défaut : softphone web + visio web.*

**Q16. Quelle visio en priorité ?** Google Meet, Teams, Zoom web. *Par défaut : Meet puis Teams.*

**Q17. Où s'affichent les conseils ?** Panneau latéral Chrome, fenêtre flottante, second écran. *Par défaut : panneau latéral Chrome.*

**Q18. Le panneau doit-il être visible en partage d'écran ?** Attention : le cacher aux participants pose un problème de transparence. *Par défaut : visible uniquement par l'utilisateur, jamais dissimulé activement.*

**Q19. Quelle latence est acceptable entre une phrase du prospect et le conseil ?** 1 s, 3 s, 5 s ? *Par défaut : moins de 3 s pour une alerte, moins de 5 s pour un conseil.*

**Q20. Traitement local ou cloud ?** Pour la confidentialité, certaines analyses (visage) peuvent rester dans le navigateur. *Par défaut : visage en local, transcription et conseils dans le cloud.*

## D. Voix et émotions

**Q21. Quelle liste fermée d'états à détecter ?** Intérêt, hésitation, agacement, ennui, confusion, enthousiasme, stress. Moins il y en a, plus c'est fiable. *Par défaut : 5 états (intérêt, hésitation, agacement, ennui, enthousiasme).*

**Q22. Qui est analysé ?** Le prospect, l'utilisateur lui-même, ou les deux ? *Par défaut : le prospect pour réagir, l'utilisateur pour s'auto-corriger (débit, ton).*

**Q23. Comment afficher une émotion sans distraire pendant l'appel ?** Pastille de couleur, jauge, tendance, texte court. *Par défaut : une pastille + une phrase courte seulement en cas de changement.*

**Q24. Quel seuil de confiance avant d'afficher un indice ?** *Par défaut : 70 %, avec libellé « probable » en dessous.*

**Q25. Faut-il calibrer chaque personne ?** Mesurer une base de départ (30 premières secondes) pour tenir compte des voix et accents. *Par défaut : oui.*

**Q26. Quel modèle de voix ?** Service externe (type Hume), modèle open source, entraînement propre. *Par défaut : service externe pour le MVP, remplaçable.*

**Q27. Comment valider la précision ?** Jeu d'appels annotés, langues et accents visés, taux d'erreur acceptable. *Par défaut : 30 appels annotés par vous avant d'afficher quoi que ce soit en production.*

## E. Expressions du visage

**Q28. Visage de qui ?** Le prospect en visio, l'utilisateur, les deux ? Rien pour un appel téléphonique. *Par défaut : le prospect, quand la caméra est allumée.*

**Q29. Les images quittent-elles le navigateur ?** Traitement local des images (aucune image envoyée au serveur, seulement des scores) ou traitement serveur. *Par défaut : traitement local uniquement.*

**Q30. Quelles expressions exactement ?** Sourire, froncement, regard détourné, hochements de tête, posture. Les « micro-expressions » sont peu fiables scientifiquement. *Par défaut : sourire, froncement, hochements, regard.*

**Q31. Que faire quand le visage est invisible ?** Caméra coupée, plusieurs participants, mauvais éclairage. *Par défaut : l'outil indique « visage non analysable » et se rabat sur la voix.*

**Q32. Comment limiter les biais ?** Couleur de peau, éclairage, handicap, neurodiversité, différences culturelles. *Par défaut : avertissement visible + indices présentés comme des hypothèses, jamais comme des faits.*

## F. Juridique et éthique

**Q33. Dans quels pays sont les utilisateurs et leurs interlocuteurs ?** France/UE ? Autres ? *Par défaut : France et UE, avec le RGPD et l'AI Act comme cadre.*

**Q34. Comment informez-vous les participants, et obtenez-vous leur accord ?** Annonce orale, bannière, mention dans l'invitation. L'analyse de la voix et du visage demande en principe une information claire, et souvent un consentement. *Par défaut : annonce affichée à l'utilisateur à rappeler en début d'appel + opt-out immédiat.*

**Q35. Les participants peuvent-ils refuser ou arrêter l'analyse à tout moment ?** *Par défaut : oui, l'utilisateur coupe l'analyse en un clic et elle s'arrête pour la suite de l'appel.*

**Q36. L'outil sera-t-il utilisé par des salariés ?** L'AI Act interdit la reconnaissance des émotions des personnes sur le lieu de travail (hors raisons médicales ou de sécurité). Quel est le statut de l'utilisateur (salarié, indépendant) et celui du prospect ? Un juriste doit trancher. *Par défaut : cadrer l'outil comme aide personnelle, pas comme outil d'évaluation de salariés.*

**Q37. Que stockez-vous, et combien de temps ?** Audio, transcription, scores, images. *Par défaut : aucune image, aucun audio ; transcription et scores 30 jours, effaçables.*

**Q38. Un manager peut-il consulter les données d'un commercial ?** Cela change fortement l'analyse juridique. *Par défaut : non.*

**Q39. Un juriste ou DPO validera-t-il l'outil avant son utilisation réelle ?** Une analyse d'impact (AIPD) sera probablement nécessaire. *Par défaut : oui, avant tout appel avec un vrai prospect.*

## G. IA et données

**Q40. Quel modèle de langage pour les conseils en direct ?** Un modèle rapide (type Haiku) pour la latence, un plus fort pour le bilan. Budget par appel ? *Par défaut : modèle rapide en direct, modèle plus fort après l'appel.*

**Q41. Vos données peuvent-elles passer par des API tierces ?** Transcription, LLM, détection d'émotion. Acceptable pour vos clients ? Contrats de sous-traitance nécessaires ? *Par défaut : oui, avec fournisseurs conformes RGPD.*

**Q42. Comment évaluer la qualité des conseils ?** Rejouer des appels enregistrés, boutons « utile / pas utile », comparaison avec un coach humain. *Par défaut : boutons pouce + rejeu d'appels.*

**Q43. Quelle fréquence maximale de conseils ?** Et que faire quand l'IA n'est pas sûre : se taire ou proposer ? *Par défaut : au plus 1 conseil toutes les 20 s, silence si confiance faible.*

## H. Produit et business

**Q44. Quel modèle économique ?** Abonnement par utilisateur, par minute d'appel, offre équipe. *Par défaut : abonnement mensuel par utilisateur.*

**Q45. Quels concurrents connaissez-vous ?** Gong, Cogito, Balto, Observe.AI, Hume, outils de coaching d'appel. En quoi êtes-vous différent ? *Par défaut : différenciation = guidage en direct + lecture du ton, pour du cold call.*

**Q46. Quelles intégrations CRM en premier ?** HubSpot, Salesforce, Pipedrive. *Par défaut : HubSpot.*

**Q47. Qui sont vos trois à cinq premiers testeurs ?** Comment recueillez-vous leurs retours ? *Par défaut : vous + quelques commerciaux de votre entourage.*

## I. Exécution

**Q48. Quel délai pour un MVP utilisable sur un vrai appel ?** *Par défaut : 4 semaines, d'abord avec voix seule, puis visage.*

**Q49. Qui développe, et quel est votre niveau technique ?** Vous et moi seulement, ou une équipe ? Cela change le choix des outils et la documentation. *Par défaut : vous et moi, avec instructions pas à pas.*

**Q50. Quel budget mensuel d'infrastructure et d'API ?** Hébergement, transcription, LLM, détection d'émotion. *Par défaut : moins de 100 €/mois en phase test.*

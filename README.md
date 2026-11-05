# Call-help

Copilote d'appel en temps réel pour cold calls, présentations et meetings en visio.
Il suit le fil directeur de l'appel et souffle des conseils au bon moment : réponse aux objections, prochaine étape, rythme. Il s'appuie aussi sur une lecture du ton et des expressions de l'interlocuteur.

<p>
  <img src="docs/demo-panel.png" alt="Panneau latéral pendant la démo, mode clair" width="300">
  <img src="docs/demo-panel-dark.png" alt="Panneau latéral pendant la démo, mode sombre" width="300">
</p>

## Documents

- [`docs/decisions.md`](docs/decisions.md) — décisions produit et points de vigilance
- [`docs/architecture.md`](docs/architecture.md) — architecture, protocole, options open source, planning
- [`docs/questions-projet.md`](docs/questions-projet.md) — les 50 questions de cadrage
- [`docs/donnees-synthetiques.md`](docs/donnees-synthetiques.md) — générateur des 1 000 appels synthétiques (Claude + ElevenLabs)

## Structure

```
backend/app      API FastAPI + WebSocket : scénario, métriques, émotions, moteur de conseils
backend/datagen  Générateur d'appels synthétiques annotés et évaluation du moteur
extension/       Extension Chrome (React) : panneau latéral, capture de l'onglet d'appel
```

## Lancer en local

Récupérer le projet (une seule fois) :

```bash
git clone https://github.com/valentinjeambrun2-maker/Call-help.git
```

```bash
cd Call-help && git checkout claude/trusting-noether-ou47yu
```

Backend (Python 3.11+), depuis le dossier `Call-help` :

```bash
cd backend && python3 -m venv .venv && .venv/bin/pip install -e '.[dev,llm]'
```

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Sans clé API, seuls les conseils par règles sont actifs. Pour les conseils affinés par l'IA, définissez `ANTHROPIC_API_KEY` avant de lancer le backend.

Panneau en mode navigateur (démo, sans extension), dans un second terminal, depuis le dossier `Call-help`. Il faut **Node.js 20 ou plus récent** (`node -v` pour vérifier).

```bash
cd extension
npm install
npm run dev
```

Ouvrez ensuite http://localhost:5173/sidepanel.html et cliquez sur **Voir la démo** : un appel de 53 s est rejoué en temps réel.

Extension Chrome :

```bash
cd extension
npm run build
```

Puis `chrome://extensions` → mode développeur → **Charger l'extension non empaquetée** → dossier `extension/dist`.

## Tests

```bash
cd backend
.venv/bin/pytest
```

Rejouer un appel annoté en ligne de commande :

```bash
cd backend
.venv/bin/python -m demo.replay
```

Générer et évaluer des appels synthétiques : voir [`docs/donnees-synthetiques.md`](docs/donnees-synthetiques.md).

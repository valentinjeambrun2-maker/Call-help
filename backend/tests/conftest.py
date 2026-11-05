"""Les tests n'appellent jamais les vraies API (coût, lenteur), même si backend/.env
contient des clés : les fournisseurs sont simulés dans les tests qui en ont besoin."""

import os

os.environ["CALLHELP_LLM"] = "off"
os.environ["CALLHELP_KEY_CHECK"] = "off"  # pas de vérification des clés au démarrage de l'app
# Jamais les vraies clés de backend/.env : le backend relit ce fichier à chaque appel.
os.environ["CALLHELP_ENV_FILE"] = os.path.join(os.path.dirname(__file__), "no-such.env")

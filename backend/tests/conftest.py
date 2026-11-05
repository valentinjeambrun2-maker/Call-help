"""Les tests n'appellent jamais les vraies API (coût, lenteur), même si backend/.env
contient des clés : les fournisseurs sont simulés dans les tests qui en ont besoin."""

import os

os.environ["CALLHELP_LLM"] = "off"

// Vérifie la version de Node avant `npm run dev` / `npm run build`.
// Vite 6 fonctionne à partir de Node 20 ; en dessous, l'erreur affichée est trompeuse.
const [major, minor] = process.versions.node.split(".").map(Number);
void minor;
if (major < 20) {
  console.error(`
  Node ${process.versions.node} est trop ancien pour Call-help : il faut Node 20 ou plus récent.

  1. Installez Node LTS depuis https://nodejs.org (bouton « LTS », fichier .pkg sur Mac).
  2. Fermez puis rouvrez le Terminal, et vérifiez :  node -v
  3. Dans le dossier extension :  rm -rf node_modules && npm install
  4. Relancez :  npm run dev
`);
  process.exit(1);
}

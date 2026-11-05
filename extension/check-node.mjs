// Vérifie la version de Node avant `npm run dev` / `npm run build`.
// Vite 8 a besoin de Node 22.12+ : en dessous, npm saute son module natif et
// l'erreur affichée (« Cannot find native binding ») est trompeuse.
const [major, minor] = process.versions.node.split(".").map(Number);
if (major < 22 || (major === 22 && minor < 12)) {
  console.error(`
  Node ${process.versions.node} est trop ancien pour Call-help : il faut Node 22.12 ou plus récent.

  1. Installez Node LTS depuis https://nodejs.org (bouton « LTS », fichier .pkg sur Mac).
  2. Fermez puis rouvrez le Terminal, et vérifiez :  node -v
  3. Dans le dossier extension :  rm -rf node_modules && npm install
  4. Relancez :  npm run dev
`);
  process.exit(1);
}

import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Points d'entrée : panneau latéral, service worker, document offscreen (captation),
// page d'autorisation du micro et page de démonstration.
export default defineConfig({
  plugins: [react()],
  // En développement, ouvre directement le panneau (la racine redirige aussi vers lui).
  server: { open: "/sidepanel.html" },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        sidepanel: fileURLToPath(new URL("./sidepanel.html", import.meta.url)),
        offscreen: fileURLToPath(new URL("./offscreen.html", import.meta.url)),
        demo: fileURLToPath(new URL("./demo.html", import.meta.url)),
        permission: fileURLToPath(new URL("./permission.html", import.meta.url)),
        background: fileURLToPath(new URL("./src/background/index.ts", import.meta.url)),
      },
      output: {
        entryFileNames: (chunk) => (chunk.name === "background" ? "background.js" : "assets/[name]-[hash].js"),
      },
    },
  },
});

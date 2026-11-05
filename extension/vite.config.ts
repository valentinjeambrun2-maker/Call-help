import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Trois points d'entrée : panneau latéral, service worker et document offscreen.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        sidepanel: fileURLToPath(new URL("./sidepanel.html", import.meta.url)),
        offscreen: fileURLToPath(new URL("./offscreen.html", import.meta.url)),
        background: fileURLToPath(new URL("./src/background/index.ts", import.meta.url)),
      },
      output: {
        entryFileNames: (chunk) => (chunk.name === "background" ? "background.js" : "assets/[name]-[hash].js"),
      },
    },
  },
});

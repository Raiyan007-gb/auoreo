import { readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

/**
 * FastAPI serves web/dist with `StaticFiles(html=True)`, which has no SPA fallback but does
 * serve `404.html` for unknown paths. Emit a copy of index.html pinned to `<base href="/">`
 * so deep links like /session/<id> boot the app with assets resolved from the root.
 */
function spaFallback(): Plugin {
  let outDir = "";
  return {
    name: "auoreo-spa-fallback",
    apply: "build",
    configResolved(c) {
      outDir = resolve(c.root, c.build.outDir);
    },
    writeBundle() {
      const html = readFileSync(join(outDir, "index.html"), "utf8");
      writeFileSync(join(outDir, "404.html"), html.replace("<head>", '<head>\n    <base href="/" />'));
    },
  };
}

const backend = process.env.AUOREO_URL ?? "http://127.0.0.1:4096";

// Vite 8 (Rolldown). Output lands in web/dist, which FastAPI mounts at "/" after its routers.
export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  base: "./",
  plugins: [react(), tailwindcss(), spaFallback()],
  resolve: {
    alias: {
      "@sdk": fileURLToPath(new URL("../sdk", import.meta.url)),
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    proxy: {
      "/api": { target: backend, changeOrigin: true },
      "/ws": { target: backend.replace(/^http/, "ws"), ws: true },
    },
  },
  build: {
    outDir: "../dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 600,
  },
});

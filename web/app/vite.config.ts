import { createHash } from "node:crypto";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const backend = process.env.AUOREO_URL ?? "http://127.0.0.1:4096";

/** PWA app-shell manifest: after the build, list every file in outDir (hashed assets, icons,
 * index.html) into `sw-assets.json` and stamp a content hash into `sw.js` so each build is a
 * new service-worker version. sw.js itself, the list and source maps are never precached. */
function swAssets(): Plugin {
  let outDir = "";
  const walk = (dir: string): string[] =>
    readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
      e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)],
    );
  return {
    name: "auoreo-sw-assets",
    apply: "build",
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
    },
    closeBundle() {
      const skip = new Set(["/sw.js", "/sw-assets.json"]);
      const files = walk(outDir)
        .map((f) => "/" + relative(outDir, f).split(sep).join("/"))
        .filter((f) => !skip.has(f) && !f.endsWith(".map"))
        .sort();
      const hash = createHash("sha256");
      for (const f of files) hash.update(f).update(readFileSync(join(outDir, f)));
      const build = hash.digest("hex").slice(0, 12);
      writeFileSync(join(outDir, "sw-assets.json"), JSON.stringify({ build, assets: files }, null, 2) + "\n");
      const sw = join(outDir, "sw.js");
      writeFileSync(sw, readFileSync(sw, "utf8").replace("__AUOREO_BUILD__", build));
    },
  };
}

// Vite 8 (Rolldown). Output lands in web/dist, which FastAPI mounts at "/" after its routers
// (`SpaStaticFiles`: extension-less unknown paths get index.html). Assets use absolute
// `/assets/…` URLs so deep links such as /session/<id> resolve them from the root.
export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  base: "/",
  plugins: [react(), tailwindcss(), swAssets()],
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

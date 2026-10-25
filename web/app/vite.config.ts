import { fileURLToPath, URL } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const backend = process.env.AUOREO_URL ?? "http://127.0.0.1:4096";

// Vite 8 (Rolldown). Output lands in web/dist, which FastAPI mounts at "/" after its routers
// (`SpaStaticFiles`: extension-less unknown paths get index.html). Assets use absolute
// `/assets/…` URLs so deep links such as /session/<id> resolve them from the root.
export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  base: "/",
  plugins: [react(), tailwindcss()],
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

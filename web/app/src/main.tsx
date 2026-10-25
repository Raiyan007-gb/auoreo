import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "@/App";
import { OfflineBanner } from "@/components/OfflineBanner";
import { initTheme } from "@/lib/theme";
import "@/index.css";

initTheme();

const root = document.getElementById("root");
if (!root) throw new Error("#root missing");
createRoot(root).render(
  <StrictMode>
    <OfflineBanner />
    <App />
  </StrictMode>,
);

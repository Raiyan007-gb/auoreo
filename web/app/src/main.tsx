import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "@/App";
import { OfflineBanner } from "@/components/OfflineBanner";
import { WorkspaceTrustBanner } from "@/components/WorkspaceTrustBanner";
import { applyDensity } from "@/lib/density";
import { initTheme } from "@/lib/theme";
import "@/index.css";

initTheme();
applyDensity();

const root = document.getElementById("root");
if (!root) throw new Error("#root missing");
createRoot(root).render(
  <StrictMode>
    <OfflineBanner />
    <WorkspaceTrustBanner />
    <App />
  </StrictMode>,
);

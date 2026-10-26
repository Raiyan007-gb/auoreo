import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { AppShell, EmptyState } from "@/components/AppShell";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ControlProvider } from "@/lib/control";
import { ApprovalsPage } from "@/pages/Approvals";
import { GoalsPage } from "@/pages/Goals";
import { HarnessPage } from "@/pages/Harness";
import { MemoryPage } from "@/pages/Memory";
import { SessionRoute } from "@/pages/Session";
import { SessionsPage } from "@/pages/Sessions";
import { SettingsPage } from "@/pages/Settings";
import { UsagePage } from "@/pages/Usage";
// --- W0-A
import { JobsPage } from "@/pages/Jobs";
import { PluginsPage } from "@/pages/Plugins";
import { SkillsPage } from "@/pages/Skills";
import { TeamsPage } from "@/pages/Teams";
// --- end W0-A
// --- W0-B
import { AuditPage } from "@/pages/Audit";
import { DoctorPage } from "@/pages/Doctor";
import { WelcomePage } from "@/pages/Welcome";
import { MeetingsPage } from "@/pages/Meetings"; // --- F4
// --- G5b
import { EventsProvider } from "@/lib/eventsContext";
import { lazy, Suspense } from "react";
const WebhooksPage = lazy(() => import("@/pages/Webhooks").then((m) => ({ default: m.WebhooksPage })));
const PlatformsPage = lazy(() => import("@/pages/Platforms").then((m) => ({ default: m.PlatformsPage })));

const SharePage = lazy(() => import("@/pages/Share").then((m) => ({ default: m.SharePage }))); // --- I5

export function App() {
  // --- I5: the public read-only share view opens no WS/SSE connection and has no app shell.
  if (window.location.pathname.startsWith("/share/")) {
    return (
      <BrowserRouter>
        <Routes>
          <Route path="share/:token" element={<Suspense><SharePage /></Suspense>} />
        </Routes>
      </BrowserRouter>
    );
  }
  return (
    <TooltipProvider>
      <ControlProvider>
        <EventsProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<Navigate to="/sessions" replace />} />
              <Route path="sessions" element={<SessionsPage />} />
              <Route path="session/:id" element={<SessionRoute />} />
              <Route path="approvals" element={<ApprovalsPage />} />
              <Route path="memory" element={<MemoryPage />} />
              <Route path="harness" element={<HarnessPage />} />
              <Route path="usage" element={<UsagePage />} />
              <Route path="settings" element={<SettingsPage />} />
              {/* --- W0-B */}
              <Route path="audit" element={<AuditPage />} />
              <Route path="doctor" element={<DoctorPage />} />
              {/* --- W0-A */}
              <Route path="skills" element={<SkillsPage />} />
              <Route path="plugins" element={<PluginsPage />} />
              <Route path="jobs" element={<JobsPage />} />
              <Route path="teams" element={<TeamsPage />} />
              {/* --- end W0-A */}
              <Route path="welcome" element={<WelcomePage />} />
              <Route path="goals" element={<GoalsPage />} /> {/* --- W0-C */}
              <Route path="meetings" element={<MeetingsPage />} /> {/* --- F4 */}
              <Route path="webhooks" element={<Suspense><WebhooksPage /></Suspense>} /> {/* --- G5b */}
              <Route path="platforms" element={<Suspense><PlatformsPage /></Suspense>} /> {/* --- G5b */}
              <Route path="*" element={<div className="p-4"><EmptyState title="Page not found" /></div>} />
            </Route>
          </Routes>
        </BrowserRouter>
        </EventsProvider>
      </ControlProvider>
    </TooltipProvider>
  );
}

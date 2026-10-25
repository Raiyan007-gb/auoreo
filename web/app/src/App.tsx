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

export function App() {
  return (
    <TooltipProvider>
      <ControlProvider>
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
              <Route path="*" element={<div className="p-4"><EmptyState title="Page not found" /></div>} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ControlProvider>
    </TooltipProvider>
  );
}

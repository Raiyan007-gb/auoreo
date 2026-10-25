import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { AppShell, EmptyState } from "@/components/AppShell";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ControlProvider } from "@/lib/control";
import { ApprovalsPage } from "@/pages/Approvals";
import { HarnessPage } from "@/pages/Harness";
import { MemoryPage } from "@/pages/Memory";
import { AuditPage, DoctorPage } from "@/pages/Placeholder";
import { SessionRoute } from "@/pages/Session";
import { SessionsPage } from "@/pages/Sessions";
import { SettingsPage } from "@/pages/Settings";
import { UsagePage } from "@/pages/Usage";

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
              <Route path="audit" element={<AuditPage />} />
              <Route path="doctor" element={<DoctorPage />} />
              <Route path="*" element={<div className="p-4"><EmptyState title="Page not found" /></div>} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ControlProvider>
    </TooltipProvider>
  );
}

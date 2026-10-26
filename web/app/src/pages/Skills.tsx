import type { HarnessEntry } from "@sdk/client";
import { RotateCwIcon } from "lucide-react";
import { useState } from "react";
import { ErrorNote, PageHeader } from "@/components/AppShell";
import { AsyncBody } from "@/components/ops/AsyncBody";
import { useAction } from "@/components/ops/opsUtils";
import { SkillsHubPanel } from "@/components/SkillsHub"; // --- I5
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAsync } from "@/hooks/useAsync";
import { api } from "@/lib/api";

const SOURCE_VARIANT: Record<string, BadgeVariant> = {
  runtime: "info",
  project: "success",
  bundled: "secondary",
};

/** YAML-ish rendering of the frontmatter fields the harness row carries. */
function frontmatter(s: HarnessEntry): string {
  const quote = (v: string) => (/[:#"'\n]/.test(v) ? JSON.stringify(v) : v);
  return ["---", `name: ${quote(s.name)}`, `description: ${quote(s.description)}`, "---"].join("\n");
}

function SkillDialog({ skill, onClose }: { skill: HarnessEntry | null; onClose: () => void }) {
  return (
    <Dialog open={skill !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl" data-testid="skill-dialog">
        {skill && (
          <>
            <DialogTitle className="font-mono">{skill.name}</DialogTitle>
            <DialogDescription>
              Agent Skill — the body is loaded lazily when an agent calls the <code>skill</code> tool.
            </DialogDescription>
            <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Source</dt>
              <dd>
                <Badge variant={SOURCE_VARIANT[skill.source] ?? "outline"}>{skill.source}</Badge>
              </dd>
              <dt className="text-muted-foreground">Path</dt>
              <dd className="font-mono text-xs break-all">{skill.path ?? "—"}</dd>
            </dl>
            <section aria-label="SKILL.md frontmatter">
              <h3 className="mb-1 text-xs font-medium text-muted-foreground">SKILL.md frontmatter</h3>
              <pre className="max-h-64 overflow-auto rounded-md border bg-muted/40 p-3 font-mono text-xs whitespace-pre-wrap">
                {frontmatter(skill)}
              </pre>
            </section>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function SkillsPage() {
  const skills = useAsync(() => api.skills(), []);
  const action = useAction();
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState<HarnessEntry | null>(null);
  const [generation, setGeneration] = useState<number | null>(null);

  const doReload = async () => {
    const r = await action.run("reload", () => api.reload());
    if (r) {
      setGeneration(r.generation);
      skills.reload();
    }
  };

  const f = filter.toLowerCase();
  return (
    <div className="flex flex-col">
      <PageHeader
        title="Skills"
        description="Agent Skills discovered from skills/<name>/SKILL.md in every definition root. Plugins and MCP servers live on their own page."
        actions={
          <>
            <Input
              aria-label="Filter skills"
              placeholder="Filter…"
              className="w-48"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <Button variant="outline" onClick={() => void doReload()} disabled={action.busy !== null}>
              <RotateCwIcon aria-hidden className={action.busy ? "animate-spin" : undefined} /> Reload
            </Button>
          </>
        }
      />
      <Tabs defaultValue="installed" className="p-4">
        <TabsList aria-label="Skills views">
          <TabsTrigger value="installed">Installed</TabsTrigger>
          <TabsTrigger value="hub" data-testid="skills-hub-tab">
            Hub
          </TabsTrigger>
        </TabsList>
        <TabsContent value="hub">
          <SkillsHubPanel onChanged={skills.reload} />
        </TabsContent>
        <TabsContent value="installed" className="space-y-3">
        <ErrorNote error={action.error} />
        {generation !== null && (
          <p role="status" className="text-xs text-muted-foreground" data-testid="skills-reloaded">
            Reloaded — generation {generation}
          </p>
        )}
        <AsyncBody state={skills} empty="No skills found" emptyHint="Add a folder skills/<name>/SKILL.md and press Reload.">
          {(rows) => {
            const shown = rows.filter(
              (r) => !f || r.name.toLowerCase().includes(f) || r.description.toLowerCase().includes(f),
            );
            return (
              <Table aria-label="Skills">
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Description</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {shown.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={3} className="text-center text-sm text-muted-foreground">
                        No skills match “{filter}”.
                      </TableCell>
                    </TableRow>
                  )}
                  {shown.map((s) => (
                    <TableRow key={s.name} data-testid="skill-row" data-name={s.name}>
                      <TableCell className="whitespace-nowrap">
                        <Button
                          variant="link"
                          className="h-auto p-0 font-mono text-xs"
                          onClick={() => setOpen(s)}
                          aria-label={`Show details for ${s.name}`}
                        >
                          {s.name}
                        </Button>
                      </TableCell>
                      <TableCell>
                        <Badge variant={SOURCE_VARIANT[s.source] ?? "outline"} title={s.path ?? undefined}>
                          {s.source}
                        </Badge>
                      </TableCell>
                      <TableCell className="max-w-2xl text-xs text-muted-foreground">
                        <span className="line-clamp-2">{s.description}</span>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            );
          }}
        </AsyncBody>
        </TabsContent>
      </Tabs>
      <SkillDialog skill={open} onClose={() => setOpen(null)} />
    </div>
  );
}

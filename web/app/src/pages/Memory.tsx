import type { SearchHit } from "@sdk/client";
import { PlusIcon, SearchIcon } from "lucide-react";
import { useState } from "react";
import { EmptyState, ErrorNote, PageHeader } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label, NativeSelect } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CuratorPanel, FactsPanel, FilesPanel } from "@/components/MemoryPanels";
import { MemoryUsageGauge } from "@/components/MemoryUsageGauge"; // --- I5
import { api } from "@/lib/api";
import { errorText } from "@/lib/utils";

const CATEGORIES = ["user", "project", "preference", "fact"];

/** Citation in the same shape the knowledge_search tool emits: fact:<id> or path. */
function citation(h: SearchHit): string {
  return h.source ? h.source : `${h.kind}:${h.id}`;
}

export function MemoryPage() {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const [fact, setFact] = useState("");
  const [category, setCategory] = useState("user");
  const [factMsg, setFactMsg] = useState<string | null>(null);
  const [factErr, setFactErr] = useState<string | null>(null);

  const search = async (query = q) => {
    if (!query.trim()) return;
    setSearchErr(null);
    try {
      setHits(await api.memorySearch(query.trim()));
    } catch (e) {
      setSearchErr(errorText(e));
    }
  };

  const add = async () => {
    if (!fact.trim()) return;
    setFactErr(null);
    try {
      const r = await api.addFact({ content: fact.trim(), category });
      setFactMsg(r.created ? `Saved as fact:${r.id}` : `Already known as fact:${r.id}`);
      setFact("");
    } catch (e) {
      setFactErr(errorText(e));
    }
  };

  return (
    <div className="flex flex-col">
      <PageHeader title="Memory" description="Hybrid keyword + semantic recall over facts, documents and past messages." />
      <div className="px-4 pt-4 sm:px-8">
        <MemoryUsageGauge />
      </div>
      <Tabs defaultValue="search" className="px-4 pt-4 pb-10 sm:px-8 sm:pt-5">
        <TabsList aria-label="Memory views">
          <TabsTrigger value="search">Search</TabsTrigger>
          <TabsTrigger value="facts">Facts</TabsTrigger>
          <TabsTrigger value="files">Files</TabsTrigger>
          <TabsTrigger value="curator">Curator</TabsTrigger>
        </TabsList>
        <TabsContent value="facts">
          <FactsPanel />
        </TabsContent>
        <TabsContent value="files">
          <FilesPanel />
        </TabsContent>
        <TabsContent value="curator">
          <CuratorPanel />
        </TabsContent>
        <TabsContent value="search" className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <section className="space-y-3" aria-label="Search memory">
          <form
            role="search"
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void search();
            }}
          >
            <Input aria-label="Search memory" placeholder="Search memory…" value={q} onChange={(e) => setQ(e.target.value)} />
            <Button type="submit">
              <SearchIcon aria-hidden /> Search
            </Button>
          </form>
          {searchErr && <ErrorNote error={searchErr} />}
          {hits && hits.length === 0 && <EmptyState title="No matches" />}
          <ul className="space-y-2" data-testid="memory-results">
            {(hits ?? []).map((h) => (
              <li key={`${h.kind}:${h.id}`} className="rounded-lg border p-3" data-testid="memory-hit">
                <p className="whitespace-pre-wrap">{h.text}</p>
                <div className="mt-2 flex items-center gap-1.5">
                  <Badge variant="outline" className="font-mono" title="Citation">
                    {citation(h)}
                  </Badge>
                  <Badge variant="secondary">{h.kind}</Badge>
                  <span className="ml-auto font-mono text-xs text-muted-foreground">score {h.score.toFixed(3)}</span>
                </div>
              </li>
            ))}
          </ul>
        </section>
        <Card>
          <CardHeader>
            <CardTitle>Add a fact</CardTitle>
            <CardDescription>Durable knowledge agents can recall in later sessions.</CardDescription>
          </CardHeader>
          <form
            className="grid gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void add();
            }}
          >
            <Label htmlFor="fact">Fact</Label>
            <Input id="fact" value={fact} maxLength={2000} onChange={(e) => setFact(e.target.value)} />
            <Label htmlFor="fact-cat">Category</Label>
            <NativeSelect id="fact-cat" value={category} onChange={(e) => setCategory(e.target.value)}>
              {CATEGORIES.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </NativeSelect>
            <Button type="submit" disabled={!fact.trim()}>
              <PlusIcon aria-hidden /> Add fact
            </Button>
            {factMsg && (
              <p role="status" className="text-xs text-success">
                {factMsg}
              </p>
            )}
            {factErr && <ErrorNote error={factErr} />}
          </form>
        </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

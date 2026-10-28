# AUOREO web: SQA report and UI/UX modernization plan

Tested 2026-10-28 on the live server (`http://127.0.0.1:4096`, Windows 11, Chrome 150, dark theme)
through Browser MCP, plus `auoreo doctor`, the Python suites and the Playwright e2e suite.

## 1. What was tested

Every page and its main flows: Sessions (search, status chips, Archived, New session, Queue),
Session (send, stream, raw/rendered, thinking toggle, Share create/open/revoke, Fork, Compact,
Export MD/JSON, Archive, Delete, approval card), Approvals, Memory (add, search, pin, helpful,
forget, Files, Curator), Goals (add, progress, status), Meetings (start), Harness (tabs, filter,
reload), Skills (detail dialog, Hub), Plugins & MCP (both tabs, per-session sources, hot-reload
log), Teams (Teams, Kanban, Board), Jobs (Cron, runs drawer, Webhooks, Deliveries), Webhooks,
Messaging, Usage, Audit, Doctor, Settings (Models, Environment, General), command palette.

No uncaught JS errors on any page; every API call returned 2xx except the expected ones.

## 2. Bugs found and fixed

| Area | Bug | Fix |
|---|---|---|
| Shell tool (Windows) | `bash` resolved to the WSL launcher (`System32\bash.exe`): every command failed with `0x8007072c`, output garbled as UTF-16 | `backends/sandbox.py`: `windows_bash()` / `host_argv()` pick Git Bash and skip WSL stubs |
| Shell tool (Windows) | timeout called `os.killpg` (POSIX only): a timed-out command crashed; background children survived | `taskkill /T` tree kill on Windows |
| `auoreo doctor` (Windows) | crashed with `UnicodeEncodeError` on the cp1252 console (✓, →) | `cli/main.py` switches stdout/stderr to UTF-8 on Windows |
| `auoreo doctor` | `gh_auth` detail mojibake (`âœ“`) | decode `gh` output as UTF-8 |
| `auoreo doctor` | `port` warned "in use" when the occupant is auoreo itself | probe `/api/health`; report "in use by a running auoreo server" |
| `auoreo doctor` | `backend:browser` warn (no Playwright Chromium) | `browser_executable_path` set to the installed Chrome (`auoreo config undo` reverts) |
| Plugin signatures (Windows) | CRLF checkout changed the hash of imagegen/miio/mqtt | `.gitattributes`: `plugins/** eol=lf` |
| API | unknown `/api/*` and `/hooks/*` paths returned the SPA HTML with **200** | `SpaStaticFiles` 404s those namespaces (OS path separators handled) |
| Favicon | `./favicon.svg` 404 on every nested route (`/session/…`, `/share/…`) | absolute `/favicon.svg` |
| Sessions | "Archived" chip listed every session (archived + active) | archived-only, with its own empty state |
| New session | two "default" entries in Agent | empty option reads "Server default"; long descriptions clipped (full text in tooltip) |
| Dropdowns | white-on-white option lists in dark mode (every `<select>`) | `color-scheme` follows the app theme; option colours from tokens |
| Settings → Models | "No provider connected" while an env key (ANTHROPIC_API_KEY) is in use | names the provider in use and where its key lives |
| Session rail | Model "server default" while the header showed the real model | rail shows the model the turn ran on |
| Composer | Interrupt icon looked like a checkbox | `CircleStop` icon |
| Memory | stats card kept "Facts 0" after adding a fact | gauge reloads after a write |
| Usage / Messaging | copy still said "settings.json" (settings live in state.db) | point to `auoreo config set` / the settings database |
| e2e (Windows) | `npm run e2e` crashed (`spawn npx ENOENT`); config found only a Linux Chromium | run Playwright's CLI with Node directly; `PLAYWRIGHT_CHROMIUM_PATH` or installed Chrome |

Results: Python suites touched — 50 passed, 1 skipped (incl. `test_permission_reply_over_api`,
which failed before the bash fix). e2e — 73 passed, 1 failed (`pwa.spec.ts` offline banner,
also failing before these changes). Backend fixes need a server restart (`code_sha` warns until then).

## 3. Doctor items left (not fixable in code here)

| Check | Why it stays | What to do |
|---|---|---|
| plugin-signatures | calendar, discord, email, telegram changed in I7 after being signed in I6 | release owner re-runs `packaging/sign_plugins.py` with the signing key |
| backend:sandbox, egress-sandbox | no bubblewrap / Landlock on Windows | run in Docker/WSL2 for isolation, or accept `policy=warn` |
| backend:stt, backend:tts | optional voice deps not installed | `uv pip install faster-whisper`; KittenTTS wheel |
| rtc:turn | no TURN server | only needed for calls across NAT (`deploy/coturn`) |
| desktop:health | `cua_driver` health report times out (0 displays seen) | see `docs/teleop.md` |
| update | release manifest 404 | publish a release or set `update_url` |
| code_sha | running server predates the fixes | restart `auoreo serve` |

## 4. UI/UX modernization plan

### Phase 1 — Friction fixes (1–2 days) — done 2026-10-28

All ten items below are implemented. New API: `DELETE /api/goals/{id}`,
`DELETE /api/voice/meetings/{id}`, `GET|POST /api/doctor/fixes`, `GET /api/context?session_id=…`
(batch); a goal set to `done` without a progress value becomes 100%; forks are titled
"<title> (fork)"; bundled MCP servers report `available: false` with the enable command.
Item 6 keeps the per-session checkbox usable for globally-off sources (the choice applies once
the source is switched on) and labels them "off everywhere". Item 7 collapses each connector's
preview (it differs per platform: Telegram renames, clamp warnings) and opens it when a label is
clamped. Item 8 renames the Teams "Board" tab to "Blackboard".

1. **Session header**: the action icons wrap to a second row once the right rail is shown. Keep
   Share and Call visible; move Fork, Compact, Export ×2, Archive, Delete into a "More" menu.
2. **Thread notices**: "Compacting context (manual)…" stays forever after the result arrives.
   Turn progress notices into one row that updates in place (spinner → result).
3. **Fork** keeps the same title; name it "<title> (fork)".
4. **Goals**: marking a goal *done* should set progress to 100%. Add delete for goals and meetings
   (both can only be dropped/abandoned today).
5. **Command palette**: "compact" also matches agents and plugins through their long
   descriptions. Rank name matches first, search descriptions only as a fallback, cap 3 per group.
6. **Plugins & MCP**: "not connected" for servers that are simply *off* reads like a failure.
   Show "Off · `auoreo mcp add --bundle time`" with an Enable button; show "not installed"
   instead of Lock "missing"; per-session sources should mark globally-off sources as such.
7. **Messaging**: the same slash-command preview is repeated under every connector; show it once.
8. **Teams**: the Kanban tab is headed "Board" while a separate Board tab exists; rename one.
9. **Doctor page**: uses two thirds of the width; lay groups out in two columns, add counts per
   group and an "Apply safe fixes" button (the CLI already has `--fix`).
10. **Fewer requests**: `/api/permissions/pending` is fetched 2–3× per navigation, `/audit/verify`
    twice, and Usage makes one `/context` call per session (N+1). Share one approvals store;
    add a batch context endpoint.

### Phase 2 — Visual system — done 2026-10-28

Also done: a named type scale (`--text-2xs|ui|title` next to Tailwind's xs/sm/base/lg; 55 ad-hoc
pixel sizes replaced, `cn()` taught the new names), one page gutter and body padding
(`page-gutter` / `page-body`) and one card padding (16px, 20px from sm), a table density toggle
(`lib/density`, on Sessions, Audit and Settings → General, saved per browser) and sticky table
headers from the `lg` breakpoint up (narrower screens keep sideways scrolling).
The older notes below are kept for what was built in the first pass.


Done: one primary per screen (the default button is now neutral; each screen's main action is
`brand`), skeleton loaders (`components/ui/skeleton`: lists, Sessions, Doctor), toasts
(`lib/toast` + `components/ui/toast`: session archive/restore/delete, goal and meeting delete,
doctor fixes, copy report), empty states with an action (Sessions → New session), row actions
revealed on hover/focus (always shown on touch), lowercase decision pills on Audit.
Left: the type/spacing scale pass, a table density toggle, sticky table headers (the tables sit in
an `overflow-x-auto` wrapper, so they need their own scroll container first).

- **One primary per screen**: in dark mode, white "primary" buttons (Run now, Start, Search,
  Save) compete with the teal brand button. Use teal for the single main action, outline/ghost
  for the rest.
- **Type and spacing scale**: 12/13/14/16/20/24 with a 4px grid; consistent page gutters and card
  padding (today 16–20px vary per page).
- **Skeleton loaders** instead of "Loading…" text; **toasts** for action results (archive, fork,
  copy, saved) instead of inline status lines that shift the layout.
- **Empty states with an action**: every empty state gets a primary button (New session, Add goal,
  Start meeting) next to the hint.
- **Tables**: sticky header, row actions revealed on hover/focus (the sessions table shows five
  icon buttons on every row), compact/comfortable density toggle.
- **Status language**: one pill component everywhere (Audit shows `ESCALATE` uppercase next to
  lowercase `allow`/`deny`).

### Phase 3 — Layout and navigation — done 2026-10-28

- **Inbox** (`/inbox`, `/approvals` redirects): pending approvals and agent questions in one
  view; the sidebar and top-bar badges count both (`hooks/useNeedsYou`). MCP link cards only
  appear after their consent question was answered, so they are not counted twice.
- **Sidebar** groups fold (remembered per browser; a folded group still shows the current page and
  carries the waiting count). **Top bar**: breadcrumbs, search, inbox count; on phones it is the
  app bar. **Mobile**: bottom tabs (Sessions, Inbox, Search) and an edge-to-edge composer.
- **Session page**: agent, model, policy, tokens and context moved into the composer toolbar; the
  header keeps title, status, Thinking, Call, Share and ⋯. Below `xl` a button opens the side
  panel as a sheet. Finished runs of 3+ tool calls collapse into one expandable row.
- **Settings → Models**: "Manage here" connects a provider whose key already lives in
  Environment or the real environment (`POST /api/providers/adopt`; the connection points at the
  same secret, nothing is copied; removing it later deletes that key).

The notes below are the original plan.

- **Inbox**: one "Needs you" view for approvals, agent questions and MCP elicitations, with the
  badge in the sidebar (today only approvals are counted).
- **Collapsible sidebar groups** (17 items) and a top bar with breadcrumbs + global search.
- **Session page**: model/agent chips and the context gauge inside the composer toolbar; a
  tool-call timeline that groups consecutive tool cards; rail toggle below the `xl` breakpoint.
- **Settings → Models**: "Manage here" on an environment key, so env-configured providers can
  list and switch models without re-pasting the key.
- **Mobile**: bottom tab bar (Sessions, Approvals, Search) and a full-width composer.

### Phase 4 — Quality gates — done 2026-10-28

- **Accessibility**: `e2e/a11y.spec.ts` runs axe-core (WCAG 2.1 A/AA, contrast included) over every
  page plus the tab, dialog, menu and sheet states, in light and dark (74 scenes). It found these
  real problems, all fixed: a search button whose label hid its visible text, two more buttons with
  the same flaw ("Run now", "Run again"), dimmed rows that failed contrast, scrollable value boxes
  the keyboard could not reach, an in-text link told apart by colour alone, and a modal menu that
  hid focusable content. `e2e/focus.spec.ts` checks the Tab order and a visible focus indicator on
  every stop (it found tab panels without one).
- **Visual regression**: `npm run e2e:visual` compares 24 screenshots (light/dark × desktop/phone)
  taken on a fresh server; baselines are per platform. Windows baselines are committed; Linux ones
  come from the *visual baselines* workflow. Changing the brand colour to red failed 3 screenshots,
  so the gate does catch design changes.
- **CI**: the e2e job now runs on Ubuntu and Windows (Windows is informational until it has a track
  record) and runs the screenshot comparison after the functional specs.

Original plan:

- Visual regression: Playwright screenshots of every page in light and dark, compared in CI.
- Accessibility pass: focus order, focus rings on custom chips, colour contrast of muted text.
- Run the e2e suite on Windows in CI too (it now runs locally with installed Chrome).

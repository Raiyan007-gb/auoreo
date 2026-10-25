# Coding tools

GitHub wrappers, PowerShell, test/lint runners and the `verify_plan` gate (spec §2.3, §2.6,
§9.3). They are all `exposure: deferred`: an agent finds them with `tool_search`
("github pull request", "run tests", "lint", "powershell", "verify plan").

## GitHub (`toolset: github`)

Thin wrappers over the GitHub CLI. Each one builds an argv list (never a shell string), runs
`gh` through the selected sandbox backend with `net: direct`, and returns `gh --json` output
capped at about 20k characters (diffs and logs are clipped head + tail).

| Tool | Permission | Default | gh command |
|---|---|---|---|
| `gh_pr_view` | `github_read` | allow | `gh pr view [N] --json …` |
| `gh_pr_list` | `github_read` | allow | `gh pr list --json …` |
| `gh_pr_diff` | `github_read` | allow | `gh pr diff N [--name-only]` |
| `gh_pr_checks` | `github_read` | allow | `gh pr checks N --json …` (exit 8 = pending, not an error) |
| `gh_issue_view` / `gh_issue_list` | `github_read` | allow | `gh issue view/list --json …` |
| `gh_run_list` | `github_read` | allow | `gh run list --json …` |
| `gh_run_view` | `github_read` | allow | `gh run view ID --json … --jq …`, or `--log-failed`/`--log` tail (≤ 2000 lines) |
| `gh_repo_view` | `github_read` | allow | `gh repo view --json …` |
| `gh_api` | `github_read` | allow | `gh api --method GET repos/{owner}/{repo}/…` |
| `gh_api_write` | `github_write` | ask | `gh api --method POST/PATCH/PUT/DELETE repos/…` |
| `gh_pr_create` | `github_write` | ask | `gh pr create --title= --body= [--base= --head= --draft]` |
| `gh_pr_comment` / `gh_issue_comment` | `github_write` | ask | `gh pr/issue comment N --body=` |
| `gh_issue_create` | `github_write` | ask | `gh issue create --title= --body= [--label=]` |
| `gh_pr_merge` | `github_write` | ask, plus a human every time | `gh pr merge N --squash [--auto]` |

Every rule pattern is the `repo` argument (`*` when it is omitted), so
`{permission: github_write, pattern: "acme/widgets", action: allow}` allows writes to one
repo. `gh_pr_merge` is `interactive_only`, and it asks a human even when a rule or yolo mode
allows `github_write`. It can only squash-merge or turn on auto-merge.

### Auth

`gh` authenticates in this order:

1. `GH_TOKEN` / `GITHUB_TOKEN`, looked up in env → keyring → `secrets.db`
   (`auoreo secrets set GITHUB_TOKEN`). The tools declare these names in `requires_env`, so
   the executor copies only them into the child's scrubbed env. `GITHUB_TOKEN` is passed on as
   `GH_TOKEN`.
2. The credentials saved by `gh auth login` (under `~/.config/gh`, found through `HOME`).

`GH_HOST`, `GH_CONFIG_DIR`, `HTTPS_PROXY`, `NO_PROXY` and `SSL_CERT_FILE` are passed through
too. When `gh` is missing, a tool says to install it from <https://cli.github.com/>. When it is
not authenticated (exit 4, HTTP 401), the tool says to run `gh auth login` or store a token.
Prompts, the pager and update checks are turned off.

### Repository scope

The target is the `repo` argument (`owner/name` or `host/owner/name`). Without it, the tools
use the cwd checkout's `origin` remote, or its first remote when there is no `origin`. The
cwd repo is always allowed. Any other repo has to match a glob in `settings.json`:

```json
{ "github_allowed_repos": ["acme/*", "octo-org/docs"] }
```

Anything else is refused before `gh` runs. `gh_api` and `gh_api_write` only reach
`repos/OWNER/REPO/...` endpoints of an allowed repo. `{owner}`/`{repo}` placeholders are
filled in, and `user`, `orgs`, `search` and `graphql` are refused. String fields go as
`-f key=value`, which `gh` never reads as `@file`. Titles, bodies, labels and API fields that
the secret redactor would mask (`ghp_…`, `sk-…`, `X_TOKEN=…`, PEM keys) are refused before
anything is sent.

## PowerShell (`pwsh`)

`pwsh -NoProfile -NonInteractive -Command <script>` runs through the sandbox backend. On
Windows, `powershell.exe` is the fallback. Output is clipped like `bash` and ends with
`[exit code N]`. The permission is `bash`, and the script text is the pattern, so bash rules,
policies and floors all apply. `permissions/defaults.yaml` adds PowerShell floors:
`Remove-Item -Recurse` on `/` or `C:\`, `Format-Volume`, `Clear-Disk`, `Stop-Computer`,
`Restart-Computer`, and download-and-execute (`iex (iwr …)`, `iwr … | iex`,
`Invoke-Expression (New-Object Net.WebClient).DownloadString(…)`). When neither shell
exists, the tool says so.

## Test and lint runners

Both tools run with `sandbox_mode: workspace-write` and only write caches (`.pytest_cache`,
`.ruff_cache`, `.mypy_cache`). Their permissions are `run_tests` and `run_lint`, and both
default to `ask` because they execute project code (conftest files, eslint configs). To let
them run without prompts, allow them in your permissions:

```yaml
rules:
  - {permission: run_tests, pattern: "*", action: allow}
  - {permission: run_lint, pattern: "*", action: allow}
```

`run_tests` detects the runner from marker files, in this order:

| Runner | Detected from | Command |
|---|---|---|
| pytest | `pytest.ini`, `conftest.py`, `[tool.pytest…]`, `tests/**/test_*.py` | `uv run pytest` (with `uv.lock`), else `pytest` / `python3 -m pytest`, plus `-q -rfE --tb=short --junitxml=…` |
| npm | `package.json` `scripts.test` | `pnpm test` / `yarn test` / `npm test --` (with `CI=1`) |
| go | `go.mod` | `go test -json ./...` |
| cargo | `Cargo.toml` | `cargo test` |

It accepts `path`, `k`, `coverage` and `runner`. The result starts with a summary: passed,
failed, skipped and error counts, then up to 10 failures with `file:line` and their assertion
lines, then the coverage total. The raw output follows the summary. When the output is
long, the executor's pruner spills the full text to `tool-results/` and keeps the head and
tail inline.

`run_lint` runs the linters the project configures: `ruff check --output-format=json` and
`ruff format --check`, `mypy`, `eslint --format json`, and `tsc --noEmit` (when
`tsconfig.json` and `typescript` are present). It prints one section per tool with at most
40 findings. A linter that is configured but not installed is reported as skipped.

The parsers are pure functions in `src/auoreo/tools/testrun.py`. They are tested against
captured output in `tests/fixtures/testrun/`.

## `verify_plan` gate

`verify_plan` checks the plan recorded by `update_plan` (`ctx.state.plan`). Each plan step
can carry an optional `done_when`. The checks are deterministic and never call a model. The
plan fails when:

- the plan is empty;
- a step has no acceptance criterion: no `done_when`, and no backticked command in the step;
- a step names a path outside the working directory (`../x`, `~/x`, or an absolute path whose
  top-level directory exists, such as `/etc/hosts`; `/api/events` is ignored);
- two steps are the same text;
- a step holds an unresolved question (ends in `?`, or contains TBD, TODO or `unclear`).

Destructive steps (delete, drop, force-push, migrate, deploy, merge, …) do not fail the plan.
They are listed under "needs approval when executed".

A pass records the plan's hash, computed over each step's `step` and `done_when`; status
changes do not alter it. When `settings.plan_requires_verify` is `true` (default `false`),
`exit_plan_mode` refuses to leave plan mode unless that hash matches the current plan.
Changing a step's content through `update_plan` therefore requires running `verify_plan`
again. The refusal message tells the agent to call `verify_plan`. The flow:

```
enter_plan_mode → research → update_plan(steps with done_when) → verify_plan
  → (FAIL: fix with update_plan, verify again) → PASS → exit_plan_mode → human approves
```

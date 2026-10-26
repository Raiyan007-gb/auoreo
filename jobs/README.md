# jobs/

`jobs/<name>.yaml` runs an agent on a schedule in its own session and delivers the result.

```yaml
name: morning-brief
description: Summarize open work every weekday morning.
schedule: "0 9 * * 1-5"        # 5-field cron | "every 30m" | "30m" | "every monday 9am <tz>" | ISO one-shot
tz: Europe/Berlin
agent: default
prompt: |
  It is {{now}}. Review the repository status and summarize what needs attention today.
deliver: [log]                 # backends/delivery/<name>[:target] | origin | home | all (fan-out)
enabled: true
catch_up: true                 # fire once for a run missed while the server was down
timeout_s: 900                 # hard interrupt per run (default: teams2.job_timeout_s = 180)
# I3 extras (all optional)
model: claude-sonnet           # run model; `provider:` prefixes it when it has no "/"
script: collect.py             # <home>/scripts/collect.py; stdout (sandboxed read-only, redacted,
                               # capped at i3.cron.script_max_chars) injected as UNTRUSTED data
context_from: [previous]       # this job's last output and/or other job names
workdir: ./reports             # script cwd (must exist)
notify_on_complete: true       # inject <job-notification> into origin_session after each run
header: "[{job}] {when}"       # delivery header/footer override ("" disables)
```

Runs older than the newest `i3.cron.keep_runs` (1000) per job are pruned; job files are written
0600 under a lock. `auoreo cron add <name> -s ... -p ... --dry-run` previews the next 5 fires.

APScheduler only fires the trigger; AUOREO records `job_runs`, runs the agent
non-interactively (approval-needing tools are denied), and writes a `deliveries` row.
`auoreo cron list|run|pause|resume` and `GET /api/cron` show state.

Agents create reminders and jobs with the `cronjob` tool (approval required) and clients with
`POST /api/cron` (`dry_run: true` previews the next 5 fires); both write
`<home>/jobs/<name>.yaml`, and only those files can be removed again through them.

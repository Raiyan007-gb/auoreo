# jobs/

`jobs/<name>.yaml` runs an agent on a schedule in its own session and delivers the result.

```yaml
name: morning-brief
description: Summarize open work every weekday morning.
schedule: "0 9 * * 1-5"        # 5-field cron | "every 30m" | ISO-8601 one-shot
tz: Europe/Berlin
agent: default
prompt: |
  It is {{now}}. Review the repository status and summarize what needs attention today.
deliver: [log]                 # backends/delivery/<name>, tried in order
enabled: true
catch_up: true                 # fire once for a run missed while the server was down
timeout_s: 900
```

APScheduler only fires the trigger; AUOREO records `job_runs`, runs the agent
non-interactively (approval-needing tools are denied), and writes a `deliveries` row.
`auoreo cron list|run|pause|resume` and `GET /api/cron` show state.

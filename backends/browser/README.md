# backends/browser/

Browser automation for the `browser_*` tools (ABC: `src/auoreo/backends/browser.py`).

| Backend | Use |
|---|---|
| `playwright` (default) | Headless Chromium via `playwright==1.63.*`; one isolated `BrowserContext` per session (1280×800, user agent ends in `auoreo/0.1`). |
| `fake` | Canned HTML keyed by URL; deterministic snapshots for tests and keyless demos (`settings.backends.browser = "fake"`). |

## Chromium binary

The Python package must match the browser build (Playwright 1.63 ↔ `chromium-1243`).
Point Playwright at already-installed browsers instead of running `playwright install`:

```bash
export PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers      # contains chromium-1243, chromium_headless_shell-1243
```

or set an explicit binary in `~/.auoreo/settings.json`:

```json
{"browser_executable_path": "/opt/pw-browsers/chromium-1243/chrome-linux64/chrome"}
```

`auoreo doctor` launches and closes Chromium and prints its version.

## Policy

Every navigation passes the SSRF guard (`auoreo.tools.net.check_url`): http/https only,
`file:`/`javascript:`/`data:` refused, private/loopback/link-local hosts blocked unless
`settings.allow_private_urls` is true. The landing URL is re-checked after navigations,
clicks, submits and history moves (redirects are not routed by Playwright), and every
sub-request is routed through the same check. `browser_navigate` uses the `webfetch`
permission with the URL as pattern (`default_action: ask`), so the first visit to a host is
approved like `web_fetch`. A session's browser closes when the session is deleted or the
process shuts down; `browser_close` closes it early.

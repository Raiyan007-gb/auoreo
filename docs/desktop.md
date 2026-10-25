# Desktop control (M4d)

The `computer` tool lets an agent observe and drive native apps. It talks to the selected
`desktop` backend (`ctx.backend("desktop")`, ABC in `src/auoreo/backends/desktop.py`):

| Backend | Default | Use |
|---|---|---|
| `cua_driver` | yes | `cua-driver mcp` over MCP stdio (≥ 0.20). Needs a display and the driver's accessibility + screen-recording permissions. |
| `fake` | | In-memory windows, Pillow-drawn screenshots, recorded actions, scriptable verify verdicts. Tests and keyless demos (`settings.backends.desktop = "fake"`). |

`auoreo doctor` and `GET /api/voice/status` report readiness; `cua_driver.check()` says
whether the binary is missing, the display is missing, or the version is below the floor.

## The tool

```json
{"op": "click", "target": "500,320"}
{"op": "type", "text": "hello"}
{"op": "key", "key": "cmd+s"}
{"op": "scroll", "direction": "down", "amount": 3}
{"op": "drag", "target": "100,100", "to": "600,400"}
{"op": "focus_app", "target": "TextEdit"}
{"op": "screenshot"} / {"op": "list_windows"} / {"op": "wait", "amount": 2}
```

- `target` is an element reference from the backend (cua-driver `element_token`) or `"x,y"`
  in **relative 0–1000 coordinates** of the primary display; the backend maps them to pixels.
- Mutating ops return a fresh screenshot (attached as an image for vision models, normalized
  through the shared image ladder) and a **verify** verdict: the pre/post screenshots are
  compared (changed-pixel fraction on a 256 px greyscale thumbnail). `matched=False` means
  nothing visibly changed — the agent should re-observe instead of repeating blindly.
- `delivery_mode: background` (default) acts without raising or focusing the target window.

## Permissions and safety

- The tool's permission is `computer` with `op` as the pattern, `default_action: ask`,
  `is_destructive: true`. Allow, for example, read-only observation in a project rule:

  ```yaml
  # .auoreo/permissions/desktop.yaml
  rules:
    - {permission: computer, pattern: screenshot, action: allow}
    - {permission: computer, pattern: list_windows, action: allow}
  ```

- `delivery_mode: foreground` additionally needs `computer_foreground` (pattern = op),
  checked inside the handler via `ctx.authorize` — a separate approval card, never implied by
  the `computer` grant.
- **Floors** (refused before the backend is called, in every mode): key combos that lock the
  screen, log out, shut down or kill the session (`cmd+ctrl+q`, `cmd+shift+q`,
  `ctrl+alt+del`, `ctrl+alt+backspace`, `super+l`, `ctrl+alt+l`, power/sleep keys, `alt+f4`
  without a target window, …) and typed text that pipes a download into a shell
  (`curl … | sh`), wipes the filesystem (`rm -rf /`), formats disks, or is a fork bomb.
- Every action — including refusals — writes an audit row `computer.act` (op, target,
  delivery mode, verify verdict; typed text is recorded as a length only).

## cua-driver

The backend spawns `<cua_driver_command> mcp` with the MCP SDK v2 `Client` and a scrubbed
environment (no API keys; `CUA_DRIVER_RS_TELEMETRY_ENABLED=0` unless `cua_telemetry: true`).
Tool mapping (from the cua-driver MCP reference): screenshot → `get_desktop_state`,
`click`/`double_click`/`right_click`, type → `type_text`, key → `press_key` (single key) or
`hotkey` (combo), `scroll`, `drag`, `list_windows`, focus_app → `launch_app`. Actions carry
`target: {kind: "desktop", display_id: "primary"}` (or `element_token`), `delivery_mode` and a
`session` label.

Not verifiable in CI (no display): the mapping is contract-tested with a stubbed MCP call, and
`drag`'s `to_x/to_y` and `launch_app`'s `name` argument names are unverified against a live
driver — check `cua-driver describe drag` / `describe launch_app` on macOS/Windows before
relying on them.

## The operator agent

`agents/operator/AGENT.md` (tools: `computer`, `screenshot`, `vision_understand`,
`ask_user_question`) works observe → act → verify, background first, and asks the user
before anything irreversible or anything needing credentials.

```bash
auoreo run --agent operator "open TextEdit and type a shopping list"
```

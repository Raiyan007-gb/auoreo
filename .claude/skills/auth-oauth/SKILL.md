---
name: auth-oauth
description: Implement server + provider authentication — bearer token for the API (OPENCODE-style server password), provider OAuth/PKCE login, credential storage in env/keyring. Use when building agent_api/auth, agent_cli/login, agent_gateway credentials in this repo.
---

# Auth & OAuth — agent_api/auth, agent_cli/login

This repo's auth model (plan.md Part H #11, Part B "Auth"): (1) the **server** can be protected by a bearer token/password; (2) **model providers** are authenticated via env keys or an **OAuth/PKCE** login flow; Keycloak/OIDC is a later, optional layer (plan.md Phase 5).

## 1. Server token (loopback/local default; required when binding beyond loopback)

Mirror opencode's `OPENCODE_SERVER_PASSWORD`: server reads `AGENT_PLATFORM_SERVER_PASSWORD`. If unset AND hostname ≠ loopback, **warn on startup** ("server is unsecured" — opencode's exact behavior in `serve.ts`).

```python
import secrets
from typing import Annotated
from fastapi import Depends, HTTPException, Header

def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    if settings.server_password is None:
        return                                   # local single-user → allow
    expected = f"Bearer {settings.server_password}".encode()
    if not secrets.compare_digest((authorization or "").encode(), expected):   # constant-time
        raise HTTPException(status_code=401, detail="invalid or missing token",
                            headers={"WWW-Authenticate": "Bearer"})

@app.get("/health", dependencies=[Depends(require_token)]) ...
```

- Attach via `dependencies=[...]` or a router-wide dependency on group routers (`agent_api/middleware/auth.py`).
- mDNS/`--mdns` forces hostname `0.0.0.0` → password effectively required there.
- Alternative: `fastapi.security.HTTPBearer(auto_error=False)` as the dependency (returns
  `HTTPAuthorizationCredentials | None`) — same check, but the scheme shows up in OpenAPI/`/docs` and generated SDKs.

## 2. Provider login flow (`agent login`)

Flow per provider: initiate OAuth → user authorizes in browser → redirect to local callback → exchange code → store token. Use `Authlib` for OAuth2/PKCE (Context7: `/authlib/authlib`).

```python
from authlib.common.security import generate_token
from authlib.integrations.httpx_client import AsyncOAuth2Client

# 1. start
client = AsyncOAuth2Client(
    client_id,
    redirect_uri="http://127.0.0.1:PORT/callback",
    code_challenge_method="S256",  # Authlib derives the S256 challenge from code_verifier
)
code_verifier = generate_token(48)
url, state = client.create_authorization_url(authorize_url, code_verifier=code_verifier)  # sync, no await
# persist (state, code_verifier) until the callback arrives

# 2. callback (agent_api exposes the callback route) — fetch_token verifies `state` (CSRF)
token = await client.fetch_token(
    token_endpoint, authorization_response=str(request.url), code_verifier=code_verifier
)

# 3. store
await credential_store.set(provider_id, {"type": "oauth", "token": token})  # keyring / env file
```

- Localhost callback: bind the callback server to `127.0.0.1` (like opencode's `oauth-callback`), pick a free port; the CLI opens the browser via `webbrowser.open`.
- Support `login` as a **plugin hook** (`providers.ts`, `login.ts` in opencode) — provider-specific params (scopes, endpoints) come from the plugin, keeping core provider-neutral.
- Environment-key fallback: if `OPENAI_API_KEY` (etc.) is set, `agent login` for that provider is unnecessary; `agent_gateway` honors env keys first, then stored creds (LiteLLM reads the standard `*_API_KEY` env vars automatically — see `litellm` skill).

## 3. Credential store

- Interface `agent_core/auth.CredentialStore` with backends: **env** (read-only, primary) → **keyring** (OS keychain) → **file** (`~/.agent-platform/auth.json` mode 0600, test/local only).
- Never store plaintext secrets in the repo, or in `.env` accidentally committed. Keep `.env` git-ignored (plan.md Part K).
- Token refresh: store `refresh_token` + `expires_at`; refresh before expiry in the gateway. With Authlib,
  construct the client with `token=stored, token_endpoint=..., update_token=async_cb` — it auto-refreshes
  (60 s before expiry by default, `leeway=`) on the next request and calls `update_token` to persist the new token.

## 4. MCP server auth (later, agent_mcp/oauth.py)

Some external MCP servers require OAuth (see `mcp` skill). Reuse the same OAuth helpers; add an MCP `oauth` capability flag per server config.

## 5. Multi-tenancy (Phase 5, optional)

Keep the auth layer an interface so Keycloak/OIDC (realms, `organization_id` on objects) slots in without rewriting handlers (plan.md Phase 5). For now: single-admin token + per-provider creds is the MVP surface.

## Security rules (plan.md Part K)

- Secrets via env/keyring only; no keys in code or committed files.
- Server binds `127.0.0.1` by default; password required if exposed.
- Hashing: server password compared server-side only; never expose it via API. Auth errors are generic (401 without hinting valid vs invalid).
- TLS in front when exposed (reverse proxy) — never plaintext auth over a public wire.
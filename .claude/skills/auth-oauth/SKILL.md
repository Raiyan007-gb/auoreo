---
name: auth-oauth
description: Implement server + provider authentication — bearer token for the API (OPENCODE-style server password), provider OAuth/PKCE login, credential storage in env/keyring. Use when building agent_api/auth, agent_cli/login, agent_gateway credentials in this repo.
---

# Auth & OAuth — agent_api/auth, agent_cli/login

This repo's auth model (plan.md Part H #11, Part B "Auth"): (1) the **server** can be protected by a bearer token/password; (2) **model providers** are authenticated via env keys or an **OAuth/PKCE** login flow; Keycloak/OIDC is a later, optional layer (plan.md Phase 5).

## 1. Server token (loopback/local default; required when binding beyond loopback)

Mirror opencode's `OPENCODE_SERVER_PASSWORD`: server reads `AGENT_PLATFORM_SERVER_PASSWORD`. If unset AND hostname ≠ loopback, **warn on startup** ("server is unsecured" — opencode's exact behavior in `serve.ts`).

```python
from fastapi import Depends, HTTPException, Header

def require_token(authorization: str | None = Header(default=None)) -> None:
    if settings.server_password is None:
        return                                   # local single-user → allow
    if authorization != f"Bearer {settings.server_password}":
        raise HTTPException(status_code=401, detail="invalid or missing token")

@app.get("/health", dependencies=[Depends(require_token)]) ...
```

- Attach via `dependencies=[...]` or a router-wide dependency on group routers (`agent_api/middleware/auth.py`).
- mDNS/`--mdns` forces hostname `0.0.0.0` → password effectively required there.

## 2. Provider login flow (`agent login`)

Flow per provider: initiate OAuth → user authorizes in browser → redirect to local callback → exchange code → store token. Use `Authlib` for OAuth2/PKCE.

```python
from authlib.integrations.httpx_client import AsyncOAuth2Client

# 1. start
client = AsyncOAuth2Client(
    client_id,
    redirect_uri="http://127.0.0.1:PORT/callback",
    code_challenge_method="S256",        # Authlib passes PKCE automatically; set S256 at init
)
url, state = await client.create_authorization_url(authorize_url, code_verifier=...)

# 2. callback (agent_api exposes the callback route)
token = await client.fetch_token(token_endpoint, authorization_response=request.url, code_verifier=...)

# 3. store
await credential_store.set(provider_id, {"type": "oauth", "token": token})   # keyring / env file
```

- Localhost callback: bind the callback server to `127.0.0.1` (like opencode's `oauth-callback`), pick a free port; the CLI opens the browser via `webbrowser.open`.
- Support `login` as a **plugin hook** (`providers.ts`, `login.ts` in opencode) — provider-specific params (scopes, endpoints) come from the plugin, keeping core provider-neutral.
- Environment-key fallback: if `OPENAI_API_KEY` (etc.) is set, `agent login` for that provider is unnecessary; `agent_gateway` honors env keys first, then stored creds (LiteLLM reads the standard `*_API_KEY` env vars automatically — see `litellm` skill).

## 3. Credential store

- Interface `agent_core/auth.CredentialStore` with backends: **env** (read-only, primary) → **keyring** (OS keychain) → **file** (`~/.agent-platform/auth.json` mode 0600, test/local only).
- Never store plaintext secrets in the repo, or in `.env` accidentally committed. Keep `.env` git-ignored (plan.md Part K).
- Token refresh: store `refresh_token` + `expires_at`; refresh before expiry in the gateway.

## 4. MCP server auth (later, agent_mcp/oauth.py)

Some external MCP servers require OAuth (see `mcp` skill). Reuse the same OAuth helpers; add an MCP `oauth` capability flag per server config.

## 5. Multi-tenancy (Phase 5, optional)

Keep the auth layer an interface so Keycloak/OIDC (realms, `organization_id` on objects) slots in without rewriting handlers (plan.md Phase 5). For now: single-admin token + per-provider creds is the MVP surface.

## Security rules (plan.md Part K)

- Secrets via env/keyring only; no keys in code or committed files.
- Server binds `127.0.0.1` by default; password required if exposed.
- Hashing: server password compared server-side only; never expose it via API. Auth errors are generic (401 without hinting valid vs invalid).
- TLS in front when exposed (reverse proxy) — never plaintext auth over a public wire.
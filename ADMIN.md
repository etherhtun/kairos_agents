# Kairos Agent — System Design & Admin Guide

Full architecture, infrastructure, and release process for Kairos Agent.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  USER'S MACHINE                     │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │  Kairos Agent                               │   │
│  │                                             │   │
│  │  app.py          macOS menubar (rumps)      │   │
│  │  app_win.py      Windows tray (pystray)     │   │
│  │  app_docker.py   Docker / headless (Flask)  │   │
│  │  server.py       Web dashboard (port 7432)  │   │
│  │                                             │   │
│  │  jobs/upload_sync.py  → sync orchestration  │   │
│  │    sync/sync.py       → fetch + merge       │   │
│  │      classifier.py    → strategy labels     │   │
│  │      brokers/tiger.py → Tiger API           │   │
│  │      brokers/moomoo.py→ Moomoo OpenD API    │   │
│  │                                             │   │
│  │  credentials.json     (upload token)        │   │
│  │  tiger_openapi_config.properties            │   │
│  │      (never leaves machine)                 │   │
│  │                                             │   │
│  │  Output: ~/.kairos-agent/data.json          │   │
│  └───────────────┬─────────────────────────────┘   │
│                  │ POST /api/upload                 │
│                  │ Authorization: Bearer {token}    │
└──────────────────┼──────────────────────────────────┘
                   │
          ┌────────▼────────────────────────────┐
          │     Cloudflare Pages + Workers       │
          │                                     │
          │  /api/upload  (CF Access bypassed)   │
          │    └─ KV: token:{uuid} → {sub,email}│
          │    └─ R2: profiles/{sub}/data.json  │
          │                                     │
          │  /api/trades  (CF Zero Trust auth)   │
          │    └─ R2: profiles/{sub}/data.json  │
          │                                     │
          │  /api/setup-token (CF Zero Trust)   │
          │    └─ KV: generates + stores token  │
          └───────────────────────────────────── ┘
                   │
         ┌─────────▼──────────────┐
         │  Browser (portal)      │
         │  kairos-f3w.pages.dev  │
         └────────────────────────┘
```

---

## Components

### Agent files

| File | Platform | Purpose |
|------|----------|---------|
| `app.py` | macOS + Windows | Unified tray app entry point (pystray — cross-platform) |
| `app_docker.py` | Docker | Headless entry point — no tray, web dashboard on port 7432 |
| `server.py` | All | Web dashboard + API (`/api/status`, `/api/sync`, `/api/reset`, `/api/logs`) |
| `ssl_patch.py` | Docker / CLI | certifi SSL fix — imported by app_docker.py and upload_sync.py |
| `jobs/upload_sync.py` | All | Sync orchestration + R2 upload |
| `jobs/creds.py` | All | Local credential store |
| `sync/sync.py` | All | Trade fetch, merge, analytics |
| `sync/classifier.py` | All | Options strategy classification |
| `sync/brokers/tiger.py` | All | Tiger Brokers API client (TigerOpen SDK) |
| `sync/brokers/moomoo.py` | All | Moomoo API client (Futu OpenD SDK) |
| `sync/brokers/webull.py` | All | Webull (stub — not yet configured) |
| `kairos.spec` | macOS | PyInstaller build config |
| `kairos_win.spec` | Windows | PyInstaller build config (same `app.py` entry point) |
| `Dockerfile` | Docker | Container image definition |
| `docker-compose.yml` | Docker | Compose config (port 7432, volume mount) |
| `update.sh` | Docker | Pull latest image and restart container |
| `com.kairos.menubar.plist` | macOS | launchd template for auto-start on login (edit paths before use) |

**Key design decisions:**
- `app.py` is a **unified cross-platform entry point** using `pystray` for both macOS and Windows — no separate `app_win.py`
- Setup wizard is served via `server.py` at `/setup` — no separate setup.py dialogs
- Sync runs **in-process** (`importlib.reload`) — not subprocess — to avoid spawning a second tray icon
- `BUNDLE_VERSION` in `upload_sync.py` controls re-extraction of `sync/` code from the bundle to `~/.kairos-agent/sync/` — always bump on release when sync code changes
- No `keyring` — credentials in `credentials.json` with `chmod 600` to avoid unsigned-app Keychain prompts
- Tiger credentials parsed manually from `.properties` file (`_load_props()`) — `TigerOpenClientConfig` does not support a `config_file_path` constructor
- Moomoo connects via local OpenD on `127.0.0.1:11111` (macOS/Windows) or `host.docker.internal:11111` (Docker)

### Cloudflare Pages (Portal + API)

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /api/setup-token` | CF Zero Trust JWT | Generate/return upload token |
| `POST /api/upload` | Bearer token (KV) | Receive `data.json` from agent → R2 |
| `GET /api/trades` | CF Zero Trust JWT | Return user's `data.json` from R2 |
| `GET /api/prices` | None (public) | Live market data + technical indicators |
| `GET /api/history` | None (public) | OHLC data from Yahoo Finance |

### Cloudflare Infrastructure

| Resource | Name / ID | Purpose |
|----------|-----------|---------|
| R2 Bucket | `kairos-profiles` | Per-user `data.json` at `profiles/{sub}/data.json` |
| KV Namespace | `TOKENS` (`755904cd9183434bbd6acfa45933dc11`) | Token ↔ sub mapping |
| CF Access App | Kairos Portal | Protects all portal routes |
| CF Access Bypass | Path: `/api/upload` | Allows agent to POST without browser session |

---

## Data Flow

### New User Setup
1. User logs into portal (CF Zero Trust → Google)
2. `GET /api/setup-token` — decodes JWT `sub`, generates UUID token:
   - KV `token:{uuid}` → `{sub, email, created}`
   - KV `profile:{sub}` → `{token, email, created}`
3. User copies token → Kairos Agent setup wizard
4. Token saved to `~/.kairos-agent/credentials.json`

### Sync & Upload
1. Agent loads Tiger credentials from `~/.kairos-agent/tiger_openapi_config.properties`
2. Fetches trades (incremental: last 90 days; first run: full history)
3. Moomoo trades fetched via OpenD if available
4. Classifies strategies, builds analytics JSON
5. Writes `~/.kairos-agent/data.json`
6. `POST /api/upload` with `Authorization: Bearer {token}`
7. Worker validates KV → writes `R2: profiles/{sub}/data.json`

### Reset & Resync
When the user clicks "Reset & Resync" on the dashboard:
1. `data.json` and `leg_cache.json` are deleted locally
2. Empty payload is sent to portal to clear the R2 profile
3. Full sync is triggered immediately

---

## Cloudflare Setup (from scratch)

This covers the full infrastructure setup for both the portal (`kairos/`) and the agent upload endpoint.

### Step 1 — R2 Bucket (legacy shadow copy) + D1 Database (primary)

**D1 (primary store since v2.5):**
```
Cloudflare Dashboard → Workers & Pages → D1 → Create database
  Name: kairos-db
  Run schema.sql to create tables
```

**R2 (legacy shadow copy — kept during rollout):**
```
Cloudflare Dashboard → R2 → Create bucket
  Name: kairos-profiles
```

### Step 2 — KV Namespace (token store)

```
Workers & Pages → KV → Create namespace
  Name: TOKENS
```

Copy the generated **KV Namespace ID** — you'll need it in step 3.

### Step 3 — wrangler.toml (portal repo)

In the `kairos/` repo, update `wrangler.toml` with the KV ID from step 2:

```toml
name = "kairos"
pages_build_output_dir = "."

[[r2_buckets]]
binding    = "PROFILES"
bucket_name = "kairos-profiles"

[[kv_namespaces]]
binding = "TOKENS"
id      = "YOUR_KV_NAMESPACE_ID"
```

### Step 4 — Create Pages Project & connect GitHub

```
Workers & Pages → Pages → Create → Connect to Git
  Repository: etherhtun/kairos
  Framework preset: None
  Build command: (leave empty)
  Build output directory: .
```

Once connected, every push to `main` deploys automatically.

**Or deploy manually** (no git connection needed):
```bash
cd kairos/
npm install -g wrangler
wrangler login
npx wrangler pages deploy . --project-name kairos
```

### Step 5 — Bind resources to the Pages project

```
Workers & Pages → kairos → Settings → Functions → Bindings
  R2 bucket:    PROFILES → kairos-profiles
  KV namespace: TOKENS   → TOKENS
```

> Bindings must be set for both **Production** and **Preview** environments if you use preview deployments.

### Step 6 — CF Zero Trust (access gate)

```
Zero Trust → Access → Applications → Add → Self-hosted
  Application name: Kairos
  Domain: kairos-f3w.pages.dev

  Policy 1 — Allow
    Action: Allow
    Rule: Emails → your@email.com
    (or Emails ending in → yourdomain.com)

  Policy 2 — Bypass (CRITICAL for agent uploads)
    Action: Bypass
    Rule: Everyone
    Path: /api/upload
```

Without the Bypass policy on `/api/upload`, the agent cannot POST trade data — it will get a 403.

### Step 7 — Verify

1. Visit `https://kairos-f3w.pages.dev` — you should be prompted for Google login
2. After login, go to `/connect-tiger` — an upload token should appear
3. Use that token in the agent setup wizard
4. Run a manual sync — check `~/.kairos-agent/logs/sync.log` for `Uploaded: {'ok': True}`

---

## Release Process

### 1. Bump BUNDLE_VERSION (if sync code changed)

In `jobs/upload_sync.py`:
```python
# Bump whenever sync/, brokers/, or classifier.py changes.
# See CLAUDE.md for full history.
BUNDLE_VERSION = '3.7'
```

> `BUNDLE_VERSION` controls re-extraction of `sync/` from the PyInstaller bundle to `~/.kairos-agent/sync/`. Users with older installs automatically get fresh sync code on their next run.

### 2. Tag and push — CI builds both platforms automatically

```bash
git add .
git commit -m "chore: bump to v2.1.6"
git push
git tag v2.1.6
git push origin v2.1.6
```

GitHub Actions (`.github/workflows/release.yml`) will:
- Build `Kairos-v2.1.6-mac.dmg` on a macOS runner
- Build `Kairos-v2.1.6-windows.zip` on a Windows runner
- Build and push `ghcr.io/etherhtun/kairos-agent:v2.1.6` and `:latest`
- Upload all artifacts to the GitHub Release automatically

> `CFBundleShortVersionString` in `kairos.spec` is set automatically from the `APP_VERSION` environment variable passed by CI — no manual edit needed.

### 3. Portal download links

The connect pages (`connect-tiger.html`, `connect-moomoo.html`) fetch the latest release tag dynamically from the GitHub API on page load — **no manual update needed**.

### 4. Update Docker container (users)

Users running Docker can update with:
```bash
./update.sh
# or manually:
docker rm -f kairos-agent
docker pull ghcr.io/etherhtun/kairos-agent:latest
docker run -d --name kairos-agent --restart unless-stopped \
  -p 127.0.0.1:7432:7432 -v ~/.kairos-agent:/root/.kairos-agent \
  ghcr.io/etherhtun/kairos-agent:latest
```

---

## CSS Variable Reference

All variables are defined in `kairos/assets/css/variables.css`.

| Variable | Value | Notes |
|----------|-------|-------|
| `--accent` | `var(--brand-green)` | Alias — use for primary actions |
| `--brand-green` | `#66bb6a` | Primary brand colour |
| `--brand-blue` | `#4fc3f7` | Secondary colour |
| `--panel` | `#161b22` dark / `#ffffff` light | Card/modal backgrounds |
| `--panel2` | `#1c2333` dark / `#f6f8fa` light | Nested card backgrounds |
| `--surface` | `var(--panel)` | Alias for panel |
| `--surface-2` | `var(--panel2)` | Alias for panel2 |
| `--text` | `#e6edf3` dark / `#1f2328` light | Primary text |
| `--text-primary` | `var(--text)` | Alias for text |
| `--text-muted` | `#8b949e` dark / `#656d76` light | Secondary text |
| `--warn` | `var(--brand-gold)` | Warning colour alias |

---

## User Management

### View all profiles
```
Cloudflare Dashboard → R2 → kairos-profiles → Browse → profiles/
```

### Delete a user's data
```
R2 → kairos-profiles → profiles/{sub}/data.json → Delete
```

### Revoke a user's upload token
```
Workers & Pages → KV → TOKENS
→ Delete: token:{uuid}
→ Delete: profile:{sub}
```
User must re-run Setup from the portal connect page to get a new token.

---

## Logs & Error Codes

Agent sync log: `~/.kairos-agent/logs/sync.log`

| Code | Step | Meaning |
|------|------|---------|
| 0 | done | Sync and upload succeeded |
| -2 | sync | Sync module threw an exception |
| -3 | upload | `data.json` missing after sync |
| -4 | upload | `upload_token` not configured — run Setup |
| -5 | upload_failed | HTTP error from `/api/upload` |

---

## Security Notes

- Upload tokens are UUIDs — 1 per user, unguessable
- Tiger credentials never leave the user's machine
- R2 data is isolated per `sub` — users cannot access each other's data
- CF Zero Trust enforces auth on all portal routes
- `/api/upload` bypass is scoped to that path only — rest of portal remains protected
- Local credentials stored with `chmod 600` (macOS/Linux) or restricted ACL (Windows)

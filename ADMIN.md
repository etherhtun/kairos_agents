# Kairos — System Design & Admin Guide

This document covers the full architecture, infrastructure, and release process for Kairos.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  USER'S MACHINE                     │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │  Kairos Agent (macOS menubar / Win tray)    │   │
│  │  app.py / app_win.py                        │   │
│  │                                             │   │
│  │  jobs/upload_sync.py                        │   │
│  │    └─ sync/sync.py ──► classifier.py        │   │
│  │         └─ brokers/tiger.py                 │   │
│  │              └─ tiger_openapi_config.props  │   │
│  │                   (never leaves machine)    │   │
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

### 1. Kairos Agent

| File | Platform | Purpose |
|------|----------|---------|
| `app.py` | macOS | Menubar app entry point (rumps) |
| `app_win.py` | Windows | System tray entry point (pystray) |
| `jobs/setup.py` | macOS | Setup wizard (rumps dialogs + osascript) |
| `jobs/setup_win.py` | Windows | Setup wizard (tkinter) |
| `jobs/upload_sync.py` | Both | Sync orchestration + R2 upload |
| `jobs/creds.py` | Both | Local credential store |
| `sync/sync.py` | Both | Trade fetch, merge, analytics |
| `sync/classifier.py` | Both | Options strategy classification |
| `sync/brokers/tiger.py` | Both | Tiger Brokers API client |
| `kairos.spec` | macOS | PyInstaller build config |
| `kairos_win.spec` | Windows | PyInstaller build config |

**Key design decisions:**
- Sync runs **in-process** (`importlib.reload`) — not subprocess — to avoid spawning a second menubar icon
- `BUNDLE_VERSION` in `upload_sync.py` triggers force-refresh of `~/.kairos-agent/sync/` on app update — always bump this on release
- No `keyring` — credentials stored in `credentials.json` with `chmod 600` to avoid unsigned-app keychain prompts
- Tiger credentials parsed manually from `.properties` file (`_load_props()`) — `TigerOpenClientConfig` does not support `config_file_path`
- macOS setup timer fires from main thread via `rumps.Timer` (not `threading.Thread`) — required for AppKit safety

### 2. Cloudflare Pages (Portal + API)

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /api/setup-token` | CF Zero Trust JWT | Generate/return upload token |
| `POST /api/upload` | Bearer token (KV) | Receive `data.json` from agent → R2 |
| `GET /api/trades` | CF Zero Trust JWT | Return user's `data.json` from R2 |
| `GET /api/prices` | None (public) | Live market data + technical indicators |
| `GET /api/history` | None (public) | OHLC data from Yahoo Finance |

### 3. Cloudflare Infrastructure

| Resource | Name / ID | Purpose |
|----------|-----------|---------|
| R2 Bucket | `kairos-profiles` | Per-user `data.json` — path: `profiles/{sub}/data.json` |
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
2. Fetches trades (incremental: last 30 days; first run: 3 years)
3. Classifies strategies, builds analytics JSON
4. Writes `~/.kairos-agent/data.json`
5. `POST /api/upload` with `Authorization: Bearer {token}`
6. Worker validates KV → writes `R2: profiles/{sub}/data.json`

### Portal Data Load
1. Browser `GET /api/trades` (CF Access cookie)
2. Worker decodes JWT → extracts `sub`
3. Reads `R2: profiles/{sub}/data.json` → returns to browser

---

## Cloudflare Setup (from scratch)

### Step 1 — R2 Bucket
```
Dashboard → R2 → Create bucket → kairos-profiles
```

### Step 2 — KV Namespace
```
Workers & Pages → KV → Create namespace → TOKENS
```
Copy the KV ID into `wrangler.toml`.

### Step 3 — wrangler.toml
```toml
name = "kairos"
pages_build_output_dir = "."

[[r2_buckets]]
binding = "PROFILES"
bucket_name = "kairos-profiles"

[[kv_namespaces]]
binding = "TOKENS"
id = "755904cd9183434bbd6acfa45933dc11"
```

### Step 4 — Bind to Pages Project
```
Workers & Pages → kairos-f3w → Settings → Functions
→ R2 bindings: PROFILES → kairos-profiles
→ KV bindings: TOKENS → TOKENS
```

### Step 5 — CF Zero Trust
```
Zero Trust → Access → Applications → Self-hosted
  Domain: kairos-f3w.pages.dev
  Policy: Allow (Google auth)
  Bypass policy: Path /api/upload (allows agent uploads)
```

---

## Release Process

### 1. Bump versions

In `jobs/upload_sync.py`:
```python
BUNDLE_VERSION = '1.5'   # ← increment every release
```

In `kairos.spec`:
```python
'CFBundleShortVersionString': '1.2.0',
```

### 2. Push a version tag — CI builds both platforms automatically

```bash
git add .
git commit -m "chore: bump to v1.2.0"
git push
git tag v1.2.0
git push origin v1.2.0
```

GitHub Actions (`.github/workflows/release.yml`) will:
- Build `Kairos-v1.2.0-mac.dmg` on a macOS runner
- Build `Kairos-v1.2.0-windows.zip` on a Windows runner
- Upload both to the GitHub Release automatically

### 3. Update portal download links

In `kairos/connect-tiger.html` — update both download hrefs to the new version tag.

In `kairos/index.html` — update the version label in the Connect Broker modal.

Commit and push → Cloudflare Pages deploys automatically.

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
User must re-run Setup from `/connect-tiger` to get a new token.

---

## Logs & Error Codes

Agent sync logs: `~/.kairos-agent/logs/sync.log`

| Code | Step | Meaning |
|------|------|---------|
| -2 | sync | Sync module threw an exception |
| -3 | upload | `data.json` missing after sync |
| -4 | upload | `upload_token` not configured |
| -5 | upload_failed | HTTP error from `/api/upload` |

---

## Security Notes

- Upload tokens are UUIDs — 1 per user, unguessable
- Tiger credentials never leave the user's machine
- R2 data is isolated per `sub` — users cannot access each other's data
- CF Zero Trust enforces auth on all portal routes
- `/api/upload` bypass is scoped to that path only — portal remains protected
- Local credentials stored with `chmod 600` (macOS/Linux) or restricted ACL (Windows)

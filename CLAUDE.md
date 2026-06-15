# CLAUDE.md — Kairos Agent

Developer reference for AI-assisted work on this repo.

---

## What This Repo Does

**Kairos Agent** is a local Python daemon that:

1. Connects to **Tiger Brokers** (TigerOpen SDK) and **Moomoo/Futu** (OpenD SDK) to fetch trades, positions, account balances, and dividends
2. Classifies options strategies (IC, CSP, CC, etc.) via `sync/classifier.py`
3. Writes `~/.kairos-agent/data.json` then POSTs it to the Kairos portal (`/api/sync`) with a Bearer token
4. Runs as a **macOS menu-bar app** (pystray), **Windows tray app**, or **Docker headless container** with a web dashboard on port 7432

---

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Tiger API | TigerOpen SDK (`tigeropen`) |
| Moomoo API | Moomoo SDK (`moomoo-api`) via local **OpenD** on `127.0.0.1:11111` |
| macOS/Windows UI | `pystray` (unified cross-platform tray) |
| Docker UI | Flask web dashboard (`app_docker.py` → `server.py`) |
| Upload | `urllib.request` POST to `/api/sync` with Bearer token |
| Credentials | `~/.kairos-agent/credentials.json` (chmod 600, never committed) |
| Tiger config | `~/.kairos-agent/tiger_openapi_config.properties` |

---

## Project Layout

```
jobs/
├── upload_sync.py   ← orchestrator: runs sync then POSTs data.json
└── creds.py         ← reads credentials.json

sync/
├── sync.py          ← fetch + merge all brokers
├── classifier.py    ← options strategy classification
└── brokers/
    ├── base.py      ← BrokerBase, Position, Trade, AccountSummary dataclasses
    ├── tiger.py     ← Tiger Brokers API client
    ├── moomoo.py    ← Moomoo/Futu OpenD API client
    └── webull.py    ← stub (not yet configured)

server.py            ← Flask web dashboard (port 7432)
app.py               ← macOS/Windows tray entry point (pystray)
app_docker.py        ← Docker headless entry point
config.py            ← UPLOAD_URL, UPLOAD_URL_V1 constants
ssl_patch.py         ← certifi SSL fix (Docker / CLI)
Dockerfile
docker-compose.yml
update.sh            ← docker pull + restart helper
```

---

## Bundle Version System

`BUNDLE_VERSION` in `jobs/upload_sync.py` controls re-extraction of `sync/` code from the PyInstaller bundle to `~/.kairos-agent/sync/`. **Always bump on any change to `sync/`, `brokers/`, or `classifier.py`.**

```python
# History (condensed):
# 2.7  dividend collection: Tiger + Moomoo get_dividends
# 2.9  Tiger: use get_fund_details for dividends
# 3.0  Tiger: remove fund_type param, filter client-side
# 3.1  Tiger: rate-limit fix (7s sleep between pages)
# 3.3  Moomoo: fix get_acc_cashflow → get_acc_cash_flow typo
# 3.4  Tiger: fix symbol from contract_name/desc, date from business_date
# 3.5  Moomoo: get_acc_cash_flow takes single clearing_date — iterate per day
# 3.6  Moomoo: early-exit for FUTUSGNP (turned out unnecessary — reverted in 3.7)
# 3.7  Moomoo: fix column names cashflow_type/cashflow_amount/cashflow_remark
BUNDLE_VERSION = '3.7'
```

---

## Broker API Reference

### Tiger (`sync/brokers/tiger.py`)

**Dividends — `get_fund_details()`**

| SDK detail | Value |
|---|---|
| Method | `self._client.get_fund_details(account=..., fund_type='Dividend', start_date=..., end_date=..., count=100)` |
| Pagination | `offset` param; loop until fewer rows than `count` returned |
| Rate limit | 7s sleep between pages (Tiger enforces strict limits) |
| Symbol column | No `symbol` col — use `contract_name` or `desc` (fallback chain) |
| Date column | `business_date` (not `pay_date`, `date`, or `timestamp`) |
| Amount column | `net_amount` → `cash_amount` → `amount` (fallback chain) |
| Fund type filter | Pass `fund_type='Dividend'` to SDK **and** filter rows client-side for keywords: `dividend`, `div`, `distribution` (SDK may return mixed results) |

### Moomoo (`sync/brokers/moomoo.py`)

**Connection**

- OpenD must be running locally on `127.0.0.1:11111` (or `host.docker.internal:11111` in Docker)
- Auto-detects security firm by iterating `_FIRMS` list and checking `get_acc_list()` for a REAL account
- Connected firm is stored in `self._security_firm` (e.g. `'FUTUSG'`)
- For **FUTUSG (Singapore)** accounts: `TrdMarket.ALL` must be used (not `TrdMarket.US`); fall back gracefully if SDK doesn't have `ALL`

**Dividends — `get_acc_cash_flow()`**

| SDK detail | Value |
|---|---|
| Method | `ctx.get_acc_cash_flow(clearing_date='YYYY-MM-DD', trd_env=TrdEnv.REAL, acc_id=...)` |
| Date param | **Single date only** — no range. Must iterate per calendar day. |
| Type filter | `cashflow_type` column (not `cash_flow_type`) — filter for `'DIVIDEND'` or `'DIV'` in value |
| Withholding tax | `cashflow_type` contains `'TAX'` or `'WITHHOLDING'` — collect separately per symbol and **subtract from gross** to get net amount stored in `total_amount` |
| Amount column | `cashflow_amount` (not `cash_flow_value` or `amount`) |
| Symbol | Parse from `cashflow_remark` — first word is always the ticker, e.g. `"JEPI 14.92650... SHARES DIVIDENDS ..."` |
| Rate limit | 0.05s sleep per day — ~20 calls/sec, well within Moomoo limits |
| Max lookback | Cap at 365 days to keep runtime reasonable |

**Previous wrong assumptions (do not revert):**
- ❌ `cash_flow_type` — wrong column name
- ❌ `cash_flow_value` — wrong column name
- ❌ `remark` — wrong column name (it's `cashflow_remark`)
- ❌ `FUTUSGNP` as security firm — not a valid SDK enum; correct value is `FUTUSG`
- ❌ `get_acc_cashflow` — typo; correct name is `get_acc_cash_flow`

---

## Auth & Upload

- Upload token is a UUID stored in `~/.kairos-agent/credentials.json`
- Agent POSTs `~/.kairos-agent/data.json` to `/api/sync` (primary) with `Authorization: Bearer {token}`
- Falls back to `/api/upload` (legacy) on 404
- Token is validated server-side against Cloudflare KV (`TOKENS`)
- Upload response includes `written: {trades, positions, accounts, dividends}` and `d1_ok: true/false` — check these for silent failures

---

## Docker Workflow

```bash
# First run (no existing container)
docker compose up -d

# Update to latest image
./update.sh
# or manually:
docker pull ghcr.io/etherhtun/kairos-agent:latest
docker compose down && docker compose up -d

# If container exists but wasn't started by compose (name conflict):
docker stop kairos-agent && docker rm kairos-agent
docker compose up -d

# Logs
docker logs kairos-agent --tail 50 -f

# Run a test script inside the container
docker cp test_moomoo_dividends2.py kairos-agent:/tmp/
docker exec -it kairos-agent python3 /tmp/test_moomoo_dividends2.py
```

**Version label:** `APP_VERSION` build arg already includes the `v` prefix (e.g. `v2.1.6`). Do **not** prepend another `v` in code — produces `vv2.1.6`.

---

## Release Process

1. Make changes to `sync/` or `brokers/` → bump `BUNDLE_VERSION` in `jobs/upload_sync.py`
2. Commit and push to `main`
3. Tag and push:
   ```bash
   git tag v2.1.6
   git push origin v2.1.6
   ```
4. GitHub Actions (`.github/workflows/release.yml`) auto-builds:
   - macOS DMG
   - Windows ZIP
   - Docker multi-arch image → `ghcr.io/etherhtun/kairos-agent:v2.1.6` and `:latest`

---

## Troubleshooting

### Dividends show $0.00 / 0 payments

**Check 1 — Tiger**

Run a sync and look for:
```
[tiger] Dividend rows before dedup: 0
```
If 0, Tiger API returned nothing. Possible causes:
- `fund_type='Dividend'` returning empty — Tiger may not have records in the date range
- Column mismatch: symbol must come from `contract_name` or `desc` (not `symbol`); date from `business_date`

**Check 2 — Moomoo**

Run a sync and look for:
```
[moomoo] Dividends: 0 records found
```
If 0, likely causes:
- OpenD not running — check `127.0.0.1:11111` is accessible
- Wrong column names in older bundle — ensure bundle is at 3.7+
- `get_acc_cash_flow` returning `ret=-1` — check Docker logs for the error message

Test interactively:
```bash
docker cp test_moomoo_dividends2.py kairos-agent:/tmp/
docker exec -it kairos-agent python3 /tmp/test_moomoo_dividends2.py
```

**Check 3 — Portal not reading dividends**

Query D1 directly via Cloudflare dashboard or check the upload response for `"dividends": 0` in `written`.

---

### Moomoo `get_acc_cash_flow` returns `ret=-1`

Common error messages and fixes:

| Error message | Cause | Fix |
|---|---|---|
| `get_acc_cashflow not in SDK` | Typo — wrong method name | Use `get_acc_cash_flow` (underscore between `acc` and `cash`) |
| `No available real accounts with HK market authority in FUTU HK` | Wrong security firm (`FUTUSGNP` not valid) or wrong `filter_trdmarket` | Use `security_firm=ft.SecurityFirm.FUTUSG`; ensure `TrdMarket.ALL` |
| `cashflow unavailable, ret=-1` with empty clearing_date | Passing `start`/`end` kwargs instead of `clearing_date` | Method only accepts single `clearing_date='YYYY-MM-DD'` — iterate per day |

---

### Tiger rate limit / `get_fund_details` returns nothing after page 1

Tiger enforces a strict rate limit on `get_fund_details`. Sleep **7 seconds** between pages. If it still fails, reduce `count` per page from 100 to 50.

---

### Docker container name conflict

```
Error: Conflict. The container name "/kairos-agent" is already in use
```
Container was started manually (not via compose). Fix:
```bash
docker stop kairos-agent && docker rm kairos-agent
docker compose up -d
```

---

### Dashboard shows `vv2.1.x` (double v)

`APP_VERSION` env var already contains the `v` prefix. In `server.py`:
```python
# Wrong — produces vv2.1.x:
ver_label = f'v{app_ver}'
# Correct:
ver_label = app_ver if app_ver else 'Kairos Agent'
```

---

### Upload succeeds but D1 shows 0 dividends written

Check the upload response body — it now includes:
```json
{ "ok": true, "written": { "trades": 42, "dividends": 0 }, "d1_ok": true }
```
If `d1_ok: false`, there's a D1 error in `d1_error`. If `dividends: 0` with `d1_ok: true`, the broker returned 0 dividend records — debug at the broker level, not the upload level.

---

## Security

- Tiger credentials (`tiger_openapi_config.properties`) never leave the user's machine
- Upload token in `credentials.json` — `chmod 600`, never committed, never logged
- Tiger client is **read-only** — no `place_order()` permission anywhere in this codebase
- D1 data is isolated per `user_sub` — enforced on the portal side, not here

# Kairos Agent

Kairos Agent collects your trade data from Tiger Brokers and Moomoo (Futu) and uploads it to Cloudflare. The [Kairos portal](https://kairos-f3w.pages.dev) reads that data and displays your private trading journal.

Available as a **macOS menubar app**, **Windows system tray app**, or **Docker container**.

---

## How It Works

```
Tiger Brokers API
Moomoo (via local OpenD app)
      │
      │  (credentials stay on your machine)
      ▼
Kairos Agent
  1. Fetches trade history from each broker
  2. Classifies option strategies (IC, BPS, BCS, etc.)
  3. Builds analytics → ~/.kairos-agent/data.json
  4. Uploads data.json to Cloudflare R2

        POST /api/upload
              │
              ▼
        Cloudflare R2
        profiles/{you}/data.json
              │
        GET /api/trades
              │
              ▼
        Kairos Portal
        kairos-f3w.pages.dev
```

**Your broker credentials never leave your machine.** Only the processed trade analytics JSON is uploaded to Cloudflare.

---

## What You Need to Create Locally

The repo is **clone-and-go** for building. To actually **run** the agent, you need two credential files that are gitignored and never committed:

| File | Where it comes from | Created by |
|------|-------------------|------------|
| `~/.kairos-agent/credentials.json` | Your upload token from the portal | Setup wizard (automatic) or manually |
| `~/.kairos-agent/tiger_openapi_config.properties` | Download from Tiger app → API Management | Setup wizard (automatic) or manually |

**Using the app (macOS/Windows/Docker):** The setup wizard creates both files for you — just paste your token and upload the properties file when prompted. Nothing to do manually.

**Running `sync.py` directly (CLI / dev):** You have two options:

Option A — Properties file (recommended):
```bash
mkdir -p ~/.kairos-agent
cp /path/to/tiger_openapi_config.properties ~/.kairos-agent/
echo '{"upload_token": "your-token-here"}' > ~/.kairos-agent/credentials.json
chmod 600 ~/.kairos-agent/credentials.json ~/.kairos-agent/tiger_openapi_config.properties
```

Option B — `.env` file in the repo root (fallback, used if properties file is absent):
```bash
# Copy the example and fill in your values
cp .env.example .env
# Edit .env: set TIGER_ID, TIGER_ACCOUNT, TIGER_LICENSE, TIGER_ENV
# The private key must still be in tiger_openapi_config.properties
```

> **Moomoo** needs no config file — OpenD on your machine is detected automatically on `127.0.0.1:11111`.

---

## What the Agent Does

- Connects to Tiger Brokers using your local `tiger_openapi_config.properties`
- Connects to Moomoo via your locally running OpenD app (no config file needed)
- First run: fetches full trade history; subsequent runs: last 90 days (incremental)
- Classifies option strategies (Iron Condor, Bull Put Spread, Bear Call Spread, etc.)
- Builds a `data.json` analytics file locally at `~/.kairos-agent/data.json`
- Uploads `data.json` to your private Cloudflare R2 profile slot using your upload token
- Auto-syncs once daily at 4:30 PM on weekdays
- Lives in the menubar (macOS) or system tray (Windows) — no Dock icon, no extra windows

## What the Portal Does

- Reads `data.json` from your Cloudflare R2 slot (via `GET /api/trades`)
- Displays your trade journal, P&L charts, strategy breakdown, open positions
- Requires Google login — each user only sees their own data

---

## Requirements

| Platform | Requirement |
|----------|-------------|
| macOS | macOS 12 (Monterey) or later · Apple Silicon or Intel |
| Windows | Windows 10 or 11 (64-bit) |
| Docker | Docker Desktop (any platform) |
| All | Tiger Brokers account with API access enabled |
| All | Kairos portal account at `kairos-f3w.pages.dev` |
| Moomoo | OpenD desktop app installed and running locally |

---

## Installation — macOS / Windows App

### Step 1 — Get your upload token

1. Log in to [kairos-f3w.pages.dev](https://kairos-f3w.pages.dev)
2. Click **Connect Broker** → **Tiger Brokers** (or Moomoo) → **Go to Connect page**
3. Copy your upload token — this links the agent to your portal account

### Step 2 — Download the app

| Platform | Download |
|----------|----------|
| macOS | [Kairos-v1.5.5-mac.dmg](https://github.com/etherhtun/kairos_agents/releases/download/v1.5.5/Kairos-v1.5.5-mac.dmg) |
| Windows | [Kairos-v1.5.5-windows.zip](https://github.com/etherhtun/kairos_agents/releases/download/v1.5.5/Kairos-v1.5.5-windows.zip) |

Latest release: [github.com/etherhtun/kairos_agents/releases/latest](https://github.com/etherhtun/kairos_agents/releases/latest)

### macOS Install

1. Open the `.dmg` → drag **Kairos** to **Applications**
2. Eject the disk image
3. Open Kairos from Applications or Launchpad

**Gatekeeper warning** ("Kairos" Not Opened):
- **System Settings → Privacy & Security → Open Anyway**, or
- Right-click Kairos in Finder → **Open** → **Open**

### Windows Install

1. Extract `Kairos-v1.5.5-windows.zip`
2. Move the `Kairos` folder anywhere (e.g. `C:\Program Files\Kairos`)
3. Run `Kairos.exe`

**SmartScreen warning:** Click **More info → Run anyway**

Auto-start on login: right-click `Kairos.exe` → Create shortcut → place in:
```
C:\Users\<you>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup
```

### Step 3 — Setup Wizard

On first launch the setup wizard opens automatically:

1. **Upload token** — paste the token copied from the portal connect page
2. **Tiger config file** — select `tiger_openapi_config.properties` from Tiger app → **API Management → Download Config**

Moomoo is detected automatically via OpenD — no config file needed.

Once saved, the agent runs a full sync and uploads your data. Open the portal to see your trades.

---

## Installation — Docker

Docker is the recommended option for always-on syncing (NAS, server, or headless machine).

### Step 1 — Get your upload token

Same as above — log into the portal and copy your token from the connect page.

### Step 2 — Run the container

```bash
docker run -d \
  --name kairos-agent \
  --restart unless-stopped \
  -p 127.0.0.1:7432:7432 \
  -v ~/.kairos-agent:/root/.kairos-agent \
  ghcr.io/etherhtun/kairos-agent:latest
```

On Windows replace `~/.kairos-agent` with `%USERPROFILE%\.kairos-agent` and paste as one line.

### Step 3 — Configure

Open the setup page at [http://127.0.0.1:7432/setup](http://127.0.0.1:7432/setup), paste your token, and upload your Tiger config file.

### Updating to the latest version

```bash
docker rm -f kairos-agent
docker pull ghcr.io/etherhtun/kairos-agent:latest
docker run -d \
  --name kairos-agent \
  --restart unless-stopped \
  -p 127.0.0.1:7432:7432 \
  -v ~/.kairos-agent:/root/.kairos-agent \
  ghcr.io/etherhtun/kairos-agent:latest
```

Your credentials and data in `~/.kairos-agent` are preserved across updates.

---

## Menubar / Tray Menu

| Item | Description |
|------|-------------|
| Last sync | Time of last successful sync |
| Sync now | Manually trigger a sync immediately |
| Auto-sync at 4:30 PM | Toggle daily weekday auto-sync |
| Open portal | Open `kairos-f3w.pages.dev` in your browser |
| View logs | Open `~/.kairos-agent/logs/sync.log` |
| Setup / reconfigure | Re-run the setup wizard |
| Quit | Exit the agent |

For Docker, use the web dashboard at `http://127.0.0.1:7432` to trigger syncs and view logs.

---

## Local File Storage

| File | Purpose |
|------|---------|
| `~/.kairos-agent/credentials.json` | Upload token (local only, chmod 600) |
| `~/.kairos-agent/tiger_openapi_config.properties` | Tiger API credentials (never uploaded) |
| `~/.kairos-agent/data.json` | Latest trade analytics (uploaded to Cloudflare) |
| `~/.kairos-agent/data.backup.json` | Backup of previous sync |
| `~/.kairos-agent/logs/sync.log` | Sync run history |
| `~/.kairos-agent/state.json` | App state (last sync date, preferences) |
| `~/.kairos-agent/sync/` | Sync code extracted from app bundle |
| `~/.kairos-agent/sync/leg_cache.json` | Tiger multi-leg order cache (speeds up incremental runs) |

---

## Troubleshooting

**Setup wizard doesn't appear on first launch**
→ Delete `~/.kairos-agent/state.json` and relaunch the app

**Tiger connection error / empty data**
→ Re-run Setup and re-select `tiger_openapi_config.properties`
→ Check Tiger app → API Management that API access is enabled

**Moomoo shows no trades**
→ Make sure OpenD is running on your machine (Docker connects via `host.docker.internal:11111`)
→ Verify OpenD is logged in and trade history permission is enabled in Futu

**Upload fails with 401 Unauthorized**
→ Token may have changed — go to portal connect page, copy current token, re-run Setup

**Portal shows "No data yet" after sync**
→ Check `~/.kairos-agent/logs/sync.log` for upload errors
→ Confirm token in the wizard matches the portal connect page

**Sync is slow after Reset & Resync**
→ Expected — `leg_cache.json` is cleared, so all Tiger multi-leg details must be re-fetched. One-time cost; cache rebuilds on completion

**macOS: blocked by Gatekeeper**
→ System Settings → Privacy & Security → Open Anyway

**Windows: blocked by SmartScreen**
→ More info → Run anyway

---

## Building from Source

```bash
git clone https://github.com/etherhtun/kairos_agents.git
cd kairos_agents

# macOS
pip install rumps pyinstaller certifi
pip install -r requirements.txt
APP_VERSION=1.5.5 python -m PyInstaller kairos.spec --noconfirm
# Output: dist/Kairos.app

# Windows
pip install pystray pillow plyer pyinstaller certifi
pip install -r requirements_win.txt
set APP_VERSION=1.5.5
python -m PyInstaller kairos_win.spec --noconfirm
# Output: dist/Kairos/Kairos.exe
```

Releases are built automatically via [GitHub Actions](.github/workflows/release.yml) when a version tag is pushed.

---

## Disclaimer

> Kairos is a personal trading journal tool only. It does not provide financial advice, investment recommendations, or trading signals. All data is for informational and record-keeping purposes only.
>
> You are solely responsible for your trading decisions. Past performance does not guarantee future results.
>
> Kairos is not affiliated with Tiger Brokers, Moomoo, Futu, Webull, or any other brokerage.

---

## License

MIT

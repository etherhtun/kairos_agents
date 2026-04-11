# Kairos Agent

Kairos Agent is a macOS menubar / Windows tray app that collects your trade data from Tiger Brokers and uploads it to Cloudflare. The [Kairos portal](https://kairos-f3w.pages.dev) then reads that data from Cloudflare and displays your trading journal.

---

## How It Works

```
Tiger Brokers API
      │
      │  (your credentials stay on your machine)
      ▼
Kairos Agent  ──────────────────────────────────────────────────┐
  1. Fetches trade history from Tiger                           │
  2. Classifies option strategies (IC, BPS, BCS, etc.)         │
  3. Builds analytics → ~/.kairos-agent/data.json              │
  4. Uploads data.json to Cloudflare R2                         │
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
                                                  (reads from Cloudflare,
                                                   displays your journal)
```

**Your broker credentials never leave your machine.** Only the processed trade analytics JSON is uploaded to Cloudflare.

---

## What the Agent Does

- Connects to Tiger Brokers using your local `tiger_openapi_config.properties`
- Fetches trade history (first run: full history; subsequent runs: last 30 days)
- Classifies option strategies (Iron Condor, Bull Put Spread, Bear Call Spread, etc.)
- Builds a `data.json` analytics file locally at `~/.kairos-agent/data.json`
- Uploads `data.json` to your private Cloudflare R2 profile slot using your upload token
- Auto-syncs once daily at 4:30 PM on weekdays
- Lives in the menubar (macOS) or system tray (Windows) — no Dock icon, no extra windows

## What the Portal Does

- Reads `data.json` from your Cloudflare R2 profile slot (via `GET /api/trades`)
- Displays your trade journal, P&L charts, strategy breakdown, open positions
- Requires login (Google) — each user only sees their own data

---

## Requirements

| Platform | Requirement |
|----------|-------------|
| macOS    | macOS 12 (Monterey) or later · Apple Silicon or Intel |
| Windows  | Windows 10 or 11 (64-bit) |
| Both     | Tiger Brokers account with API access enabled |
| Both     | Kairos portal account at `kairos-f3w.pages.dev` |

---

## Installation

### Step 1 — Get your upload token

1. Log in to [kairos-f3w.pages.dev](https://kairos-f3w.pages.dev)
2. Click **Connect Broker** → **Tiger Brokers** → **Go to Connect page**
3. Copy your upload token — this links the agent to your portal account

### Step 2 — Download the app

| Platform | Download |
|----------|----------|
| macOS    | [Kairos-v1.1.1-mac.dmg](https://github.com/etherhtun/kairos-agent/releases/download/v1.1.1/Kairos-v1.1.1-mac.dmg) |
| Windows  | [Kairos-v1.1.1-windows.zip](https://github.com/etherhtun/kairos-agent/releases/download/v1.1.1/Kairos-v1.1.1-windows.zip) |

Latest release: [github.com/etherhtun/kairos-agent/releases/latest](https://github.com/etherhtun/kairos-agent/releases/latest)

---

### macOS Install

1. Open the `.dmg` → drag **Kairos** to **Applications**
2. Eject the disk image
3. Open Kairos from Applications or Launchpad

**macOS Gatekeeper (unsigned app warning):**
> "Kairos" Not Opened — Apple could not verify...

Fix: **System Settings → Privacy & Security → Open Anyway**
Or: Right-click Kairos in Finder → **Open** → **Open**

---

### Windows Install

1. Extract `Kairos-v1.1.1-windows.zip`
2. Move the `Kairos` folder anywhere (e.g. `C:\Program Files\Kairos`)
3. Run `Kairos.exe`

**Windows Defender SmartScreen warning:** Click **More info → Run anyway**

To auto-start on login: right-click `Kairos.exe` → Create shortcut → paste shortcut into:
```
C:\Users\<you>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup
```

---

### Step 3 — Setup Wizard

On first launch the setup wizard appears automatically:

1. **Upload token** — paste the token you copied from the portal `/connect-tiger` page
2. **Tiger config file** — select `tiger_openapi_config.properties` downloaded from Tiger app → **API Management → Download Config**

Once complete, the agent runs a full sync immediately and uploads your data to Cloudflare. Open the portal to see your trades.

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

---

## Local File Storage

| File | Purpose |
|------|---------|
| `~/.kairos-agent/credentials.json` | Upload token (local only) |
| `~/.kairos-agent/tiger_openapi_config.properties` | Tiger API credentials (local only, never uploaded) |
| `~/.kairos-agent/data.json` | Latest trade analytics (uploaded to Cloudflare) |
| `~/.kairos-agent/data.backup.json` | Backup of previous sync |
| `~/.kairos-agent/logs/sync.log` | Sync run history |
| `~/.kairos-agent/state.json` | App state (last sync date, preferences) |
| `~/.kairos-agent/sync/` | Sync code extracted from app bundle |

Credentials files are stored with restricted permissions (`chmod 600`). Broker keys are never transmitted anywhere.

---

## Disclaimer

> Kairos is a personal trading journal tool only. It does not provide financial advice, investment recommendations, or trading signals. All data displayed is for informational and record-keeping purposes only.
>
> You are solely responsible for your trading decisions. Past performance does not guarantee future results.
>
> Kairos is not affiliated with Tiger Brokers, Webull, MooMoo, or any other brokerage.

---

## Troubleshooting

**Setup wizard doesn't appear on first launch**
→ Delete `~/.kairos-agent/state.json` and relaunch the app

**Tiger ID empty / connection error**
→ Re-run Setup and re-select your `tiger_openapi_config.properties`
→ Check Tiger app → API Management that API access is enabled

**Upload fails with 401 Unauthorized**
→ Your token may have changed — go to portal `/connect-tiger`, copy the current token, re-run Setup

**Portal shows "No data yet" after sync**
→ Open View logs and check for upload errors
→ Confirm the token in the wizard matches the one shown on the portal

**macOS: blocked by Gatekeeper**
→ System Settings → Privacy & Security → Open Anyway

**Windows: blocked by SmartScreen**
→ More info → Run anyway

---

## Building from Source

```bash
git clone https://github.com/etherhtun/kairos-agent.git
cd kairos-agent

# macOS
pip install rumps pyinstaller
pip install -r sync/requirements.txt
python -m PyInstaller kairos.spec --noconfirm
# Output: dist/Kairos.app

# Windows
pip install pystray pillow plyer pyinstaller
pip install -r sync/requirements.txt
python -m PyInstaller kairos_win.spec --noconfirm
# Output: dist/Kairos/Kairos.exe
```

Releases are built automatically via [GitHub Actions](.github/workflows/release.yml) when a version tag is pushed.

---

## License

MIT

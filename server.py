"""
server.py
=========
Local HTTP server for Kairos Agent.
Serves the setup wizard and live dashboard at http://127.0.0.1:7432

Security mitigations built in:
  1. Binds to 127.0.0.1 only — not reachable from other machines
  2. Host header validation — blocks DNS rebinding attacks
  3. CSRF token (secrets.token_hex) — required on all state-changing POSTs
  4. Tiger credentials are write-only — never sent back to the browser
  5. All user/broker data is HTML-escaped before rendering
"""

import datetime
import html
import http.server
import io
import json
import os
import pathlib
import secrets
import socketserver
import stat
import threading
import urllib.parse

from jobs import creds
from jobs.upload_sync import last_data_age_hours

# ── Module-level state (set by start()) ──────────────────────────────────────
_PORT      = 7432
_state     = {}
_save_fn   = None
_sync_fn   = None
_log_file  = None

# One CSRF token per process lifetime — regenerated on each app launch
CSRF_TOKEN = secrets.token_hex(32)


# ── Public API ────────────────────────────────────────────────────────────────

def start(port: int, state: dict, save_fn, sync_fn, log_file, host: str = '127.0.0.1'):
    global _PORT, _state, _save_fn, _sync_fn, _log_file
    _PORT     = port
    _state    = state
    _save_fn  = save_fn
    _sync_fn  = sync_fn
    _log_file = log_file

    srv = socketserver.ThreadingTCPServer((host, port), _Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()


# ── Security helpers ──────────────────────────────────────────────────────────

def _valid_host(host: str) -> bool:
    """Only allow localhost / 127.0.0.1 — blocks DNS rebinding."""
    return host.split(':')[0] in ('localhost', '127.0.0.1')


def _csrf_ok_header(headers) -> bool:
    return headers.get('X-CSRF-Token') == CSRF_TOKEN


def _parse_multipart(body: bytes, boundary: str) -> dict:
    """Minimal multipart/form-data parser. Returns {name: str | {filename, data}}."""
    parts  = {}
    sep    = ('--' + boundary).encode()
    chunks = body.split(sep)
    for chunk in chunks[1:]:
        if chunk.strip() in (b'--', b'--\r\n', b''):
            continue
        raw_headers, _, content = chunk.partition(b'\r\n\r\n')
        content = content.rstrip(b'\r\n')

        headers = {}
        for line in raw_headers.strip().split(b'\r\n'):
            if b':' in line:
                k, _, v = line.partition(b':')
                headers[k.lower().strip().decode()] = v.strip().decode()

        cd   = headers.get('content-disposition', '')
        name = ''
        fname = None
        for token in cd.split(';'):
            token = token.strip()
            if token.startswith('name='):
                name = token[5:].strip('"')
            elif token.startswith('filename='):
                fname = token[9:].strip('"')

        if not name:
            continue
        if fname is not None:
            parts[name] = {'filename': fname, 'data': content}
        else:
            parts[name] = content.decode(errors='replace')

    return parts


# ── CSS / HTML shared styles ──────────────────────────────────────────────────

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0f1117;color:#e0e0e0;font-family:system-ui,sans-serif;
     min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:#1a1d27;border:1px solid #2a2d3a;border-radius:14px;padding:32px;width:440px}
h1{font-size:20px;font-weight:700;color:#fff;margin-bottom:4px}
.sub{font-size:13px;color:#777;margin-bottom:24px}
.row{display:flex;align-items:center;gap:10px;padding:11px 0;border-top:1px solid #22253a}
.dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.dot.ok{background:#00c864}.dot.warn{background:#f5a623}.dot.bad{background:#e74c3c}
.lbl{font-size:12px;color:#888}.val{font-size:13px;font-weight:500;margin-left:auto}
.actions{display:flex;gap:10px;margin-top:24px;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;padding:10px 18px;border-radius:8px;
     font-size:13px;font-weight:600;cursor:pointer;border:none;text-decoration:none;
     transition:opacity .15s}
.btn:hover{opacity:.85}
.green{background:#00c864;color:#000}.ghost{background:transparent;color:#888;
     border:1px solid #2a2d3a}.ghost:hover{color:#e0e0e0;border-color:#555}
#msg{margin-top:14px;font-size:12px;min-height:18px;color:#888}
hr{border:none;border-top:1px solid #22253a;margin:20px 0}
@keyframes spin{to{transform:rotate(360deg)}}
.sh{font-size:15px;font-weight:600;color:#e0e0e0;margin-bottom:6px}
.hint{font-size:11px;color:#666;line-height:1.55;margin-bottom:12px}
label{font-size:11px;font-weight:700;color:#777;text-transform:uppercase;
      letter-spacing:.06em;display:block;margin-bottom:5px}
input[type=text],input[type=password]{width:100%;padding:10px 12px;
  background:#0f1117;border:1px solid #2a2d3a;border-radius:8px;
  color:#e0e0e0;font-size:13px;margin-bottom:16px}
input:focus{outline:none;border-color:#00c864}
input[type=file]{color:#999;font-size:12px;margin-bottom:16px;display:block}
.notice{background:#1f1a0a;border:1px solid #5a3e00;border-radius:8px;
        padding:12px;font-size:12px;color:#f5a623;line-height:1.6;margin-bottom:14px}
.ok-box{background:#0a1f14;border:1px solid #005a28;border-radius:8px;
        padding:12px;font-size:12px;color:#00c864;margin-bottom:14px}
.err-box{background:#1f0a0a;border:1px solid #5a0000;border-radius:8px;
         padding:12px;font-size:12px;color:#e74c3c;margin-bottom:14px}
"""

def _page(title: str, body: str) -> str:
    return (
        f'<!DOCTYPE html><html lang="en"><head>'
        f'<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{html.escape(title)}</title>'
        f'<style>{_CSS}</style></head><body>{body}</body></html>'
    )


# ── Dashboard ─────────────────────────────────────────────────────────────────

def _dashboard() -> str:
    age = last_data_age_hours()
    if age is None:
        dot, sync_txt = 'bad', 'Never synced'
    elif age < 24:
        dot, sync_txt = 'ok',   f'{age}h ago'
    elif age < 48:
        dot, sync_txt = 'warn', f'{age}h ago (stale)'
    else:
        dot, sync_txt = 'bad',  f'{int(age/24)}d ago'

    auto_txt   = 'On' if _state.get('auto', True) else 'Off'
    setup_done = _state.get('setup_done', False)
    setup_dot  = 'ok' if setup_done else 'bad'
    setup_txt  = 'Complete' if setup_done else 'Not configured'
    app_ver    = os.environ.get('APP_VERSION', '')
    ver_label  = f'v{app_ver}' if app_ver else 'Kairos Agent'

    return _page('Kairos Agent', f"""
<div class="card" style="width:500px;max-width:96vw">
  <h1>Kairos Agent</h1>
  <div class="sub">Local sync dashboard &nbsp;·&nbsp; {html.escape(ver_label)}</div>

  <div class="row">
    <div class="dot {dot}"></div>
    <span class="lbl">Last sync</span>
    <span class="val">{html.escape(sync_txt)}</span>
  </div>
  <div class="row">
    <div class="dot {setup_dot}"></div>
    <span class="lbl">Setup</span>
    <span class="val">{html.escape(setup_txt)}</span>
  </div>
  <div class="row">
    <div class="dot ok"></div>
    <span class="lbl">Auto-sync 4:30 PM weekdays</span>
    <span class="val">{html.escape(auto_txt)}</span>
  </div>

  <div class="actions">
    <button class="btn green" id="sync-btn" onclick="syncNow(this)">Sync Now</button>
    <a class="btn ghost" href="/setup">Setup / Reconfigure</a>
    <a class="btn ghost" href="https://kairos-f3w.pages.dev" target="_blank">Portal ↗</a>
  </div>
  <div id="msg"></div>

  <hr style="border:none;border-top:1px solid #22253a;margin:20px 0 14px">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
    <div style="font-size:13px;font-weight:600;color:#e0e0e0">Sync log</div>
    <button onclick="loadLog()" style="background:none;border:none;color:#666;font-size:11px;cursor:pointer;padding:0;transition:color .15s" onmouseover="this.style.color='#e0e0e0'" onmouseout="this.style.color='#666'">↺ refresh</button>
  </div>
  <div id="log-box" style="background:#0a0c10;border:1px solid #1e2130;border-radius:8px;padding:12px 14px;font-family:'Courier New',monospace;font-size:10.5px;color:#7ee787;line-height:1.75;max-height:320px;overflow-y:auto;white-space:pre-wrap;word-break:break-word;">Loading…</div>
</div>

<script>
const CSRF = '{CSRF_TOKEN}';
let _polling = null;

async function loadLog() {{
  try {{
    const r = await fetch('/api/logs');
    const d = await r.json();
    const lines = d.lines || [];
    // Find start of last run
    let start = 0;
    for (let i = lines.length - 1; i >= 0; i--) {{
      if (lines[i].includes('RUN @') || lines[i].includes('=====')) {{
        // Go back a bit to include the separator
        start = Math.max(0, i - 1);
        break;
      }}
    }}
    const box = document.getElementById('log-box');
    const text = lines.slice(start).join('\\n').trim();
    box.textContent = text || 'No sync log yet. Click Sync Now to run your first sync.';
    box.scrollTop = box.scrollHeight;
    return text;
  }} catch(e) {{
    document.getElementById('log-box').textContent = 'Failed to load log.';
    return '';
  }}
}}

function _startPoll() {{
  if (_polling) return;
  _polling = setInterval(async () => {{
    const text = await loadLog();
    if (text.includes('Uploaded:') || text.includes('sync failed')) {{
      clearInterval(_polling);
      _polling = null;
      setTimeout(() => location.reload(), 1200);
    }}
  }}, 2500);
}}

function syncNow(btn) {{
  btn.disabled = true; btn.textContent = 'Syncing…';
  document.getElementById('msg').textContent = '';
  fetch('/api/sync', {{method:'POST',headers:{{'X-CSRF-Token':CSRF}}}})
    .then(r => r.json()).then(d => {{
      document.getElementById('msg').textContent = d.message || '';
      if (d.ok) _startPoll();
      else {{ btn.disabled = false; btn.textContent = 'Sync Now'; }}
    }}).catch(() => {{ btn.disabled = false; btn.textContent = 'Sync Now'; }});
}}

loadLog();
</script>
""")


# ── Setup wizard ──────────────────────────────────────────────────────────────

def _setup(flash: str = '', flash_type: str = '') -> str:
    flash_html = ''
    if flash:
        cls = {'ok': 'ok-box', 'error': 'err-box'}.get(flash_type, 'notice')
        flash_html = f'<div class="{cls}">{html.escape(flash)}</div>'

    existing_token = creds.get('upload_token') or ''
    has_props      = creds.has_tiger_props()
    props_status   = '✓ Tiger config already saved — upload new file to replace' \
                     if has_props else 'Not yet configured'

    return _page('Kairos Setup', f"""
<div class="card">
  <h1>Kairos Setup</h1>
  <div class="sub">Connect your Tiger Brokers account</div>
  {flash_html}

  <form method="POST" action="/api/setup" enctype="multipart/form-data">
    <input type="hidden" name="csrf_token" value="{CSRF_TOKEN}">

    <div class="sh">Step 1 — Upload Token</div>
    <div class="hint">
      Get this from the Kairos portal:<br>
      <strong>kairos-f3w.pages.dev → Connect Tiger → Copy token</strong>
    </div>
    <label for="tok">Upload Token</label>
    <input type="text" id="tok" name="upload_token"
           value="{html.escape(existing_token)}"
           placeholder="Paste your upload token here" autocomplete="off">

    <hr>

    <div class="sh">Step 2 — Tiger Config File</div>
    <div class="hint">
      Download from Tiger app:<br>
      <strong>Tiger app → API Management → Download Config</strong>
    </div>
    <div class="notice">
      ⚠ Your <strong>tiger_openapi_config.properties</strong> contains your private API key.
      Kairos stores it locally only (~/.kairos-agent/) and never uploads it to any server.
      Only import a file you downloaded directly from the Tiger app.
    </div>
    <div style="font-size:12px;color:#666;margin-bottom:10px">{html.escape(props_status)}</div>
    <label for="props">tiger_openapi_config.properties</label>
    <input type="file" id="props" name="tiger_props" accept=".properties">

    <div class="actions">
      <button type="submit" class="btn green">Save &amp; Continue</button>
      <a href="/" class="btn ghost">← Dashboard</a>
    </div>
  </form>
</div>
""")


# ── Setup success page ───────────────────────────────────────────────────────

def _setup_success() -> str:
    return _page('Kairos — Syncing', f"""
<div class="card">
  <h1 style="color:#00c864">✓ Setup Complete</h1>
  <div class="sub">Kairos Agent is configured — starting your first sync now</div>

  <div id="sync-msg" class="notice" style="margin-top:20px">
    <span id="sync-spinner" style="display:inline-block;animation:spin 1s linear infinite;margin-right:6px">⟳</span>
    Syncing your Tiger trades… this may take 1–2 minutes on first run.
  </div>

  <div class="actions" style="margin-top:20px">
    <a id="portal-btn" class="btn green" href="https://kairos-f3w.pages.dev"
       style="display:none" target="_blank">Open Portal →</a>
    <a class="btn ghost" href="/">Dashboard</a>
  </div>

  <div style="margin-top:16px;font-size:11px;color:#555">
    You can also trigger manual syncs anytime from the tray icon.
  </div>
</div>

<script>
let _elapsed = 0;
let _done    = false;

async function poll() {{
  if (_done) return;
  try {{
    const r = await fetch('/api/status');
    const d = await r.json();
    if (d.last_sync) {{
      _done = true;
      const msg = document.getElementById('sync-msg');
      msg.className   = 'ok-box';
      msg.innerHTML   = '✓ First sync complete! Your trades are ready on the portal.';
      document.getElementById('portal-btn').style.display = '';
    }} else {{
      _elapsed += 5;
      document.getElementById('sync-msg').innerHTML =
        '<span style="display:inline-block;animation:spin 1s linear infinite;margin-right:6px">⟳</span>' +
        'Syncing your Tiger trades… ' + _elapsed + 's elapsed';
      setTimeout(poll, 5000);
    }}
  }} catch(e) {{
    setTimeout(poll, 5000);
  }}
}}

setTimeout(poll, 5000);
</script>
""")


# ── HTTP handler ──────────────────────────────────────────────────────────────

class _Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass  # suppress stdout access log

    def _send(self, code: int, body: str, ctype: str = 'text/html; charset=utf-8'):
        b = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(b)

    def _json(self, code: int, data: dict):
        self._send(code, json.dumps(data), 'application/json')

    def _guard(self) -> bool:
        """Return True (and proceed) if Host header is safe."""
        if not _valid_host(self.headers.get('Host', '')):
            self._send(403, '<h1>403 Forbidden</h1>')
            return False
        return True

    # GET ─────────────────────────────────────────────────────────────────────

    def do_GET(self):
        if not self._guard():
            return
        path = self.path.split('?')[0]

        if path == '/':
            self._send(200, _dashboard())
        elif path == '/setup':
            self._send(200, _setup())
        elif path == '/api/status':
            age = last_data_age_hours()
            self._json(200, {
                'setup_done': _state.get('setup_done', False),
                'last_sync':  _state.get('last_sync'),
                'age_hours':  age,
                'auto':       _state.get('auto', True),
            })
        elif path == '/api/logs':
            lines = []
            if _log_file and pathlib.Path(str(_log_file)).exists():
                lines = pathlib.Path(str(_log_file)).read_text(
                    errors='replace').splitlines()[-100:]
            self._json(200, {'lines': lines})
        else:
            self._send(404, '<h1>404 Not Found</h1>')

    # POST ────────────────────────────────────────────────────────────────────

    def do_POST(self):
        if not self._guard():
            return
        path = self.path.split('?')[0]

        if path == '/api/sync':
            if not _csrf_ok_header(self.headers):
                self._json(403, {'ok': False, 'message': 'CSRF check failed'})
                return
            if _sync_fn:
                _sync_fn()
            self._json(200, {'ok': True, 'message': 'Sync started…'})

        elif path == '/api/setup':
            self._handle_setup()

        else:
            self._send(404, '<h1>404</h1>')

    def _handle_setup(self):
        length       = int(self.headers.get('Content-Length', 0))
        raw_body     = self.rfile.read(length)
        content_type = self.headers.get('Content-Type', '')

        token_val    = ''
        props_bytes  = None
        props_fname  = ''

        if 'multipart/form-data' in content_type:
            boundary = ''
            for part in content_type.split(';'):
                part = part.strip()
                if part.startswith('boundary='):
                    boundary = part[9:].strip('"')
            if not boundary:
                self._send(200, _setup('Bad request — missing boundary.', 'error'))
                return

            fields   = _parse_multipart(raw_body, boundary)
            csrf_val = fields.get('csrf_token', '')
            if csrf_val != CSRF_TOKEN:
                self._send(200, _setup('CSRF check failed. Please try again.', 'error'))
                return

            token_val = (fields.get('upload_token') or '').strip()
            fp = fields.get('tiger_props')
            if isinstance(fp, dict) and fp.get('filename'):
                props_fname = fp['filename']
                props_bytes = fp['data']

        elif 'application/x-www-form-urlencoded' in content_type:
            fields    = dict(urllib.parse.parse_qsl(raw_body.decode()))
            csrf_val  = fields.get('csrf_token', '')
            if csrf_val != CSRF_TOKEN:
                self._send(200, _setup('CSRF check failed. Please try again.', 'error'))
                return
            token_val = fields.get('upload_token', '').strip()
        else:
            self._send(200, _setup('Unsupported content type.', 'error'))
            return

        # Validate ────────────────────────────────────────────────────────────
        if not token_val:
            self._send(200, _setup('Upload token is required.', 'error'))
            return

        if props_bytes is not None:
            if props_fname and not props_fname.endswith('.properties'):
                self._send(200, _setup(
                    'Wrong file type — please select tiger_openapi_config.properties',
                    'error'))
                return
            if not props_bytes:
                self._send(200, _setup('Properties file appears empty.', 'error'))
                return

        # Save token ──────────────────────────────────────────────────────────
        creds.set('upload_token', token_val)

        # Save Tiger props (write-only — never returned to browser) ───────────
        if props_bytes is not None:
            agent_dir = pathlib.Path.home() / '.kairos-agent'
            agent_dir.mkdir(parents=True, exist_ok=True)
            dest = agent_dir / 'tiger_openapi_config.properties'
            dest.write_bytes(props_bytes)
            os.chmod(dest, stat.S_IRUSR | stat.S_IWUSR)

        # Update state ────────────────────────────────────────────────────────
        if creds.is_complete():
            _state['setup_done'] = True
            if _save_fn:
                _save_fn(_state)
            # Auto-trigger first sync in background
            if _sync_fn:
                _sync_fn()
            self._send(200, _setup_success())
        else:
            self._send(200, _setup(
                'Token saved. Please also select your tiger_openapi_config.properties file.',
                ''))

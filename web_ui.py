"""Web UI — scan controls for the Amazon Haul Price Monitor.

Local:  python web_ui.py  → http://127.0.0.1:8787
Render: uvicorn web_ui:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import hashlib
import hmac
import html
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
import uvicorn

from config import load_settings
from ui_settings import load_ui_settings, save_ui_settings

_monitor = None
_thread: threading.Thread | None = None
_status = {"running": False, "stopping": False, "scanning": False, "last_message": "Idle"}

AUTH_COOKIE = "ahm_session"


def _is_running() -> bool:
    return bool(_thread and _thread.is_alive())


def _is_stopping() -> bool:
    return bool(_status.get("stopping")) and _is_running()


def _is_cloud() -> bool:
    if os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"):
        return True
    host = (os.getenv("HOST") or "").strip()
    return host in ("0.0.0.0", "::")


def _ui_password() -> str:
    raw = os.getenv("UI_PASSWORD")
    if raw is None:
        return ""
    pw = str(raw).strip()
    if len(pw) >= 2 and pw[0] == pw[-1] and pw[0] in ("'", '"'):
        pw = pw[1:-1].strip()
    return pw


def _auth_token(password: str) -> str:
    return hashlib.sha256(f"ahm:{password}".encode("utf-8")).hexdigest()


def _password_ok(given: str, expected: str) -> bool:
    a = (given or "").strip().encode("utf-8")
    b = (expected or "").strip().encode("utf-8")
    if not a or not b or len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def _is_authed(request: Request) -> bool:
    pw = _ui_password()
    if not pw:
        return True
    token = request.cookies.get(AUTH_COOKIE) or ""
    return bool(token and hmac.compare_digest(token, _auth_token(pw)))


def _try_start_monitor(*, message: str = "Started.") -> str:
    global _monitor, _thread
    if _is_running():
        return "already_running"
    from monitor import Monitor

    s = load_settings()
    if not s.keepa_api_key or not s.tiers:
        return "missing_credentials"

    _monitor = Monitor(s)

    def _run() -> None:
        _status["running"] = True
        _status["stopping"] = False
        _status["last_message"] = "Monitor running\u2026"
        try:
            _monitor.run_forever()
        finally:
            _status["running"] = False
            _status["stopping"] = False
            _status["last_message"] = "Stopped."
            try:
                _monitor.close()
            except Exception:
                pass

    _thread = threading.Thread(target=_run, name="ahm-monitor", daemon=True)
    _thread.start()
    _status["stopping"] = False
    _status["last_message"] = message
    return "ok"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    _status["last_message"] = "Idle \u2014 click Start monitor or Run 1 scan now."
    yield
    if _monitor and _is_running():
        _monitor.request_stop()


app = FastAPI(title="Amazon Haul Price Monitor", lifespan=_lifespan)


@app.middleware("http")
async def _password_gate(request: Request, call_next):
    path = request.url.path
    if path in ("/health", "/login", "/favicon.ico"):
        return await call_next(request)
    if not _ui_password() or _is_authed(request):
        return await call_next(request)
    return RedirectResponse("/login", status_code=303)


# Major Amazon categories (Keepa root node IDs) for the selectbox.
MAJOR_CATEGORIES = [
    ("Automotive", 15684181),
    ("Tools & Home Improvement", 228013),
    ("Electronics", 172282),
    ("Home & Kitchen", 1055398),
    ("Sports & Outdoors", 3375251),
    ("Health & Household", 3760901),
    ("Office Products", 1064954),
    ("Pet Supplies", 2975446011),
    ("Industrial & Scientific", 16310091),
    ("Garden & Outdoor", 2972638011),
    ("Appliances", 2619525011),
    ("Computers", 541966),
    ("Camera & Photo", 502394),
    ("Musical Instruments", 11091801),
    ("Books", 283155),
    ("Clothing & Jewelry", 7141123011),
    ("Beauty", 11055981),
    ("Toys & Games", 165793011),
    ("Baby", 165796011),
    ("Cell Phones", 2335752011),
    ("Video Games", 468642),
    ("Movies & TV", 2625373011),
    ("Software", 229534),
    ("Grocery", 16310101),
    ("Arts & Crafts", 2617941011),
    ("Luggage", 3290252011),
    ("CDs & Vinyl", 5174),
]

PAGE = """<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
__REFRESH_TAG__
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Amazon Haul Price Monitor</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=Sora:wght@600;700&display=swap" rel="stylesheet"/>
<style>
:root, [data-theme="light"] { --bg:#f0f2f5; --bg-elev:#fff; --bg-soft:#e8ecf1; --border:#8b97a8; --border-strong:#5f6b7c; --text:#10151c; --muted:#4a5565; --accent:#ff9900; --accent-text:#1a1200; --accent-soft:rgba(255,153,0,0.16); --ok:#0b7a42; --ok-soft:rgba(11,122,66,0.12); --danger:#c62828; --shadow:0 1px 2px rgba(16,24,40,0.06),0 10px 28px rgba(16,24,40,0.08); }
[data-theme="dark"] { --bg:#0e1116; --bg-elev:#171b22; --bg-soft:#1e2430; --border:#556174; --border-strong:#7b879c; --text:#eef1f5; --muted:#a0aec0; --accent:#ff9900; --accent-text:#1a1200; --accent-soft:rgba(255,153,0,0.16); --ok:#3dd68c; --ok-soft:rgba(61,214,140,0.14); --danger:#ff6b5a; --shadow:0 1px 2px rgba(0,0,0,0.4),0 14px 36px rgba(0,0,0,0.4); }
* { box-sizing:border-box; } html,body { margin:0; min-height:100%; }
body { font-family:"IBM Plex Sans",system-ui,sans-serif; background:radial-gradient(900px 420px at 8% -8%,var(--accent-soft),transparent 50%),var(--bg); color:var(--text); line-height:1.45; }
.shell { max-width:900px; margin:0 auto; padding:0 20px 56px; }
.topbar { position:sticky; top:0; z-index:20; backdrop-filter:blur(14px); background:var(--bg-elev); border-bottom:2px solid var(--border); }
.topbar-inner { max-width:900px; margin:0 auto; padding:16px 20px; display:flex; align-items:center; justify-content:space-between; gap:16px; }
.brand { display:flex; align-items:center; gap:12px; min-width:0; }
.brand-mark { width:42px; height:42px; border-radius:11px; background:linear-gradient(145deg,#ffb84d,#ff9900 55%,#e88700); color:#1a1200; font-family:Sora,sans-serif; font-weight:700; display:grid; place-items:center; font-size:1.1rem; border:2px solid #b86d00; flex-shrink:0; }
.brand h1 { font-family:Sora,sans-serif; font-size:1.08rem; font-weight:700; margin:0; letter-spacing:-0.02em; }
.brand p { margin:3px 0 0; font-size:0.8rem; color:var(--muted); }
.top-actions { display:flex; align-items:center; gap:10px; flex-shrink:0; }
.theme-btn { appearance:none; border:2px solid var(--border); background:var(--bg-elev); color:var(--text); border-radius:999px; padding:8px 14px; font:inherit; font-size:0.82rem; font-weight:600; cursor:pointer; }
.pill { display:inline-flex; align-items:center; gap:6px; padding:5px 11px; border-radius:999px; font-size:0.75rem; font-weight:700; border:2px solid var(--border); }
.pill::before { content:""; width:7px; height:7px; border-radius:50%; background:currentColor; }
.on { background:var(--ok-soft); color:var(--ok); border-color:var(--ok); }
.off { background:var(--bg-elev); color:var(--muted); }
.panel { background:var(--bg-elev); border:2px solid var(--border); border-radius:16px; padding:20px 22px 22px; box-shadow:var(--shadow); margin-top:20px; }
.panel-head { display:flex; align-items:baseline; justify-content:space-between; gap:10px; margin-bottom:14px; padding-bottom:12px; border-bottom:2px solid var(--border); }
.panel-head h3 { font-family:Sora,sans-serif; font-size:1.02rem; font-weight:700; margin:0; letter-spacing:-0.02em; }
.panel-head .tag { font-size:0.72rem; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; color:var(--muted); border:2px solid var(--border); border-radius:999px; padding:3px 9px; }
.lead { margin:0 0 16px; color:var(--muted); font-size:0.88rem; }
.status-strip { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; padding:12px 14px; border-radius:12px; background:var(--bg-soft); border:2px solid var(--border); margin-bottom:16px; }
.status-strip .msg { margin:0; font-size:0.92rem; font-weight:500; }
.grid { display:grid; grid-template-columns:1fr 1fr; gap:14px 16px; }
@media (max-width:720px) { .grid { grid-template-columns:1fr; } }
label.field { display:flex; flex-direction:column; gap:6px; }
label.field > span { font-size:0.8rem; font-weight:700; color:var(--muted); }
input, select, textarea { width:100%; padding:11px 12px; border-radius:10px; border:2px solid var(--border); background:#fafbfc; color:var(--text); font:inherit; font-size:0.95rem; }
[data-theme="dark"] input, [data-theme="dark"] select, [data-theme="dark"] textarea { background:var(--bg-soft); }
input:focus, select:focus, textarea:focus { outline:none; border-color:var(--accent); box-shadow:0 0 0 3px var(--ring); }
.hint { font-size:0.74rem; color:var(--muted); margin:0; }
.check { display:flex; align-items:flex-start; gap:10px; margin-top:14px; padding:12px 14px; border-radius:12px; background:var(--bg-soft); border:2px solid var(--border); }
.check input { width:auto; margin-top:3px; accent-color:var(--accent); }
.check span { font-size:0.9rem; }
.actions { display:flex; flex-wrap:wrap; gap:10px; margin-top:16px; }
button { appearance:none; border:0; border-radius:10px; padding:11px 16px; font:inherit; font-weight:700; cursor:pointer; display:inline-flex; align-items:center; }
button:disabled { opacity:0.45; cursor:not-allowed; }
.primary { background:var(--accent); color:var(--accent-text); border:2px solid #b86d00; }
.danger { background:var(--danger); color:#fff; border:2px solid transparent; }
.ghost { background:var(--bg-elev); border:2px solid var(--border); color:var(--text); }
.tier-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:10px; }
@media (max-width:720px) { .tier-grid { grid-template-columns:1fr; } }
.tier { border:2px solid var(--border); border-radius:12px; padding:13px 14px; background:var(--bg-soft); display:flex; justify-content:space-between; gap:10px; align-items:center; }
.tier strong { display:block; font-size:0.95rem; }
.tier small { display:block; color:var(--muted); font-size:0.78rem; margin-top:2px; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:0.78rem; background:var(--bg-soft); border:1.5px solid var(--border); padding:2px 7px; border-radius:6px; }
details.secret-box { border:2px solid var(--border); border-radius:12px; background:var(--bg-soft); padding:0; overflow:hidden; }
details.secret-box > summary { list-style:none; cursor:pointer; padding:12px 14px; font-weight:700; display:flex; align-items:center; justify-content:space-between; gap:10px; user-select:none; }
details.secret-box > summary::-webkit-details-marker { display:none; }
details.secret-box > summary::after { content:"Show"; font-size:0.75rem; font-weight:700; color:var(--muted); border:2px solid var(--border); border-radius:999px; padding:3px 10px; background:var(--bg-elev); }
details.secret-box[open] > summary::after { content:"Hide"; }
.secret-body { padding:0 14px 14px; border-top:2px solid var(--border); padding-top:14px; }
.key-row { display:flex; gap:8px; align-items:stretch; }
.key-row input { flex:1; }
.webhook-list { display:flex; flex-direction:column; gap:12px; }
.webhook-item { border:2px solid var(--border); border-radius:12px; padding:12px 14px; background:var(--bg-soft); }
.webhook-item .wh-top { display:flex; justify-content:space-between; align-items:center; gap:10px; margin-bottom:8px; }
.webhook-item strong { font-size:0.92rem; }
.webhook-item small { color:var(--muted); font-size:0.78rem; }
.webhook-item.disabled-ch { opacity:0.72; border-style:dashed; }
.ch-enable { display:inline-flex; align-items:center; gap:8px; font-size:0.82rem; font-weight:700; color:var(--text); white-space:nowrap; }
.ch-enable input { width:auto; accent-color:var(--accent); }
input.mono { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:0.82rem; }
</style>
<script>
(function () { var saved = localStorage.getItem("ahm-theme"); if (saved === "dark" || saved === "light") document.documentElement.setAttribute("data-theme", saved); })();
function toggleTheme() { var html=document.documentElement; var next=html.getAttribute("data-theme")==="dark"?"light":"dark"; html.setAttribute("data-theme",next); localStorage.setItem("ahm-theme",next); var btn=document.getElementById("themeBtn"); if(btn) btn.textContent=next==="dark"?"Light mode":"Dark mode"; }
document.addEventListener("DOMContentLoaded", function(){ var btn=document.getElementById("themeBtn"); if(btn) btn.textContent=document.documentElement.getAttribute("data-theme")==="dark"?"Light mode":"Dark mode"; });
</script>
</head>
<body>
<header class="topbar">
  <div class="topbar-inner">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">h</div>
      <div>
        <h1>Amazon Haul Price Monitor</h1>
        <p>Amazon Haul scrape \u2192 Keepa baseline \u2192 Discord alerts</p>
      </div>
    </div>
    <div class="top-actions">
      <span class="pill __STATUS_CLASS__">__STATUS_LABEL__</span>
      <button type="button" class="theme-btn" id="themeBtn" onclick="toggleTheme()">Dark mode</button>
    </div>
  </div>
</header>
<div class="shell">
  <div class="panel">
    <div class="panel-head"><h3>Status</h3><span class="tag">Live</span></div>
    <div class="status-strip">
      <p class="msg">__LAST_MESSAGE__</p>
      <span class="pill __STATUS_CLASS__">__STATUS_LABEL__</span>
    </div>
    <div class="actions">
      <form method="post" action="/start" style="display:inline">__START_BTN__</form>
      <form method="post" action="/stop" style="display:inline">__STOP_BTN__</form>
      <form method="post" action="/once" style="display:inline">
        <button class="primary" type="submit">Run 1 scan now</button>
      </form>
      <form method="post" action="/force-scan" style="display:inline">
        <button class="ghost" type="submit">Force scan (ignore dedup)</button>
      </form>
    </div>
    <p class="hint" style="margin-top:8px;">Monitor does not auto-start. Click Start monitor or Run 1 scan now. Settings auto-apply within seconds \u2014 no restart needed.</p>
  </div>

  <div class="panel">
    <div class="panel-head"><h3>Keepa API key</h3><span class="tag">Credentials</span></div>
    <details class="secret-box">
      <summary>API key (__KEY_STATUS__)</summary>
      <div class="secret-body">
        <form method="post" action="/save-key">
          <label class="field">
            <span>Keepa API key</span>
            <div class="key-row">
              <input class="mono" type="text" name="keepa_api_key" value="" placeholder="__KEEPA_PLACEHOLDER__" autocomplete="off" spellcheck="false"/>
            </div>
            <p class="hint">__KEEPA_HINT__</p>
          </label>
          <div class="actions"><button class="primary" type="submit">Save API key</button></div>
        </form>
      </div>
    </details>
  </div>

  <div class="panel">
    <div class="panel-head"><h3>Discord webhooks</h3><span class="tag">Channels</span></div>
    <form method="post" action="/save-webhooks">
      <label class="field">
        <span>Discord webhook (Amazon Haul channel)</span>
        <input class="mono" type="url" name="discord_webhook_80" value="__WH80__" placeholder="https://discord.com/api/webhooks/..."/>
        <p class="hint">All alerts (50/60/70/80 tiers) go to this single channel. Tier label shown on each alert.</p>
      </label>
      <div class="actions">
        <button class="primary" type="submit">Save webhook</button>
        <button class="ghost" type="submit" formaction="/test-alert">Send test alert</button>
      </div>
      <p class="hint">Test alert goes to the configured Discord webhook.</p>
    </form>
  </div>

  <div class="panel">
    <div class="panel-head"><h3>Configure scan</h3><span class="tag">Settings</span></div>
    <form method="post" action="/save">
      <div class="status-strip">
        <p class="msg">__LAST_MESSAGE__</p>
        <span class="pill __STATUS_CLASS__">__STATUS_LABEL__</span>
      </div>
      <div class="grid">
        <label class="field">
          <span>Scan interval (minutes)</span>
          <input type="number" name="scan_interval_min" min="5" max="120" value="__SCAN_INTERVAL__"/>
        </label>
        <label class="field">
          <span>Max alerts per channel / scan</span>
          <input type="number" name="max_alerts_per_tier" min="1" max="20" value="__MAX_ALERTS__"/>
        </label>
        <label class="field">
          <span>Min discount %</span>
          <input type="number" name="min_discount" min="40" max="95" value="__MIN_DISCOUNT__"/>
        </label>
        <label class="field">
          <span>Min regular price ($, avg90)</span>
          <input type="number" step="0.01" name="min_original_price" value="__MIN_PRICE__"/>
        </label>
        <label class="field">
          <span>Min savings ($)</span>
          <input type="number" step="0.01" name="min_savings" value="__MIN_SAVINGS__"/>
        </label>
        <label class="field">
          <span>Min Haul price ($)</span>
          <input type="number" step="0.01" name="min_haul_price" value="__MIN_HAUL__"/>
        </label>
      </div>
      <label class="field" style="margin-top:14px">
        <span>Haul keywords (comma-separated)</span>
        <textarea name="haul_keywords" rows="3" placeholder="automotive, tools, electronics...">__KEYWORDS__</textarea>
      </label>
      <div class="grid" style="margin-top:14px;">
        <label class="field">
          <span>Include categories (comma IDs)</span>
          <input type="text" name="include_categories" value="__INC_CAT__" placeholder="15684181,228013"/>
        </label>
        <label class="field">
          <span>Exclude categories (comma IDs)</span>
          <input type="text" name="exclude_categories" value="__EXC_CAT__" placeholder="7141123011,11055981"/>
        </label>
        <div style="grid-column:1/-1;">
          <p class="hint" style="margin:8px 0 4px;">Or pick from major categories:</p>
          <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:2px 16px;font-size:0.82rem;max-height:220px;overflow-y:auto;padding:6px;border:1px solid var(--border);border-radius:8px;background:var(--bg-soft);">
            __CAT_CHECKBOXES__
          </div>
        </div>
        <label class="field">
          <span>Include brands (comma)</span>
          <input type="text" name="include_brands" value="__INC_BRAND__" placeholder="wiblooter,tchenpart"/>
        </label>
        <label class="field">
          <span>Exclude brands (comma)</span>
          <input type="text" name="exclude_brands" value="__EXC_BRAND__" placeholder="generic"/>
        </label>
        <label class="field">
          <span>Title keywords (comma)</span>
          <input type="text" name="title_keywords" value="__TITLE_KW__" placeholder="compatible"/>
        </label>
        <label class="field">
          <span>Exclude keywords (comma)</span>
          <input type="text" name="exclude_keywords" value="__EXC_KW__" placeholder="phone case,sticker"/>
        </label>
        <label class="field">
          <span>Min review count</span>
          <input type="number" name="min_review_count" min="0" value="__MIN_REV__"/>
        </label>
        <label class="field">
          <span>Min rating</span>
          <input type="number" step="0.1" name="min_rating" min="0" max="5" value="__MIN_RAT__"/>
        </label>
      </div>
      <label class="check" style="margin-top:10px;">
        <input type="checkbox" name="reject_promotions" value="1" __REJ_PROMO__/><span>Reject promotions/coupons</span>
      </label>
      <label class="check">
        <input type="checkbox" name="reject_business" value="1" __REJ_BIZ__/><span>Reject business-only</span>
      </label>
      <label class="check">
        <input type="checkbox" name="exclude_books" value="1" __EXC_BOOKS__/><span>Exclude books/kindle/audiobooks</span>
      </label>
      <div class="actions"><button class="primary" type="submit">Save settings</button></div>
    </form>
  </div>
</div>
</body>
</html>
"""


def _webhook_badge(has_url: bool, enabled: bool = True) -> str:
    if not has_url:
        return '<span class="pill off">missing</span>'
    if not enabled:
        return '<span class="pill off">paused</span>'
    return '<span class="pill on">active</span>'


def _display_webhook(ui: dict, tier: int) -> str:
    import os

    v = str(ui.get(f"discord_webhook_{tier}") or "").strip()
    if v:
        return v
    # Fall back to single DISCORD_WEBHOOK env var, then tier-specific.
    single = (os.getenv("DISCORD_WEBHOOK") or "").strip()
    if single:
        return single
    return (os.getenv(f"DISCORD_WEBHOOK_{tier}") or "").strip()


def _render_page(**kwargs: str) -> str:
    html = PAGE
    for key, value in kwargs.items():
        html = html.replace(f"__{key}__", str(value))
    return html


@app.get("/", response_class=HTMLResponse)
def home(_: Request) -> str:
    ui = load_ui_settings()
    s = load_settings()
    running = _is_running()
    stopping = _is_stopping()
    key = s.keepa_api_key or ""
    if stopping:
        status_class, status_label = "off", "STOPPING\u2026"
    elif running:
        status_class, status_label = "on", "RUNNING"
    else:
        status_class, status_label = "off", "STOPPED"

    wh = {t: _display_webhook(ui, t) for t in (50, 60, 70, 80)}
    if key:
        masked = (key[:4] + "\u2026" + key[-4:]) if len(key) > 12 else "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022"
        keepa_placeholder = f"Saved ({masked}) \u2014 paste new key to replace"
        keepa_hint = "Key is already saved (env/UI). Field stays empty so browser autofill cannot overwrite it."
    else:
        keepa_placeholder = "Paste Keepa API key"
        keepa_hint = "No key saved yet."

    keywords_val = ui.get("haul_keywords") or ",".join(s.haul_keywords)
    inc_cat = ui.get("include_categories", "")
    exc_cat = ui.get("exclude_categories", "")
    inc_set = set(str(inc_cat).replace(" ", "").split(",")) if inc_cat else set()
    exc_set = set(str(exc_cat).replace(" ", "").split(",")) if exc_cat else set()
    cat_cb = ""
    for name, cid in MAJOR_CATEGORIES:
        cid_str = str(cid)
        inc_checked = "checked" if cid_str in inc_set else ""
        exc_checked = "checked" if cid_str in exc_set else ""
        cat_cb += (
            f'<div style="display:flex;align-items:center;gap:8px;padding:3px 0;border-bottom:1px solid var(--border);">'
            f'<span style="min-width:150px;font-size:0.8rem;">{name}</span>'
            f'<label style="display:flex;flex-direction:column;align-items:center;gap:1px;font-size:0.7rem;cursor:pointer;">'
            f'<input type="checkbox" name="inc_cat_cb" value="{cid}" {inc_checked} style="margin:0;"/>Inc</label>'
            f'<label style="display:flex;flex-direction:column;align-items:center;gap:1px;font-size:0.7rem;cursor:pointer;">'
            f'<input type="checkbox" name="exc_cat_cb" value="{cid}" {exc_checked} style="margin:0;"/>Exc</label>'
            f'</div>'
        )
    inc_brand = ui.get("include_brands", "")
    exc_brand = ui.get("exclude_brands", "")
    title_kw = ui.get("title_keywords", "")
    exc_kw = ui.get("exclude_keywords", "")
    refresh_tag = '<meta http-equiv="refresh" content="5"/>' if _status.get("scanning") else ""
    return _render_page(
        REFRESH_TAG=refresh_tag,
        STATUS_CLASS=status_class,
        STATUS_LABEL=status_label,
        LAST_MESSAGE=html.escape(_status.get("last_message", "") or "Idle"),
        SCAN_INTERVAL=str(ui["scan_interval_min"]),
        MAX_ALERTS=str(ui["max_alerts_per_tier"]),
        MIN_DISCOUNT=str(ui["min_discount"]),
        MIN_PRICE=str(ui["min_original_price"]),
        MIN_SAVINGS=str(ui["min_savings"]),
        MIN_HAUL=str(ui["min_haul_price"]),
        KEYWORDS=html.escape(str(keywords_val)),
        CAT_CHECKBOXES=cat_cb,
        INC_CAT=html.escape(str(inc_cat)),
        EXC_CAT=html.escape(str(exc_cat)),
        INC_BRAND=html.escape(str(inc_brand)),
        EXC_BRAND=html.escape(str(exc_brand)),
        TITLE_KW=html.escape(str(title_kw)),
        EXC_KW=html.escape(str(exc_kw)),
        MIN_REV=str(ui.get("min_review_count", 0)),
        MIN_RAT=str(ui.get("min_rating", 0.0)),
        REJ_PROMO="checked" if ui.get("reject_promotions", False) else "",
        REJ_BIZ="checked" if ui.get("reject_business", False) else "",
        EXC_BOOKS="checked" if ui.get("exclude_books", True) else "",
        KEEPA_PLACEHOLDER=html.escape(keepa_placeholder),
        KEEPA_HINT=html.escape(keepa_hint),
        KEY_STATUS="saved" if key else "not set",
        WH80=html.escape(wh[80]),
        START_BTN='<button class="primary" type="submit">Start monitor</button>'
        if not running
        else '<button class="ghost" type="button" disabled>Start monitor</button>',
        STOP_BTN='<button class="danger" type="submit">Stop</button>'
        if running and not stopping
        else (
            '<button class="ghost" type="button" disabled>Stopping\u2026</button>'
            if stopping
            else '<button class="ghost" type="button" disabled>Stop</button>'
        ),
    )


@app.post("/save-key")
def save_key(keepa_api_key: str = Form("")):
    key = (keepa_api_key or "").strip()
    save_ui_settings({"keepa_api_key": key})
    _status["last_message"] = "Keepa API key saved." if key else "UI API key cleared (env key still used if set)."
    return RedirectResponse("/", status_code=303)


@app.post("/save-webhooks")
def save_webhooks(discord_webhook_80: str = Form("")):
    wh = (discord_webhook_80 or "").strip()
    # Single channel: all tiers route to the same webhook.
    save_ui_settings(
        {
            "discord_webhook_50": wh,
            "discord_webhook_60": wh,
            "discord_webhook_70": wh,
            "discord_webhook_80": wh,
            "channel_enabled_50": True,
            "channel_enabled_60": True,
            "channel_enabled_70": True,
            "channel_enabled_80": True,
        }
    )
    _status["last_message"] = "Discord webhook saved (all tiers -> single channel)."
    return RedirectResponse("/", status_code=303)

@app.post("/test-alert")
def test_alert(
    discord_webhook_50: str = Form(""),
    discord_webhook_60: str = Form(""),
    discord_webhook_70: str = Form(""),
    discord_webhook_80: str = Form(""),
    channel_enabled_50: str | None = Form(None),
    channel_enabled_60: str | None = Form(None),
    channel_enabled_70: str | None = Form(None),
    channel_enabled_80: str | None = Form(None),
):
    # Persist only non-empty values so a bare test click cannot wipe saved webhooks.
    updates: dict = {}
    for tier, val in (
        (50, discord_webhook_50),
        (60, discord_webhook_60),
        (70, discord_webhook_70),
        (80, discord_webhook_80),
    ):
        v = (val or "").strip()
        if v:
            updates[f"discord_webhook_{tier}"] = v
    for tier, val in (
        (50, channel_enabled_50),
        (60, channel_enabled_60),
        (70, channel_enabled_70),
        (80, channel_enabled_80),
    ):
        if val is not None:
            updates[f"channel_enabled_{tier}"] = val == "1"
    if updates:
        save_ui_settings(updates)

    s = load_settings()
    webhook = (s.webhooks.get(80) or "").strip()
    if not webhook:
        _status["last_message"] = "Test alert failed \u2014 set a Discord webhook first."
        return RedirectResponse("/", status_code=303)

    from discord_notifier import send_alert
    from keepa_client import amazon_image_url
    from models import AlertItem

    item = AlertItem(
        asin="B0FH9M9WCG",
        title="TEST ALERT \u2014 Haul embed sample",
        image_url=amazon_image_url("31RmBhqOkZL.jpg", size=120),
        new_price=6.95,
        old_price=32.99,
        discount=79,
        seller=s.seller_label,
        promotion=False,
        business_required=False,
        brand="Wiblooter",
        review_count=0,
        rating=4.7,
        categories=[],
        root_cat=0,
        amazon_url="https://www.amazon.com/dp/B0FH9M9WCG?s=bazaar&psc=1",
        keepa_url=f"https://keepa.com/#!product/{s.keepa_domain}-B0FH9M9WCG",
        google_url="https://www.google.com/search?q=Wiblooter+Dashboard+Cover",
        sas_url="https://sas.selleramp.com/sas/lookup?search_term=B0FH9M9WCG&sas_cost_price=6.95",
        ebay_url="https://www.ebay.com/sch/i.html?_nkw=Wiblooter+Dashboard+Cover",
        atc_url="https://www.amazon.com/dp/B0FH9M9WCG?s=bazaar&psc=1",
        graph_url="",
        channel_prefix=s.channel_prefix,
    )
    ok, err = send_alert(webhook, "", item, 70)
    _status["last_message"] = "Test alert sent to test webhook." if ok else f"Test alert failed: {err}"
    return RedirectResponse("/", status_code=303)


def _merge_cats(text_val: str, cb_vals: list) -> str:
    """Merge text input IDs with checkbox IDs, dedupe."""
    ids = set()
    for part in (text_val or "").replace(";", ",").split(","):
        part = part.strip()
        if part:
            ids.add(part)
    for v in (cb_vals or []):
        v = str(v).strip()
        if v:
            ids.add(v)
    return ",".join(sorted(ids, key=lambda x: int(x) if x.isdigit() else 0))

@app.post("/save")
def save(
    scan_interval_min: int = Form(10),
    max_alerts_per_tier: int = Form(5),
    min_discount: int = Form(50),
    min_original_price: float = Form(15.0),
    min_savings: float = Form(8.0),
    min_haul_price: float = Form(3.0),
    haul_keywords: str = Form(""),
    include_categories: str = Form(""),
    exclude_categories: str = Form(""),
    include_brands: str = Form(""),
    exclude_brands: str = Form(""),
    title_keywords: str = Form(""),
    exclude_keywords: str = Form(""),
    min_review_count: int = Form(0),
    min_rating: float = Form(0.0),
    reject_promotions: str | None = Form(None),
    reject_business: str | None = Form(None),
    exclude_books: str | None = Form(None),
    inc_cat_cb: list[str] = Form(default=[]),
    exc_cat_cb: list[str] = Form(default=[]),
):
    save_ui_settings(
        {
            "scan_interval_min": scan_interval_min,
            "max_alerts_per_tier": max_alerts_per_tier,
            "min_discount": min_discount,
            "min_original_price": min_original_price,
            "min_savings": min_savings,
            "min_haul_price": min_haul_price,
            "haul_keywords": haul_keywords,
            "include_categories": _merge_cats(include_categories.strip(), inc_cat_cb),
            "exclude_categories": _merge_cats(exclude_categories.strip(), exc_cat_cb),
            "include_brands": include_brands.strip(),
            "exclude_brands": exclude_brands.strip(),
            "title_keywords": title_keywords.strip(),
            "exclude_keywords": exclude_keywords.strip(),
            "min_review_count": min_review_count,
            "min_rating": min_rating,
            "reject_promotions": reject_promotions == "1",
            "reject_business": reject_business == "1",
            "exclude_books": exclude_books == "1",
        }
    )
    _status["last_message"] = f"Saved. Scan every {scan_interval_min} min."
    return RedirectResponse("/", status_code=303)


@app.post("/start")
def start():
    result = _try_start_monitor(message="Started.")
    if result == "missing_credentials":
        _status["last_message"] = (
            "Cannot start \u2014 set Keepa API key + Discord webhooks in the UI (or Render env)."
        )
    elif result == "already_running":
        pass
    return RedirectResponse("/", status_code=303)


@app.post("/stop")
def stop():
    global _monitor
    if _monitor and _is_running():
        _status["stopping"] = True
        _status["last_message"] = "Stopping\u2026 finishing current Keepa call, then idle."
        _monitor.request_stop()
        try:
            _monitor.keepa.close()
        except Exception:
            pass
    elif not _is_running():
        _status["stopping"] = False
        _status["last_message"] = "Already stopped."
    return RedirectResponse("/", status_code=303)


@app.post("/once")
def once():
    from monitor import Monitor

    s = load_settings()
    if not s.keepa_api_key or not s.tiers:
        _status["last_message"] = (
            "Cannot scan \u2014 set Keepa API key + Discord webhooks in the UI (or Render env)."
        )
        return RedirectResponse("/", status_code=303)

    def _run() -> None:
        mon = Monitor(s)
        try:
            n = mon.run_once()
            _status["last_message"] = f"One-shot scan done \u2014 {n} alert(s) sent."
            _status["scanning"] = False
        except Exception as exc:  # noqa: BLE001
            _status["last_message"] = f"Scan error: {exc}"
            _status["scanning"] = False
        finally:
            mon.close()
            _status["scanning"] = False

    threading.Thread(target=_run, daemon=True).start()
    _status["scanning"] = True
    _status["last_message"] = "One-shot scan started\u2026"
    return RedirectResponse("/", status_code=303)


@app.get("/debug-scrape")
def debug_scrape():
    """Fetch one Haul search page and return parsed listings (for debugging)."""
    import json
    from haul_scraper import HaulScraper, parse_search_html
    s = load_settings()
    scraper = HaulScraper(s)
    try:
        url = "https://www.amazon.com/s?i=bazaar&k=automotive"
        html = scraper.fetch_html(url)
        listings = parse_search_html(html, page_url=url)
        priced = [x for x in listings if x.price is not None]
        return PlainTextResponse(
            "URL: " + url + "\n"
            "HTML bytes: " + str(len(html)) + "\n"
            "Listings: " + str(len(listings)) + "\n"
            "Priced: " + str(len(priced)) + "\n"
            "Blocked markers: " + str(any(m in html.lower() for m in ("captcha", "robot", "automated access"))) + "\n"
            "First 5:\n" + "\n".join(
                f"  {x.asin} ${x.price} {x.title[:60]}" for x in listings[:5]
            ) + "\n"
            "First 3 priced:\n" + "\n".join(
                f"  {x.asin} ${x.price} {x.title[:60]}" for x in priced[:3]
            )
        )
    except Exception as exc:  # noqa: BLE001
        return PlainTextResponse("Error: " + str(exc))
    finally:
        scraper.close()


@app.get("/debug-scan")
def debug_scan():
    """Run a quick scan (1 page) synchronously and return results as text."""
    import io
    import contextlib
    from monitor import Monitor

    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG)

    try:
        s = load_settings()
        if not s.keepa_api_key:
            return PlainTextResponse("ERROR: KEEPA_API_KEY not set")
        if not s.webhooks:
            return PlainTextResponse("ERROR: No webhooks configured. s.webhooks=" + str(s.webhooks) + " s.tiers=" + str(s.tiers))

        # Temporarily limit to 1 page for speed
        orig_pages = s.haul_pages
        s.haul_pages = 1
        mon = Monitor(s)
        try:
            n = mon.run_once()
            result = buf.getvalue()
            result += "\n\n=== RESULT: " + str(n) + " alert(s) sent ===\n"
            result += "Webhooks: " + str(list(s.webhooks.keys())) + "\n"
            result += "Tiers: " + str(s.tiers) + "\n"
            result += "Last message: " + str(_status.get("last_message", "")) + "\n"
            return PlainTextResponse(result)
        except Exception as exc:  # noqa: BLE001
            import traceback
            result = buf.getvalue()
            result += "\n\n=== EXCEPTION ===\n" + traceback.format_exc()
            return PlainTextResponse(result)
        finally:
            mon.close()
            s.haul_pages = orig_pages
    finally:
        root_logger.removeHandler(handler)


@app.post("/force-scan")
def force_scan():
    from monitor import Monitor

    s = load_settings()
    if not s.keepa_api_key or not s.tiers:
        _status["last_message"] = "Cannot scan \u2014 set Keepa API key + Discord webhooks."
        return RedirectResponse("/", status_code=303)

    def _run() -> None:
        mon = Monitor(s)
        try:
            n = mon.run_once(force=True)
            _status["last_message"] = f"Force scan done \u2014 {n} alert(s) sent (dedup bypassed)."
        except Exception as exc:  # noqa: BLE001
            _status["last_message"] = f"Scan error: {exc}"
        finally:
            mon.close()

    threading.Thread(target=_run, daemon=True).start()
    _status["last_message"] = "Force scan started\u2026"
    return RedirectResponse("/", status_code=303)


@app.get("/health")
def health():
    return PlainTextResponse("ok")


LOGIN_PAGE = """<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Login \u2014 Amazon Haul Price Monitor</title>
<style>body{font-family:system-ui,sans-serif;max-width:360px;margin:64px auto;padding:0 16px;}label{display:block;margin-bottom:8px;font-weight:600;}input{width:100%;padding:10px;box-sizing:border-box;margin-bottom:12px;}button{padding:10px 16px;cursor:pointer;}.err{color:#c62828;margin-bottom:12px;}</style>
</head><body>
<h1>Amazon Haul Price Monitor</h1><p>Enter the UI password to continue.</p>
__ERROR__
<form method="post" action="/login">
  <label>Password</label>
  <input type="password" name="password" autofocus required/>
  <button type="submit">Log in</button>
</form>
</body></html>"""


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    if not _ui_password():
        return RedirectResponse("/", status_code=303)
    if _is_authed(request):
        return RedirectResponse("/", status_code=303)
    return LOGIN_PAGE.replace("__ERROR__", "")


@app.post("/login")
def login_post(password: str = Form("")):
    pw = _ui_password()
    if not pw:
        return RedirectResponse("/", status_code=303)
    if not _password_ok((password or "").strip(), pw):
        return HTMLResponse(
            LOGIN_PAGE.replace("__ERROR__", '<p class="err">Wrong password.</p>'),
            status_code=401,
        )
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        AUTH_COOKIE,
        _auth_token(pw),
        httponly=True,
        samesite="lax",
        secure=bool(os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL")),
        max_age=60 * 60 * 24 * 30,
    )
    return resp


def main() -> None:
    host = (os.getenv("HOST") or "").strip() or ("0.0.0.0" if _is_cloud() else "127.0.0.1")
    try:
        port = int((os.getenv("PORT") or "8787").strip())
    except ValueError:
        port = 8787
    open_browser = not _is_cloud() and host in ("127.0.0.1", "localhost")
    if open_browser:
        import time
        import webbrowser

        def _open() -> None:
            time.sleep(1.2)
            webbrowser.open(f"http://127.0.0.1:{port}")

        threading.Thread(target=_open, daemon=True).start()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()

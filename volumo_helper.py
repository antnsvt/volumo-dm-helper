#!/usr/bin/env python3
"""Volumo DM Helper - local Windows mode.

Serves a picker + dashboard on http://127.0.0.1:8765/. All scrape logic lives
in _volumo_core; this file only owns the HTTP server, the HTML pages, and the
STATE machine that the picker poll/dashboard read from.

The tool NEVER sends Instagram messages itself. All DMs go out manually.
"""
import http.server
import json
import socketserver
import sys
import threading
import time
import webbrowser
from datetime import date
from pathlib import Path

try:
    import requests  # noqa: F401  (imported here so the import check fires before _volumo_core)
    from bs4 import BeautifulSoup  # noqa: F401
except ImportError:
    print("\nMissing libraries. Please double-click setup.bat first.\n")
    input("Press Enter to close...")
    sys.exit(1)

from _volumo_core import (
    BASE,
    build_creators,
    discover_genres,
    ensure_files,
    fetch,
    load_selected_genres,
    load_templates,
    mark_sent,
    render_message,
    save_handle,
    save_selected_genres,
    search_missing_instagram_handles,
)

PORT = 8765
HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
TEMPLATE_FILE = DATA_DIR / "template.txt"

STATE = {
    "status": "idle",          # idle | scraping | done | error
    "message": "Ready.",
    "creators": [],
    "date": "",
    "error": None,
    "available_genres": [],    # discovered from the homepage
    "last_selection": [],      # slugs the user picked previously
    "source_urls": {},         # origin label -> page URL (for dashboard links)
}
STATE_LOCK = threading.Lock()


def _set_state(**kw):
    with STATE_LOCK:
        for k, v in kw.items():
            STATE[k] = v


def _progress(msg):
    _set_state(message=msg)
    print(msg)


def run_scrape(sources):
    """Background thread: scrape, populate STATE['creators']."""
    try:
        _set_state(status="scraping", error=None, creators=[], date="", source_urls={})
        creators, source_urls = build_creators(sources, DATA_DIR, _progress)
        search_missing_instagram_handles(DATA_DIR, creators, progress_cb=_progress)
        tmpls = load_templates(TEMPLATE_FILE)
        for c in creators:
            c["message"] = render_message(c, tmpls)
        _set_state(
            status="done",
            creators=creators,
            source_urls=source_urls,
            date=date.today().isoformat(),
            message=f"Done. {len(creators)} creator(s).",
        )
        save_selected_genres(DATA_DIR, [s["slug"] for s in sources])
        _set_state(last_selection=[s["slug"] for s in sources])
    except Exception as e:
        _set_state(status="error", error=str(e), message=f"Error: {e}")
        print(f"Scrape failed: {e}")


# ---------- HTML ----------

PICKER_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Volumo DM Helper — Choose pages</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "Segoe UI", system-ui, sans-serif; background: #141417; color: #eee; margin: 0; padding: 24px; max-width: 900px; margin-left: auto; margin-right: auto; }
  h1 { margin: 0 0 4px; font-size: 22px; }
  .sub { color: #888; font-size: 13px; margin-bottom: 20px; }
  .section { background: #1f1f24; border-radius: 12px; padding: 18px; margin-bottom: 16px; border: 1px solid #2c2c33; }
  .section h2 { margin: 0 0 12px; font-size: 16px; color: #ccc; }
  .row-buttons { margin-bottom: 12px; display: flex; gap: 8px; }
  .genres { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 6px 16px; }
  label.cb { display: flex; align-items: center; gap: 8px; padding: 6px 0; cursor: pointer; font-size: 14px; color: #ddd; }
  label.cb:hover { color: #fff; }
  label.cb input { transform: scale(1.15); cursor: pointer; }
  button, .btn { background: #4a9fe0; color: #fff; border: 0; padding: 10px 18px; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 600; }
  button:hover { background: #5fb0f0; }
  button.secondary { background: #383843; }
  button.secondary:hover { background: #4a4a55; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .footer { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 4px 0; }
  .count { color: #888; font-size: 13px; }
  .progress { background: #1f1f24; border-radius: 12px; padding: 18px; margin-top: 16px; border: 1px solid #2c2c33; }
  .progress h2 { margin: 0 0 8px; font-size: 16px; }
  .progress-msg { color: #ccc; font-size: 13px; font-family: ui-monospace, "SF Mono", monospace; word-break: break-word; min-height: 1.4em; }
  .spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #4a9fe0; border-right-color: transparent; border-radius: 50%; animation: spin 0.7s linear infinite; margin-left: 8px; vertical-align: middle; }
  @keyframes spin { to { transform: rotate(360deg); } }
  .hidden { display: none !important; }
  .error { color: #f77; }
  a.btn-link { color: #6cf; text-decoration: none; font-size: 13px; }
  a.btn-link:hover { text-decoration: underline; }
</style>
</head>
<body>
<h1>Volumo DM Helper</h1>
<p class="sub">Pick which Volumo pages to check today. Featured creators from all selected pages will be merged on the dashboard (duplicates removed).</p>

<div id="picker">
  <div class="section">
    <h2>Featured pages</h2>
    <label class="cb"><input type="checkbox" id="cb-main"> <span>Volumo main page</span></label>
  </div>

  <div class="section">
    <h2>Genres</h2>
    <div class="row-buttons">
      <button class="secondary" onclick="setAllGenres(true)">Select all</button>
      <button class="secondary" onclick="setAllGenres(false)">Clear all</button>
    </div>
    <div id="genres" class="genres">Loading genres from volumo.com...</div>
  </div>

  <div class="footer">
    <div class="count" id="count">0 pages selected</div>
    <button id="start" disabled>Start scraping</button>
  </div>
</div>

<div id="progress-box" class="progress hidden">
  <h2>Working... <span class="spinner"></span></h2>
  <div id="progress-msg" class="progress-msg">Starting...</div>
</div>

<script>
let genres = [];

async function loadGenres() {
  // If a scrape is already running (e.g. user reopened browser), jump to progress.
  const sr = await fetch('/status');
  const ss = await sr.json();
  if (ss.status === 'scraping') {
    document.getElementById('picker').classList.add('hidden');
    document.getElementById('progress-box').classList.remove('hidden');
    pollStatus();
    return;
  }
  if (ss.status === 'done') {
    document.getElementById('progress-box').classList.remove('hidden');
    document.getElementById('progress-box').innerHTML =
      '<h2>A dashboard is ready</h2>' +
      '<div class="progress-msg">From your last scrape. ' +
      '<a class="btn-link" href="/dashboard">Open dashboard →</a> &nbsp; or pick pages below to re-scrape.</div>';
  }
  try {
    const r = await fetch('/genres');
    const data = await r.json();
    genres = data.genres || [];
    const last = new Set(data.last_selection || ['main']);
    document.getElementById('cb-main').checked = last.has('main');
    document.getElementById('cb-main').addEventListener('change', updateCount);
    const grid = document.getElementById('genres');
    grid.innerHTML = '';
    for (const g of genres) {
      const lbl = document.createElement('label');
      lbl.className = 'cb';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.dataset.slug = g.slug;
      cb.dataset.name = g.name;
      cb.checked = last.has(g.slug);
      cb.addEventListener('change', updateCount);
      const sp = document.createElement('span');
      sp.textContent = g.name;
      lbl.appendChild(cb);
      lbl.appendChild(sp);
      grid.appendChild(lbl);
    }
    updateCount();
  } catch (e) {
    document.getElementById('genres').innerHTML = '<div class="error">Failed to load genres: ' + e.message + '</div>';
  }
}

function setAllGenres(checked) {
  document.querySelectorAll('#genres input[type=checkbox]').forEach(cb => { cb.checked = checked; });
  updateCount();
}

function getSelected() {
  const out = [];
  if (document.getElementById('cb-main').checked) out.push({slug: 'main', name: 'Main page'});
  document.querySelectorAll('#genres input[type=checkbox]:checked').forEach(cb => {
    out.push({slug: cb.dataset.slug, name: cb.dataset.name});
  });
  return out;
}

function updateCount() {
  const sel = getSelected();
  document.getElementById('count').textContent = sel.length + ' page' + (sel.length === 1 ? '' : 's') + ' selected';
  document.getElementById('start').disabled = sel.length === 0;
}

async function start() {
  const sources = getSelected();
  if (!sources.length) return;
  document.getElementById('start').disabled = true;
  document.getElementById('picker').classList.add('hidden');
  document.getElementById('progress-box').classList.remove('hidden');
  document.getElementById('progress-box').innerHTML =
    '<h2>Working... <span class="spinner"></span></h2>' +
    '<div id="progress-msg" class="progress-msg">Starting...</div>';
  await fetch('/start-scrape', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({sources}),
  });
  pollStatus();
}

async function pollStatus() {
  const r = await fetch('/status');
  const s = await r.json();
  const msgEl = document.getElementById('progress-msg');
  if (msgEl) msgEl.textContent = s.message || s.status;
  if (s.status === 'done') {
    window.location = '/dashboard';
  } else if (s.status === 'error') {
    msgEl.textContent = 'Error: ' + (s.error || 'unknown');
    msgEl.className = 'progress-msg error';
  } else {
    setTimeout(pollStatus, 500);
  }
}

document.getElementById('start').onclick = start;
loadGenres();
</script>
</body>
</html>
"""


DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Volumo DM Helper</title>
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, "Segoe UI", system-ui, sans-serif; background: #141417; color: #eee; margin: 0; padding: 24px; }
  .head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }
  h1 { margin: 0 0 4px; font-size: 22px; }
  .meta { color: #888; font-size: 13px; }
  .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); }
  .card { background: #1f1f24; border-radius: 12px; padding: 16px; border: 1px solid #2c2c33; transition: opacity 0.15s; }
  .card.sent { opacity: 0.42; }
  .name { font-size: 17px; font-weight: 600; margin-bottom: 8px; }
  .kind { display: inline-block; font-size: 10px; padding: 3px 8px; background: #383843; border-radius: 10px; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
  .kind.month { background: #3a8050; }
  .kind.release { background: #3a5a80; }
  .origins { font-size: 11px; color: #888; margin-bottom: 8px; }
  .origins a { color: #6cf; text-decoration: none; }
  .origins a:hover { text-decoration: underline; }
  .ig { font-size: 13px; margin: 4px 0 10px; word-break: break-all; }
  .ig a { color: #6cf; text-decoration: none; }
  .missing { color: #f77; }
  .source { color: #888; font-size: 11px; margin-left: 4px; }
  .warn { background: #c44; color: #fff; padding: 1px 7px; border-radius: 8px; font-size: 11px; margin-left: 6px; font-weight: 700; cursor: help; }
  textarea { width: 100%; min-height: 120px; background: #141417; color: #eee; border: 1px solid #333; border-radius: 6px; padding: 10px; font: 13px/1.4 inherit; resize: vertical; }
  .row { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; align-items: center; }
  button, .btn { background: #4a9fe0; color: #fff; border: 0; padding: 8px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; text-decoration: none; display: inline-block; }
  button:hover, .btn:hover { background: #5fb0f0; }
  button.secondary, .btn.secondary { background: #383843; }
  button.secondary:hover, .btn.secondary:hover { background: #4a4a55; }
  input.handle { width: 100%; padding: 8px 10px; background: #141417; color: #eee; border: 1px solid #333; border-radius: 6px; font: inherit; }
  label.sent-toggle { display: flex; align-items: center; gap: 6px; font-size: 13px; cursor: pointer; color: #aaa; }
  .toast { position: fixed; bottom: 20px; right: 20px; background: #4a9fe0; padding: 10px 18px; border-radius: 8px; opacity: 0; transition: opacity 0.18s; pointer-events: none; font-size: 14px; }
  .toast.show { opacity: 1; }
  .empty { color: #888; padding: 40px; text-align: center; }
  .filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
  .filter-chip { background: #2c2c33; color: #ccc; padding: 4px 10px; border-radius: 12px; font-size: 12px; cursor: pointer; border: 1px solid transparent; }
  .filter-chip.active { background: #4a9fe0; color: #fff; }
  .filter-chip:hover { border-color: #4a9fe0; }
</style>
</head>
<body>
<div class="head">
  <div>
    <h1>Volumo DM Helper</h1>
    <div class="meta" id="meta">Loading...</div>
  </div>
  <div>
    <a class="btn secondary" href="/picker">Change pages</a>
  </div>
</div>
<div class="filters" id="filters"></div>
<div class="grid" id="grid"></div>
<div class="toast" id="toast"></div>
<script>
let allCreators = [];
let sourceUrls = {};
let activeFilter = null;

async function loadData() {
  const r = await fetch('/data.json?t=' + Date.now());
  return r.json();
}
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1400);
}
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  if (attrs) for (const k in attrs) {
    if (k === 'class') e.className = attrs[k];
    else if (k === 'onclick') e.onclick = attrs[k];
    else e.setAttribute(k, attrs[k]);
  }
  for (const c of kids) {
    if (c == null) continue;
    e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return e;
}
async function api(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  return r.json();
}

function renderFilters() {
  const origins = new Set();
  for (const c of allCreators) for (const o of (c.origins || [])) origins.add(o);
  const sorted = Array.from(origins).sort();
  const f = document.getElementById('filters');
  f.innerHTML = '';
  if (sorted.length <= 1) return;
  const all = el('span', {class: 'filter-chip' + (activeFilter === null ? ' active' : '')}, 'All');
  all.onclick = () => { activeFilter = null; renderFilters(); renderCards(); };
  f.appendChild(all);
  for (const o of sorted) {
    const chip = el('span', {class: 'filter-chip' + (activeFilter === o ? ' active' : '')}, o);
    chip.onclick = () => { activeFilter = o; renderFilters(); renderCards(); };
    f.appendChild(chip);
  }
}

function renderCards() {
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  const list = activeFilter
    ? allCreators.filter(c => (c.origins || []).includes(activeFilter))
    : allCreators;
  if (!list.length) {
    grid.appendChild(el('div', {class: 'empty'}, 'No creators to show.'));
    return;
  }
  for (const c of list) {
    const card = el('div', {class: 'card' + (c.sent_today ? ' sent' : '')});
    const kindClass = c.kind_key.includes('month') ? 'month' : (c.kind_key.includes('release') ? 'release' : '');
    card.appendChild(el('div', {class: 'kind ' + kindClass}, c.display_kind));
    card.appendChild(el('div', {class: 'name'}, c.name));
    if (c.origins && c.origins.length) {
      const orow = el('div', {class: 'origins'});
      orow.appendChild(document.createTextNode('Featured on: '));
      c.origins.forEach((o, i) => {
        if (i > 0) orow.appendChild(document.createTextNode(', '));
        const url = sourceUrls[o];
        if (url) {
          orow.appendChild(el('a', {href: url, target: '_blank', title: 'Open ' + o + ' (for screenshot)'}, o));
        } else {
          orow.appendChild(document.createTextNode(o));
        }
      });
      card.appendChild(orow);
    }
    if (c.instagram) {
      const ig = el('div', {class: 'ig'});
      const link = el('a', {href: 'https://www.instagram.com/' + c.instagram + '/', target: '_blank'}, '@' + c.instagram);
      ig.appendChild(link);
      if (c.confidence === 'weak') {
        ig.appendChild(el('span', {class: 'warn', title: "IG bio didn't mention music or this artist's genre — double-check this handle"}, '!'));
      }
      ig.appendChild(el('span', {class: 'source'}, '(' + (c.source || '') + ')'));
      card.appendChild(ig);
    } else {
      card.appendChild(el('div', {class: 'ig missing'}, 'Instagram unknown — look up below'));
    }
    const ta = el('textarea');
    ta.value = c.message;
    card.appendChild(ta);

    // Prominent "Open page for screenshot" button at the top of the actions.
    const screenshotOrigin = (c.origins || []).find(o => o && o.toLowerCase() !== 'main page')
                            || (c.origins || [])[0];
    const screenshotUrl = screenshotOrigin ? sourceUrls[screenshotOrigin] : null;
    if (screenshotUrl) {
      const screenshotRow = el('div', {class: 'row'});
      screenshotRow.appendChild(el('a', {
        class: 'btn',
        href: screenshotUrl,
        target: '_blank',
      }, 'Open ' + screenshotOrigin + ' page'));
      card.appendChild(screenshotRow);
    }

    const row = el('div', {class: 'row'});
    const copyBtn = el('button', {}, 'Copy message');
    copyBtn.onclick = () => { navigator.clipboard.writeText(ta.value); toast('Copied!'); };
    row.appendChild(copyBtn);

    if (c.instagram) {
      row.appendChild(el('a', {
        class: 'btn secondary',
        href: 'https://www.instagram.com/' + c.instagram + '/',
        target: '_blank',
      }, 'Open Instagram'));
    } else {
      row.appendChild(el('a', {
        class: 'btn secondary',
        href: 'https://www.google.com/search?q=' + encodeURIComponent('"' + c.name + '" instagram'),
        target: '_blank',
      }, 'Google search'));
    }
    if (c.profile_url) {
      row.appendChild(el('a', {
        class: 'btn secondary',
        href: c.profile_url,
        target: '_blank',
      }, 'Artist page'));
    }
    card.appendChild(row);

    if (!c.instagram || c.confidence === 'weak') {
      const hrow = el('div', {class: 'row'});
      const inp = el('input', {
        class: 'handle',
        placeholder: c.confidence === 'weak'
          ? 'Override handle (clears the ! flag) — press Enter'
          : 'Paste IG handle here, press Enter',
      });
      if (c.confidence === 'weak' && c.instagram) inp.value = c.instagram;
      inp.onkeydown = async (e) => {
        if (e.key === 'Enter' && inp.value.trim()) {
          await api('/save-handle', {name: c.name, handle: inp.value.trim()});
          toast('Saved!');
          refresh();
        }
      };
      hrow.appendChild(inp);
      card.appendChild(hrow);
    }

    const sentRow = el('div', {class: 'row'});
    const lbl = el('label', {class: 'sent-toggle'});
    const cb = el('input', {type: 'checkbox'});
    cb.checked = c.sent_today;
    cb.onchange = async () => {
      await api('/mark-sent', {name: c.name, sent: cb.checked});
      refresh();
    };
    lbl.appendChild(cb);
    lbl.appendChild(document.createTextNode(' Mark as sent today'));
    sentRow.appendChild(lbl);
    card.appendChild(sentRow);

    grid.appendChild(card);
  }
}

function renderMeta(data) {
  const total = allCreators.length;
  const withIG = allCreators.filter(c => c.instagram).length;
  const flagged = allCreators.filter(c => c.confidence === 'weak').length;
  const sent = allCreators.filter(c => c.sent_today).length;
  let line = data.date + ' — ' + total + ' creators, ' + withIG + ' with Instagram';
  if (flagged) line += ', ' + flagged + ' flagged (!)';
  line += ', ' + sent + ' messaged today';
  document.getElementById('meta').textContent = line;
}

async function refresh() {
  try {
    const d = await loadData();
    allCreators = d.creators || [];
    sourceUrls = d.source_urls || {};
    renderMeta(d);
    renderFilters();
    renderCards();
  } catch (e) {
    document.getElementById('meta').textContent = 'Error loading data: ' + e.message;
  }
}
refresh();
</script>
</body>
</html>
"""


# ---------- Server ----------

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        body_bytes = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with STATE_LOCK:
                status = STATE["status"]
            self._send(200, DASHBOARD_HTML if status == "done" else PICKER_HTML)
        elif self.path == "/dashboard":
            self._send(200, DASHBOARD_HTML)
        elif self.path == "/picker":
            self._send(200, PICKER_HTML)
        elif self.path.startswith("/data.json"):
            with STATE_LOCK:
                payload = {
                    "creators": STATE["creators"],
                    "date": STATE["date"],
                    "status": STATE["status"],
                    "source_urls": STATE["source_urls"],
                }
            self._send(200, json.dumps(payload), "application/json")
        elif self.path.startswith("/status"):
            with STATE_LOCK:
                payload = {
                    "status": STATE["status"],
                    "message": STATE["message"],
                    "error": STATE["error"],
                }
            self._send(200, json.dumps(payload), "application/json")
        elif self.path.startswith("/genres"):
            with STATE_LOCK:
                avail = list(STATE["available_genres"])
            if not avail:
                try:
                    home = fetch(BASE + "/")
                    avail = discover_genres(home)
                    _set_state(available_genres=avail)
                except Exception as e:
                    self._send(500, json.dumps({"error": str(e)}), "application/json")
                    return
            payload = {"genres": avail, "last_selection": load_selected_genres(DATA_DIR)}
            self._send(200, json.dumps(payload), "application/json")
        else:
            self._send(404, "Not found", "text/plain")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}

        if self.path == "/start-scrape":
            with STATE_LOCK:
                if STATE["status"] == "scraping":
                    self._send(409, json.dumps({"error": "already scraping"}),
                               "application/json")
                    return
            raw_sources = data.get("sources") or []
            sources = []
            for s in raw_sources:
                if isinstance(s, dict) and s.get("slug"):
                    sources.append({"slug": s["slug"], "name": s.get("name") or s["slug"]})
                elif isinstance(s, str):
                    sources.append({"slug": s, "name": s})
            if not sources:
                sources = [{"slug": "main", "name": "Main page"}]
            threading.Thread(target=run_scrape, args=(sources,), daemon=True).start()
            self._send(200, json.dumps({"ok": True}), "application/json")
        elif self.path == "/save-handle":
            name = (data.get("name") or "").strip()
            handle = (data.get("handle") or "").strip().lstrip("@")
            if name and handle:
                save_handle(DATA_DIR, name, handle, "verified")
                tmpls = load_templates(TEMPLATE_FILE)
                with STATE_LOCK:
                    for c in STATE["creators"]:
                        if c["name"].strip().lower() == name.strip().lower():
                            c["instagram"] = handle
                            c["source"] = "manual"
                            c["confidence"] = "verified"
                            c["message"] = render_message(c, tmpls)
            self._send(200, json.dumps({"ok": True}), "application/json")
        elif self.path == "/mark-sent":
            name = (data.get("name") or "").strip()
            sent = bool(data.get("sent", True))
            if name:
                mark_sent(DATA_DIR, name, sent)
                with STATE_LOCK:
                    for c in STATE["creators"]:
                        if c["name"].strip().lower() == name.strip().lower():
                            c["sent_today"] = sent
            self._send(200, json.dumps({"ok": True}), "application/json")
        else:
            self._send(404, "Not found", "text/plain")


class ReusableServer(socketserver.TCPServer):
    allow_reuse_address = True


def serve():
    httpd = ReusableServer(("127.0.0.1", PORT), Handler)
    print(f"\nServer running at http://127.0.0.1:{PORT}/")
    print("Pick which Volumo pages to scrape in the browser tab that just opened.")
    print("Leave this window open. Close it to stop.\n")
    httpd.serve_forever()


# ---------- Main ----------

def main():
    ensure_files(DATA_DIR)
    _set_state(last_selection=load_selected_genres(DATA_DIR))
    print("Volumo DM Helper")
    print("=" * 40)
    threading.Thread(
        target=lambda: (time.sleep(0.8), webbrowser.open(f"http://127.0.0.1:{PORT}/")),
        daemon=True,
    ).start()
    try:
        serve()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

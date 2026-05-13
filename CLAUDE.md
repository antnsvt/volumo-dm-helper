# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Two-mode tool that scrapes volumo.com for featured creators, looks up their Instagram handles, and renders personalized DMs the user copies/pastes manually:

- **Local mode** (`run.bat` → `volumo_helper.py`): Python HTTP server on `127.0.0.1:8765`. Used on the Windows PC.
- **Cloud mode** (`web/` deployed to GitHub Pages + GitHub Actions scrapes): PWA accessible from any device, including installed-to-home-screen on iPhone Safari.

Both modes share the same scrape/IG-lookup library (`_volumo_core.py`) and the same data files under `data/`. Code is in `main`; data lives on a parallel `state` branch and is `.gitignore`d on `main`.

**Hard constraint — do not break this:** the tool NEVER sends Instagram messages itself. All DMs go out manually from @volumomusic. Any change that introduces automated sending will get the brand account banned. Outbound HTTP is read-only scraping plus Playwright navigation for IG profile *verification* only.

## Repo layout

```
volumo_helper.py        Local-mode HTTP server. Owns STATE + HTML pages only.
_volumo_core.py         Shared library: scrape, parsers, IG verification, CSV
                          helpers, templates. Mode-agnostic.
scrape_cloud.py         Cloud entrypoint (GH Actions). CLI; reads VOLUMO_SOURCES
                          env var, writes data/state.json + cache.csv + genres.json.
mutate.py               Cloud mutation handler (GH Actions). Applies a single
                          save-handle / mark-sent edit from repository_dispatch.
web/                    Static frontend (GH Pages). PWA-installable on iOS.
  index.html, app.js, style.css, manifest.webmanifest, sw.js, icons/
.github/workflows/
  scrape.yml            workflow_dispatch → runs scrape_cloud.py → pushes data/
                          to state branch. Concurrency-grouped so two devices
                          can't race the commit.
  mutate.yml            repository_dispatch (save-handle | mark-sent) → mutate.py
                          → pushes data/ to state branch.
  pages.yml             push to main with web/** changes → deploys web/ to Pages.
data/                   Untracked on main; canonical copy lives on state branch.
                        cache.csv, sent_log.csv, template.txt, selected_genres.txt,
                          state.json, genres.json.
run.bat, setup.bat      Local-mode launchers (Windows).
README.txt              End-user instructions (both modes).
```

## Architecture

### Local mode flow
1. `run.bat` → `python volumo_helper.py` → `main()` starts HTTP server on 127.0.0.1:8765 + auto-opens the browser.
2. Picker UI POSTs to `/start-scrape` → spawns daemon thread running `run_scrape()`.
3. `run_scrape` calls `_volumo_core.build_creators(sources, DATA_DIR, _progress)` then `_volumo_core.search_missing_instagram_handles(DATA_DIR, creators, progress_cb=_progress)`, updates module-global `STATE` (guarded by `STATE_LOCK`).
4. Browser polls `/status`, then `/data.json` from the dashboard. Mutations (`/save-handle`, `/mark-sent`) update both the relevant CSV in `data/` and the in-memory `STATE["creators"]`.

### Cloud mode flow
1. User opens `<user>.github.io/<repo>/` on phone/Mac → `web/app.js` boots.
2. First load prompts for repo owner + repo name + fine-grained PAT, stored in `localStorage`. PAT scopes required: **Actions read/write**, **Contents read/write**, **Metadata read**.
3. `app.js` GETs `data/state.json` (via `/repos/{o}/{r}/contents/data/state.json?ref=state` — Contents API, not `raw.githubusercontent.com`, because raw has a ~5min CDN cache that breaks "I just changed it" UX).
4. **Scrape**: dashboard "Scrape now" → POST `/actions/workflows/scrape.yml/dispatches` with `inputs.sources="main,tech-house,..."`. `scrape.yml` checks out `main` (code) + `state` branch (data), runs `scrape_cloud.py`, commits `data/` back to `state`. `app.js` polls `/actions/workflows/scrape.yml/runs` until no active run, then refetches `state.json`.
5. **Mutations**: "Save handle" / "Mark sent" → POST `/dispatches` with `event_type` = `save-handle` or `mark-sent`. `mutate.yml` runs `mutate.py` which calls into `_volumo_core.save_handle` / `_volumo_core.mark_sent` on the state-branch checkout. ~5–15s round-trip per click; UI updates optimistically and re-reads state after a short delay.

### Why two branches
- `main` = code (Python, web, workflows). `web/**` changes trigger `pages.yml` to redeploy GH Pages.
- `state` = data only (`data/*`). Untracked on main, so commits from `scrape.yml` and `mutate.yml` never trigger Pages rebuilds.
- The frontend reads from `?ref=state` on the Contents API. Workflows check out `state` into `_state/` alongside the main checkout.

### Scrape pipeline (`_volumo_core.build_creators`)
Two parallel phases + sequential merge — unchanged from the original architecture:

- **Phase 1** — fetch source pages in a `ThreadPoolExecutor`. `parse_page()` walks `<h2>/<h3>/<a>` in document order and keeps only links whose most recent section heading is in `ALLOWED_SECTIONS`.
- **Dedup by URL**, merging `extra_origins`.
- **Phase 2** — enrich in another thread pool: release-page → profile URL; profile-page → IG handle. Cached handles short-circuit. **New**: for fresh volumo-source handles, `_confidence_via_requests()` does a lightweight `requests` GET of `instagram.com/<handle>/` and reads `og:description` to compute `confidence` ("verified" | "weak").
- **Sequential merge** by lowercased name with `KIND_PRIORITY` (`Artist of the Month` < `Featured Release` < `Featured Artist`).
- Then `search_missing_instagram_handles` runs N concurrent Playwright pages trying handle variants from `_name_variants()`. `_async_verify_ig_handle` returns `(matched, confidence)` — the bio check fires inside Playwright via `_extract_ig_bio_async`.

### IG bio sanity check
`_bio_confidence(bio, artist_name, origins) -> "verified" | "weak"`:
- Empty bio → `"verified"` (Instagram increasingly serves a login wall to headless browsers and private accounts often have empty meta; flagging on absence creates more false positives than missed flags).
- Bio contains any term from `MUSIC_KEYWORDS` → `"verified"`.
- Bio contains all tokens (≥3 chars) of any origin name (e.g. "tech" AND "house" for "Tech House") → `"verified"`.
- Otherwise → `"weak"`.

Dashboard renders a red `!` pill next to weak handles. The override input is pre-filled with the suspect handle so the user can correct it in one tap. Cache still stores weak handles (4th column `confidence`) so we don't redo Playwright work on next run.

### Cache schema
`data/cache.csv`: `artist_name, instagram_handle, last_updated, confidence`. `load_cache()` reads legacy 3-column files (defaults missing `confidence` to `"verified"`); `save_handle()` always writes 4 columns, so files upgrade in place on first write.

### Templates
`data/template.txt` uses `[kind]...[/kind]` blocks; same kind can repeat for variants. `render_message` picks variants deterministically by hashing the artist name (`sum(ord(ch)) % len(variants)`).

## Adding a new section type (Volumo HTML changes)
If Volumo adds a new featured section (e.g. "Editor's picks"), update three things in `_volumo_core.py`:
1. `ALLOWED_SECTIONS` — heading → (`"artist"` | `"release"`, display label).
2. `KIND_PRIORITY` — where the new label sorts when an artist appears in multiple sections.
3. `_kind_key()` — the kind_key string the templates use (`render_message` resolves it).

## Known doc/code mismatch
The README still references per-creator page screenshots (Pillow + Chromium for cropped section screenshots, a `screenshots/` directory, "Download / Copy image" buttons). **That feature is not implemented.** Pillow is only used at setup time to generate PWA icons. If the user asks about screenshots, confirm before silently building it — it's a meaningful amount of work (cookie-banner dismissal, section-aware cropping, image hosting).

## When extending the cloud frontend
- All API calls go through `ghGet`/`ghPost` in `web/app.js` — they inject the PAT bearer header.
- Mutations should use `repository_dispatch` (not direct Contents API PUTs) so `mutate.yml`'s concurrency group serializes writes. PUTs from two devices would race the file SHA.
- The `state` branch is the only place data lives in git. Never push data files to `main`.
- The service worker (`web/sw.js`) caches the shell only — never API responses, because they're per-PAT and time-sensitive.
- PAT in localStorage is XSS-exfiltratable. Never add tracking scripts, analytics, or third-party JS to `web/`.

## Running locally after the refactor
`run.bat` is unchanged from the user's perspective: launches the local server, opens the browser. The only thing that moved is data — `cache.csv` etc. now live in `data/` instead of the repo root. `ensure_files(DATA_DIR)` creates that folder on first run if it's missing.

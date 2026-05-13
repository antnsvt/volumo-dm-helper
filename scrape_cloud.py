#!/usr/bin/env python3
"""Cloud scrape entrypoint - run by GitHub Actions on workflow_dispatch.

Reads page slugs from env var VOLUMO_SOURCES (comma-separated, e.g.
"main,tech-house,techno"). Writes:

  data/state.json   - latest scrape result (read by the web UI)
  data/cache.csv    - updated by build_creators / save_handle
  data/genres.json  - fresh genre list (read by the picker)

State file does NOT track 'scraping' status. The frontend polls the GitHub
Actions API for run progress and only re-reads state.json after the run
completes. state.json on disk only ever holds 'done' or 'error'.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from _volumo_core import (
    BASE,
    build_creators,
    discover_genres,
    ensure_files,
    fetch,
    load_templates,
    render_message,
    save_selected_genres,
    search_missing_instagram_handles,
)

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("VOLUMO_DATA_DIR", ROOT / "data")).resolve()
TEMPLATE_FILE = DATA_DIR / "template.txt"


def _parse_sources():
    raw = (os.environ.get("VOLUMO_SOURCES") or "main").strip()
    return [s.strip() for s in raw.split(",") if s.strip()]


def _resolve_names(slugs):
    """Turn slugs into [{slug, name}]. Falls back to slug-as-name if a slug
    isn't found among the discovered genres (e.g. the homepage)."""
    slug_to_name = {"main": "Main page"}
    genres = []
    try:
        home = fetch(BASE + "/")
        genres = discover_genres(home)
        for g in genres:
            slug_to_name[g["slug"]] = g["name"]
    except Exception as e:
        print(f"warn: discover_genres failed ({e}); falling back to slugs as names")
    return (
        [{"slug": s, "name": slug_to_name.get(s, s)} for s in slugs],
        genres,
    )


def _write_state(payload):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "state.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_genres(genres):
    (DATA_DIR / "genres.json").write_text(
        json.dumps({"genres": genres}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ensure_files(DATA_DIR)

    slugs = _parse_sources()
    print(f"sources: {slugs}")

    sources, genres = _resolve_names(slugs)
    if genres:
        _write_genres(genres)

    messages = []

    def progress(msg):
        print(msg, flush=True)
        messages.append(msg)

    started_at = datetime.now(timezone.utc).isoformat()
    try:
        creators, source_urls = build_creators(sources, DATA_DIR, progress)
        search_missing_instagram_handles(DATA_DIR, creators, progress_cb=progress)
        tmpls = load_templates(TEMPLATE_FILE)
        for c in creators:
            c["message"] = render_message(c, tmpls)
        save_selected_genres(DATA_DIR, [s["slug"] for s in sources])

        _write_state({
            "status": "done",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "creators": creators,
            "source_urls": source_urls,
            "last_selection": [s["slug"] for s in sources],
            "messages": messages[-200:],
        })
        print(f"\nDONE: {len(creators)} creator(s).")
    except Exception as e:
        _write_state({
            "status": "error",
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": str(e),
            "messages": messages[-200:],
        })
        print(f"\nERROR: {e}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()

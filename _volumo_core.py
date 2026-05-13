#!/usr/bin/env python3
"""Mode-agnostic scrape, IG-lookup, and template logic for Volumo DM Helper.

Used by:
  * volumo_helper.py  - local Windows mode (HTTP server on 127.0.0.1)
  * scrape_cloud.py   - cloud mode (GitHub Actions CLI)

All functions take a `data_dir` (Path) so the same code can read/write CSVs
in different locations, and a `progress_cb` so each mode plugs in its own
status reporting (local mode updates STATE+prints; cloud mode appends to a
messages list serialized into state.json).
"""
import asyncio
import csv
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


# ---------- Constants ----------

BASE = "https://volumo.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

VOL_IG_NAMES = {"volumomusic", "volumo", "volumo_music", "volumo.music"}

# Section headings we treat as featured-content sources. The main page uses
# "Featured Volumo Direct releases"; genre pages use "Featured releases" -
# we accept both as the same kind.
ALLOWED_SECTIONS = {
    "artist of the month":              ("artist",  "Artist of the Month"),
    "featured artists":                 ("artist",  "Featured Artist"),
    "featured releases":                ("release", "Featured Release"),
    "featured volumo direct releases":  ("release", "Featured Release"),
}
SPECIAL_H3_SECTIONS = {"artist of the month"}

KIND_PRIORITY = {"Artist of the Month": 0, "Featured Release": 1, "Featured Artist": 2}

# Words that suggest a real music account in an IG bio.
MUSIC_KEYWORDS = {
    "music", "musician", "dj", "producer", "artist", "label", "release",
    "track", "ep", "remix", "spotify", "beatport", "soundcloud", "volumo",
    "mixmag", "boilerroom", "bandcamp", "apple music", "tidal",
    "deezer", "song", "single", "album", "festival", "club", "mix",
}

_BLOCKED_TOP_SLUGS = {
    "for-artists", "label", "labels", "charts", "chart", "releases",
    "track", "tracks", "artist", "artists", "album", "albums",
    "gift-card", "login", "signup", "search", "uk", "et", "directory",
    "about", "contact", "help", "support", "terms", "privacy",
}

CACHE_FIELDS = ["artist_name", "instagram_handle", "last_updated", "confidence"]

# Serializes read-modify-write on cache.csv when parallel workers find handles.
_CACHE_WRITE_LOCK = threading.Lock()


# ---------- Utilities ----------

def _slugify(name):
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", name or "").strip("_").lower()
    return (s[:60] or "creator")


def _kind_key(kind):
    k = kind.lower()
    if "month" in k and "release" in k:
        return "release_of_month"
    if "month" in k:
        return "artist_of_month"
    if "release" in k:
        return "release"
    return "artist"


def _noop_progress(msg):
    pass


# ---------- CSV helpers ----------

def _paths(data_dir: Path):
    return {
        "cache": data_dir / "cache.csv",
        "sent": data_dir / "sent_log.csv",
        "template": data_dir / "template.txt",
        "genres": data_dir / "selected_genres.txt",
    }


def ensure_files(data_dir: Path):
    p = _paths(data_dir)
    if not p["cache"].exists():
        with p["cache"].open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(CACHE_FIELDS)
    if not p["sent"].exists():
        with p["sent"].open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["date", "artist_name"])
    if not p["template"].exists():
        p["template"].write_text(
            "[artist]\nHey {name}, you're on Volumo's main page. Congrats!\n[/artist]\n",
            encoding="utf-8",
        )


def load_cache(data_dir: Path):
    """Returns {lowercased_name: (handle, confidence)}.

    Older cache.csv files without a confidence column are treated as
    'verified' (back-compat).
    """
    cache = {}
    cache_file = _paths(data_dir)["cache"]
    with cache_file.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            handle = (row.get("instagram_handle") or "").strip()
            if not handle:
                continue
            confidence = (row.get("confidence") or "verified").strip() or "verified"
            cache[row["artist_name"].strip().lower()] = (handle, confidence)
    return cache


def save_handle(data_dir: Path, name, handle, confidence="verified"):
    handle = handle.strip().lstrip("@")
    confidence = (confidence or "verified").strip() or "verified"
    name_key = name.strip().lower()
    cache_file = _paths(data_dir)["cache"]
    with _CACHE_WRITE_LOCK:
        rows = []
        found = False
        with cache_file.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["artist_name"].strip().lower() == name_key:
                    row["instagram_handle"] = handle
                    row["last_updated"] = date.today().isoformat()
                    row["confidence"] = confidence
                    found = True
                rows.append(row)
        if not found:
            rows.append({
                "artist_name": name,
                "instagram_handle": handle,
                "last_updated": date.today().isoformat(),
                "confidence": confidence,
            })
        with cache_file.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CACHE_FIELDS)
            w.writeheader()
            for row in rows:
                # Fill in missing keys from legacy rows.
                row.setdefault("confidence", "verified")
                w.writerow({k: row.get(k, "") for k in CACHE_FIELDS})


def load_sent_today(data_dir: Path):
    today = date.today().isoformat()
    sent = set()
    sent_file = _paths(data_dir)["sent"]
    with sent_file.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["date"] == today:
                sent.add(row["artist_name"].strip().lower())
    return sent


def mark_sent(data_dir: Path, name, sent):
    today = date.today().isoformat()
    name_key = name.strip().lower()
    sent_file = _paths(data_dir)["sent"]
    rows = []
    with sent_file.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["date"] == today and row["artist_name"].strip().lower() == name_key:
                continue
            rows.append(row)
    if sent:
        rows.append({"date": today, "artist_name": name})
    with sent_file.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "artist_name"])
        w.writeheader()
        w.writerows(rows)


def load_selected_genres(data_dir: Path):
    f = _paths(data_dir)["genres"]
    if not f.exists():
        return ["main"]
    return [s.strip() for s in f.read_text(encoding="utf-8").splitlines() if s.strip()]


def save_selected_genres(data_dir: Path, slugs):
    f = _paths(data_dir)["genres"]
    f.write_text("\n".join(slugs) + "\n", encoding="utf-8")


# ---------- HTTP fetch ----------

def fetch(url, retries=2):
    last = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last = e
            if i < retries:
                time.sleep(2)
    raise last


# ---------- Volumo page parsers ----------

def parse_page(html, origin_label):
    """Extract featured artists/releases from a Volumo page (main or genre).

    Walks headings + links in document order. A link is kept only if the most
    recent section-defining heading matches the whitelist.
    """
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    items = []
    seen = set()
    current_section = ""

    for el in body.find_all(["h2", "h3", "a"]):
        if el.name == "h2":
            txt = el.get_text(strip=True)
            if txt:
                current_section = txt
            continue
        if el.name == "h3":
            txt = el.get_text(strip=True)
            if txt.lower() in SPECIAL_H3_SECTIONS:
                current_section = txt
            continue

        href = el.get("href", "")
        if not href:
            continue
        path = urlparse(href).path or href
        am = re.match(r"^/artist/(\d+)-([^/]+)/?$", path)
        rm = re.match(r"^/album/(\d+)-([^/]+)/?$", path)
        if not (am or rm):
            continue
        name = el.get_text(strip=True)
        if not name or len(name) > 120:
            continue
        if re.fullmatch(r"\d+\s*(albums?|tracks?|songs?|releases?)", name, re.I):
            continue
        if name.lower() in {"artist page", "view profile", "more", "see all"}:
            continue

        meta = ALLOWED_SECTIONS.get(current_section.lower().strip())
        if not meta:
            continue
        wanted, kind_label = meta
        if wanted == "artist" and not am:
            continue
        if wanted == "release" and not rm:
            continue

        if am:
            profile_url = urljoin(BASE, path)
            release_url = None
        else:
            profile_url = None
            release_url = urljoin(BASE, path)

        dedup_key = profile_url or release_url
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        items.append({
            "kind": kind_label,
            "name": name,
            "profile_url": profile_url,
            "release_url": release_url,
            "section": current_section,
            "origin": origin_label,
        })

    return items


def parse_artist_profile(html):
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        m = re.search(r"instagram\.com/([^/?#]+)", a["href"])
        if not m:
            continue
        handle = m.group(1).strip("/")
        if handle.lower() in VOL_IG_NAMES:
            continue
        if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", handle):
            continue
        return handle
    return None


def parse_release_for_artist(html):
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        path = urlparse(a["href"]).path or a["href"]
        if re.match(r"^/artist/(\d+)-(.+?)/?$", path):
            return urljoin(BASE, path), a.get_text(strip=True)
    return None, None


def discover_genres(home_html):
    """Find the 'Popular genres' section and return [{slug, name}]."""
    soup = BeautifulSoup(home_html, "html.parser")
    out = []
    seen = set()
    for h2 in soup.find_all("h2"):
        if "genre" not in h2.get_text(strip=True).lower():
            continue
        nxt = h2
        for _ in range(800):
            nxt = nxt.next_element
            if nxt is None:
                break
            if hasattr(nxt, "name") and nxt.name == "h2" and nxt is not h2:
                break
            if hasattr(nxt, "name") and nxt.name == "a" and nxt.get("href"):
                path = urlparse(nxt["href"]).path
                txt = nxt.get_text(strip=True)
                m = re.fullmatch(r"/([a-z][a-z0-9-]*)/?", path)
                if not m or not txt:
                    continue
                slug = m.group(1)
                if slug in _BLOCKED_TOP_SLUGS or slug in seen:
                    continue
                if "learn more" in txt.lower():
                    continue
                seen.add(slug)
                out.append({"slug": slug, "name": txt})
        break
    return sorted(out, key=lambda g: g["name"].lower())


# ---------- Instagram handle guessing + verification ----------

def _name_variants(name):
    """Generate plausible Instagram handle variants for an artist name, most
    likely first. Used to search for the IG handle when it's not on Volumo.
    """
    base = re.sub(r"[^A-Za-z0-9 ]+", " ", name or "").strip().lower()
    parts = [p for p in base.split() if p]
    if not parts:
        return []
    joined = "".join(parts)
    underscored = "_".join(parts)
    dotted = ".".join(parts)
    last = parts[-1]
    first = parts[0]
    seq = [
        joined,
        "iam" + joined,
        joined + "music",
        underscored,
        joined + "official",
        dotted,
        joined + "_music",
        joined + "_official",
        joined + "dj" if not joined.startswith("dj") else None,
        "dj" + joined if not joined.startswith("dj") else None,
        last if len(last) >= 3 and last != joined else None,
        last + "music" if len(last) >= 3 and last != joined else None,
        first + "music" if len(first) >= 3 and first != joined else None,
        joined + "_",
        joined + ".music",
    ]
    out, seen = [], set()
    for v in seq:
        if not v:
            continue
        if 2 <= len(v) <= 30 and re.fullmatch(r"[a-z0-9._]+", v) and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _ig_title_matches_artist(og, artist_name):
    """Decide whether an instagram.com/<handle>/ og:title plausibly belongs to
    artist_name. Stricter for short/single-word names to avoid matching
    generic accounts."""
    if not og:
        return False
    title_lower = og.lower()
    words = [w for w in re.findall(r"[a-z0-9]+", (artist_name or "").lower()) if len(w) >= 2]
    if not words:
        return False
    if len(words) == 1 and len(words[0]) < 6:
        return False
    if len(words) > 1:
        return all(w in title_lower for w in words)
    return words[0] in title_lower


def _bio_confidence(bio, artist_name, origins):
    """Return 'verified' or 'weak' based on whether the IG bio looks like a
    music account.

    Rules (in order):
      1. Empty bio - return 'verified'. IG often gives headless browsers a
         login wall or omits the description tag; flagging on absence creates
         too many false positives.
      2. Bio mentions any MUSIC_KEYWORDS word - 'verified'.
      3. Bio mentions all tokens of any origin name (e.g. 'tech' AND 'house'
         for origin 'Tech House') - 'verified'.
      4. Otherwise - 'weak'.
    """
    if not bio or not bio.strip():
        return "verified"
    blow = bio.lower()
    for kw in MUSIC_KEYWORDS:
        if kw in blow:
            return "verified"
    for o in (origins or []):
        if not o:
            continue
        tokens = [t for t in re.findall(r"[a-z0-9]+", o.lower()) if len(t) >= 3]
        if tokens and all(t in blow for t in tokens):
            return "verified"
    return "weak"


def _ig_meta_via_requests(handle):
    """Returns (og_title, bio) from instagram.com/<handle>/ using plain HTTP.

    Cheap pre-check for handles found on a trusted Volumo profile. Returns
    ("", "") on any failure - callers should treat that as 'inconclusive'
    and fall back to confidence='verified' (the same way an empty bio is
    treated)."""
    try:
        r = requests.get(
            f"https://www.instagram.com/{handle}/",
            headers=HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            return "", ""
    except Exception:
        return "", ""
    soup = BeautifulSoup(r.text, "html.parser")
    og_title_el = soup.find("meta", attrs={"property": "og:title"})
    og_desc_el = soup.find("meta", attrs={"property": "og:description"})
    og_title = (og_title_el.get("content") if og_title_el else "") or ""
    bio = (og_desc_el.get("content") if og_desc_el else "") or ""
    if not bio:
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict) and data.get("description"):
                    bio = data["description"]
                    break
            except Exception:
                continue
    return og_title, bio


def _confidence_via_requests(handle, artist_name, origins):
    """Confidence for handles found on the Volumo profile (trusted source).

    Skips the og:title strictness gate - the handle came from Volumo, so we
    just check whether the bio looks musical."""
    _og, bio = _ig_meta_via_requests(handle)
    return _bio_confidence(bio, artist_name, origins)


async def _extract_ig_bio_async(page):
    """Pull bio text from an open Playwright page on instagram.com/<handle>/.

    Tries og:description first, then JSON-LD description. Returns '' if
    neither yields content."""
    bio = ""
    try:
        el = await page.query_selector('meta[property="og:description"]')
        if el:
            bio = (await el.get_attribute("content")) or ""
    except Exception:
        pass
    if bio:
        return bio
    try:
        els = await page.query_selector_all('script[type="application/ld+json"]')
        for el in els:
            txt = (await el.text_content()) or ""
            if not txt.strip():
                continue
            try:
                data = json.loads(txt)
                if isinstance(data, dict) and data.get("description"):
                    return data["description"]
            except Exception:
                continue
    except Exception:
        pass
    return ""


async def _async_verify_ig_handle(page, handle, artist_name, origins):
    """Returns (matched: bool, confidence: str). Confidence is 'verified' or
    'weak' (only meaningful when matched=True)."""
    try:
        await page.goto(f"https://www.instagram.com/{handle}/",
                        wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(500)
        title_el = await page.query_selector('meta[property="og:title"]')
        og = await title_el.get_attribute("content") if title_el else None
    except Exception:
        return False, "weak"
    if not _ig_title_matches_artist(og, artist_name):
        return False, "weak"
    bio = await _extract_ig_bio_async(page)
    return True, _bio_confidence(bio, artist_name, origins)


# ---------- Build creator list ----------

def build_creators(sources, data_dir: Path, progress_cb=None):
    """sources: list of {'slug': str, 'name': str}. Slug 'main' = homepage.

    Returns (creators_list, source_urls_dict). progress_cb is called with
    short status strings; pass None for silent operation.
    """
    progress = progress_cb or _noop_progress
    cache = load_cache(data_dir)
    sent_today = load_sent_today(data_dir)
    source_urls = {}

    def _fetch_source(src):
        slug = src["slug"]
        label = src["name"]
        url = BASE + "/" if slug == "main" else BASE + "/" + slug
        try:
            html = fetch(url)
            return (label, url, parse_page(html, label), None)
        except Exception as e:
            return (label, url, [], e)

    progress(f"Fetching {len(sources)} Volumo page(s) in parallel...")
    all_items = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(sources)))) as ex:
        for label, url, items, err in ex.map(_fetch_source, sources):
            source_urls[label] = url
            if err:
                progress(f"  {label}: failed ({err})")
            else:
                progress(f"  {label}: {len(items)} item(s)")
                all_items.extend(items)

    seen_urls = set()
    deduped = []
    for it in all_items:
        url = it.get("profile_url") or it.get("release_url")
        if url in seen_urls:
            for kept in deduped:
                if (kept.get("profile_url") or kept.get("release_url")) == url:
                    if it["origin"] not in kept.setdefault("extra_origins", []):
                        kept["extra_origins"].append(it["origin"])
                    break
            continue
        seen_urls.add(url)
        deduped.append(it)

    def _origins_for(item):
        return [item["origin"]] + item.get("extra_origins", [])

    def _enrich(item):
        profile_url = item.get("profile_url")
        artist_name = item["name"]
        if not profile_url and item.get("release_url"):
            try:
                rhtml = fetch(item["release_url"])
                p, n = parse_release_for_artist(rhtml)
                if p:
                    profile_url = p
                if n:
                    artist_name = n
            except Exception as e:
                progress(f"  release fetch failed for {item['name']}: {e}")
        key = artist_name.strip().lower()
        cached = cache.get(key)
        ig = None
        confidence = "verified"
        source = None
        if cached:
            ig, confidence = cached
            source = "cache"
        if not ig and profile_url:
            try:
                phtml = fetch(profile_url)
                found = parse_artist_profile(phtml)
                if found:
                    ig = found
                    source = "volumo"
                    confidence = _confidence_via_requests(
                        found, artist_name, _origins_for(item)
                    )
            except Exception as e:
                progress(f"  profile fetch failed for {artist_name}: {e}")
        return {
            "item": item,
            "profile_url": profile_url,
            "artist_name": artist_name,
            "ig": ig,
            "confidence": confidence,
            "source": source,
        }

    progress(f"Looking up details for {len(deduped)} creator(s) in parallel...")
    with ThreadPoolExecutor(max_workers=8) as ex:
        enriched = list(ex.map(_enrich, deduped))

    for e in enriched:
        if e["source"] == "volumo" and e["ig"]:
            save_handle(data_dir, e["artist_name"], e["ig"], e["confidence"])

    by_name = {}
    order = []
    for e in enriched:
        it = e["item"]
        artist_name = e["artist_name"]
        key = artist_name.strip().lower()

        if key in by_name:
            existing = by_name[key]
            new_pri = KIND_PRIORITY.get(it["kind"], 99)
            old_pri = KIND_PRIORITY.get(existing["display_kind"], 99)
            if new_pri < old_pri:
                existing["display_kind"] = it["kind"]
                existing["kind_key"] = _kind_key(it["kind"])
                existing["section"] = it["section"]
            for o in _origins_for(it):
                if o not in existing["origins"]:
                    existing["origins"].append(o)
            continue

        creator = {
            "name": artist_name,
            "display_kind": it["kind"],
            "kind_key": _kind_key(it["kind"]),
            "section": it["section"],
            "origins": _origins_for(it),
            "profile_url": e["profile_url"],
            "release_url": it.get("release_url"),
            "instagram": e["ig"],
            "confidence": e["confidence"] if e["ig"] else "verified",
            "source": e["source"],
            "sent_today": key in sent_today,
        }
        by_name[key] = creator
        order.append(key)

    creators = [by_name[k] for k in order]
    creators.sort(key=lambda c: KIND_PRIORITY.get(c["display_kind"], 99))
    return creators, source_urls


# ---------- Instagram search (Playwright) ----------

async def _async_search_missing(data_dir, creators, num_workers, progress):
    from playwright.async_api import async_playwright

    missing = [c for c in creators if not c.get("instagram")]
    if not missing:
        return

    work_q = asyncio.Queue()
    for c in missing:
        work_q.put_nowait(c)

    done = [0]
    total = len(missing)
    progress_lock = asyncio.Lock()

    async def worker(page):
        while True:
            try:
                c = work_q.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                for variant in _name_variants(c["name"]):
                    try:
                        matched, conf = await _async_verify_ig_handle(
                            page, variant, c["name"], c.get("origins") or []
                        )
                        if matched:
                            c["instagram"] = variant
                            c["confidence"] = conf
                            c["source"] = "search"
                            save_handle(data_dir, c["name"], variant, conf)
                            break
                    except Exception:
                        continue
            finally:
                async with progress_lock:
                    done[0] += 1
                    if c.get("instagram"):
                        flag = " !" if c.get("confidence") == "weak" else ""
                        tag = f"@{c['instagram']}{flag}"
                    else:
                        tag = "not found"
                    progress(f"  IG [{done[0]}/{total}] {c['name']} -> {tag}")

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception as e:
            progress(f"Browser launch failed ({e}). Install Chromium via "
                     f"`playwright install chromium`.")
            return
        progress(f"Searching Instagram for {total} missing handle(s) "
                 f"with {num_workers} parallel pages...")
        ctxs = [await browser.new_context(viewport={"width": 1280, "height": 900})
                for _ in range(num_workers)]
        pages = [await ctx.new_page() for ctx in ctxs]
        await asyncio.gather(*(worker(pg) for pg in pages))
        await browser.close()


def search_missing_instagram_handles(data_dir: Path, creators, num_workers=4,
                                     progress_cb=None):
    progress = progress_cb or _noop_progress
    missing = [c for c in creators if not c.get("instagram")]
    if not missing:
        return
    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        progress("Instagram search skipped (playwright not installed).")
        return
    try:
        asyncio.run(_async_search_missing(data_dir, creators, num_workers, progress))
    except Exception as e:
        progress(f"Instagram search failed: {e}")


# ---------- Templates ----------

def load_templates(template_path: Path):
    """Parse template.txt into {kind: [variant1, variant2, ...]}."""
    raw = template_path.read_text(encoding="utf-8")
    tmpls = {}
    for m in re.finditer(r"\[([a-z_]+)\]\s*(.*?)\s*\[/\1\]", raw, re.DOTALL):
        kind = m.group(1)
        body = m.group(2).strip()
        if body:
            tmpls.setdefault(kind, []).append(body)
    if not tmpls:
        tmpls["default"] = [raw.strip()]
    return tmpls


def render_message(creator, tmpls):
    key = creator["kind_key"]
    variants = (tmpls.get(key) or tmpls.get("artist")
                or tmpls.get("default") or ["Hey {name}!"])
    idx = sum(ord(ch) for ch in creator["name"]) % len(variants)
    tmpl = variants[idx]
    return (tmpl
            .replace("{name}", creator["name"])
            .replace("{release}", creator.get("section", "")))

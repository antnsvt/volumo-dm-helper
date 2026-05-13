Volumo DM Helper
================

What it does
------------
Each day, you pick which Volumo pages to check (main page and/or any of the
genre pages), and the tool finds the featured creators across all of them,
looks up their Instagram handles, and prepares a personalized message for
each one that you copy and paste into an Instagram DM.

If the same artist is featured on multiple pages (e.g. main page and the
Tech House genre page), they only show up once in the dashboard.

When an artist's Instagram isn't listed on their Volumo profile, the tool
searches Instagram itself by trying plausible handle variants (joined name,
"iam" prefix, "music"/"official" suffix, etc.) and verifies each candidate
against the artist's actual Instagram page metadata. For short single-word
artist names (Noha, ISKRA, etc.) this is skipped because it would too easily
match unrelated accounts -- you look those up by hand.

A second sanity check reads the candidate's Instagram bio. If it doesn't
mention any music-industry term (DJ, producer, label, release, EP, remix,
Spotify, Beatport, SoundCloud, etc.) and doesn't reference the genre the
artist was featured on, the handle is shown with a red "!" badge so you can
double-check it before sending. The handle is still saved so you don't have
to look it up again -- click the override input to correct it.

The tool NEVER sends anything itself. All messages go out manually from
@volumomusic, sent by you. This protects the brand account from getting
banned by Instagram's anti-automation systems.

Two ways to run
---------------
This tool ships with two modes that share the same data:

  A. Local Windows app (run.bat)         -- runs on your PC, open in browser.
  B. Cloud / PWA (GitHub Pages)          -- open from iPhone, Mac, anywhere.

Pick A if you only use it from your PC. Pick B if you want it on your phone
or Mac too. You can also do both -- they read/write the same cache and sent
log on the cloud, with the local mode using a private copy on your PC.

A. Local mode (Windows)
=======================

One-time setup
--------------
1. Install Python 3.10 or newer from https://www.python.org/downloads/
   IMPORTANT: during installation, tick "Add Python to PATH".

2. Double-click setup.bat. A black window opens and installs four libraries
   (requests, beautifulsoup4, playwright, Pillow), then downloads a copy of
   Chromium (about 100MB). Press any key to close when it says "Done".

3. (Optional) Open data/template.txt in Notepad and adjust the message
   wording. Use {name} where you want the artist's name to appear. Don't
   change the [artist] / [release] / [artist_of_month] section markers.

Daily use
---------
1. Double-click run.bat. A console window opens and your browser opens to
   the page picker.

2. On the picker page, tick the pages you want scanned today. Your last
   selection is remembered.

3. Click "Start scraping". 30s for the main page alone; up to a few minutes
   if you picked many genre pages.

4. On the dashboard, each card is one featured creator. For each one:
     - Click "Open Instagram" -- their profile opens in a new tab.
     - Click "Copy message" -- the personalized message is on your clipboard.
     - Paste it into Instagram, send.
     - Tick "Mark as sent today".

5. If a handle has a red "!" next to it, the bio didn't look musical. Click
   the override box, paste the correct handle, press Enter. This clears the
   flag and saves the right handle for next time.

6. If a creator's Instagram is unknown (red text), click "Google search",
   find the handle, paste it into the input on the card.

7. When done, close the console window.

Files in this folder
--------------------
volumo_helper.py        Local entrypoint. Imports _volumo_core for scrape logic.
_volumo_core.py         Shared scrape/IG-lookup library (also used by cloud mode).
scrape_cloud.py         Cloud entrypoint (run by GitHub Actions).
mutate.py               Cloud mutation handler (run by GitHub Actions).
data/                   Your cache, sent log, and templates (created on first run).
  cache.csv             Saved IG handles + confidence. Grows over time.
  sent_log.csv          Who you've messaged when.
  template.txt          Message templates. Edit freely.
  selected_genres.txt   Your last page selection.
web/                    Cloud-mode static frontend (HTML/JS/CSS + PWA bits).
.github/workflows/      Cloud-mode automation (scrape, mutate, deploy-Pages).
run.bat                 Local mode: run this every morning.
setup.bat               Local mode: run once after installing Python.
README.txt              This file.

B. Cloud / PWA mode
===================

What this gives you
-------------------
A web app at https://<yourname>.github.io/volumo-dm-helper/ that:
  - Works from any device (iPhone, Mac, iPad, Android).
  - Installs to your iPhone home screen like a native app.
  - Triggers scrapes in GitHub Actions (so your PC doesn't need to be on).
  - Stores cache and sent log in a private branch of your repo.
  - Costs nothing: public-repo Actions minutes are unlimited, Pages is free.

One-time setup
--------------
1. Create a free GitHub account if you don't have one.

2. Create a new PUBLIC repo on GitHub. Suggested name: volumo-dm-helper.
   (Public is fine -- the repo holds artist names and IG handles, not
   anything sensitive. Public repos also get unlimited free Actions minutes.)

3. On your PC, open a terminal in this folder and push the code:

     git init
     git branch -m main
     git add .
     git commit -m "initial commit"
     git remote add origin https://github.com/<your-username>/volumo-dm-helper.git
     git push -u origin main

4. Create the `state` branch with your existing data. The data folder is
   already prepared locally with your cache/sent log, so:

     git checkout --orphan state
     git rm -rf .                          # clears the working tree (don't worry, files are kept on main)
     git checkout main -- data
     git mv data/* .                       # state branch has data at the root
     rmdir data
     git add .
     git commit -m "seed state"
     git push -u origin state
     git checkout main

   (If git complains about "data/*" wildcards on Windows, do them one by one
   with `git mv data/cache.csv .` etc.)

5. Enable GitHub Pages:
     - Go to your repo on github.com.
     - Settings -> Pages.
     - Under "Build and deployment", set Source to "GitHub Actions".
     - Wait ~2 minutes after your first push to main for the pages.yml
       workflow to deploy. The URL will be shown in the Pages settings.

6. Create a fine-grained personal access token (PAT):
     - https://github.com/settings/tokens?type=beta
     - Click "Generate new token".
     - Token name: Volumo DM Helper (or anything).
     - Expiration: 90 days is fine; the app will warn you when it expires.
     - Repository access: "Only select repositories" -> pick your volumo-dm-helper repo.
     - Repository permissions:
         Actions:    Read and write
         Contents:   Read and write
         Metadata:   Read-only (auto-added, mandatory)
     - Click "Generate token".
     - COPY THE TOKEN NOW -- you won't see it again. It starts with github_pat_.

7. Open https://<your-username>.github.io/volumo-dm-helper/ in your browser.
   A setup screen asks for your GitHub username, repo name, and PAT. Paste
   them, click Save. It validates and shows the dashboard (empty on first
   visit).

Daily use
---------
1. Open the app on your phone or Mac.

2. Tap "Change pages", pick the pages you want, tap "Scrape now".

3. Wait 2-4 min (first run after idle) or 1-2 min (subsequent runs). The
   progress card shows the GitHub Actions run status.

4. When it's done, the dashboard appears. Same UX as local mode: tap
   "Open Instagram", tap "Copy message", paste, send, tap "Mark as sent".

5. The "!" badge works the same way as local mode -- tap the handle to
   correct it.

Install on iPhone home screen
-----------------------------
1. Open the app URL in Safari.
2. Tap the Share icon at the bottom.
3. Scroll down and tap "Add to Home Screen".
4. The icon appears on your home screen. Tapping it launches the app
   fullscreen, without Safari chrome.

Install on macOS
----------------
1. Open the app URL in Safari (or Chrome).
2. File menu -> Add to Dock (Safari 17+) -- behaves like a native app.

Replacing your PAT (every 90 days)
----------------------------------
When the token expires the app will start failing with 401. Generate a new
fine-grained PAT with the same scopes (above), then in the app tap the gear
icon, confirm reset, and paste the new token.

If something breaks
-------------------
LOCAL MODE:

- "python is not recognized as an internal or external command"
    Python isn't installed or isn't on PATH. Reinstall Python and tick
    "Add Python to PATH".

- "Missing libraries. Please double-click setup.bat first."
    Run setup.bat once.

- Picker shows "Failed to load genres"
    Volumo's homepage might be down or Volumo changed its HTML. Try later.

- Port 8765 already in use
    Another copy of the script is still running. Close other run.bat windows.

CLOUD MODE:

- App stuck on "Loading..."
    Open browser dev tools (Cmd+Option+I or F12). Check the Console. The
    most common cause is a 401 from a stale PAT -- replace it (see above).

- "Scrape now" times out without a run appearing
    Open the repo's Actions tab on github.com. The workflow file may not be
    on the default branch yet, or the run was cancelled. Re-trigger from
    the Actions tab manually to test.

- Scrape runs but state.json doesn't update
    Check the run's logs in the Actions tab. The most common cause is that
    the `state` branch doesn't exist yet -- create it (see setup step 4).

- "!" flags on lots of artists
    Instagram is showing the login wall to GitHub's IPs. Re-run the scrape
    later -- IG's anti-bot is rate-limited. Or run the local mode for now
    and the cache will sync up next time you push.

- Dashboard shows yesterday's data after a scrape
    The frontend re-fetches state.json automatically when the run
    completes. Pull-to-refresh on iOS, or tap the gear icon and back, to
    force a re-read.

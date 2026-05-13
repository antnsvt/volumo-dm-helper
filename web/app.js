/* Volumo DM Helper — static frontend served by GitHub Pages.
 *
 * Reads state.json + cache.csv from the repo's `state` branch via the
 * Contents API (with auth, so freshly fetched — no raw.githubusercontent CDN
 * delay). Mutations and scrapes go through Actions workflows so a single
 * device serializes writes.
 */
(() => {

const API = 'https://api.github.com';
const SCRAPE_WORKFLOW = 'scrape.yml';
const POLL_INTERVAL_MS = 5000;
const MUTATE_REFETCH_DELAY_MS = 8000;

// Hardcoded for this deploy. The app is single-tenant per Pages site so the
// user doesn't have to retype the owner/repo every time the token rotates.
const OWNER = 'antnsvt';
const REPO = 'volumo-dm-helper';

const STORE = {
  owner: () => OWNER,
  repo: () => REPO,
  pat: () => localStorage.getItem('volumo.pat') || '',
  set: (pat) => { localStorage.setItem('volumo.pat', pat); },
  clear: () => { localStorage.removeItem('volumo.pat'); },
};

let allCreators = [];
let sourceUrls = {};
let lastSelection = ['main'];
let availableGenres = [];
let activeFilter = null;
let pollHandle = null;

// ---------- DOM helpers ----------

const $ = (id) => document.getElementById(id);

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  if (attrs) for (const k in attrs) {
    if (k === 'class') e.className = attrs[k];
    else if (k === 'onclick') e.onclick = attrs[k];
    else if (k.startsWith('data-')) e.setAttribute(k, attrs[k]);
    else e.setAttribute(k, attrs[k]);
  }
  for (const c of kids) {
    if (c == null) continue;
    e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return e;
}

function toast(msg, isError) {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast show' + (isError ? ' error' : '');
  setTimeout(() => t.classList.remove('show'), 2200);
}

function setView(name) {
  for (const v of ['picker-view', 'progress-view', 'dashboard-view']) {
    $(v).classList.toggle('hidden', v !== name);
  }
}

// ---------- GitHub API ----------

function ghHeaders() {
  return {
    'Accept': 'application/vnd.github+json',
    'Authorization': `Bearer ${STORE.pat()}`,
    'X-GitHub-Api-Version': '2022-11-28',
  };
}

async function ghGet(path) {
  const r = await fetch(API + path, { headers: ghHeaders() });
  if (!r.ok) throw new Error(`GET ${path} → ${r.status}`);
  return r.json();
}

async function ghPost(path, body, expect=204) {
  const r = await fetch(API + path, {
    method: 'POST',
    headers: { ...ghHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) {
    let msg = `POST ${path} → ${r.status}`;
    try { msg += ' ' + (await r.text()).slice(0, 200); } catch (_) {}
    throw new Error(msg);
  }
  if (r.status === 204 || expect === 204) return null;
  return r.json();
}

function b64decodeUtf8(b64) {
  const binary = atob((b64 || '').replace(/\s/g, ''));
  const bytes = Uint8Array.from(binary, c => c.charCodeAt(0));
  return new TextDecoder('utf-8').decode(bytes);
}

async function loadJsonFromRepo(path) {
  const o = STORE.owner(), r = STORE.repo();
  try {
    const res = await ghGet(`/repos/${o}/${r}/contents/${path}?ref=state`);
    return JSON.parse(b64decodeUtf8(res.content));
  } catch (e) {
    if (String(e.message).includes('404')) return null;
    throw e;
  }
}

// ---------- Setup flow ----------

function showSetup(errMsg) {
  $('setup').classList.remove('hidden');
  $('app').classList.add('hidden');
  $('setup-pat').value = STORE.pat();
  $('setup-error').textContent = errMsg || '';
}

async function saveSetup() {
  const pat = $('setup-pat').value.trim();
  if (!pat) {
    $('setup-error').textContent = 'Token is required.';
    return;
  }
  STORE.set(pat);
  $('setup-error').textContent = 'Validating...';
  try {
    const repoInfo = await ghGet(`/repos/${OWNER}/${REPO}`);
    console.log('Auth OK — repo:', repoInfo.full_name);
    $('setup').classList.add('hidden');
    $('app').classList.remove('hidden');
    bootstrap();
  } catch (e) {
    $('setup-error').textContent = 'Failed: ' + e.message;
  }
}

// ---------- Bootstrap ----------

async function bootstrap() {
  if (!STORE.pat() || !STORE.owner() || !STORE.repo()) {
    showSetup();
    return;
  }
  $('meta').textContent = 'Loading...';
  try {
    await loadGenres();
    const active = await findActiveRun();
    if (active) {
      showProgress(active);
      return;
    }
    const state = await loadJsonFromRepo('data/state.json');
    if (state && state.creators && state.creators.length) {
      renderDashboard(state);
    } else {
      showPicker();
    }
  } catch (e) {
    $('meta').textContent = 'Error: ' + e.message;
    toast('Load failed: ' + e.message, true);
    if (String(e.message).includes('401')) {
      showSetup('Token rejected — check the PAT and its scopes.');
    }
  }
}

async function loadGenres() {
  const data = await loadJsonFromRepo('data/genres.json');
  availableGenres = (data && data.genres) || [];
  const state = await loadJsonFromRepo('data/state.json');
  if (state && state.last_selection) lastSelection = state.last_selection;
}

// ---------- Picker ----------

function showPicker() {
  setView('picker-view');
  $('meta').textContent = 'Pick which Volumo pages to scrape';
  const lastSet = new Set(lastSelection);
  $('cb-main').checked = lastSet.has('main');
  $('cb-main').onchange = updateCount;
  const grid = $('genres');
  grid.innerHTML = '';
  if (!availableGenres.length) {
    grid.innerHTML = '<div class="muted">No genres cached yet. Run a scrape with just '
      + '<b>main</b> first; the genre list will be populated for next time.</div>';
  }
  for (const g of availableGenres) {
    const lbl = el('label', { class: 'cb' });
    const cb = el('input', { type: 'checkbox' });
    cb.dataset.slug = g.slug;
    cb.dataset.name = g.name;
    cb.checked = lastSet.has(g.slug);
    cb.addEventListener('change', updateCount);
    lbl.appendChild(cb);
    lbl.appendChild(el('span', null, g.name));
    grid.appendChild(lbl);
  }
  updateCount();
  $('btn-genres-all').onclick = () => { setAllGenres(true); updateCount(); };
  $('btn-genres-none').onclick = () => { setAllGenres(false); updateCount(); };
  $('btn-start').onclick = onStartScrape;
}

function setAllGenres(checked) {
  document.querySelectorAll('#genres input[type=checkbox]')
    .forEach(cb => { cb.checked = checked; });
}

function selectedSlugs() {
  const out = [];
  if ($('cb-main').checked) out.push('main');
  document.querySelectorAll('#genres input[type=checkbox]:checked')
    .forEach(cb => out.push(cb.dataset.slug));
  return out;
}

function updateCount() {
  const n = selectedSlugs().length;
  $('count').textContent = n + ' page' + (n === 1 ? '' : 's') + ' selected';
  $('btn-start').disabled = n === 0;
}

async function onStartScrape() {
  const slugs = selectedSlugs();
  if (!slugs.length) return;
  $('btn-start').disabled = true;
  $('progress-msg').textContent = 'Queueing GitHub Actions run...';
  setView('progress-view');
  try {
    await ghPost(
      `/repos/${STORE.owner()}/${STORE.repo()}/actions/workflows/${SCRAPE_WORKFLOW}/dispatches`,
      { ref: 'main', inputs: { sources: slugs.join(',') } },
    );
    toast('Scrape queued');
    await sleep(2500);
    const run = await findActiveRun();
    if (run) showProgress(run);
    else {
      $('progress-msg').textContent = 'Run did not appear within 2.5s — checking again in 5s...';
      await sleep(5000);
      const run2 = await findActiveRun();
      if (run2) showProgress(run2);
      else { await bootstrap(); }
    }
  } catch (e) {
    $('progress-msg').textContent = 'Failed to start: ' + e.message;
    toast('Dispatch failed: ' + e.message, true);
  }
}

// ---------- Progress polling ----------

async function findActiveRun() {
  const o = STORE.owner(), r = STORE.repo();
  const data = await ghGet(
    `/repos/${o}/${r}/actions/workflows/${SCRAPE_WORKFLOW}/runs?per_page=5`
  );
  const active = (data.workflow_runs || []).find(r =>
    r.status === 'queued' || r.status === 'in_progress' || r.status === 'waiting'
  );
  return active || null;
}

function showProgress(run) {
  setView('progress-view');
  $('meta').textContent = 'Scrape in progress';
  $('progress-msg').textContent =
    `Run #${run.run_number}: ${run.status}${run.html_url ? '\n' + run.html_url : ''}`;
  $('btn-cancel-poll').onclick = stopPolling;
  startPolling();
}

function startPolling() {
  stopPolling();
  pollHandle = setInterval(pollOnce, POLL_INTERVAL_MS);
}

function stopPolling() {
  if (pollHandle) { clearInterval(pollHandle); pollHandle = null; }
}

async function pollOnce() {
  try {
    const run = await findActiveRun();
    if (run) {
      $('progress-msg').textContent =
        `Run #${run.run_number}: ${run.status}${run.html_url ? '\n' + run.html_url : ''}`;
      return;
    }
    stopPolling();
    toast('Scrape finished');
    await bootstrap();
  } catch (e) {
    $('progress-msg').textContent += '\nPoll error: ' + e.message;
  }
}

// ---------- Dashboard ----------

function renderDashboard(state) {
  setView('dashboard-view');
  allCreators = state.creators || [];
  sourceUrls = state.source_urls || {};
  renderMeta(state);
  renderFilters();
  renderCards();
}

function renderMeta(state) {
  const total = allCreators.length;
  const withIG = allCreators.filter(c => c.instagram).length;
  const flagged = allCreators.filter(c => c.confidence === 'weak').length;
  const sent = allCreators.filter(c => c.sent_today).length;
  let line = (state.date || '') + ' — ' + total + ' creators, ' + withIG + ' with Instagram';
  if (flagged) line += ', ' + flagged + ' flagged (!)';
  line += ', ' + sent + ' messaged today';
  $('meta').textContent = line;
}

function renderFilters() {
  const origins = new Set();
  for (const c of allCreators) for (const o of (c.origins || [])) origins.add(o);
  const sorted = Array.from(origins).sort();
  const f = $('filters');
  f.innerHTML = '';
  if (sorted.length <= 1) return;
  const all = el('span', { class: 'filter-chip' + (activeFilter === null ? ' active' : '') }, 'All');
  all.onclick = () => { activeFilter = null; renderFilters(); renderCards(); };
  f.appendChild(all);
  for (const o of sorted) {
    const chip = el('span', { class: 'filter-chip' + (activeFilter === o ? ' active' : '') }, o);
    chip.onclick = () => { activeFilter = o; renderFilters(); renderCards(); };
    f.appendChild(chip);
  }
}

function renderCards() {
  const grid = $('grid');
  grid.innerHTML = '';
  const list = activeFilter
    ? allCreators.filter(c => (c.origins || []).includes(activeFilter))
    : allCreators;
  if (!list.length) {
    grid.appendChild(el('div', { class: 'empty' }, 'No creators to show.'));
    return;
  }
  for (const c of list) {
    grid.appendChild(buildCard(c));
  }
}

function buildCard(c) {
  const card = el('div', { class: 'card' + (c.sent_today ? ' sent' : '') });
  const kindClass = c.kind_key && c.kind_key.includes('month') ? 'month'
                  : c.kind_key && c.kind_key.includes('release') ? 'release' : '';
  card.appendChild(el('div', { class: 'kind ' + kindClass }, c.display_kind));
  card.appendChild(el('div', { class: 'name' }, c.name));

  if (c.origins && c.origins.length) {
    const orow = el('div', { class: 'origins' });
    orow.appendChild(document.createTextNode('Featured on: '));
    c.origins.forEach((o, i) => {
      if (i > 0) orow.appendChild(document.createTextNode(', '));
      const url = sourceUrls[o];
      if (url) {
        orow.appendChild(el('a', {
          href: url, target: '_blank', rel: 'noopener',
          title: 'Open ' + o + ' (for screenshot)',
        }, o));
      } else {
        orow.appendChild(document.createTextNode(o));
      }
    });
    card.appendChild(orow);
  }

  if (c.instagram) {
    const ig = el('div', { class: 'ig' });
    ig.appendChild(el('a', {
      href: 'https://www.instagram.com/' + c.instagram + '/',
      target: '_blank', rel: 'noopener',
    }, '@' + c.instagram));
    if (c.confidence === 'weak') {
      ig.appendChild(el('span', {
        class: 'warn',
        title: "IG bio didn't mention music or this artist's genre — double-check this handle",
      }, '!'));
    }
    ig.appendChild(el('span', { class: 'source' }, '(' + (c.source || '') + ')'));
    card.appendChild(ig);
  } else {
    card.appendChild(el('div', { class: 'ig missing' }, 'Instagram unknown — look up below'));
  }

  const ta = el('textarea');
  ta.value = c.message || '';
  card.appendChild(ta);

  const row = el('div', { class: 'row' });
  const copyBtn = el('button', null, 'Copy message');
  copyBtn.onclick = async () => {
    try { await navigator.clipboard.writeText(ta.value); toast('Copied'); }
    catch (e) { toast('Copy failed: ' + e.message, true); }
  };
  row.appendChild(copyBtn);

  if (c.instagram) {
    row.appendChild(el('a', {
      class: 'btn secondary',
      href: 'https://www.instagram.com/' + c.instagram + '/',
      target: '_blank', rel: 'noopener',
    }, 'Open Instagram'));
  } else {
    row.appendChild(el('a', {
      class: 'btn secondary',
      href: 'https://www.google.com/search?q=' + encodeURIComponent('"' + c.name + '" instagram'),
      target: '_blank', rel: 'noopener',
    }, 'Google search'));
  }
  if (c.profile_url) {
    row.appendChild(el('a', {
      class: 'btn secondary',
      href: c.profile_url,
      target: '_blank', rel: 'noopener',
    }, 'Volumo page'));
  }
  card.appendChild(row);

  if (!c.instagram || c.confidence === 'weak') {
    const hrow = el('div', { class: 'row' });
    const inp = el('input', {
      type: 'text',
      placeholder: c.confidence === 'weak'
        ? 'Override handle (clears the ! flag) — press Enter'
        : 'Paste IG handle here, press Enter',
    });
    if (c.confidence === 'weak' && c.instagram) inp.value = c.instagram;
    inp.onkeydown = async (e) => {
      if (e.key === 'Enter' && inp.value.trim()) {
        await onSaveHandle(c.name, inp.value.trim());
      }
    };
    hrow.appendChild(inp);
    card.appendChild(hrow);
  }

  const sentRow = el('div', { class: 'row' });
  const lbl = el('label', { class: 'sent-toggle' });
  const cb = el('input', { type: 'checkbox' });
  cb.checked = !!c.sent_today;
  cb.onchange = () => onMarkSent(c.name, cb.checked);
  lbl.appendChild(cb);
  lbl.appendChild(document.createTextNode(' Mark as sent today'));
  sentRow.appendChild(lbl);
  card.appendChild(sentRow);

  return card;
}

// ---------- Mutations (repository_dispatch) ----------

async function dispatchMutation(eventType, payload) {
  await ghPost(
    `/repos/${STORE.owner()}/${STORE.repo()}/dispatches`,
    { event_type: eventType, client_payload: payload || {} },
  );
}

async function onSaveHandle(name, handle) {
  // Optimistic local update
  for (const c of allCreators) {
    if (c.name.trim().toLowerCase() === name.trim().toLowerCase()) {
      c.instagram = handle.replace(/^@/, '');
      c.confidence = 'verified';
      c.source = 'manual';
    }
  }
  renderCards();
  toast('Saving...');
  try {
    await dispatchMutation('save-handle', { name, handle });
    setTimeout(refreshState, MUTATE_REFETCH_DELAY_MS);
  } catch (e) {
    toast('Save failed: ' + e.message, true);
  }
}

async function onMarkSent(name, sent) {
  for (const c of allCreators) {
    if (c.name.trim().toLowerCase() === name.trim().toLowerCase()) {
      c.sent_today = sent;
    }
  }
  renderCards();
  try {
    await dispatchMutation('mark-sent', { name, sent });
    setTimeout(refreshState, MUTATE_REFETCH_DELAY_MS);
  } catch (e) {
    toast('Update failed: ' + e.message, true);
  }
}

async function refreshState() {
  try {
    const state = await loadJsonFromRepo('data/state.json');
    if (state && state.creators) renderDashboard(state);
  } catch (e) {
    /* silently ignore refetch failures - dashboard stays as-is */
  }
}

// ---------- Misc ----------

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function wireHeader() {
  $('btn-picker').onclick = showPicker;
  $('btn-settings').onclick = () => {
    if (confirm('Reset settings? You will need to re-enter your GitHub token.')) {
      STORE.clear();
      showSetup();
    }
  };
}

function wireSetup() {
  $('setup-save').onclick = saveSetup;
  $('setup-pat').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.ctrlKey) saveSetup();
  });
}

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  });
}

wireSetup();
wireHeader();
if (STORE.pat()) {
  $('app').classList.remove('hidden');
  bootstrap();
} else {
  showSetup();
}

})();

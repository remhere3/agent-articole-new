/* ── State ────────────────────────────────────────────────────────────── */
let allUsers = [];
let allTopics = [];

/* ── Navigation ──────────────────────────────────────────────────────── */
function showSection(name) {
  document.querySelectorAll('section[id^="section-"]').forEach(s => s.classList.add('d-none'));
  document.querySelectorAll('.sidebar .list-group-item').forEach(el => el.classList.remove('active'));

  document.getElementById('section-' + name).classList.remove('d-none');
  const navEl = document.getElementById('nav-' + name);
  if (navEl) navEl.classList.add('active');

  if (name === 'dashboard') loadDashboard();
  else if (name === 'topics')   loadTopics();
  else if (name === 'users')    loadUsers();
  else if (name === 'results')  { populateTopicFilter('filter-topic'); loadResults(); }
  else if (name === 'runs')     { populateTopicFilter('filter-runs-topic'); loadRuns(); }
  else if (name === 'articles') { populateTopicFilter('filter-articles-topic'); loadArticles(); }
}

/* ── API helpers ──────────────────────────────────────────────────────── */
async function api(method, path, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch('/api' + path, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || JSON.stringify(err));
  }
  return res.json();
}

function toast(msg, type = 'success') {
  const el = document.getElementById('toast');
  const body = document.getElementById('toast-body');
  el.className = `toast align-items-center text-white border-0 bg-${type === 'success' ? 'success' : 'danger'}`;
  body.textContent = msg;
  bootstrap.Toast.getOrCreateInstance(el, { delay: 3500 }).show();
}

function fmt(dateStr) {
  if (!dateStr) return '—';
  try {
    return new Date(dateStr).toLocaleString('ro-RO', { timeZone: 'Europe/Bucharest' });
  } catch { return dateStr; }
}

function providerBadge(p) {
  const cls = { anthropic: 'badge-anthropic', tavily: 'badge-tavily', ollama: 'badge-ollama', searxng: 'badge-searxng', author: 'badge-author' };
  return `<span class="badge ${cls[p] || 'bg-secondary'}">${p}</span>`;
}

function statusBadge(s) {
  const cls = { success: 'badge-success', error: 'badge-error', running: 'badge-running' };
  return `<span class="badge ${cls[s] || 'bg-secondary'}">${s}</span>`;
}

/* ── Dashboard ───────────────────────────────────────────────────────── */
async function loadDashboard() {
  const [topics, users, results, runs] = await Promise.all([
    api('GET', '/topics'),
    api('GET', '/users'),
    api('GET', '/searches/results?limit=1000'),
    api('GET', '/searches/runs?limit=1000'),
  ]);
  allTopics = topics; allUsers = users;

  document.getElementById('stat-topics').textContent = topics.filter(t => t.active).length;
  document.getElementById('stat-users').textContent = users.length;
  document.getElementById('stat-results').textContent = results.length;
  document.getElementById('stat-runs').textContent = runs.length;

  const container = document.getElementById('dashboard-topics');
  container.innerHTML = topics.filter(t => t.active).map(t => `
    <div class="col-md-6 col-lg-4">
      <div class="topic-card">
        <div class="d-flex justify-content-between align-items-start">
          <div>
            <h6 class="mb-1">${esc(t.name)}</h6>
            <div class="text-muted small mb-2">${esc(t.keywords)}</div>
          </div>
          ${providerBadge(t.provider)}
        </div>
        <div class="d-flex gap-2 flex-wrap small text-muted mb-2">
          <span><i class="bi bi-calendar3"></i> ${t.days_back}z</span>
          <span><i class="bi bi-clock"></i> /${t.periodicity_hours}h</span>
          <span><i class="bi bi-people"></i> ${t.users.length}</span>
          <span><i class="bi bi-check-circle ${t.send_email ? 'text-success' : 'text-muted'}"></i> email</span>
        </div>
        <div class="d-flex gap-2">
          <button class="btn btn-sm btn-outline-primary" onclick="runSearch(${t.id})">
            <i class="bi bi-play-fill me-1"></i>Ruleaza
          </button>
          <span class="text-muted small align-self-center">Ultima: ${fmt(t.last_run_at)}</span>
        </div>
      </div>
    </div>`).join('') || '<div class="col text-muted">Nu exista topicuri active.</div>';
}

/* ── Topics ──────────────────────────────────────────────────────────── */
async function loadTopics() {
  allTopics = await api('GET', '/topics');
  allUsers  = await api('GET', '/users');
  const el = document.getElementById('topics-list');
  el.innerHTML = allTopics.map(t => `
    <div class="topic-card">
      <div class="d-flex justify-content-between align-items-start flex-wrap gap-2">
        <div>
          <h6 class="mb-1">${esc(t.name)}
            <span class="ms-2 badge ${t.active ? 'bg-success' : 'bg-secondary'}">${t.active ? 'activ' : 'inactiv'}</span>
          </h6>
          <div class="text-muted small mb-1">${esc(t.keywords)}</div>
          <div class="d-flex gap-3 small text-muted">
            <span>${providerBadge(t.provider)}</span>
            <span><i class="bi bi-calendar3"></i> ultimele ${t.days_back} zile</span>
            <span><i class="bi bi-clock"></i> la fiecare ${t.periodicity_hours}h</span>
            <span><i class="bi bi-people"></i> ${t.users.map(u => esc(u.name)).join(', ') || 'nimeni'}</span>
          </div>
        </div>
        <div class="d-flex gap-2">
          <button class="btn btn-sm btn-outline-primary" onclick="runSearch(${t.id})" title="Ruleaza acum">
            <i class="bi bi-play-fill"></i>
          </button>
          <button class="btn btn-sm btn-outline-secondary" onclick="editTopic(${t.id})" title="Editeaza"
            data-bs-toggle="modal" data-bs-target="#modalTopic">
            <i class="bi bi-pencil"></i>
          </button>
          <button class="btn btn-sm btn-outline-danger" onclick="deleteTopic(${t.id})" title="Sterge">
            <i class="bi bi-trash"></i>
          </button>
        </div>
      </div>
    </div>`).join('') || '<div class="text-muted">Nu exista topicuri.</div>';
}

function openTopicModal() {
  document.getElementById('topic-id').value = '';
  document.getElementById('topic-name').value = '';
  document.getElementById('topic-user_question').value = '';
  document.getElementById('topic-keywords').value = '';
  document.getElementById('topic-days_back').value = 7;
  document.getElementById('topic-periodicity_hours').value = 24;
  document.getElementById('topic-timeout_seconds').value = 300;
  document.getElementById('topic-provider').value = 'anthropic';
  document.getElementById('topic-active').checked = true;
  document.getElementById('topic-send_email').checked = true;
  document.getElementById('modalTopicTitle').textContent = 'Topic nou';
  const testBtn = document.getElementById('btn-test-provider');
  if (testBtn) testBtn.textContent = 'Testează';
  renderUserCheckboxes([]);
}

function renderUserCheckboxes(selectedIds) {
  const container = document.getElementById('topic-users-checkboxes');
  if (!allUsers.length) {
    container.innerHTML = '<span class="text-muted small">Nu exista utilizatori. Adauga mai intai.</span>';
    return;
  }
  container.innerHTML = allUsers.map(u => `
    <div class="form-check form-check-inline">
      <input class="form-check-input" type="checkbox" id="tu-${u.id}" value="${u.id}"
        ${selectedIds.includes(u.id) ? 'checked' : ''}>
      <label class="form-check-label small" for="tu-${u.id}">${esc(u.name)}</label>
    </div>`).join('');
}

function editTopic(id) {
  const t = allTopics.find(x => x.id === id);
  if (!t) return;
  document.getElementById('topic-id').value = t.id;
  document.getElementById('topic-name').value = t.name;
  document.getElementById('topic-user_question').value = t.user_question || '';
  document.getElementById('topic-keywords').value = t.keywords;
  document.getElementById('topic-days_back').value = t.days_back;
  document.getElementById('topic-periodicity_hours').value = t.periodicity_hours;
  document.getElementById('topic-timeout_seconds').value = t.timeout_seconds || 300;
  document.getElementById('topic-provider').value = t.provider;
  document.getElementById('topic-active').checked = t.active;
  document.getElementById('topic-send_email').checked = t.send_email;
  document.getElementById('modalTopicTitle').textContent = 'Editeaza topic';
  const testBtn = document.getElementById('btn-test-provider');
  if (testBtn) testBtn.textContent = 'Testează';
  renderUserCheckboxes(t.users.map(u => u.id));
}

async function saveTopic() {
  const id = document.getElementById('topic-id').value;
  const userIds = [...document.querySelectorAll('#topic-users-checkboxes input:checked')].map(el => +el.value);
  const userQ    = document.getElementById('topic-user_question').value.trim();
  const keywords = document.getElementById('topic-keywords').value.trim();
  const payload = {
    name: document.getElementById('topic-name').value.trim(),
    user_question: userQ || null,
    keywords: keywords || null,
    days_back: +document.getElementById('topic-days_back').value,
    periodicity_hours: +document.getElementById('topic-periodicity_hours').value,
    timeout_seconds: +document.getElementById('topic-timeout_seconds').value || 300,
    provider: document.getElementById('topic-provider').value,
    active: document.getElementById('topic-active').checked,
    send_email: document.getElementById('topic-send_email').checked,
    user_ids: userIds,
  };
  if (!payload.name) { toast('Completeaza numele topicului', 'danger'); return; }
  if (!userQ && !keywords) { toast('Completeaza cel putin intrebarea sau cuvintele cheie', 'danger'); return; }
  try {
    if (id) await api('PUT', `/topics/${id}`, payload);
    else    await api('POST', '/topics', payload);
    bootstrap.Modal.getInstance(document.getElementById('modalTopic')).hide();
    toast('Topic salvat');
    loadTopics();
    loadDashboard();
  } catch (e) { toast(e.message, 'danger'); }
}

async function deleteTopic(id) {
  if (!confirm('Stergi topicul si toate rezultatele asociate?')) return;
  try { await api('DELETE', `/topics/${id}`); toast('Topic sters'); loadTopics(); }
  catch (e) { toast(e.message, 'danger'); }
}

let _runTimerInterval = null;

function _startRunTimer(topicName) {
  const banner = document.getElementById('run-timer-banner');
  const clock  = document.getElementById('run-timer-clock');
  const label  = document.getElementById('run-timer-topic');
  banner.style.display = 'flex';
  label.textContent = topicName || 'cautare';
  let secs = 0;
  clock.textContent = '0s';
  _runTimerInterval = setInterval(() => {
    secs++;
    clock.textContent = secs < 60 ? `${secs}s` : `${Math.floor(secs/60)}m ${secs%60}s`;
  }, 1000);
}

function _stopRunTimer() {
  clearInterval(_runTimerInterval);
  _runTimerInterval = null;
  document.getElementById('run-timer-banner').style.display = 'none';
}

async function runSearch(topicId) {
  const topic = (allTopics || []).find(t => t.id === topicId);
  _startRunTimer(topic ? topic.name : `#${topicId}`);
  try {
    const run = await api('POST', `/searches/run/${topicId}`);
    _stopRunTimer();
    toast(`Finalizat in ${run.results_count} articole`, 'success');
    loadDashboard();
  } catch (e) {
    _stopRunTimer();
    toast(e.message, 'danger');
  }
}

/* ── Users ───────────────────────────────────────────────────────────── */
async function loadUsers() {
  allUsers = await api('GET', '/users');
  const el = document.getElementById('users-list');
  el.innerHTML = `
    <table class="table table-hover bg-white rounded shadow-sm overflow-hidden">
      <thead class="table-light"><tr>
        <th>#</th><th>Nume</th><th>Email</th><th>Status</th><th>Creat</th><th></th>
      </tr></thead>
      <tbody>
        ${allUsers.map(u => `
          <tr>
            <td class="text-muted">${u.id}</td>
            <td>${esc(u.name)}</td>
            <td>${esc(u.email)}</td>
            <td><span class="badge ${u.active ? 'bg-success' : 'bg-secondary'}">${u.active ? 'activ' : 'inactiv'}</span></td>
            <td class="text-muted small">${fmt(u.created_at)}</td>
            <td>
              <button class="btn btn-sm btn-outline-secondary me-1" onclick="editUser(${u.id})"
                data-bs-toggle="modal" data-bs-target="#modalUser"><i class="bi bi-pencil"></i></button>
              <button class="btn btn-sm btn-outline-danger" onclick="deleteUser(${u.id})"><i class="bi bi-trash"></i></button>
            </td>
          </tr>`).join('')}
      </tbody>
    </table>` || '<p class="text-muted">Nu exista utilizatori.</p>';
}

function openUserModal() {
  document.getElementById('user-id').value = '';
  document.getElementById('user-name').value = '';
  document.getElementById('user-email').value = '';
  document.getElementById('user-active').checked = true;
  document.getElementById('modalUserTitle').textContent = 'Utilizator nou';
}

function editUser(id) {
  const u = allUsers.find(x => x.id === id);
  if (!u) return;
  document.getElementById('user-id').value = u.id;
  document.getElementById('user-name').value = u.name;
  document.getElementById('user-email').value = u.email;
  document.getElementById('user-active').checked = u.active;
  document.getElementById('modalUserTitle').textContent = 'Editeaza utilizator';
}

async function saveUser() {
  const id = document.getElementById('user-id').value;
  const payload = {
    name: document.getElementById('user-name').value.trim(),
    email: document.getElementById('user-email').value.trim(),
    active: document.getElementById('user-active').checked,
  };
  if (!payload.name || !payload.email) { toast('Completeaza toate campurile', 'danger'); return; }
  try {
    if (id) await api('PUT', `/users/${id}`, payload);
    else    await api('POST', '/users', payload);
    bootstrap.Modal.getInstance(document.getElementById('modalUser')).hide();
    toast('Utilizator salvat');
    loadUsers();
  } catch (e) { toast(e.message, 'danger'); }
}

async function deleteUser(id) {
  if (!confirm('Stergi utilizatorul?')) return;
  try { await api('DELETE', `/users/${id}`); toast('Utilizator sters'); loadUsers(); }
  catch (e) { toast(e.message, 'danger'); }
}

/* ── Results ─────────────────────────────────────────────────────────── */
async function populateTopicFilter(selectId) {
  if (!allTopics.length) allTopics = await api('GET', '/topics');
  const sel = document.getElementById(selectId);
  const cur = sel.value;
  sel.innerHTML = '<option value="">Toate topicurile</option>' +
    allTopics.map(t => `<option value="${t.id}" ${cur == t.id ? 'selected' : ''}>${esc(t.name)}</option>`).join('');
}

async function loadResults() {
  const topicId = document.getElementById('filter-topic').value;
  const qTopic  = topicId ? `&topic_id=${topicId}` : '';
  const [results, runs] = await Promise.all([
    api('GET', `/searches/results?limit=500${qTopic}`),
    api('GET', `/searches/runs?limit=100${qTopic}`),
  ]);
  const el = document.getElementById('results-list');
  if (!results.length) { el.innerHTML = '<p class="text-muted">Nu exista rezultate.</p>'; return; }

  // index runs by id
  const runMap = Object.fromEntries(runs.map(r => [r.id, r]));

  // group results by run_id (order preserved — results come newest first)
  const groups = {};
  const groupOrder = [];
  for (const r of results) {
    const rid = r.run_id ?? 0;
    if (!groups[rid]) { groups[rid] = []; groupOrder.push(rid); }
    groups[rid].push(r);
  }

  el.innerHTML = '<div class="accordion" id="results-accordion">' +
    groupOrder.map((rid, idx) => {
      const run     = runMap[rid] || {};
      const items   = groups[rid];
      const topic   = allTopics.find(t => t.id === run.topic_id);
      const topicName = topic?.name || `Topic #${run.topic_id || '?'}`;
      let dur = '';
      if (run.started_at && run.finished_at) {
        const ms = new Date(run.finished_at) - new Date(run.started_at);
        dur = ` · ${(ms/1000).toFixed(1)}s`;
      }
      const dateStr = run.started_at ? fmt(run.started_at) : '—';
      const isOpen  = idx === 0;
      const colId   = `run-collapse-${rid}`;

      const queryLabel = topic?.user_question
        ? `<span style="font-style:italic;color:#555;font-size:.78rem;max-width:340px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(topic.user_question)}">
            <i class="bi bi-chat-left-quote me-1" style="color:#0044aa"></i>${esc(topic.user_question.substring(0,80))}${topic.user_question.length>80?'…':''}</span>`
        : topic?.keywords
        ? `<span style="font-family:'Inconsolata',monospace;color:#c45c00;font-size:.78rem;">${esc(topic.keywords)}</span>`
        : '';

      const header = `
        <div class="d-flex align-items-center gap-2 flex-wrap" style="min-width:0;width:100%">
          <span class="fw-600" style="font-size:.88rem;">${esc(topicName)}</span>
          <span class="text-muted" style="font-size:.78rem;">Run #${rid}</span>
          ${providerBadge(run.provider || items[0]?.provider || '?')}
          <span class="badge" style="background:rgba(26,107,74,.12);color:#1a6b4a;font-family:'Inconsolata',monospace;">
            ${items.length} articole
          </span>
          <span class="text-muted" style="font-size:.78rem;">${dateStr}${dur}</span>
          ${queryLabel}
        </div>`;

      const body = items.map(r => `
        <div class="result-card">
          <div class="d-flex justify-content-between align-items-start gap-2">
            <div style="min-width:0">
              <a href="${esc(r.url)}" target="_blank" style="word-break:break-word">${esc(r.title)}</a>
              <div class="text-muted small mt-1">
                ${r.source         ? `<span>${esc(r.source)}</span> &bull; ` : ''}
                ${r.published_date ? `<span>${esc(r.published_date)}</span> &bull; ` : ''}
                ${r.authors        ? `<span>${esc(r.authors)}</span>` : ''}
              </div>
              ${r.summary ? `<div class="mt-1 small text-secondary">${esc(r.summary)}</div>` : ''}
            </div>
            <button class="btn btn-sm btn-outline-danger flex-shrink-0" onclick="deleteResult(${r.id})">
              <i class="bi bi-x"></i>
            </button>
          </div>
        </div>`).join('');

      return `
        <div class="accordion-item" style="border:1px solid var(--border);border-radius:6px;margin-bottom:8px;overflow:hidden;">
          <h2 class="accordion-header">
            <button class="accordion-button ${isOpen ? '' : 'collapsed'}"
              type="button" data-bs-toggle="collapse" data-bs-target="#${colId}"
              style="background:var(--surface);padding:.65rem 1rem;font-family:'Outfit',sans-serif;">
              ${header}
            </button>
          </h2>
          <div id="${colId}" class="accordion-collapse collapse ${isOpen ? 'show' : ''}" data-bs-parent="#results-accordion">
            <div class="accordion-body" style="padding:.75rem;">
              ${rid ? `<div class="d-flex justify-content-end gap-2 mb-2">
                <button class="btn btn-sm btn-outline-secondary" title="Export CSV (aceasta rulare)" onclick="exportRun(${rid}, 'csv')"><i class="bi bi-filetype-csv me-1"></i>CSV</button>
                <button class="btn btn-sm btn-outline-secondary" title="Export JSON (aceasta rulare)" onclick="exportRun(${rid}, 'json')"><i class="bi bi-filetype-json me-1"></i>JSON</button>
              </div>` : ''}
              ${body}
            </div>
          </div>
        </div>`;
    }).join('') + '</div>';
}

async function deleteResult(id) {
  try { await api('DELETE', `/searches/results/${id}`); toast('Rezultat sters'); loadResults(); }
  catch (e) { toast(e.message, 'danger'); }
}

/* ── Runs ────────────────────────────────────────────────────────────── */
async function loadRuns() {
  const topicId = document.getElementById('filter-runs-topic').value;
  const url = '/searches/runs?limit=100' + (topicId ? `&topic_id=${topicId}` : '');
  const el = document.getElementById('runs-list');
  let runs;
  try {
    runs = await api('GET', url);
  } catch(e) {
    el.innerHTML = `<p class="text-danger">Eroare la incarcare rulari: ${esc(e.message)}</p>`;
    return;
  }

  if (!runs || !runs.length) {
    el.innerHTML = '<p class="text-muted">Nu exista rulari.</p>';
    _updateRunsDeleteBtn();
    return;
  }

  const rows = runs.map(r => {
    const topicName = allTopics.find(t => t.id === r.topic_id)?.name || '#' + r.topic_id;
    let dur = '—';
    if (r.started_at && r.finished_at) {
      const ms = new Date(r.finished_at) - new Date(r.started_at);
      dur = (ms / 1000).toFixed(1) + 's';
    }
    const hasResults = r.results_count > 0;
    const previewBtn = r.status === 'success' && hasResults
      ? `<a href="/api/searches/runs/${r.id}/preview-email" target="_blank" class="btn btn-sm btn-outline-secondary py-0 px-1" title="Previzualizare email"><i class="bi bi-eye"></i></a>`
      : '';
    const exportBtns = hasResults
      ? `<button class="btn btn-sm btn-outline-secondary py-0 px-1" title="Export CSV (aceasta rulare)" onclick="exportRun(${r.id}, 'csv')"><i class="bi bi-filetype-csv"></i></button>` +
        `<button class="btn btn-sm btn-outline-secondary py-0 px-1 ms-1" title="Export JSON (aceasta rulare)" onclick="exportRun(${r.id}, 'json')"><i class="bi bi-filetype-json"></i></button>`
      : '';
    return '<tr>' +
      '<td><input type="checkbox" class="run-cb" value="' + r.id + '" onchange="_updateRunsDeleteBtn()"></td>' +
      '<td class="text-muted">' + r.id + '</td>' +
      '<td>' + esc(topicName) + '</td>' +
      '<td>' + providerBadge(r.provider || '?') + '</td>' +
      '<td>' + statusBadge(r.status) + '</td>' +
      '<td>' + r.results_count + '</td>' +
      '<td class="text-muted small">' + fmt(r.started_at) + '</td>' +
      '<td class="small">' + dur + '</td>' +
      '<td class="small text-muted" style="white-space:nowrap">' + fmtTelemetry(r) + '</td>' +
      '<td>' + fmtCost(r) + '</td>' +
      '<td class="text-danger small" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;">' +
        (r.error_message ? esc(r.error_message.substring(0, 80)) : '') +
      '</td>' +
      '<td style="white-space:nowrap">' + previewBtn + (previewBtn && exportBtns ? ' ' : '') + exportBtns + '</td>' +
    '</tr>';
  }).join('');

  el.innerHTML =
    '<table class="table table-hover bg-white rounded shadow-sm" id="runs-table">' +
      '<thead class="table-light"><tr>' +
        '<th style="width:36px"><input type="checkbox" id="runs-select-all" title="Selecteaza tot" onchange="toggleAllRuns(this)"></th>' +
        '<th>#</th><th>Topic</th><th>Provider</th><th>Status</th><th>Articole</th><th>Inceput</th><th>Durata</th><th>Telemetrie</th><th>Cost est.</th><th>Eroare</th><th></th>' +
      '</tr></thead>' +
      '<tbody>' + rows + '</tbody>' +
    '</table>';
  _updateRunsDeleteBtn();
}

function toggleAllRuns(cb) {
  document.querySelectorAll('.run-cb').forEach(el => el.checked = cb.checked);
  _updateRunsDeleteBtn();
}

function _updateRunsDeleteBtn() {
  const checked = document.querySelectorAll('.run-cb:checked');
  const btn = document.getElementById('btn-delete-runs');
  const cnt = document.getElementById('runs-sel-count');
  if (!btn) return;
  if (checked.length > 0) {
    btn.classList.remove('d-none');
    cnt.textContent = checked.length;
  } else {
    btn.classList.add('d-none');
  }
}

async function deleteSelectedRuns() {
  const ids = [...document.querySelectorAll('.run-cb:checked')].map(el => +el.value);
  if (!ids.length) return;
  if (!confirm(`Stergi ${ids.length} rular${ids.length === 1 ? 'e' : 'i'}? Articolele aferente vor fi sterse si ele.`)) return;
  await api('DELETE', '/searches/runs', { ids });
  toast(ids.length + ' rular' + (ids.length === 1 ? 'e sterse' : 'i sterse') + '.', 'success');
  loadRuns();
  loadDashboard();
}


/* ── Helpers ─────────────────────────────────────────────────────────── */
function fmtTelemetry(r) {
  const parts = [];
  if (r.api_calls   != null) parts.push('req: ' + r.api_calls);
  if (r.tokens_input  != null) parts.push('in: ' + r.tokens_input.toLocaleString('ro-RO'));
  if (r.tokens_output != null) parts.push('out: ' + r.tokens_output.toLocaleString('ro-RO'));
  return parts.length ? parts.join(' · ') : '—';
}

function fmtCost(r) {
  if (r.estimated_cost_usd == null) return '<span class="text-muted">—</span>';
  return `<span class="text-muted small">$${r.estimated_cost_usd.toFixed(4)}</span>`;
}

function esc(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* ── Log panel ───────────────────────────────────────────────────────── */
let _logSource  = null;
let _logOpen    = false;
let _unreadLogs = 0;

function toggleLogs() {
  const panel = document.getElementById('log-panel');
  const fab   = document.getElementById('log-fab');
  _logOpen = !_logOpen;

  if (_logOpen) {
    panel.classList.remove('log-panel-closed');
    panel.classList.add('log-panel-open');
    fab.classList.add('active');
    _unreadLogs = 0;
    document.getElementById('log-fab-badge').classList.remove('visible');
    // Scroll la ultimul log cand se deschide
    const body = document.getElementById('log-body');
    body.scrollTop = body.scrollHeight;
  } else {
    panel.classList.remove('log-panel-open');
    panel.classList.add('log-panel-closed');
    fab.classList.remove('active');
  }
  // Stream-ul ramane mereu activ — nu se opreste la inchiderea panoului
}

function _startLogStream() {
  if (_logSource) return;
  const status = document.getElementById('log-status');
  status.textContent = 'conectare...';
  status.className = 'badge bg-warning';

  _logSource = new EventSource('/api/logs/stream');

  _logSource.onopen = () => {
    status.textContent = '● live';
    status.className = 'badge bg-success';
  };

  _logSource.onmessage = (e) => {
    _appendLog(e.data);
  };

  _logSource.onerror = () => {
    status.textContent = 'reconectare...';
    status.className = 'badge bg-warning';
    if (_logSource) { _logSource.close(); _logSource = null; }
    setTimeout(_startLogStream, 3000);  // reconecteaza intotdeauna
  };
}

function _stopLogStream() {
  if (_logSource) { _logSource.close(); _logSource = null; }
  const status = document.getElementById('log-status');
  status.textContent = 'deconectat';
  status.className = 'badge bg-secondary';
}

function _appendLog(line) {
  const body = document.getElementById('log-body');
  const div  = document.createElement('div');
  div.className = 'log-line';
  div.innerHTML = _colorizeLog(line);
  body.appendChild(div);

  // Pastreaza maxim 1500 linii in DOM
  while (body.children.length > 1500) body.removeChild(body.firstChild);

  // Auto-scroll numai daca utilizatorul e la baza
  const atBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 40;
  if (atBottom) body.scrollTop = body.scrollHeight;

  // Badge + pulse FAB cand panoul e inchis
  if (!_logOpen) {
    _unreadLogs++;
    const badge = document.getElementById('log-fab-badge');
    badge.textContent = _unreadLogs > 99 ? '99+' : _unreadLogs;
    badge.classList.add('visible');
    const fab = document.getElementById('log-fab');
    fab.classList.remove('pulse');
    void fab.offsetWidth;  // reflow pentru restart animatie
    fab.classList.add('pulse');
  }
}

function _colorizeLog(raw) {
  const esc = s => s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

  // Format backend: "HH:MM:SS [LEVEL] module.name: message"
  const m = raw.match(/^(\d{2}:\d{2}:\d{2})\s+\[(\w+)\]\s+([^:]+):\s*([\s\S]*)$/);
  if (!m) return `<span style="color:#6b7280">${esc(raw)}</span>`;

  const [, ts, level, mod, msg] = m;

  const lvlColor = {
    INFO:'#9cdcfe', WARNING:'#dcdcaa', ERROR:'#f48771',
    DEBUG:'#6a9955', CRITICAL:'#ff6b6b'
  }[level] || '#d4d4d4';

  // Culoare mesaj in functie de continut
  let msgColor = '#d4d4d4';
  if (msg.includes('╔═') || msg.includes('START'))    msgColor = '#4ec9b0';
  else if (msg.includes('╚═'))                        msgColor = '#c586c0';
  else if (msg.includes('SUCCESS'))                   msgColor = '#86efac';
  else if (level === 'ERROR' || msg.includes('ERROR'))msgColor = '#f48771';
  else if (level === 'WARNING')                       msgColor = '#dcdcaa';
  else if (msg.startsWith('  →') || msg.startsWith('→')) msgColor = '#e9d5ff';
  else if (msg.includes('[Anthropic]'))               msgColor = '#93c5fd';
  else if (msg.includes('[Tavily]'))                  msgColor = '#fde68a';
  else if (msg.includes('[Ollama'))                   msgColor = '#86efac';

  // Highlight dinamic: timpi, run-uri, numere articole
  const hlMsg = esc(msg)
    .replace(/(\d+\.\d+s)/g,         '<b style="color:#fbbf24">$1</b>')
    .replace(/(Run #\d+)/g,          '<b style="color:#60a5fa">$1</b>')
    .replace(/(\d+) articole/g,      '<b style="color:#a5f3c8">$1 articole</b>')
    .replace(/(╔═[^\n]*)/g,          '<b>$1</b>')
    .replace(/(╚═[^\n]*)/g,          '<b>$1</b>');

  return `<span style="color:#374151">${esc(ts)}</span> ` +
         `<span style="color:${lvlColor};font-weight:600">[${level}]</span> ` +
         `<span style="color:#4b5563">${esc(mod)}:</span> ` +
         `<span style="color:${msgColor}">${hlMsg}</span>`;
}

function clearLogs() {
  document.getElementById('log-body').innerHTML = '';
  _unreadLogs = 0;
  document.getElementById('log-fab-badge').classList.remove('visible');
}

/* ── Footer status ───────────────────────────────────────────────────── */
async function loadFooterStatus() {
  try {
    const s = await fetch('/api/status').then(r => r.json()).catch(() => null);
    if (!s) return;

    const set = (id, text) => {
      const el = document.getElementById(id);
      if (el) el.querySelector('span').textContent = text;
    };

    set('f-model',   s.anthropic_configured ? s.anthropic_model : 'Anthropic: neconfigurat');
    set('f-tavily',  s.tavily_configured    ? 'Tavily: activ'   : 'Tavily: neconfigurat');
    set('f-ollama',  `Ollama: ${s.ollama_model}`);
    set('f-smtp',    s.smtp_configured      ? 'Email: configurat' : 'Email: neconfigurat');
    set('f-topics',  `${s.active_topics} topicuri active`);
    set('f-results', `${s.total_results} articole`);

    if (s.last_run_at) {
      const d = new Date(s.last_run_at);
      set('f-lastrun', `Ultima rulare: ${d.toLocaleString('ro-RO', { timeZone: 'Europe/Bucharest' })} (${s.last_run_status})`);
    }
  } catch { /* footer e cosmetic, nu blocheaza */ }
}

/* ── Test provider ───────────────────────────────────────────────────── */
async function testProvider() {
  const provider = document.getElementById('topic-provider').value;
  const btn = document.getElementById('btn-test-provider');
  if (!btn) return;
  btn.disabled = true;
  btn.textContent = '…';
  try {
    const res = await fetch(`/api/searches/validate-provider/${provider}`).then(r => r.json());
    btn.disabled = false;
    if (res.ok) {
      btn.textContent = '✓ OK';
      btn.className = 'btn btn-sm btn-success';
      setTimeout(() => { btn.textContent = 'Testează'; btn.className = 'btn btn-sm btn-outline-secondary'; }, 3000);
    } else {
      btn.textContent = '✗ Eroare';
      btn.className = 'btn btn-sm btn-danger';
      toast(res.message, 'danger');
      setTimeout(() => { btn.textContent = 'Testează'; btn.className = 'btn btn-sm btn-outline-secondary'; }, 4000);
    }
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Testează';
    toast('Eroare conexiune: ' + e.message, 'danger');
  }
}

/* ── Articole (view global flat) ─────────────────────────────────────── */
let _allArticles = [];

async function loadArticles() {
  const topicId = document.getElementById('filter-articles-topic').value;
  const q = topicId ? `&topic_id=${topicId}` : '';
  const el = document.getElementById('articles-list');
  el.innerHTML = '<p class="text-muted">Se încarcă...</p>';
  try {
    _allArticles = await api('GET', `/searches/results?limit=5000${q}`);
  } catch (e) {
    el.innerHTML = `<p class="text-danger">Eroare: ${esc(e.message)}</p>`;
    return;
  }
  _renderArticles();
}

function _renderArticles() {
  const el = document.getElementById('articles-list');
  const search = (document.getElementById('articles-search')?.value || '').toLowerCase();
  const dedup  = document.getElementById('articles-dedup')?.checked;

  let items = _allArticles;

  // Deduplicare by URL
  if (dedup) {
    const seen = new Set();
    items = items.filter(r => {
      if (seen.has(r.url)) return false;
      seen.add(r.url);
      return true;
    });
  }

  // Filtrare text
  if (search) {
    items = items.filter(r =>
      (r.title || '').toLowerCase().includes(search) ||
      (r.summary || '').toLowerCase().includes(search) ||
      (r.authors || '').toLowerCase().includes(search) ||
      (r.source || '').toLowerCase().includes(search)
    );
  }

  const countEl = document.getElementById('articles-count');
  if (countEl) countEl.textContent = `${items.length} / ${_allArticles.length} articole`;

  if (!items.length) {
    el.innerHTML = '<p class="text-muted">Nu există articole pentru criteriile selectate.</p>';
    return;
  }

  const rows = items.map(r => {
    const topic = allTopics.find(t => t.id === r.topic_id);
    return `<tr>
      <td><a href="${esc(r.url)}" target="_blank" style="word-break:break-word">${esc(r.title)}</a>
        ${r.summary ? `<div class="text-muted small mt-1" style="max-width:500px">${esc(r.summary.substring(0,150))}${r.summary.length>150?'…':''}</div>` : ''}
      </td>
      <td class="small text-muted">${esc(r.source || '—')}</td>
      <td class="small">${esc(r.published_date || '—')}</td>
      <td class="small text-muted">${esc((r.authors || '').substring(0, 40))}${(r.authors||'').length>40?'…':''}</td>
      <td>${providerBadge(r.provider || '?')}</td>
      <td class="small text-muted">${esc(topic?.name || '#' + r.topic_id)}</td>
    </tr>`;
  }).join('');

  el.innerHTML = `
    <table class="table table-hover table-sm bg-white rounded shadow-sm">
      <thead class="table-light"><tr>
        <th>Titlu / Rezumat</th>
        <th>Sursă</th>
        <th>Data</th>
        <th>Autori</th>
        <th>Provider</th>
        <th>Topic</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function exportArticles(format) {
  const topicId = document.getElementById('filter-articles-topic').value;
  const q = topicId ? `&topic_id=${topicId}` : '';
  window.location.href = `/api/searches/results/export?format=${format}${q}`;
}

function exportRun(runId, format) {
  window.location.href = `/api/searches/results/export?format=${format}&run_id=${runId}`;
}

/* ── Init ────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  showSection('dashboard');
  loadFooterStatus();
  setInterval(loadFooterStatus, 30000);
  _startLogStream();  // stream intotdeauna activ, indiferent de starea panoului
});

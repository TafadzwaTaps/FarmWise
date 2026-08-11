// workers.js — auth guard + worker list/create/detail (attendance + payroll).

const API = '/api/v1';

function getToken() {
  return localStorage.getItem('farmwise_token') || sessionStorage.getItem('farmwise_token');
}

function toggleLightMode() {
  const html = document.documentElement;
  const isLight = html.getAttribute('data-theme') === 'light';
  const btn = document.getElementById('themeBtn');
  if (isLight) { html.removeAttribute('data-theme'); try { localStorage.setItem('farmwise_theme', 'dark'); } catch (e) {} btn.textContent = '🌙 Toggle theme'; }
  else { html.setAttribute('data-theme', 'light'); try { localStorage.setItem('farmwise_theme', 'light'); } catch (e) {} btn.textContent = '☀️ Toggle theme'; }
}
(function () {
  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  document.getElementById('themeBtn').textContent = isLight ? '☀️ Toggle theme' : '🌙 Toggle theme';
})();

// Mobile sidebar toggle — the sidebar is position:fixed and slid off-screen
// below 800px (see the .sidebar media query), so it needs an explicit
// open/close control on small screens instead of always being visible.
(function () {
  const menuToggle = document.getElementById('menuToggle');
  const sidebarEl = document.querySelector('.sidebar');
  const overlayEl = document.getElementById('sidebarOverlay');
  if (!menuToggle || !sidebarEl || !overlayEl) return;

  function closeSidebar() {
    sidebarEl.classList.remove('open');
    overlayEl.classList.remove('open');
  }

  menuToggle.addEventListener('click', () => {
    sidebarEl.classList.toggle('open');
    overlayEl.classList.toggle('open');
  });
  overlayEl.addEventListener('click', closeSidebar);
  // Closing on nav-link tap matters even though each link is a full page
  // load — without it the sidebar would visibly still be "open" for the
  // instant before the new page finishes loading.
  sidebarEl.querySelectorAll('.side-link').forEach(link => link.addEventListener('click', closeSidebar));
})();

let token = null;
let farmId = null;
let allWorkers = [];
let activeWorkerId = null;
let statusFilter = '';

async function api(path, { method = 'GET', body } = {}) {
  const headers = { Authorization: 'Bearer ' + token };
  if (body) headers['Content-Type'] = 'application/json';
  const res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  let data = null; try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw new Error((data && (data.error?.message || data.detail)) || `Request failed (${res.status})`);
  return data;
}

function money(n) { return '$' + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function fmtDate(iso) { return new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }); }
function initials(name) { return name.split(' ').map(w => w[0]).slice(0, 2).join('').toUpperCase(); }

// ── Rendering ──────────────────────────────────────────────────────────

function renderWorkers() {
  const grid = document.getElementById('workerGrid');
  const empty = document.getElementById('emptyState');
  const filtered = statusFilter ? allWorkers.filter(w => w.status === statusFilter) : allWorkers;

  if (allWorkers.length === 0) {
    grid.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  if (filtered.length === 0) {
    grid.innerHTML = `<p class="panel-empty">No ${statusFilter} workers.</p>`;
    return;
  }

  grid.innerHTML = filtered.map(w => `
    <div class="worker-card" data-id="${w.id}">
      <div class="worker-card-top">
        <span class="worker-avatar">${initials(w.full_name)}</span>
        <span class="status-badge status-badge--${w.status === 'active' ? 'active' : 'closed'}">${w.status}</span>
      </div>
      <div class="worker-name">${w.full_name}</div>
      <div class="worker-position">${w.position || 'No position set'}</div>
      ${w.wage_amount ? `<div class="worker-wage">${money(w.wage_amount)} <span>/ ${w.wage_type || 'unspecified'}</span></div>` : ''}
    </div>
  `).join('');

  grid.querySelectorAll('.worker-card').forEach(card => {
    card.addEventListener('click', () => openDetail(card.dataset.id));
  });
}

async function loadWorkers() {
  allWorkers = await api(`/farms/${farmId}/workers`);
  renderWorkers();
}

document.getElementById('statusTabs').addEventListener('click', (e) => {
  const tab = e.target.closest('.status-tab');
  if (!tab) return;
  document.querySelectorAll('.status-tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  statusFilter = tab.dataset.filter;
  renderWorkers();
});

// ── Create worker modal ───────────────────────────────────────────────

function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }
document.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(el.dataset.close)));
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeModal(overlay.id); });
});

document.getElementById('newWorkerBtn').addEventListener('click', () => openModal('createModal'));
document.getElementById('emptyNewWorkerBtn').addEventListener('click', () => openModal('createModal'));

document.getElementById('createForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('createAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('createSubmitBtn');
  btn.disabled = true; btn.textContent = 'Adding...';

  try {
    await api(`/farms/${farmId}/workers`, {
      method: 'POST',
      body: {
        full_name: document.getElementById('workerName').value.trim(),
        position: document.getElementById('workerPosition').value.trim() || null,
        phone_number: document.getElementById('workerPhone').value.trim() || null,
        wage_amount: document.getElementById('workerWage').value ? Number(document.getElementById('workerWage').value) : null,
        wage_type: document.getElementById('workerWageType').value || null,
        hire_date: document.getElementById('workerHireDate').value || null,
        notes: document.getElementById('workerNotes').value.trim() || null,
      },
    });
    closeModal('createModal');
    document.getElementById('createForm').reset();
    await loadWorkers();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = 'Add worker';
  }
});

// ── Worker detail modal (attendance + payroll tabs) ───────────────────

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab' + tab.dataset.tab.charAt(0).toUpperCase() + tab.dataset.tab.slice(1)).classList.add('active');
  });
});

async function openDetail(workerId) {
  activeWorkerId = workerId;
  const worker = allWorkers.find(w => w.id === workerId);
  if (!worker) return;

  document.getElementById('detailWorkerName').textContent = worker.full_name;
  document.getElementById('editAlert').classList.remove('show');

  document.getElementById('editName').value = worker.full_name || '';
  document.getElementById('editPosition').value = worker.position || '';
  document.getElementById('editPhone').value = worker.phone_number || '';
  document.getElementById('editStatus').value = worker.status || 'active';
  document.getElementById('editWage').value = worker.wage_amount ?? '';
  document.getElementById('editWageType').value = worker.wage_type || '';
  document.getElementById('editNotes').value = worker.notes || '';

  document.getElementById('aDate').value = new Date().toISOString().slice(0, 10);
  document.getElementById('payDate').value = new Date().toISOString().slice(0, 10);
  document.getElementById('attendanceAlert').classList.remove('show');
  document.getElementById('paymentAlert').classList.remove('show');

  openModal('detailModal');
  await Promise.all([loadAttendance(workerId), loadPayments(workerId)]);
}

document.getElementById('editForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('editAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('editSubmitBtn');
  btn.disabled = true; btn.textContent = 'Saving...';

  try {
    const updated = await api(`/farms/${farmId}/workers/${activeWorkerId}`, {
      method: 'PATCH',
      body: {
        full_name: document.getElementById('editName').value.trim(),
        position: document.getElementById('editPosition').value.trim() || null,
        phone_number: document.getElementById('editPhone').value.trim() || null,
        status: document.getElementById('editStatus').value,
        wage_amount: document.getElementById('editWage').value ? Number(document.getElementById('editWage').value) : null,
        wage_type: document.getElementById('editWageType').value || null,
        notes: document.getElementById('editNotes').value.trim() || null,
      },
    });
    document.getElementById('detailWorkerName').textContent = updated.full_name;
    await loadWorkers();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = 'Save changes';
  }
});

document.getElementById('deleteWorkerBtn').addEventListener('click', async () => {
  const worker = allWorkers.find(w => w.id === activeWorkerId);
  if (!worker) return;
  if (!confirm(`Delete "${worker.full_name}"? This removes their attendance and payment history too. This can't be undone.`)) return;

  try {
    await api(`/farms/${farmId}/workers/${activeWorkerId}`, { method: 'DELETE' });
    closeModal('detailModal');
    await loadWorkers();
  } catch (err) {
    alert(err.message);
  }
});

async function loadAttendance(workerId) {
  const records = await api(`/farms/${farmId}/workers/${workerId}/attendance`);
  const list = document.getElementById('attendanceList');
  list.innerHTML = records.length === 0
    ? '<p class="panel-empty">No attendance recorded.</p>'
    : records.map(r => `
        <div class="record-row">
          <div class="record-row-main" style="text-transform:capitalize">${r.status.replace('_', ' ')}</div>
          <span class="record-row-date">${fmtDate(r.date)}</span>
        </div>
      `).join('');
}

async function loadPayments(workerId) {
  const records = await api(`/farms/${farmId}/workers/${workerId}/payments`);
  const list = document.getElementById('paymentList');
  list.innerHTML = records.length === 0
    ? '<p class="panel-empty">No payments logged.</p>'
    : records.map(r => `
        <div class="record-row">
          <div class="record-row-main">${money(r.amount)}</div>
          <span class="record-row-date">${fmtDate(r.payment_date)}</span>
        </div>
      `).join('');
}

document.getElementById('attendanceForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('attendanceAlert');
  alertBox.classList.remove('show');
  try {
    await api(`/farms/${farmId}/workers/${activeWorkerId}/attendance`, {
      method: 'POST',
      body: {
        date: document.getElementById('aDate').value,
        status: document.getElementById('aStatus').value,
      },
    });
    await loadAttendance(activeWorkerId);
  } catch (err) {
    alertBox.textContent = err.message; // e.g. "already recorded for this worker" (409)
    alertBox.classList.add('show');
  }
});

document.getElementById('paymentForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('paymentAlert');
  alertBox.classList.remove('show');
  try {
    await api(`/farms/${farmId}/workers/${activeWorkerId}/payments`, {
      method: 'POST',
      body: {
        amount: Number(document.getElementById('payAmount').value),
        payment_date: document.getElementById('payDate').value,
      },
    });
    document.getElementById('payAmount').value = '';
    await loadPayments(activeWorkerId);
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  }
});

// ── Init ─────────────────────────────────────────────────────────────

function renderFarmSwitcher(farms, activeFarm) {
  const el = document.getElementById('farmName');
  if (farms.length <= 1) {
    el.textContent = activeFarm.name;
    return;
  }
  el.innerHTML = '';
  const select = document.createElement('select');
  select.id = 'farmSwitcher';
  select.style.cssText = 'background:var(--surface2);border:1px solid var(--border);color:var(--text);' +
    'font-family:var(--sans);font-weight:700;font-size:16px;border-radius:8px;padding:4px 8px;cursor:pointer;';
  farms.forEach(f => {
    const opt = document.createElement('option');
    opt.value = f.id; opt.textContent = f.name;
    if (f.id === activeFarm.id) opt.selected = true;
    select.appendChild(opt);
  });
  select.addEventListener('change', () => {
    localStorage.setItem('farmwise_active_farm_id', select.value);
    window.location.reload();
  });
  el.appendChild(select);
}

async function init() {
  token = getToken();
  if (!token) { window.location.href = '/login'; return; }

  try {
    const [me, farms] = await Promise.all([api('/auth/me'), api('/farms')]);
    document.getElementById('userGreeting').textContent = `Welcome back, ${me.full_name.split(' ')[0]}`;
    document.getElementById('roleBadge').textContent = 'owner';

    if (farms.length === 0) {
      document.getElementById('farmName').textContent = 'No farm yet';
      document.getElementById('noFarmState').style.display = 'block';
      return;
    }

    const savedFarmId = localStorage.getItem('farmwise_active_farm_id');
    const activeFarm = farms.find(f => f.id === savedFarmId) || farms[0];
    farmId = activeFarm.id;
    renderFarmSwitcher(farms, activeFarm);

    await loadWorkers();
    document.getElementById('pageContent').style.display = 'block';
  } catch (err) {
    localStorage.removeItem('farmwise_token');
    localStorage.removeItem('farmwise_refresh');
    localStorage.removeItem('farmwise_user');
    sessionStorage.clear();
    window.location.href = '/login';
  }
}

document.getElementById('logoutBtn').addEventListener('click', async () => {
  const refresh = localStorage.getItem('farmwise_refresh') || sessionStorage.getItem('farmwise_refresh');
  try {
    if (refresh) {
      await fetch(API + '/auth/logout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }),
      });
    }
  } catch (e) {}
  localStorage.removeItem('farmwise_token');
  localStorage.removeItem('farmwise_refresh');
  localStorage.removeItem('farmwise_user');
  sessionStorage.clear();
  window.location.href = '/';
});

init();

// animals.js — auth guard + batch list/create/detail (mortality + medication).

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
let allBatches = [];
let activeBatchId = null;
let statusFilter = '';

const SPECIES_ICONS = {
  chicken_broiler: '🍗', chicken_layer: '🥚', cattle: '🐄', goat: '🐐', sheep: '🐑',
  pig: '🐖', rabbit: '🐇', fish: '🐟', turkey: '🦃', duck: '🦆', bee: '🐝', other: '🐾',
};

async function api(path, { method = 'GET', body } = {}) {
  const headers = { Authorization: 'Bearer ' + token };
  if (body) headers['Content-Type'] = 'application/json';
  const res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  let data = null; try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw new Error((data && (data.error?.message || data.detail)) || `Request failed (${res.status})`);
  return data;
}

function money(n) { return '$' + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function fmtDate(iso) { if (!iso) return '—'; return new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }); }

// ── Rendering ──────────────────────────────────────────────────────────

function renderBatches() {
  const grid = document.getElementById('batchGrid');
  const empty = document.getElementById('emptyState');
  const filtered = statusFilter ? allBatches.filter(b => b.status === statusFilter) : allBatches;

  if (allBatches.length === 0) {
    grid.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  if (filtered.length === 0) {
    grid.innerHTML = `<p class="panel-empty">No ${statusFilter} batches.</p>`;
    return;
  }

  grid.innerHTML = filtered.map(b => {
    const pct = b.quantity_initial ? Math.round((b.quantity_current / b.quantity_initial) * 100) : 0;
    return `
      <div class="batch-card" data-id="${b.id}">
        <div class="batch-card-top">
          <span class="batch-species-icon">${SPECIES_ICONS[b.species] || '🐾'}</span>
          <span class="status-badge status-badge--${b.status}">${b.status}</span>
        </div>
        <div class="batch-name">${b.batch_name}</div>
        <div class="batch-species">${b.species.replace('_', ' ')}${b.breed ? ' · ' + b.breed : ''}</div>
        <div class="batch-qty-row"><span>Current stock</span><strong>${b.quantity_current} / ${b.quantity_initial}</strong></div>
        <div class="batch-qty-track"><div class="batch-qty-fill" style="width:${pct}%"></div></div>
        <div class="batch-meta">Purchased ${fmtDate(b.purchase_date)}</div>
      </div>
    `;
  }).join('');

  grid.querySelectorAll('.batch-card').forEach(card => {
    card.addEventListener('click', () => openDetail(card.dataset.id));
  });
}

async function loadBatches() {
  allBatches = await api(`/farms/${farmId}/animals/batches`);
  renderBatches();
}

// ── Status filter tabs ───────────────────────────────────────────────────

document.getElementById('statusTabs').addEventListener('click', (e) => {
  const tab = e.target.closest('.status-tab');
  if (!tab) return;
  document.querySelectorAll('.status-tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  statusFilter = tab.dataset.filter;
  renderBatches();
});

// ── Create batch modal ───────────────────────────────────────────────────

function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

document.querySelectorAll('[data-close]').forEach(el => {
  el.addEventListener('click', () => closeModal(el.dataset.close));
});
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeModal(overlay.id); });
});

document.getElementById('newBatchBtn').addEventListener('click', () => openModal('createModal'));
document.getElementById('emptyNewBatchBtn').addEventListener('click', () => openModal('createModal'));

document.getElementById('createForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('createAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('createSubmitBtn');
  btn.disabled = true; btn.textContent = 'Creating...';

  const payload = {
    batch_name: document.getElementById('batchName').value.trim(),
    species: document.getElementById('species').value,
    breed: document.getElementById('breed').value.trim() || null,
    quantity_initial: Number(document.getElementById('quantityInitial').value),
    purchase_date: document.getElementById('purchaseDate').value || null,
    purchase_price_total: document.getElementById('purchasePriceTotal').value ? Number(document.getElementById('purchasePriceTotal').value) : null,
    supplier: document.getElementById('supplier').value.trim() || null,
    average_weight_kg: document.getElementById('averageWeightKg').value ? Number(document.getElementById('averageWeightKg').value) : null,
    expected_selling_date: document.getElementById('expectedSellingDate').value || null,
    notes: document.getElementById('notes').value.trim() || null,
  };

  try {
    await api(`/farms/${farmId}/animals/batches`, { method: 'POST', body: payload });
    closeModal('createModal');
    document.getElementById('createForm').reset();
    await loadBatches();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = 'Create batch';
  }
});

// ── Batch detail modal (mortality + medication tabs) ─────────────────────

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('tab' + tab.dataset.tab.charAt(0).toUpperCase() + tab.dataset.tab.slice(1)).classList.add('active');
  });
});

async function openDetail(batchId) {
  activeBatchId = batchId;
  const batch = allBatches.find(b => b.id === batchId);
  if (!batch) return;

  document.getElementById('detailBatchName').textContent = batch.batch_name;
  document.getElementById('detailOverview').innerHTML = `
    ${SPECIES_ICONS[batch.species] || '🐾'} ${batch.species.replace('_', ' ')}${batch.breed ? ' · ' + batch.breed : ''}
    &nbsp;·&nbsp; <strong style="color:var(--text)">${batch.quantity_current}</strong> / ${batch.quantity_initial} remaining
    &nbsp;·&nbsp; <span class="status-badge status-badge--${batch.status}">${batch.status}</span>
  `;

  document.getElementById('mDate').value = new Date().toISOString().slice(0, 10);
  document.getElementById('mortalityAlert').classList.remove('show');
  document.getElementById('medicationAlert').classList.remove('show');

  openModal('detailModal');
  await Promise.all([loadMortality(batchId), loadMedication(batchId)]);
}

async function loadMortality(batchId) {
  const records = await api(`/farms/${farmId}/animals/batches/${batchId}/mortality`);
  const list = document.getElementById('mortalityList');
  list.innerHTML = records.length === 0
    ? '<p class="panel-empty">No deaths recorded.</p>'
    : records.map(r => `
        <div class="record-row">
          <div>
            <div class="record-row-main">${r.quantity} lost</div>
            ${r.cause ? `<div class="record-row-sub">${r.cause}</div>` : ''}
          </div>
          <span class="record-row-date">${fmtDate(r.date)}</span>
        </div>
      `).join('');
}

async function loadMedication(batchId) {
  const records = await api(`/farms/${farmId}/animals/batches/${batchId}/medication`);
  const list = document.getElementById('medicationList');
  list.innerHTML = records.length === 0
    ? '<p class="panel-empty">No medication history.</p>'
    : records.map(r => `
        <div class="record-row">
          <div>
            <div class="record-row-main">${r.name}</div>
            <div class="record-row-sub">${r.type}${r.next_due_date ? ' · next due ' + fmtDate(r.next_due_date) : ''}</div>
          </div>
          <span class="record-row-date">${fmtDate(r.date_administered)}</span>
        </div>
      `).join('');
}

document.getElementById('mortalityForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('mortalityAlert');
  alertBox.classList.remove('show');
  try {
    await api(`/farms/${farmId}/animals/batches/${activeBatchId}/mortality`, {
      method: 'POST',
      body: {
        date: document.getElementById('mDate').value,
        quantity: Number(document.getElementById('mQuantity').value),
        cause: document.getElementById('mCause').value.trim() || null,
      },
    });
    document.getElementById('mQuantity').value = '';
    document.getElementById('mCause').value = '';
    await loadMortality(activeBatchId);
    await loadBatches(); // quantity_current changed — refresh cards behind the modal
    const refreshed = allBatches.find(b => b.id === activeBatchId);
    if (refreshed) {
      document.getElementById('detailOverview').innerHTML = document.getElementById('detailOverview').innerHTML
        .replace(/<strong style="color:var\(--text\)">\d+<\/strong>/, `<strong style="color:var(--text)">${refreshed.quantity_current}</strong>`);
    }
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  }
});

document.getElementById('medicationForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('medicationAlert');
  alertBox.classList.remove('show');
  try {
    await api(`/farms/${farmId}/animals/batches/${activeBatchId}/medication`, {
      method: 'POST',
      body: {
        type: document.getElementById('medType').value,
        name: document.getElementById('medName').value.trim(),
        date_administered: document.getElementById('medDateAdministered').value || null,
        next_due_date: document.getElementById('medNextDueDate').value || null,
      },
    });
    document.getElementById('medName').value = '';
    document.getElementById('medDateAdministered').value = '';
    document.getElementById('medNextDueDate').value = '';
    await loadMedication(activeBatchId);
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  }
});

// ── Init ───────────────────────────────────────────────────────────────

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
    const [me, farms] = await Promise.all([
      api('/auth/me'),
      api('/farms'),
    ]);

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

    await loadBatches();
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

// feed.js — auth guard + feed purchases/consumption tabs and cost summary.

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
let activeTab = 'purchases';
let batches = [];
const loadedTabs = new Set(['purchases']); // purchases is loaded eagerly during init()

async function api(path, { method = 'GET', body } = {}) {
  const headers = { Authorization: 'Bearer ' + token };
  if (body) headers['Content-Type'] = 'application/json';
  const res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  let data = null; try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw new Error((data && (data.error?.message || data.detail)) || `Request failed (${res.status})`);
  return data;
}

function money(n) { return '$' + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function kg(n) { return Number(n).toLocaleString(undefined, { maximumFractionDigits: 1 }) + ' kg'; }
function fmtDate(iso) { return new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }); }
function todayISO() { return new Date().toISOString().slice(0, 10); }

// ── Tabs ─────────────────────────────────────────────────────────────

const ADD_BTN_LABEL = { purchases: '+ New purchase', consumption: '+ Log consumption' };

document.getElementById('entityTabs').addEventListener('click', async (e) => {
  const tab = e.target.closest('.entity-tab');
  if (!tab) return;
  document.querySelectorAll('.entity-tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  activeTab = tab.dataset.tab;

  document.getElementById('purchasesTable').style.display = activeTab === 'purchases' ? 'block' : 'none';
  document.getElementById('consumptionTable').style.display = activeTab === 'consumption' ? 'block' : 'none';
  document.getElementById('addBtn').textContent = ADD_BTN_LABEL[activeTab];

  if (activeTab === 'purchases' && !loadedTabs.has('purchases')) { await loadPurchases(); loadedTabs.add('purchases'); }
  if (activeTab === 'consumption' && !loadedTabs.has('consumption')) { await loadConsumption(); loadedTabs.add('consumption'); }
});

document.getElementById('addBtn').addEventListener('click', () => {
  if (activeTab === 'purchases') { document.getElementById('pDate').value = todayISO(); openModal('purchaseModal'); }
  if (activeTab === 'consumption') { document.getElementById('cDate').value = todayISO(); openModal('consumptionModal'); }
});

function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }
document.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(el.dataset.close)));
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeModal(overlay.id); });
});

// ── Rendering ────────────────────────────────────────────────────────

function batchName(id) {
  const b = batches.find(x => x.id === id);
  return b ? b.batch_name : '—';
}

async function loadPurchases() {
  const rows = await api(`/farms/${farmId}/feed/purchases`);
  const el = document.getElementById('purchasesTable');
  if (rows.length === 0) { el.innerHTML = '<p class="panel-empty">No feed purchases logged yet.</p>'; return; }
  el.innerHTML = `
    <table class="fin-table">
      <thead><tr><th>Date</th><th>Feed type</th><th>Supplier</th><th style="text-align:right">Qty</th><th style="text-align:right">Unit cost</th><th style="text-align:right">Total</th></tr></thead>
      <tbody>
        ${rows.map(r => `
          <tr>
            <td>${fmtDate(r.purchase_date)}</td>
            <td>${r.feed_type}</td>
            <td>${r.supplier || '—'}</td>
            <td class="amt">${kg(r.quantity_kg)}</td>
            <td class="amt">${money(r.unit_cost)}</td>
            <td class="amt">${money(r.total_cost)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

async function loadConsumption() {
  const rows = await api(`/farms/${farmId}/feed/consumption`);
  const el = document.getElementById('consumptionTable');
  if (rows.length === 0) { el.innerHTML = '<p class="panel-empty">No feed consumption logged yet.</p>'; return; }
  el.innerHTML = `
    <table class="fin-table">
      <thead><tr><th>Date</th><th>Feed type</th><th>Batch</th><th style="text-align:right">Qty</th></tr></thead>
      <tbody>
        ${rows.map(r => `
          <tr>
            <td>${fmtDate(r.date)}</td>
            <td>${r.feed_type}</td>
            <td>${r.batch_id ? batchName(r.batch_id) : '—'}</td>
            <td class="amt">${kg(r.quantity_kg)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

async function loadSummary() {
  const summary = await api(`/farms/${farmId}/feed/cost-summary`);
  document.getElementById('statPurchased').textContent = kg(summary.total_purchased_kg);
  document.getElementById('statConsumed').textContent = kg(summary.total_consumed_kg);
  document.getElementById('statRemaining').textContent = kg(summary.total_purchased_kg - summary.total_consumed_kg);
  document.getElementById('statAvgCost').textContent = money(summary.average_cost_per_kg);
}

// ── Create forms ─────────────────────────────────────────────────────

document.getElementById('purchaseForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('purchaseAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('purchaseSubmitBtn');
  btn.disabled = true; btn.textContent = 'Saving...';
  try {
    await api(`/farms/${farmId}/feed/purchases`, {
      method: 'POST',
      body: {
        feed_type: document.getElementById('pFeedType').value.trim(),
        quantity_kg: Number(document.getElementById('pQuantity').value),
        unit_cost: Number(document.getElementById('pUnitCost').value),
        purchase_date: document.getElementById('pDate').value,
        supplier: document.getElementById('pSupplier').value.trim() || null,
        notes: document.getElementById('pNotes').value.trim() || null,
      },
    });
    closeModal('purchaseModal');
    document.getElementById('purchaseForm').reset();
    await loadPurchases(); await loadSummary();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = 'Log purchase';
  }
});

document.getElementById('consumptionForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('consumptionAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('consumptionSubmitBtn');
  btn.disabled = true; btn.textContent = 'Saving...';
  try {
    await api(`/farms/${farmId}/feed/consumption`, {
      method: 'POST',
      body: {
        batch_id: document.getElementById('cBatch').value || null,
        feed_type: document.getElementById('cFeedType').value.trim(),
        quantity_kg: Number(document.getElementById('cQuantity').value),
        date: document.getElementById('cDate').value,
        notes: document.getElementById('cNotes').value.trim() || null,
      },
    });
    closeModal('consumptionModal');
    document.getElementById('consumptionForm').reset();
    await loadConsumption(); await loadSummary();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = 'Log consumption';
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

    batches = await api(`/farms/${farmId}/animals/batches`);
    const batchSelect = document.getElementById('cBatch');
    batches.forEach(b => {
      const opt = document.createElement('option');
      opt.value = b.id; opt.textContent = b.batch_name;
      batchSelect.appendChild(opt);
    });

    await loadPurchases();
    await loadSummary();
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

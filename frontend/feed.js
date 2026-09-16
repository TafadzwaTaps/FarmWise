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
  if (!res.ok) {
    const err = new Error((data && (data.error?.message || data.detail)) || `Request failed (${res.status})`);
    err.status = res.status; // lets callers distinguish 401 (session invalid) from 403/404/5xx (AUDIT.md — every page's init() used to treat ANY error the same as an expired session and force-logout, including a plain 403 permission error)
    throw err;
  }
  return data;
}

// Farm-wide currency setting (AUDIT.md — money() previously hardcoded '$'
// regardless of what a farmer set in Settings; every currency figure on
// this page showed the wrong symbol for any non-USD farm). Set in init()
// once the active farm is known; falls back to a plain code prefix (e.g.
// "ZWG 12.50") for any currency not in the small symbol map below, rather
// than guessing a symbol.
const CURRENCY_SYMBOLS = { USD: '$', ZAR: 'R', ZWL: 'Z$', ZWG: 'ZiG ', ZMW: 'ZK ', KES: 'KSh ', NGN: '₦', GHS: '₵', UGX: 'USh ', TZS: 'TSh ', EUR: '€', GBP: '£' };
let currentCurrency = 'USD';
function money(n) {
  const symbol = CURRENCY_SYMBOLS[currentCurrency] || (currentCurrency + ' ');
  return symbol + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
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
  if (activeTab === 'purchases') {
    editingPurchaseId = null;
    document.getElementById('purchaseForm').reset();
    document.getElementById('purchaseModalTitle').textContent = 'New feed purchase';
    document.getElementById('purchaseSubmitBtn').textContent = 'Log purchase';
    document.getElementById('pDate').value = todayISO();
    openModal('purchaseModal');
  }
  if (activeTab === 'consumption') {
    editingConsumptionId = null;
    document.getElementById('consumptionForm').reset();
    document.getElementById('consumptionModalTitle').textContent = 'Log feed consumption';
    document.getElementById('consumptionSubmitBtn').textContent = 'Log consumption';
    document.getElementById('cBatch').disabled = false;
    document.getElementById('cDate').value = todayISO();
    openModal('consumptionModal');
  }
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

let editingPurchaseId = null;
let editingConsumptionId = null;

async function loadPurchases() {
  const rows = await api(`/farms/${farmId}/feed/purchases`);
  window._purchasesCache = rows;
  const el = document.getElementById('purchasesTable');
  if (rows.length === 0) { el.innerHTML = '<p class="panel-empty">No feed purchases logged yet.</p>'; return; }
  el.innerHTML = `
    <table class="fin-table">
      <thead><tr><th>Date</th><th>Feed type</th><th>Supplier</th><th style="text-align:right">Qty</th><th style="text-align:right">Unit cost</th><th style="text-align:right">Total</th><th></th></tr></thead>
      <tbody>
        ${rows.map(r => `
          <tr>
            <td>${fmtDate(r.purchase_date)}</td>
            <td>${r.feed_type}</td>
            <td>${r.supplier || '—'}</td>
            <td class="amt">${kg(r.quantity_kg)}</td>
            <td class="amt">${money(r.unit_cost)}</td>
            <td class="amt">${money(r.total_cost)}</td>
            <td class="row-actions">
              <button class="row-action-btn" title="Edit" data-edit-purchase="${r.id}">✏️</button>
              <button class="row-action-btn" title="Delete" data-delete-purchase="${r.id}">🗑️</button>
            </td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
  el.querySelectorAll('[data-edit-purchase]').forEach(btn => btn.addEventListener('click', () => openEditPurchase(btn.dataset.editPurchase)));
  el.querySelectorAll('[data-delete-purchase]').forEach(btn => btn.addEventListener('click', () => deletePurchase(btn.dataset.deletePurchase)));
}

function openEditPurchase(purchaseId) {
  const p = (window._purchasesCache || []).find(x => x.id === purchaseId);
  if (!p) return;
  editingPurchaseId = purchaseId;
  document.getElementById('purchaseModalTitle').textContent = 'Edit feed purchase';
  document.getElementById('purchaseSubmitBtn').textContent = 'Save changes';
  document.getElementById('pFeedType').value = p.feed_type;
  document.getElementById('pQuantity').value = p.quantity_kg;
  document.getElementById('pUnitCost').value = p.unit_cost;
  document.getElementById('pDate').value = p.purchase_date;
  document.getElementById('pSupplier').value = p.supplier || '';
  document.getElementById('pNotes').value = p.notes || '';
  document.getElementById('purchaseAlert').classList.remove('show');
  openModal('purchaseModal');
}

async function deletePurchase(purchaseId) {
  if (!confirm('Delete this feed purchase?')) return;
  try {
    await api(`/farms/${farmId}/feed/purchases/${purchaseId}`, { method: 'DELETE' });
    await loadPurchases(); await loadSummary();
  } catch (err) {
    showToast(err.message, true);
  }
}

async function loadConsumption() {
  const rows = await api(`/farms/${farmId}/feed/consumption`);
  window._consumptionCache = rows;
  const el = document.getElementById('consumptionTable');
  if (rows.length === 0) { el.innerHTML = '<p class="panel-empty">No feed consumption logged yet.</p>'; return; }
  el.innerHTML = `
    <table class="fin-table">
      <thead><tr><th>Date</th><th>Feed type</th><th>Batch</th><th style="text-align:right">Qty</th><th></th></tr></thead>
      <tbody>
        ${rows.map(r => `
          <tr>
            <td>${fmtDate(r.date)}</td>
            <td>${r.feed_type}</td>
            <td>${r.batch_id ? batchName(r.batch_id) : '—'}</td>
            <td class="amt">${kg(r.quantity_kg)}</td>
            <td class="row-actions">
              <button class="row-action-btn" title="Edit" data-edit-consumption="${r.id}">✏️</button>
              <button class="row-action-btn" title="Delete" data-delete-consumption="${r.id}">🗑️</button>
            </td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
  el.querySelectorAll('[data-edit-consumption]').forEach(btn => btn.addEventListener('click', () => openEditConsumption(btn.dataset.editConsumption)));
  el.querySelectorAll('[data-delete-consumption]').forEach(btn => btn.addEventListener('click', () => deleteConsumption(btn.dataset.deleteConsumption)));
}

function openEditConsumption(recordId) {
  const c = (window._consumptionCache || []).find(x => x.id === recordId);
  if (!c) return;
  editingConsumptionId = recordId;
  document.getElementById('consumptionModalTitle').textContent = 'Edit feed consumption';
  document.getElementById('consumptionSubmitBtn').textContent = 'Save changes';
  document.getElementById('cBatch').value = c.batch_id || '';
  document.getElementById('cBatch').disabled = true; // batch isn't editable — see routes/feed_routes.py's FeedConsumptionUpdate
  document.getElementById('cFeedType').value = c.feed_type;
  document.getElementById('cQuantity').value = c.quantity_kg;
  document.getElementById('cDate').value = c.date;
  document.getElementById('cNotes').value = c.notes || '';
  document.getElementById('consumptionAlert').classList.remove('show');
  openModal('consumptionModal');
}

async function deleteConsumption(recordId) {
  if (!confirm('Delete this feed consumption record?')) return;
  try {
    await api(`/farms/${farmId}/feed/consumption/${recordId}`, { method: 'DELETE' });
    await loadConsumption(); await loadSummary();
  } catch (err) {
    showToast(err.message, true);
  }
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
    const body = {
      feed_type: document.getElementById('pFeedType').value.trim(),
      quantity_kg: Number(document.getElementById('pQuantity').value),
      unit_cost: Number(document.getElementById('pUnitCost').value),
      purchase_date: document.getElementById('pDate').value,
      supplier: document.getElementById('pSupplier').value.trim() || null,
      notes: document.getElementById('pNotes').value.trim() || null,
    };
    if (editingPurchaseId) {
      await api(`/farms/${farmId}/feed/purchases/${editingPurchaseId}`, { method: 'PATCH', body });
    } else {
      await api(`/farms/${farmId}/feed/purchases`, { method: 'POST', body });
    }
    closeModal('purchaseModal');
    document.getElementById('purchaseForm').reset();
    await loadPurchases(); await loadSummary();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = editingPurchaseId ? 'Save changes' : 'Log purchase';
  }
});

document.getElementById('consumptionForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('consumptionAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('consumptionSubmitBtn');
  btn.disabled = true; btn.textContent = 'Saving...';
  try {
    const body = {
      feed_type: document.getElementById('cFeedType').value.trim(),
      quantity_kg: Number(document.getElementById('cQuantity').value),
      date: document.getElementById('cDate').value,
      notes: document.getElementById('cNotes').value.trim() || null,
    };
    if (editingConsumptionId) {
      await api(`/farms/${farmId}/feed/consumption/${editingConsumptionId}`, { method: 'PATCH', body });
    } else {
      body.batch_id = document.getElementById('cBatch').value || null;
      await api(`/farms/${farmId}/feed/consumption`, { method: 'POST', body });
    }
    closeModal('consumptionModal');
    document.getElementById('consumptionForm').reset();
    document.getElementById('cBatch').disabled = false;
    await loadConsumption(); await loadSummary();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = editingConsumptionId ? 'Save changes' : 'Log consumption';
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

// AUDIT.md — shown instead of a forced logout when init() fails for a
// reason other than an invalid session (see the catch block below).
function showLoadError(status) {
  const main = document.querySelector('.main');
  if (!main) return;
  const forbidden = status === 403;
  const box = document.createElement('div');
  box.className = 'content';
  box.style.cssText = 'padding:48px 24px;text-align:center;';
  box.innerHTML = `
    <div style="font-size:2rem;margin-bottom:8px">${forbidden ? '\ud83d\udd12' : '\u26a0\ufe0f'}</div>
    <h2 style="margin:0 0 8px">${forbidden ? "You don't have access to this page" : 'Something went wrong'}</h2>
    <p style="opacity:.75;max-width:420px;margin:0 auto 16px">${forbidden
      ? "Your role on this farm doesn't include access to this page. Ask a farm owner or manager if you think this is a mistake."
      : 'Please check your connection and try again.'}</p>
    <button class="btn btn--primary" onclick="location.reload()">Try again</button>
  `;
  main.appendChild(box);
}

// AUDIT.md — a background action failing (e.g. deleting a record) used to
// show a plain browser alert(), which blocks the whole page until
// dismissed and looks jarring next to the rest of the UI. A small
// auto-dismissing toast is less disruptive for something the user can
// just try again.
function showToast(message, isError) {
  let stack = document.getElementById('toastStack');
  if (!stack) {
    stack = document.createElement('div');
    stack.id = 'toastStack';
    stack.className = 'toast-stack';
    document.body.appendChild(stack);
  }
  const toast = document.createElement('div');
  toast.className = 'toast' + (isError ? ' toast--error' : '');
  toast.textContent = message;
  stack.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}

async function init() {
  token = getToken();
  if (!token) { window.location.href = '/login'; return; }

  try {
    const [me, farms] = await Promise.all([api('/auth/me'), api('/farms')]);
    document.getElementById('userGreeting').textContent = `Welcome back, ${me.full_name.split(' ')[0]}`;

    if (farms.length === 0) {
      document.getElementById('farmName').textContent = 'No farm yet';
      document.getElementById('noFarmState').style.display = 'block';
      return;
    }

    const savedFarmId = localStorage.getItem('farmwise_active_farm_id');
    const activeFarm = farms.find(f => f.id === savedFarmId) || farms[0];
    currentCurrency = activeFarm.currency || 'USD';
    farmId = activeFarm.id;
    renderFarmSwitcher(farms, activeFarm);
    document.getElementById('roleBadge').textContent = activeFarm.my_role || '';

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
    // AUDIT.md: this used to unconditionally wipe the session and bounce to
    // /login for ANY error here — including a plain 403 (e.g. a worker
    // whose role doesn't include this page) or a transient network/server
    // error, which forced a real, currently-valid session to log out for
    // no good reason. Only an actually invalid/expired token (401) should
    // do that; everything else gets a friendly in-page message instead.
    if (err.status === 401) {
      localStorage.removeItem('farmwise_token');
      localStorage.removeItem('farmwise_refresh');
      localStorage.removeItem('farmwise_user');
      sessionStorage.clear();
      window.location.href = '/login';
      return;
    }
    console.error(err);
    showLoadError(err.status);
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

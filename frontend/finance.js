// finance.js — auth guard + sales/expenses/income tabs, tables, and add forms.

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
let activeTab = 'sales';
let batches = [];
const loadedTabs = new Set(['sales']); // sales is loaded eagerly during init()

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
function fmtDate(iso) { return new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }); }
function todayISO() { return new Date().toISOString().slice(0, 10); }

// ── Tabs ─────────────────────────────────────────────────────────────

const ADD_BTN_LABEL = { sales: '+ New sale', expenses: '+ New expense', income: '+ New income' };

document.getElementById('entityTabs').addEventListener('click', async (e) => {
  const tab = e.target.closest('.entity-tab');
  if (!tab) return;
  document.querySelectorAll('.entity-tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  activeTab = tab.dataset.tab;

  document.getElementById('salesTable').style.display = activeTab === 'sales' ? 'block' : 'none';
  document.getElementById('expensesTable').style.display = activeTab === 'expenses' ? 'block' : 'none';
  document.getElementById('incomeTable').style.display = activeTab === 'income' ? 'block' : 'none';
  document.getElementById('addBtn').textContent = ADD_BTN_LABEL[activeTab];

  // Fetch this tab's data the first time it's opened — switching tabs
  // only toggled visibility before, so a tab nobody had created a record
  // in yet rendered as a silently blank panel instead of "no X logged yet".
  if (activeTab === 'sales' && !loadedTabs.has('sales')) { await loadSales(); loadedTabs.add('sales'); }
  if (activeTab === 'expenses' && !loadedTabs.has('expenses')) { await loadExpenses(); loadedTabs.add('expenses'); }
  if (activeTab === 'income' && !loadedTabs.has('income')) { await loadIncome(); loadedTabs.add('income'); }
});

document.getElementById('addBtn').addEventListener('click', () => {
  if (activeTab === 'sales') { document.getElementById('saleDate').value = todayISO(); openModal('saleModal'); }
  if (activeTab === 'expenses') { document.getElementById('expenseDate').value = todayISO(); openModal('expenseModal'); }
  if (activeTab === 'income') { document.getElementById('incomeDate').value = todayISO(); openModal('incomeModal'); }
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

async function loadSales() {
  const rows = await api(`/farms/${farmId}/sales`);
  const el = document.getElementById('salesTable');
  if (rows.length === 0) { el.innerHTML = '<p class="panel-empty">No sales logged yet.</p>'; return; }
  el.innerHTML = `
    <table class="fin-table">
      <thead><tr><th>Date</th><th>Batch</th><th>Buyer</th><th>Qty</th><th>Payment</th><th style="text-align:right">Total</th></tr></thead>
      <tbody>
        ${rows.map(r => `
          <tr>
            <td>${fmtDate(r.sale_date)}</td>
            <td>${r.batch_id ? batchName(r.batch_id) : '—'}</td>
            <td>${r.buyer_name || '—'}</td>
            <td>${r.quantity}</td>
            <td><span class="cat-pill">${r.payment_method.replace('_', ' ')}</span></td>
            <td class="amt pos">${money(r.total_amount)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

async function loadExpenses() {
  const rows = await api(`/farms/${farmId}/expenses`);
  const el = document.getElementById('expensesTable');
  if (rows.length === 0) { el.innerHTML = '<p class="panel-empty">No expenses logged yet.</p>'; return; }
  el.innerHTML = `
    <table class="fin-table">
      <thead><tr><th>Date</th><th>Category</th><th>Vendor</th><th style="text-align:right">Amount</th></tr></thead>
      <tbody>
        ${rows.map(r => `
          <tr>
            <td>${fmtDate(r.expense_date)}</td>
            <td><span class="cat-pill">${r.category.replace('_', ' ')}</span></td>
            <td>${r.vendor || '—'}</td>
            <td class="amt neg">${money(r.amount)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

async function loadIncome() {
  const rows = await api(`/farms/${farmId}/income`);
  const el = document.getElementById('incomeTable');
  if (rows.length === 0) { el.innerHTML = '<p class="panel-empty">No other income logged yet.</p>'; return; }
  el.innerHTML = `
    <table class="fin-table">
      <thead><tr><th>Date</th><th>Category</th><th>Notes</th><th style="text-align:right">Amount</th></tr></thead>
      <tbody>
        ${rows.map(r => `
          <tr>
            <td>${fmtDate(r.income_date)}</td>
            <td><span class="cat-pill">${r.category.replace('_', ' ')}</span></td>
            <td>${r.notes || '—'}</td>
            <td class="amt pos">${money(r.amount)}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

async function loadSummary() {
  const end = todayISO();
  const start = new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
  const summary = await api(`/farms/${farmId}/finance-summary?period_start=${start}&period_end=${end}`);
  document.getElementById('statIncome').textContent = money(summary.total_income);
  document.getElementById('statExpenses').textContent = money(summary.total_expenses);
  document.getElementById('statProfit').textContent = money(summary.net_profit);
  document.getElementById('profitCard').classList.toggle('stat-card--negative', summary.net_profit < 0);
  document.getElementById('profitCard').classList.toggle('stat-card--income', summary.net_profit >= 0);
}

// ── Create forms ─────────────────────────────────────────────────────

document.getElementById('saleForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('saleAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('saleSubmitBtn');
  btn.disabled = true; btn.textContent = 'Saving...';
  try {
    await api(`/farms/${farmId}/sales`, {
      method: 'POST',
      body: {
        batch_id: document.getElementById('saleBatch').value || null,
        quantity: Number(document.getElementById('saleQuantity').value),
        unit_price: Number(document.getElementById('saleUnitPrice').value),
        discount: Number(document.getElementById('saleDiscount').value || 0),
        sale_date: document.getElementById('saleDate').value,
        buyer_name: document.getElementById('saleBuyer').value.trim() || null,
        payment_method: document.getElementById('salePayment').value,
        notes: document.getElementById('saleNotes').value.trim() || null,
      },
    });
    closeModal('saleModal');
    document.getElementById('saleForm').reset();
    await loadSales(); await loadSummary();
    batches = await api(`/farms/${farmId}/animals/batches`); // quantity_current changed
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = 'Log sale';
  }
});

document.getElementById('expenseForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('expenseAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('expenseSubmitBtn');
  btn.disabled = true; btn.textContent = 'Saving...';
  try {
    await api(`/farms/${farmId}/expenses`, {
      method: 'POST',
      body: {
        category: document.getElementById('expenseCategory').value,
        amount: Number(document.getElementById('expenseAmount').value),
        expense_date: document.getElementById('expenseDate').value,
        vendor: document.getElementById('expenseVendor').value.trim() || null,
        batch_id: document.getElementById('expenseBatch').value || null,
        notes: document.getElementById('expenseNotes').value.trim() || null,
      },
    });
    closeModal('expenseModal');
    document.getElementById('expenseForm').reset();
    await loadExpenses(); await loadSummary();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = 'Log expense';
  }
});

document.getElementById('incomeForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('incomeAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('incomeSubmitBtn');
  btn.disabled = true; btn.textContent = 'Saving...';
  try {
    await api(`/farms/${farmId}/income`, {
      method: 'POST',
      body: {
        category: document.getElementById('incomeCategory').value,
        amount: Number(document.getElementById('incomeAmount').value),
        income_date: document.getElementById('incomeDate').value,
        notes: document.getElementById('incomeNotes').value.trim() || null,
      },
    });
    closeModal('incomeModal');
    document.getElementById('incomeForm').reset();
    await loadIncome(); await loadSummary();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = 'Log income';
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
    currentCurrency = activeFarm.currency || 'USD';
    farmId = activeFarm.id;
    renderFarmSwitcher(farms, activeFarm);

    batches = await api(`/farms/${farmId}/animals/batches`);
    const batchSelect = document.getElementById('saleBatch');
    const expenseBatchSelect = document.getElementById('expenseBatch');
    batches.forEach(b => {
      const opt = document.createElement('option');
      opt.value = b.id; opt.textContent = b.batch_name;
      batchSelect.appendChild(opt);
      expenseBatchSelect.appendChild(opt.cloneNode(true));
    });

    await loadSales();
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

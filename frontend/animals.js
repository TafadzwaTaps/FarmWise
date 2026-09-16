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
let currentRole = null;
const FINANCE_VIEW_ROLES = ['farmer', 'farm_manager', 'accountant']; // matches backend's FINANCE_VIEW_ROLES / _FINANCE_VIEW_ROLES — batch profit is financial data
const MANAGE_ROLES = ['farmer', 'farm_manager']; // matches backend's _MANAGE_ROLES — batch edit/delete is manager-only
function money(n) {
  const symbol = CURRENCY_SYMBOLS[currentCurrency] || (currentCurrency + ' ');
  return symbol + Number(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtDate(iso) { if (!iso) return '—'; return new Date(iso + 'T00:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }); }
function escapeHtml(s) { const div = document.createElement('div'); div.textContent = s ?? ''; return div.innerHTML; }

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

document.getElementById('newBatchBtn').addEventListener('click', () => openCreateBatchModal());
document.getElementById('emptyNewBatchBtn').addEventListener('click', () => openCreateBatchModal());

function openCreateBatchModal() {
  document.getElementById('createForm').reset();
  document.getElementById('createAlert').classList.remove('show');
  openModal('createModal');
}

// Editing/deleting a batch — and adjusting its live stock — now all live
// inside the detail modal's "Edit" tab (matching the mobile app's single
// edit form exactly), populated once when the modal opens in openDetail()
// below rather than a separate modal reused in two modes.

document.getElementById('editBatchForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('editBatchAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('editBatchSubmitBtn');
  btn.disabled = true; btn.textContent = 'Saving...';
  try {
    await api(`/farms/${farmId}/animals/batches/${activeBatchId}`, {
      method: 'PATCH',
      body: {
        batch_name: document.getElementById('editBatchName').value.trim(),
        species: document.getElementById('editSpecies').value,
        breed: document.getElementById('editBreed').value.trim() || null,
        quantity_current: Number(document.getElementById('editQuantityCurrent').value),
        supplier: document.getElementById('editSupplier').value.trim() || null,
        status: document.getElementById('editStatus').value,
        notes: document.getElementById('editNotes').value.trim() || null,
      },
    });
    allBatches = await api(`/farms/${farmId}/animals/batches`);
    renderBatches();
    const batch = allBatches.find(b => b.id === activeBatchId);
    if (batch) {
      document.getElementById('detailBatchName').textContent = batch.batch_name;
      renderDetailOverview(batch);
      populateEditTab(batch);
    }
    showToast('Batch updated');
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = 'Save changes';
  }
});

document.getElementById('deleteBatchBtn').addEventListener('click', async () => {
  if (!confirm('Delete this batch? Its sales, mortality, and medication history is kept for your records, but the batch itself will no longer appear in your lists.')) return;
  try {
    await api(`/farms/${farmId}/animals/batches/${activeBatchId}`, { method: 'DELETE' });
    closeModal('detailModal');
    await loadBatches();
  } catch (err) {
    showToast(err.message, true);
  }
});

document.getElementById('createForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('createAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('createSubmitBtn');
  btn.disabled = true; btn.textContent = 'Creating...';

  try {
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

function renderDetailOverview(batch) {
  document.getElementById('detailOverview').innerHTML = `
    ${SPECIES_ICONS[batch.species] || '🐾'} ${batch.species.replace('_', ' ')}${batch.breed ? ' · ' + batch.breed : ''}
    &nbsp;·&nbsp; <strong style="color:var(--text)">${batch.quantity_current}</strong> / ${batch.quantity_initial} remaining
    &nbsp;·&nbsp; <span class="status-badge status-badge--${batch.status}">${batch.status}</span>
  `;
}

function populateEditTab(batch) {
  document.getElementById('editBatchName').value = batch.batch_name;
  document.getElementById('editSpecies').value = batch.species;
  document.getElementById('editBreed').value = batch.breed || '';
  document.getElementById('editQuantityCurrent').value = batch.quantity_current;
  document.getElementById('editQuantityCurrentLabel').textContent = `Current count (was ${batch.quantity_current} of ${batch.quantity_initial} initial)`;
  document.getElementById('editSupplier').value = batch.supplier || '';
  document.getElementById('editStatus').value = batch.status;
  document.getElementById('editNotes').value = batch.notes || '';
  document.getElementById('editBatchAlert').classList.remove('show');
}

async function openDetail(batchId) {
  activeBatchId = batchId;
  const batch = allBatches.find(b => b.id === batchId);
  if (!batch) return;

  document.getElementById('detailBatchName').textContent = batch.batch_name;
  renderDetailOverview(batch);

  document.getElementById('mDate').value = new Date().toISOString().slice(0, 10);
  document.getElementById('mortalityAlert').classList.remove('show');
  document.getElementById('medicationAlert').classList.remove('show');
  resetMortalityForm();
  resetMedicationForm();

  const editTab = document.getElementById('editTab');
  const canManage = MANAGE_ROLES.includes(currentRole);
  editTab.style.display = canManage ? '' : 'none';
  if (canManage) populateEditTab(batch);

  openModal('detailModal');

  const profitTab = document.getElementById('profitTab');
  const showProfit = FINANCE_VIEW_ROLES.includes(currentRole);
  profitTab.style.display = showProfit ? '' : 'none';
  // If a non-finance/non-manager role had the Profit/Edit tab open from a
  // previous batch (shouldn't happen since both are hidden for them, but
  // defensive), fall back to the Mortality tab rather than leaving an
  // empty pane active.
  if ((!showProfit && profitTab.classList.contains('active')) || (!canManage && editTab.classList.contains('active'))) {
    document.querySelector('.tab[data-tab="mortality"]').click();
  }

  const loads = [loadMortality(batchId), loadMedication(batchId)];
  if (showProfit) loads.push(loadProfit(batchId));
  await Promise.all(loads);
}

async function loadProfit(batchId) {
  const el = document.getElementById('profitContent');
  el.innerHTML = '<p class="panel-empty">Loading…</p>';
  try {
    const p = await api(`/farms/${farmId}/animals/batches/${batchId}/profit`);
    const note = (p.feed_cost_incomplete || p.medication_records_missing_cost > 0)
      ? `<p class="panel-empty" style="margin-top:8px">
           ${p.feed_cost_incomplete ? 'Some feed consumed by this batch was never purchased/priced, so feed cost may be understated. ' : ''}
           ${p.medication_records_missing_cost > 0 ? `${p.medication_records_missing_cost} medication record(s) have no cost logged, so medication cost may be understated.` : ''}
         </p>`
      : '';
    el.innerHTML = `
      <div class="record-list">
        <div class="record-row"><div class="record-row-main">Revenue</div><span>${money(p.total_sales_revenue)}</span></div>
        <div class="record-row"><div class="record-row-main">Total cost (purchase + feed + medication + allocated expenses)</div><span>${money(p.total_accumulated_cost)}</span></div>
        <div class="record-row"><div class="record-row-main">Cost of goods sold (${p.quantity_sold} sold)</div><span>${money(p.cost_of_goods_sold)}</span></div>
        <div class="record-row"><div class="record-row-main">Remaining inventory value (${p.quantity_current} on hand)</div><span>${money(p.remaining_inventory_value)}</span></div>
        ${p.quantity_mortality > 0 ? `<div class="record-row"><div class="record-row-main">Mortality loss (${p.quantity_mortality} lost)</div><span>${money(p.mortality_loss)}</span></div>` : ''}
        <div class="record-row"><div class="record-row-main" style="font-weight:700">Net profit</div><span style="font-weight:700">${money(p.net_profit)}</span></div>
      </div>
      ${note}
    `;
  } catch (err) {
    el.innerHTML = '<p class="panel-empty">Could not load profit data for this batch.</p>';
  }
}

let editingMortalityId = null;
let editingMedicationId = null;
let mortalityCache = [];
let medicationCache = [];

function resetMortalityForm() {
  editingMortalityId = null;
  document.getElementById('mortalityFormTitle').textContent = 'Record deaths';
  document.getElementById('mortalitySubmitBtn').textContent = 'Record';
  document.getElementById('mortalityCancelEditBtn').style.display = 'none';
  document.getElementById('mQuantity').closest('.field').style.display = '';
  document.getElementById('mDate').value = new Date().toISOString().slice(0, 10);
  document.getElementById('mQuantity').value = '';
  document.getElementById('mCause').value = '';
}

function resetMedicationForm() {
  editingMedicationId = null;
  document.getElementById('medicationFormTitle').textContent = 'Log vaccine / medicine / treatment';
  document.getElementById('medicationSubmitBtn').textContent = 'Log';
  document.getElementById('medicationCancelEditBtn').style.display = 'none';
  document.getElementById('medType').disabled = false;
  document.getElementById('medType').value = 'vaccine';
  document.getElementById('medName').value = '';
  document.getElementById('medDateAdministered').value = '';
  document.getElementById('medNextDueDate').value = '';
  document.getElementById('medDosage').value = '';
  document.getElementById('medAdministeredBy').value = '';
  document.getElementById('medCost').value = '';
  document.getElementById('medNotes').value = '';
}

async function loadMortality(batchId) {
  mortalityCache = await api(`/farms/${farmId}/animals/batches/${batchId}/mortality`);
  const list = document.getElementById('mortalityList');
  list.innerHTML = mortalityCache.length === 0
    ? '<p class="panel-empty">No deaths recorded.</p>'
    : mortalityCache.map(r => `
        <div class="record-row">
          <div>
            <div class="record-row-main">${r.quantity} lost${r.cause ? ' — ' + escapeHtml(r.cause) : ''}</div>
            <span class="record-row-date">${fmtDate(r.date)}</span>
          </div>
          <div class="record-row-actions">
            <button class="record-row-link record-row-link--edit" data-edit-mortality="${r.id}">Edit</button>
            <button class="record-row-link record-row-link--delete" data-delete-mortality="${r.id}">Delete</button>
          </div>
        </div>
      `).join('');
  list.querySelectorAll('[data-edit-mortality]').forEach(btn => {
    btn.addEventListener('click', () => openEditMortality(btn.dataset.editMortality));
  });
  list.querySelectorAll('[data-delete-mortality]').forEach(btn => {
    btn.addEventListener('click', () => deleteMortality(btn.dataset.deleteMortality));
  });
}

function openEditMortality(recordId) {
  const r = mortalityCache.find(x => x.id === recordId);
  if (!r) return;
  editingMortalityId = recordId;
  document.getElementById('mortalityFormTitle').textContent = 'Edit death record';
  document.getElementById('mortalitySubmitBtn').textContent = 'Save changes';
  document.getElementById('mortalityCancelEditBtn').style.display = '';
  // quantity isn't editable here — see crud.update_mortality_record's
  // docstring (backend) — delete and re-record if the number was wrong.
  document.getElementById('mQuantity').closest('.field').style.display = 'none';
  document.getElementById('mDate').value = r.date;
  document.getElementById('mCause').value = r.cause || '';
  document.getElementById('mortalityAlert').classList.remove('show');
}

document.getElementById('mortalityCancelEditBtn').addEventListener('click', () => resetMortalityForm());

async function deleteMortality(recordId) {
  if (!confirm('Delete this mortality record? The animals will be added back to the batch\'s live count.')) return;
  try {
    await api(`/farms/${farmId}/animals/batches/${activeBatchId}/mortality/${recordId}`, { method: 'DELETE' });
    if (editingMortalityId === recordId) resetMortalityForm();
    await loadMortality(activeBatchId);
    allBatches = await api(`/farms/${farmId}/animals/batches`); // quantity_current changed
    renderBatches();
    const refreshed = allBatches.find(b => b.id === activeBatchId);
    if (refreshed) renderDetailOverview(refreshed);
  } catch (err) {
    showToast(err.message, true);
  }
}

async function loadMedication(batchId) {
  medicationCache = await api(`/farms/${farmId}/animals/batches/${batchId}/medication`);
  const list = document.getElementById('medicationList');
  list.innerHTML = medicationCache.length === 0
    ? '<p class="panel-empty">No medication history.</p>'
    : medicationCache.map(r => `
        <div class="record-row">
          <div>
            <div class="record-row-main">${escapeHtml(r.name)}${r.cost != null ? ' · ' + money(r.cost) : ''}</div>
            <div class="record-row-sub">${r.type}${r.next_due_date ? ' · next due ' + fmtDate(r.next_due_date) : ''}</div>
            <span class="record-row-date">${fmtDate(r.date_administered)}</span>
          </div>
          <div class="record-row-actions">
            <button class="record-row-link record-row-link--edit" data-edit-medication="${r.id}">Edit</button>
            <button class="record-row-link record-row-link--delete" data-delete-medication="${r.id}">Delete</button>
          </div>
        </div>
      `).join('');
  list.querySelectorAll('[data-edit-medication]').forEach(btn => {
    btn.addEventListener('click', () => openEditMedication(btn.dataset.editMedication));
  });
  list.querySelectorAll('[data-delete-medication]').forEach(btn => {
    btn.addEventListener('click', () => deleteMedication(btn.dataset.deleteMedication));
  });
}

function openEditMedication(recordId) {
  const r = medicationCache.find(x => x.id === recordId);
  if (!r) return;
  editingMedicationId = recordId;
  document.getElementById('medicationFormTitle').textContent = 'Edit medication record';
  document.getElementById('medicationSubmitBtn').textContent = 'Save changes';
  document.getElementById('medicationCancelEditBtn').style.display = '';
  document.getElementById('medType').value = r.type;
  document.getElementById('medName').value = r.name;
  document.getElementById('medDateAdministered').value = r.date_administered || '';
  document.getElementById('medNextDueDate').value = r.next_due_date || '';
  document.getElementById('medDosage').value = r.dosage || '';
  document.getElementById('medAdministeredBy').value = r.administered_by || '';
  document.getElementById('medCost').value = r.cost ?? '';
  document.getElementById('medNotes').value = r.notes || '';
  document.getElementById('medicationAlert').classList.remove('show');
}

document.getElementById('medicationCancelEditBtn').addEventListener('click', () => resetMedicationForm());

async function deleteMedication(recordId) {
  if (!confirm('Delete this medication record?')) return;
  try {
    await api(`/farms/${farmId}/animals/batches/${activeBatchId}/medication/${recordId}`, { method: 'DELETE' });
    if (editingMedicationId === recordId) resetMedicationForm();
    await loadMedication(activeBatchId);
  } catch (err) {
    showToast(err.message, true);
  }
}

document.getElementById('mortalityForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('mortalityAlert');
  alertBox.classList.remove('show');
  try {
    if (editingMortalityId) {
      await api(`/farms/${farmId}/animals/batches/${activeBatchId}/mortality/${editingMortalityId}`, {
        method: 'PATCH',
        body: {
          date: document.getElementById('mDate').value,
          cause: document.getElementById('mCause').value.trim() || null,
        },
      });
      resetMortalityForm();
      await loadMortality(activeBatchId);
      return;
    }
    await api(`/farms/${farmId}/animals/batches/${activeBatchId}/mortality`, {
      method: 'POST',
      body: {
        date: document.getElementById('mDate').value,
        quantity: Number(document.getElementById('mQuantity').value),
        cause: document.getElementById('mCause').value.trim() || null,
      },
    });
    resetMortalityForm();
    await loadMortality(activeBatchId);
    await loadBatches(); // quantity_current changed — refresh cards behind the modal
    const refreshed = allBatches.find(b => b.id === activeBatchId);
    if (refreshed) renderDetailOverview(refreshed);
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  }
});

document.getElementById('medicationForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('medicationAlert');
  alertBox.classList.remove('show');
  const body = {
    type: document.getElementById('medType').value,
    name: document.getElementById('medName').value.trim(),
    date_administered: document.getElementById('medDateAdministered').value || null,
    next_due_date: document.getElementById('medNextDueDate').value || null,
    dosage: document.getElementById('medDosage').value.trim() || null,
    administered_by: document.getElementById('medAdministeredBy').value.trim() || null,
    cost: document.getElementById('medCost').value ? Number(document.getElementById('medCost').value) : null,
    notes: document.getElementById('medNotes').value.trim() || null,
  };
  try {
    if (editingMedicationId) {
      await api(`/farms/${farmId}/animals/batches/${activeBatchId}/medication/${editingMedicationId}`, { method: 'PATCH', body });
    } else {
      await api(`/farms/${farmId}/animals/batches/${activeBatchId}/medication`, { method: 'POST', body });
    }
    resetMedicationForm();
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
    const [me, farms] = await Promise.all([
      api('/auth/me'),
      api('/farms'),
    ]);

    document.getElementById('userGreeting').textContent = `Welcome back, ${me.full_name.split(' ')[0]}`;

    if (farms.length === 0) {
      document.getElementById('farmName').textContent = 'No farm yet';
      document.getElementById('noFarmState').style.display = 'block';
      return;
    }

    const savedFarmId = localStorage.getItem('farmwise_active_farm_id');
    const activeFarm = farms.find(f => f.id === savedFarmId) || farms[0];
    currentCurrency = activeFarm.currency || 'USD';
    currentRole = activeFarm.my_role || null;
    farmId = activeFarm.id;
    renderFarmSwitcher(farms, activeFarm);
    document.getElementById('roleBadge').textContent = activeFarm.my_role || '';

    await loadBatches();
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

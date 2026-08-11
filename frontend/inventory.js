// inventory.js — auth guard + inventory item grid, create/adjust modals.

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
let allItems = [];
let activeFilter = '';
let activeItemId = null;

async function api(path, { method = 'GET', body } = {}) {
  const headers = { Authorization: 'Bearer ' + token };
  if (body) headers['Content-Type'] = 'application/json';
  const res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  let data = null; try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw new Error((data && (data.error?.message || data.detail)) || `Request failed (${res.status})`);
  return data;
}

function num(n) { return Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 }); }

// ── Rendering ────────────────────────────────────────────────────────

function renderItems() {
  const grid = document.getElementById('invGrid');
  const empty = document.getElementById('emptyState');
  const filtered = activeFilter === 'low' ? allItems.filter(i => i.is_low_stock) : allItems;

  if (allItems.length === 0) {
    grid.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  if (filtered.length === 0) {
    grid.innerHTML = '<p class="panel-empty">Nothing low on stock right now.</p>';
    return;
  }

  grid.innerHTML = filtered.map(i => `
    <div class="inv-card ${i.is_low_stock ? 'inv-card--low' : ''}" data-id="${i.id}">
      <div class="inv-card-top">
        <span class="inv-cat">${i.category}</span>
        ${i.is_low_stock ? '<span class="low-stock-pill">Low stock</span>' : ''}
      </div>
      <div class="inv-name">${i.name}</div>
      <div class="inv-qty-row">
        <span class="inv-qty">${num(i.quantity_on_hand)}</span>
        <span class="inv-unit">${i.unit}</span>
      </div>
      <div class="inv-threshold">Low-stock threshold: ${num(i.low_stock_threshold)} ${i.unit}</div>
      <div class="inv-actions">
        <button class="btn btn--outline btn--sm edit-btn" data-id="${i.id}">Edit</button>
        <button class="btn btn--outline btn--sm adjust-btn" data-id="${i.id}">Adjust stock</button>
        <button class="btn btn--outline btn--sm delete-btn" data-id="${i.id}">Delete</button>
      </div>
    </div>
  `).join('');

  grid.querySelectorAll('.edit-btn').forEach(btn => {
    btn.addEventListener('click', () => openEdit(btn.dataset.id));
  });
  grid.querySelectorAll('.adjust-btn').forEach(btn => {
    btn.addEventListener('click', () => openAdjust(btn.dataset.id));
  });
  grid.querySelectorAll('.delete-btn').forEach(btn => {
    btn.addEventListener('click', () => deleteItem(btn.dataset.id));
  });
}

async function loadItems() {
  allItems = await api(`/farms/${farmId}/inventory`);
  renderItems();
}

document.getElementById('filterTabs').addEventListener('click', (e) => {
  const tab = e.target.closest('.entity-tab');
  if (!tab) return;
  document.querySelectorAll('#filterTabs .entity-tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  activeFilter = tab.dataset.filter;
  renderItems();
});

// ── Modals ───────────────────────────────────────────────────────────

function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }
document.querySelectorAll('[data-close]').forEach(el => el.addEventListener('click', () => closeModal(el.dataset.close)));
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', (e) => { if (e.target === overlay) closeModal(overlay.id); });
});

document.getElementById('newItemBtn').addEventListener('click', () => openModal('createModal'));
document.getElementById('emptyNewItemBtn').addEventListener('click', () => openModal('createModal'));

document.getElementById('createForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('createAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('createSubmitBtn');
  btn.disabled = true; btn.textContent = 'Creating...';
  try {
    await api(`/farms/${farmId}/inventory`, {
      method: 'POST',
      body: {
        category: document.getElementById('itemCategory').value,
        name: document.getElementById('itemName').value.trim(),
        unit: document.getElementById('itemUnit').value.trim(),
        quantity_on_hand: Number(document.getElementById('itemQuantity').value || 0),
        low_stock_threshold: Number(document.getElementById('itemThreshold').value || 0),
        unit_cost: document.getElementById('itemUnitCost').value ? Number(document.getElementById('itemUnitCost').value) : null,
      },
    });
    closeModal('createModal');
    document.getElementById('createForm').reset();
    await loadItems();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = 'Create item';
  }
});

function openEdit(itemId) {
  activeItemId = itemId;
  const item = allItems.find(i => i.id === itemId);
  document.getElementById('editItemName').value = item.name;
  document.getElementById('editItemUnit').value = item.unit;
  document.getElementById('editItemThreshold').value = item.low_stock_threshold;
  document.getElementById('editItemUnitCost').value = item.unit_cost ?? '';
  document.getElementById('editAlert').classList.remove('show');
  openModal('editModal');
}

document.getElementById('editForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('editAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('editSubmitBtn');
  btn.disabled = true; btn.textContent = 'Saving...';
  try {
    await api(`/farms/${farmId}/inventory/${activeItemId}`, {
      method: 'PATCH',
      body: {
        name: document.getElementById('editItemName').value.trim(),
        unit: document.getElementById('editItemUnit').value.trim(),
        low_stock_threshold: Number(document.getElementById('editItemThreshold').value || 0),
        unit_cost: document.getElementById('editItemUnitCost').value ? Number(document.getElementById('editItemUnitCost').value) : null,
      },
    });
    closeModal('editModal');
    await loadItems();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = 'Save changes';
  }
});

function openAdjust(itemId) {
  activeItemId = itemId;
  const item = allItems.find(i => i.id === itemId);
  document.getElementById('adjustItemName').textContent = `Adjust — ${item.name}`;
  document.getElementById('adjustDelta').value = '';
  document.getElementById('adjustAlert').classList.remove('show');
  openModal('adjustModal');
}

document.getElementById('adjustForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('adjustAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('adjustSubmitBtn');
  btn.disabled = true; btn.textContent = 'Applying...';
  try {
    await api(`/farms/${farmId}/inventory/${activeItemId}/adjust`, {
      method: 'POST',
      body: { delta: Number(document.getElementById('adjustDelta').value) },
    });
    closeModal('adjustModal');
    await loadItems();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = 'Apply';
  }
});

async function deleteItem(itemId) {
  const item = allItems.find(i => i.id === itemId);
  if (!confirm(`Delete "${item.name}"? This can't be undone.`)) return;
  try {
    await api(`/farms/${farmId}/inventory/${itemId}`, { method: 'DELETE' });
    await loadItems();
  } catch (err) {
    alert(err.message);
  }
}

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

    await loadItems();
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

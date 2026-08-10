// settings.js — auth guard + farm info edit, members, create farm, delete farm.

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
  sidebarEl.querySelectorAll('.side-link').forEach(link => link.addEventListener('click', closeSidebar));
})();

let token = null;
let farmId = null;

async function api(path, { method = 'GET', body } = {}) {
  const headers = { Authorization: 'Bearer ' + token };
  if (body) headers['Content-Type'] = 'application/json';
  const res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 204) return null;
  let data = null; try { data = await res.json(); } catch (e) {}
  if (!res.ok) throw new Error((data && (data.error?.message || data.detail)) || `Request failed (${res.status})`);
  return data;
}

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

// ── Farm info form ───────────────────────────────────────────────────

async function loadFarmInfo() {
  const farm = await api(`/farms/${farmId}`);
  document.getElementById('farmNameInput').value = farm.name;
  document.getElementById('farmLocation').value = farm.location || '';
  document.getElementById('farmSize').value = farm.size_hectares ?? '';
  document.getElementById('farmCurrency').value = farm.currency || 'USD';
  document.getElementById('farmDescription').value = farm.description || '';
}

document.getElementById('farmForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('farmAlert');
  const successBox = document.getElementById('farmSuccessAlert');
  alertBox.classList.remove('show'); successBox.classList.remove('show');
  const btn = document.getElementById('farmSubmitBtn');
  btn.disabled = true; btn.textContent = 'Saving...';

  try {
    const updated = await api(`/farms/${farmId}`, {
      method: 'PATCH',
      body: {
        name: document.getElementById('farmNameInput').value.trim(),
        location: document.getElementById('farmLocation').value.trim() || null,
        size_hectares: document.getElementById('farmSize').value ? Number(document.getElementById('farmSize').value) : null,
        currency: document.getElementById('farmCurrency').value,
        description: document.getElementById('farmDescription').value.trim() || null,
      },
    });
    document.getElementById('farmName').textContent = updated.name; // only applies when there's a single farm (no switcher)
    successBox.textContent = 'Saved.';
    successBox.classList.add('show');
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false; btn.textContent = 'Save changes';
  }
});

// ── Members ──────────────────────────────────────────────────────────

async function loadMembers() {
  const members = await api(`/farms/${farmId}/members`);
  const el = document.getElementById('membersList');
  el.innerHTML = members.map(m => `
    <div class="member-row">
      <div>
        <div class="member-name">${m.user_full_name}</div>
        ${m.user_email ? `<div class="member-email">${m.user_email}</div>` : ''}
      </div>
      <span class="member-role">${m.role.replace('_', ' ')}</span>
    </div>
  `).join('');
}

// ── Create additional farm ───────────────────────────────────────────

document.getElementById('newFarmForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('newFarmAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('newFarmSubmitBtn');
  btn.disabled = true; btn.textContent = 'Creating...';

  try {
    const farm = await api('/farms', {
      method: 'POST',
      body: {
        name: document.getElementById('newFarmName').value.trim(),
        location: document.getElementById('newFarmLocation').value.trim() || null,
      },
    });
    // Switch straight to the new farm rather than leaving the user on the old one.
    localStorage.setItem('farmwise_active_farm_id', farm.id);
    window.location.reload();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
    btn.disabled = false; btn.textContent = '+ Create farm';
  }
});

// ── Delete farm ──────────────────────────────────────────────────────

document.getElementById('deleteFarmBtn').addEventListener('click', async () => {
  const alertBox = document.getElementById('deleteAlert');
  alertBox.classList.remove('show');
  if (!confirm('Delete this farm? It will disappear from your farm list. This can only be undone by contacting support.')) return;

  try {
    await api(`/farms/${farmId}`, { method: 'DELETE' });
    localStorage.removeItem('farmwise_active_farm_id');
    window.location.href = '/dashboard'; // dashboard.js will pick the next remaining farm, or show "no farm yet"
  } catch (err) {
    alertBox.textContent = err.message; // e.g. 403 if you're not the owner
    alertBox.classList.add('show');
  }
});

// ── Init ─────────────────────────────────────────────────────────────

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

    await Promise.all([loadFarmInfo(), loadMembers()]);
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

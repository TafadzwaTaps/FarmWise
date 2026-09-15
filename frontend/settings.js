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
let farmOwnerId = null;
let currentUserId = null;
let isOwner = false;

async function api(path, { method = 'GET', body } = {}) {
  const headers = { Authorization: 'Bearer ' + token };
  if (body) headers['Content-Type'] = 'application/json';
  const res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 204) return null;
  let data = null; try { data = await res.json(); } catch (e) {}
  if (!res.ok) {
    const err = new Error((data && (data.error?.message || data.detail)) || `Request failed (${res.status})`);
    err.status = res.status; // lets callers distinguish 401 (session invalid) from 403/404/5xx (AUDIT.md — every page's init() used to treat ANY error the same as an expired session and force-logout, including a plain 403 permission error)
    throw err;
  }
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

const MEMBER_ROLES = ['worker', 'farm_manager', 'accountant', 'farmer'];

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s ?? '';
  return div.innerHTML;
}

async function loadMembers() {
  const members = await api(`/farms/${farmId}/members`);
  const el = document.getElementById('membersList');
  el.innerHTML = members.map(m => {
    const isThisMemberOwner = m.user_id === farmOwnerId;
    // Only the farm's actual owner (isOwner, i.e. caller's role is
    // "farmer") gets edit controls — and never on the owner's own row,
    // which routes/farm_routes.py's _require_target_member_not_owner
    // rejects server-side regardless of what this UI shows or hides.
    const showControls = isOwner && !isThisMemberOwner;
    return `
    <div class="member-row" data-member-id="${m.id}">
      <div>
        <div class="member-name">${escapeHtml(m.user_full_name)}</div>
        ${m.user_email ? `<div class="member-email">${escapeHtml(m.user_email)}</div>` : ''}
      </div>
      ${isThisMemberOwner
        ? `<span class="member-owner-badge">Owner</span>`
        : showControls
          ? `<div class="member-actions">
              <select class="member-role-select" data-member-id="${m.id}">
                ${MEMBER_ROLES.map(r => `<option value="${r}" ${r === m.role ? 'selected' : ''}>${r.replace('_', ' ')}</option>`).join('')}
              </select>
              <button type="button" class="member-remove-btn" data-member-id="${m.id}" title="Remove from farm">✕</button>
            </div>`
          : `<span class="member-role">${m.role.replace('_', ' ')}</span>`
      }
    </div>
  `;
  }).join('');

  document.getElementById('inviteMemberSection').style.display = isOwner ? 'block' : 'none';

  el.querySelectorAll('.member-role-select').forEach(select => {
    select.addEventListener('change', async () => {
      const alertBox = document.getElementById('membersAlert');
      alertBox.classList.remove('show');
      const memberId = select.dataset.memberId;
      const newRole = select.value;
      select.disabled = true;
      try {
        await api(`/farms/${farmId}/members/${memberId}`, { method: 'PATCH', body: { role: newRole } });
        await loadMembers();
      } catch (err) {
        alertBox.textContent = err.message;
        alertBox.classList.add('show');
        await loadMembers(); // revert the select back to the real role
      }
    });
  });

  el.querySelectorAll('.member-remove-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const row = btn.closest('.member-row');
      const name = row.querySelector('.member-name').textContent;
      if (!confirm(`Remove ${name} from this farm?`)) return;
      const alertBox = document.getElementById('membersAlert');
      alertBox.classList.remove('show');
      try {
        await api(`/farms/${farmId}/members/${btn.dataset.memberId}`, { method: 'DELETE' });
        await loadMembers();
      } catch (err) {
        alertBox.textContent = err.message;
        alertBox.classList.add('show');
      }
    });
  });
}

document.getElementById('inviteMemberForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('membersAlert');
  alertBox.classList.remove('show');
  const btn = document.getElementById('inviteSubmitBtn');
  btn.disabled = true;
  try {
    await api(`/farms/${farmId}/members`, {
      method: 'POST',
      body: {
        identifier: document.getElementById('inviteIdentifier').value.trim(),
        role: document.getElementById('inviteRole').value,
      },
    });
    document.getElementById('inviteMemberForm').reset();
    await loadMembers();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false;
  }
});

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

    if (farms.length === 0) {
      document.getElementById('farmName').textContent = 'No farm yet';
      document.getElementById('noFarmState').style.display = 'block';
      return;
    }

    const savedFarmId = localStorage.getItem('farmwise_active_farm_id');
    const activeFarm = farms.find(f => f.id === savedFarmId) || farms[0];
    farmId = activeFarm.id;
    farmOwnerId = activeFarm.owner_id;
    currentUserId = me.id;
    isOwner = activeFarm.my_role === 'farmer';
    renderFarmSwitcher(farms, activeFarm);
    document.getElementById('roleBadge').textContent = activeFarm.my_role || '';

    await Promise.all([loadFarmInfo(), loadMembers()]);
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

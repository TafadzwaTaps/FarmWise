// dashboard.js — auth guard + live data for the dashboard shell.
// This is the seed for Phase 2: swap the empty-state content for real
// widgets as animals/feed/finance/inventory screens get built.

const API = (location.hostname === 'localhost' || location.hostname === '127.0.0.1')
  ? 'http://localhost:8000/api/v1'
  : 'https://farmwise-api.onrender.com/api/v1';

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

async function apiGet(path, token) {
  const res = await fetch(API + path, { headers: { Authorization: 'Bearer ' + token } });
  if (!res.ok) throw new Error('Request failed: ' + res.status);
  return res.json();
}

async function init() {
  const token = getToken();
  if (!token) { window.location.href = 'login.html'; return; }

  try {
    const [me, farms] = await Promise.all([
      apiGet('/auth/me', token),
      apiGet('/farms', token),
    ]);

    document.getElementById('userGreeting').textContent = `Welcome back, ${me.full_name.split(' ')[0]}`;
    document.getElementById('roleBadge').textContent = 'owner';

    if (farms.length > 0) {
      document.getElementById('farmName').textContent = farms[0].name;
    } else {
      document.getElementById('farmName').textContent = 'No farm yet';
    }

    const grid = document.getElementById('stubGrid');
    grid.innerHTML = `
      <div class="stub-card"><strong>${farms.length}</strong>farm${farms.length === 1 ? '' : 's'} on this account</div>
      <div class="stub-card"><strong>${me.email || me.phone_number}</strong>signed in as</div>
      <div class="stub-card"><strong>Backend live</strong>auth · farms · animals · feed · finance · inventory</div>
    `;
  } catch (err) {
    // Token invalid/expired — send back to login.
    localStorage.removeItem('farmwise_token');
    localStorage.removeItem('farmwise_refresh');
    localStorage.removeItem('farmwise_user');
    sessionStorage.clear();
    window.location.href = 'login.html';
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
  } catch (e) { /* ignore — clear local session regardless */ }
  localStorage.removeItem('farmwise_token');
  localStorage.removeItem('farmwise_refresh');
  localStorage.removeItem('farmwise_user');
  sessionStorage.clear();
  window.location.href = 'landing.html';
});

init();

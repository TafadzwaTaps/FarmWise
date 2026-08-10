// dashboard.js — auth guard + live data for the executive dashboard.

// Same-origin — the frontend is served by this FastAPI app, so no host/CORS juggling.
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

async function apiGet(path, token) {
  const res = await fetch(API + path, { headers: { Authorization: 'Bearer ' + token } });
  if (!res.ok) throw new Error('Request failed: ' + res.status);
  return res.json();
}

function money(n) {
  const sign = n < 0 ? '-' : '';
  return sign + '$' + Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatDate(iso) {
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

const ACTIVITY_ICONS = { sale: '💰', expense: '🧾', mortality: '💔' };

function renderDashboard(summary) {
  // Stat cards
  document.getElementById('statAnimals').textContent = summary.current_animals.toLocaleString();
  document.getElementById('statBatches').textContent =
    `${summary.active_batch_count} active batch${summary.active_batch_count === 1 ? '' : 'es'}`;

  document.getElementById('statFeed').textContent = summary.feed.remaining_kg.toLocaleString();

  document.getElementById('statIncome').textContent = money(summary.finance.total_income);
  document.getElementById('statSalesRevenue').textContent = `${money(summary.finance.total_sales_revenue)} from sales`;

  document.getElementById('statExpenses').textContent = money(summary.finance.total_expenses);

  const profitEl = document.getElementById('statProfit');
  profitEl.textContent = money(summary.finance.net_profit);
  document.getElementById('profitCard').classList.toggle('stat-card--negative', summary.finance.net_profit < 0);
  document.getElementById('profitCard').classList.toggle('stat-card--income', summary.finance.net_profit >= 0);

  document.getElementById('statMortality').textContent = summary.mortality.rate_pct + '%';
  document.getElementById('statDeaths').textContent = `${summary.mortality.deaths_this_period} lost this period`;

  // Low stock banner
  const banner = document.getElementById('lowStockBanner');
  if (summary.inventory.low_stock_count > 0) {
    const names = summary.inventory.low_stock_items.map(i => i.name).join(', ');
    document.getElementById('lowStockText').textContent =
      `${summary.inventory.low_stock_count} item${summary.inventory.low_stock_count === 1 ? '' : 's'} running low: ${names}`;
    banner.style.display = 'flex';
  } else {
    banner.style.display = 'none';
  }

  // Expense-by-category bar chart (CSS bars — no chart library)
  const chart = document.getElementById('expenseChart');
  const categories = Object.entries(summary.finance.expenses_by_category || {}).sort((a, b) => b[1] - a[1]);
  if (categories.length === 0) {
    chart.innerHTML = '<p class="panel-empty">No expenses logged in the last 30 days.</p>';
  } else {
    const max = Math.max(...categories.map(([, v]) => v));
    chart.innerHTML = categories.map(([cat, val]) => `
      <div class="bar-row">
        <span class="bar-label">${cat.replace('_', ' ')}</span>
        <span class="bar-track"><span class="bar-fill" style="width:${max ? (val / max * 100) : 0}%"></span></span>
        <span class="bar-value">${money(val)}</span>
      </div>
    `).join('');
  }

  // Upcoming vaccinations
  const vaxList = document.getElementById('vaccinationList');
  if (summary.upcoming_vaccinations.length === 0) {
    vaxList.innerHTML = '<p class="panel-empty">Nothing due in the next 14 days.</p>';
  } else {
    vaxList.innerHTML = summary.upcoming_vaccinations.map(v => `
      <div class="list-item">
        <div>
          <div class="list-item-name">${v.name}</div>
          <div class="list-item-sub">${v.batch_name}</div>
        </div>
        <span class="list-item-date">${formatDate(v.next_due_date)}</span>
      </div>
    `).join('');
  }

  // Recent activity
  const activity = document.getElementById('activityList');
  if (summary.recent_activity.length === 0) {
    activity.innerHTML = '<p class="panel-empty">No activity yet — log a sale, expense, or mortality record to see it here.</p>';
  } else {
    activity.innerHTML = summary.recent_activity.map(a => `
      <div class="activity-row">
        <span class="activity-icon">${ACTIVITY_ICONS[a.type] || '•'}</span>
        <span class="activity-summary">${a.summary}</span>
        <span class="activity-date">${formatDate(a.date)}</span>
      </div>
    `).join('');
  }
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

async function init() {
  const token = getToken();
  if (!token) { window.location.href = '/login'; return; }

  try {
    const [me, farms] = await Promise.all([
      apiGet('/auth/me', token),
      apiGet('/farms', token),
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
    renderFarmSwitcher(farms, activeFarm);

    const summary = await apiGet(`/farms/${activeFarm.id}/dashboard-summary`, token);
    renderDashboard(summary);
    document.getElementById('dashboardContent').style.display = 'block';
  } catch (err) {
    // Token invalid/expired — send back to login.
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
  } catch (e) { /* ignore — clear local session regardless */ }
  localStorage.removeItem('farmwise_token');
  localStorage.removeItem('farmwise_refresh');
  localStorage.removeItem('farmwise_user');
  sessionStorage.clear();
  window.location.href = '/';
});

init();

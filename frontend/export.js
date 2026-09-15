// export.js — auth guard + CSV/PDF report export.
//
// Ported from the mobile app's lib/exportReport.ts + app/export.tsx. Same
// report list, same columns — the generation mechanics are necessarily
// different (no expo-print/expo-sharing on the web), but the web
// equivalents are natural fits:
//   - CSV: build the string client-side (identical logic to mobile's
//     toCSV), wrap in a Blob, trigger a download via a temporary <a download>.
//   - PDF: build the same HTML mobile renders to PDF via expo-print, open
//     it in a hidden iframe, and call window.print() — every modern
//     browser's print dialog offers "Save as PDF" natively, so this needs
//     no PDF library at all.

const API = '/api/v1';
const MANAGE_ROLES = ['farmer', 'farm_manager']; // matches mobile's more.tsx: Export was managerOnly

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

(function () {
  const menuToggle = document.getElementById('menuToggle');
  const sidebarEl = document.querySelector('.sidebar');
  const overlayEl = document.getElementById('sidebarOverlay');
  if (!menuToggle || !sidebarEl || !overlayEl) return;
  function closeSidebar() { sidebarEl.classList.remove('open'); overlayEl.classList.remove('open'); }
  menuToggle.addEventListener('click', () => { sidebarEl.classList.toggle('open'); overlayEl.classList.toggle('open'); });
  overlayEl.addEventListener('click', closeSidebar);
  sidebarEl.querySelectorAll('.side-link').forEach(link => link.addEventListener('click', closeSidebar));
})();

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

let token = null;
let farmId = null;

async function api(path) {
  const res = await fetch(API + path, { headers: { Authorization: 'Bearer ' + token } });
  let data = null; try { data = await res.json(); } catch (e) {}
  if (!res.ok) {
    const err = new Error((data && (data.error?.message || data.detail)) || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

function renderFarmSwitcher(farms, activeFarm) {
  const el = document.getElementById('farmName');
  if (farms.length <= 1) { el.textContent = activeFarm.name; return; }
  el.innerHTML = '';
  const select = document.createElement('select');
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

function money(n) { return Number(n ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function localDateISO() { return new Date().toISOString().slice(0, 10); }
function daysAgoLocalISO(days) { const d = new Date(); d.setDate(d.getDate() - days); return d.toISOString().slice(0, 10); }

// ── Report configs — same list/columns as mobile's app/export.tsx ──────

function summaryToRows(s) {
  const rows = [
    { label: 'Total sales revenue', amount: s.total_sales_revenue },
    { label: 'Total other income', amount: s.total_other_income },
    { label: 'Total income', amount: s.total_income },
    { label: 'Total expenses', amount: s.total_expenses },
    { label: 'Net profit', amount: s.net_profit },
  ];
  for (const [category, amount] of Object.entries(s.income_by_category || {})) {
    rows.push({ label: `Income — ${category.replace(/_/g, ' ')}`, amount });
  }
  for (const [category, amount] of Object.entries(s.expenses_by_category || {})) {
    rows.push({ label: `Expense — ${category.replace(/_/g, ' ')}`, amount });
  }
  return rows;
}

function buildReports() {
  const periodEnd = localDateISO();
  const periodStart = daysAgoLocalISO(30);

  return [
    {
      key: 'profit_loss', label: 'Profit & loss summary (30d)',
      fetch: async () => summaryToRows(await api(`/farms/${farmId}/finance-summary?period_start=${periodStart}&period_end=${periodEnd}`)),
      columns: [
        { label: 'Line item', value: r => r.label },
        { label: 'Amount', value: r => money(r.amount) },
      ],
    },
    {
      key: 'sales', label: 'Sales', fetch: () => api(`/farms/${farmId}/sales`),
      columns: [
        { label: 'Date', value: r => r.sale_date },
        { label: 'Buyer', value: r => r.buyer_name ?? '' },
        { label: 'Quantity', value: r => String(r.quantity) },
        { label: 'Unit price', value: r => money(r.unit_price) },
        { label: 'Discount', value: r => money(r.discount) },
        { label: 'Total', value: r => money(r.total_amount) },
        { label: 'Payment method', value: r => r.payment_method },
        { label: 'Notes', value: r => r.notes ?? '' },
      ],
    },
    {
      key: 'expenses', label: 'Expenses', fetch: () => api(`/farms/${farmId}/expenses`),
      columns: [
        { label: 'Date', value: r => r.expense_date },
        { label: 'Category', value: r => r.category },
        { label: 'Amount', value: r => money(r.amount) },
        { label: 'Vendor', value: r => r.vendor ?? '' },
        { label: 'Notes', value: r => r.notes ?? '' },
      ],
    },
    {
      key: 'income', label: 'Other income', fetch: () => api(`/farms/${farmId}/income`),
      columns: [
        { label: 'Date', value: r => r.income_date },
        { label: 'Category', value: r => r.category },
        { label: 'Amount', value: r => money(r.amount) },
        { label: 'Notes', value: r => r.notes ?? '' },
      ],
    },
    {
      key: 'animal_batches', label: 'Animal batches', fetch: () => api(`/farms/${farmId}/animals/batches`),
      columns: [
        { label: 'Batch name', value: r => r.batch_name },
        { label: 'Species', value: r => r.species },
        { label: 'Breed', value: r => r.breed ?? '' },
        { label: 'Status', value: r => r.status },
        { label: 'Initial qty', value: r => String(r.quantity_initial) },
        { label: 'Current qty', value: r => String(r.quantity_current) },
        { label: 'Purchase date', value: r => r.purchase_date ?? '' },
        { label: 'Purchase cost', value: r => (r.purchase_price_total != null ? money(r.purchase_price_total) : '') },
        { label: 'Supplier', value: r => r.supplier ?? '' },
        { label: 'Avg weight (kg)', value: r => (r.average_weight_kg != null ? String(r.average_weight_kg) : '') },
        { label: 'Expected selling date', value: r => r.expected_selling_date ?? '' },
      ],
    },
    {
      key: 'inventory', label: 'Inventory', fetch: () => api(`/farms/${farmId}/inventory`),
      columns: [
        { label: 'Name', value: r => r.name },
        { label: 'Category', value: r => r.category },
        { label: 'On hand', value: r => String(r.quantity_on_hand) },
        { label: 'Unit', value: r => r.unit },
        { label: 'Low-stock threshold', value: r => String(r.low_stock_threshold) },
        { label: 'Unit cost', value: r => (r.unit_cost != null ? money(r.unit_cost) : '') },
        { label: 'Low stock?', value: r => (r.is_low_stock ? 'Yes' : 'No') },
      ],
    },
    {
      key: 'feed_purchases', label: 'Feed purchases', fetch: () => api(`/farms/${farmId}/feed/purchases`),
      columns: [
        { label: 'Date', value: r => r.purchase_date },
        { label: 'Feed type', value: r => r.feed_type },
        { label: 'Quantity (kg)', value: r => String(r.quantity_kg) },
        { label: 'Unit cost', value: r => money(r.unit_cost) },
        { label: 'Total cost', value: r => money(r.total_cost) },
        { label: 'Supplier', value: r => r.supplier ?? '' },
        { label: 'Notes', value: r => r.notes ?? '' },
      ],
    },
    {
      key: 'feed_consumption', label: 'Feed consumption', fetch: () => api(`/farms/${farmId}/feed/consumption`),
      columns: [
        { label: 'Date', value: r => r.date },
        { label: 'Feed type', value: r => r.feed_type },
        { label: 'Quantity (kg)', value: r => String(r.quantity_kg) },
        { label: 'Notes', value: r => r.notes ?? '' },
      ],
    },
    {
      key: 'field_reports', label: 'Field reports', fetch: () => api(`/farms/${farmId}/field-reports`),
      columns: [
        { label: 'Date', value: r => r.created_at.slice(0, 10) },
        { label: 'Worker', value: r => r.worker_full_name },
        { label: 'Type', value: r => r.report_type },
        { label: 'Subject', value: r => r.subject ?? '' },
        { label: 'Notes', value: r => r.notes },
        { label: 'Status', value: r => r.status },
        { label: 'Manager feedback', value: r => r.manager_feedback ?? '' },
      ],
    },
  ];
}

// ── CSV / PDF generation ─────────────────────────────────────────────

function csvEscape(value) {
  const s = String(value ?? '');
  if (/[",\r\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function toCSV(rows, columns) {
  const header = columns.map(c => csvEscape(c.label)).join(',');
  const lines = rows.map(row => columns.map(c => csvEscape(c.value(row))).join(','));
  return [header, ...lines].join('\r\n');
}

function escapeHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function toHTML(rows, columns, title) {
  const style = `
    body { font-family: -apple-system, Helvetica, Arial, sans-serif; padding: 28px; color: #111; }
    h1 { font-size: 18px; margin: 0 0 4px; }
    .meta { color: #666; font-size: 11px; margin-bottom: 18px; }
    table { width: 100%; border-collapse: collapse; font-size: 11px; }
    th, td { border: 1px solid #ddd; padding: 6px 8px; text-align: left; }
    th { background: #f4f4f4; }
    @media print { body { padding: 0; } }
  `;
  const headerRow = columns.map(c => `<th>${escapeHtml(c.label)}</th>`).join('');
  const bodyRows = rows.map(row => `<tr>${columns.map(c => `<td>${escapeHtml(c.value(row))}</td>`).join('')}</tr>`).join('');
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title><style>${style}</style></head><body>
    <h1>${escapeHtml(title)}</h1>
    <div class="meta">Generated ${new Date().toLocaleString()} · ${rows.length} record${rows.length === 1 ? '' : 's'}</div>
    <table><thead><tr>${headerRow}</tr></thead><tbody>${bodyRows}</tbody></table>
  </body></html>`;
}

function downloadCSV(csv, filenameBase) {
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${filenameBase}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function printAsPDF(html) {
  // A hidden iframe rather than window.open(): pop-up blockers frequently
  // stop a fresh tab opened after an intervening await (the fetch above),
  // even though the click itself was a real user gesture — an iframe has
  // no such restriction and never leaves a stray blank tab behind if the
  // user cancels the print dialog.
  const iframe = document.createElement('iframe');
  iframe.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;';
  document.body.appendChild(iframe);
  iframe.contentDocument.open();
  iframe.contentDocument.write(html);
  iframe.contentDocument.close();
  iframe.onload = () => {
    iframe.contentWindow.focus();
    iframe.contentWindow.print();
    setTimeout(() => iframe.remove(), 1000);
  };
}

// ── Page wiring ───────────────────────────────────────────────────────

let reports = [];

async function handleExport(format) {
  const alertBox = document.getElementById('exportAlert');
  const emptyBox = document.getElementById('exportEmptyAlert');
  alertBox.classList.remove('show');
  emptyBox.classList.remove('show');

  const selected = reports.find(r => r.key === document.getElementById('reportSelect').value);
  const csvBtn = document.getElementById('exportCsvBtn');
  const pdfBtn = document.getElementById('exportPdfBtn');
  csvBtn.disabled = true; pdfBtn.disabled = true;

  try {
    const rows = await selected.fetch();
    if (rows.length === 0) {
      emptyBox.classList.add('show');
      return;
    }
    const filenameBase = `farmwise-${selected.key}-${localDateISO()}`;
    if (format === 'csv') {
      downloadCSV(toCSV(rows, selected.columns), filenameBase);
    } else {
      printAsPDF(toHTML(rows, selected.columns, selected.label));
    }
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    csvBtn.disabled = false; pdfBtn.disabled = false;
  }
}

document.getElementById('exportCsvBtn').addEventListener('click', () => handleExport('csv'));
document.getElementById('exportPdfBtn').addEventListener('click', () => handleExport('pdf'));

// ── Init ─────────────────────────────────────────────────────────────

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
    renderFarmSwitcher(farms, activeFarm);
    document.getElementById('roleBadge').textContent = activeFarm.my_role || '';

    if (!MANAGE_ROLES.includes(activeFarm.my_role)) {
      document.getElementById('deniedState').style.display = 'block';
      return;
    }

    reports = buildReports();
    const select = document.getElementById('reportSelect');
    reports.forEach(r => {
      const opt = document.createElement('option');
      opt.value = r.key; opt.textContent = r.label;
      select.appendChild(opt);
    });

    document.getElementById('pageContent').style.display = 'block';
  } catch (err) {
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

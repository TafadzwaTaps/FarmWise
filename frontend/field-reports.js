// field-reports.js — auth guard + field report CRUD (create/list/detail/feedback/edit/delete).

const API = '/api/v1';
const FINANCE_VIEW_ROLES = ['farmer', 'farm_manager', 'accountant'];
const MANAGE_ROLES = ['farmer', 'farm_manager'];

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

// AUDIT.md — shown instead of a forced logout when init() fails for a
// reason other than an invalid session.
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
let currentUserId = null;
let currentRole = null;
let isManager = false;
let batches = [];
let reports = [];
let statusFilter = '';
let pendingMedia = []; // {url, type} already-uploaded attachments for the create form
let uploadingMedia = false;

async function api(path, { method = 'GET', body } = {}) {
  const headers = { Authorization: 'Bearer ' + token };
  if (body) headers['Content-Type'] = 'application/json';
  const res = await fetch(API + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (res.status === 204) return null;
  let data = null; try { data = await res.json(); } catch (e) {}
  if (!res.ok) {
    const err = new Error((data && (data.error?.message || data.detail)) || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return data;
}

async function apiUpload(path, formData) {
  const res = await fetch(API + path, { method: 'POST', headers: { Authorization: 'Bearer ' + token }, body: formData });
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

function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }
document.querySelectorAll('[data-close]').forEach(btn => {
  btn.addEventListener('click', () => closeModal(btn.dataset.close));
});
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.classList.remove('open'); });
});

function fmtDateTime(iso) {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

const TYPE_ICON = { livestock: '🐔', crop: '🌾', general: '📋' };

// ── List rendering ────────────────────────────────────────────────────

function renderReports() {
  const grid = document.getElementById('reportGrid');
  const filtered = statusFilter ? reports.filter(r => r.status === statusFilter) : reports;

  if (filtered.length === 0) {
    grid.style.display = 'none';
    document.getElementById('emptyState').style.display = 'block';
    document.getElementById('emptyTitle').textContent = isManager ? 'No reports yet' : 'No reports submitted yet';
    document.getElementById('emptyMessage').textContent = isManager
      ? 'Reports from your team will show up here for review.'
      : 'Log a note, observation, or photo of livestock/crops for your manager to review.';
    document.getElementById('emptyNewReportBtn').style.display = isManager ? 'none' : 'inline-flex';
    return;
  }
  document.getElementById('emptyState').style.display = 'none';
  grid.style.display = 'grid';
  grid.innerHTML = filtered.map(r => `
    <div class="report-card" data-id="${r.id}">
      <div class="report-card-top">
        <span class="report-type-icon">${TYPE_ICON[r.report_type] || '📋'}</span>
        <span class="status-badge status-badge--${r.status}">${r.status}</span>
      </div>
      ${isManager ? `<div style="color:var(--green);font-size:12px;font-weight:700;margin-bottom:4px">${r.worker_full_name}</div>` : ''}
      ${r.subject ? `<div class="report-subject">${escapeHtml(r.subject)}</div>` : ''}
      <div class="report-notes-preview">${escapeHtml(r.notes)}</div>
      <div class="report-meta">
        <span class="report-media-count">${r.media && r.media.length ? '📎 ' + r.media.length + ' attachment' + (r.media.length === 1 ? '' : 's') : ''}</span>
        <span>${fmtDateTime(r.created_at)}</span>
      </div>
    </div>
  `).join('');
  grid.querySelectorAll('.report-card').forEach(card => {
    card.addEventListener('click', () => openDetail(card.dataset.id));
  });
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s ?? '';
  return div.innerHTML;
}

async function loadReports() {
  reports = await api(`/farms/${farmId}/field-reports`);
  renderReports();
}

document.getElementById('statusTabs').addEventListener('click', (e) => {
  const tab = e.target.closest('.status-tab');
  if (!tab) return;
  document.querySelectorAll('.status-tab').forEach(t => t.classList.remove('active'));
  tab.classList.add('active');
  statusFilter = tab.dataset.filter;
  renderReports();
});

// ── Create report ────────────────────────────────────────────────────

function resetCreateForm() {
  document.getElementById('createForm').reset();
  document.getElementById('createAlert').classList.remove('show');
  pendingMedia = [];
  renderMediaPicker();
  document.getElementById('batchField').style.display = 'none';
  document.getElementById('subjectField').style.display = 'block';
}

function renderMediaPicker() {
  const picker = document.getElementById('mediaPicker');
  const addBtn = document.getElementById('mediaAddBtn');
  picker.querySelectorAll('.media-thumb').forEach(el => el.remove());
  pendingMedia.forEach((m, idx) => {
    const thumb = document.createElement('div');
    thumb.className = 'media-thumb';
    thumb.innerHTML = m.type === 'image'
      ? `<img src="${m.url}" alt="Attachment"/>`
      : `<video src="${m.url}"></video>`;
    const removeBtn = document.createElement('button');
    removeBtn.className = 'media-thumb-remove';
    removeBtn.type = 'button';
    removeBtn.textContent = '✕';
    removeBtn.addEventListener('click', () => { pendingMedia.splice(idx, 1); renderMediaPicker(); });
    thumb.appendChild(removeBtn);
    picker.insertBefore(thumb, addBtn);
  });
  addBtn.disabled = uploadingMedia || pendingMedia.length >= 10;
  addBtn.textContent = uploadingMedia ? '…' : '+';
}

document.getElementById('mediaAddBtn').addEventListener('click', () => document.getElementById('mediaInput').click());

document.getElementById('mediaInput').addEventListener('change', async () => {
  const file = document.getElementById('mediaInput').files[0];
  document.getElementById('mediaInput').value = '';
  if (!file) return;
  uploadingMedia = true;
  renderMediaPicker();
  try {
    const formData = new FormData();
    formData.append('file', file);
    const result = await apiUpload(`/farms/${farmId}/field-reports/media`, formData);
    pendingMedia.push(result);
  } catch (err) {
    document.getElementById('createAlert').textContent = err.message;
    document.getElementById('createAlert').classList.add('show');
  } finally {
    uploadingMedia = false;
    renderMediaPicker();
  }
});

document.getElementById('reportType').addEventListener('change', (e) => {
  const isLivestock = e.target.value === 'livestock';
  document.getElementById('batchField').style.display = isLivestock ? 'block' : 'none';
  document.getElementById('subjectField').style.display = isLivestock ? 'none' : 'block';
});

document.getElementById('newReportBtn').addEventListener('click', () => { resetCreateForm(); openModal('createModal'); });
document.getElementById('emptyNewReportBtn').addEventListener('click', () => { resetCreateForm(); openModal('createModal'); });

document.getElementById('createForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const alertBox = document.getElementById('createAlert');
  alertBox.classList.remove('show');

  const reportType = document.getElementById('reportType').value;
  const notes = document.getElementById('reportNotes').value.trim();
  const batchId = document.getElementById('reportBatch').value;
  const subject = document.getElementById('reportSubject').value.trim();

  if (!notes) { alertBox.textContent = 'Add a note describing what you observed.'; alertBox.classList.add('show'); return; }
  if (reportType === 'livestock' && !batchId) { alertBox.textContent = 'Select which batch this report is about.'; alertBox.classList.add('show'); return; }
  if (uploadingMedia) { alertBox.textContent = 'Please wait for the attachment to finish uploading.'; alertBox.classList.add('show'); return; }

  const btn = document.getElementById('createSubmitBtn');
  btn.disabled = true;
  try {
    await api(`/farms/${farmId}/field-reports`, {
      method: 'POST',
      body: {
        report_type: reportType,
        notes,
        batch_id: reportType === 'livestock' ? batchId : null,
        subject: reportType !== 'livestock' ? (subject || null) : null,
        media: pendingMedia,
      },
    });
    closeModal('createModal');
    await loadReports();
  } catch (err) {
    alertBox.textContent = err.message;
    alertBox.classList.add('show');
  } finally {
    btn.disabled = false;
  }
});

// ── Detail / feedback / edit / delete ────────────────────────────────

function renderMediaGrid(media) {
  if (!media || media.length === 0) return '';
  return `<div class="detail-media-grid">${media.map(m =>
    m.type === 'image'
      ? `<img src="${m.url}" alt="Attachment" onclick="window.open('${m.url}', '_blank')"/>`
      : `<video src="${m.url}" controls onclick="event.stopPropagation()"></video>`
  ).join('')}</div>`;
}

async function openDetail(reportId) {
  const report = reports.find(r => r.id === reportId);
  if (!report) return;
  document.getElementById('detailAlert').classList.remove('show');
  document.getElementById('detailTitle').textContent = report.subject || `${report.report_type} report`;

  const isAuthor = report.worker_id === currentUserId;
  const canEdit = isAuthor && report.status === 'pending';
  const canGiveFeedback = isManager && report.status === 'pending';

  document.getElementById('detailBody').innerHTML = `
    <div style="display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap">
      <span class="status-badge status-badge--${report.status}">${report.status}</span>
      <span style="color:var(--text-dim);font-size:12px;font-family:var(--mono);align-self:center">${fmtDateTime(report.created_at)}</span>
    </div>
    ${isManager ? `<div style="color:var(--green);font-size:13px;font-weight:700;margin-bottom:10px">${report.worker_full_name}</div>` : ''}
    <div id="detailNotesView">
      <div style="font-size:13px;line-height:1.6;white-space:pre-wrap;margin-bottom:14px">${escapeHtml(report.notes)}</div>
      ${renderMediaGrid(report.media)}
    </div>
    ${canEdit ? `
      <div id="detailEditForm" style="display:none">
        <div class="field"><label for="editNotes">Notes</label><textarea id="editNotes" rows="4">${escapeHtml(report.notes)}</textarea></div>
        ${report.report_type !== 'livestock' ? `<div class="field"><label for="editSubject">Subject</label><input id="editSubject" value="${escapeHtml(report.subject || '')}"/></div>` : ''}
        <div class="form-actions">
          <button type="button" class="btn btn--outline btn--sm" id="cancelEditBtn">Cancel</button>
          <button type="button" class="btn btn--primary btn--sm" id="saveEditBtn">Save changes</button>
        </div>
      </div>
      <div class="divider"></div>
      <div style="display:flex;gap:10px">
        <button type="button" class="btn btn--outline btn--sm" id="editReportBtn">Edit</button>
        <button type="button" class="btn btn--danger btn--sm" id="deleteReportBtn">Delete</button>
      </div>
    ` : ''}
    ${report.manager_feedback ? `
      <div class="feedback-box">
        <div class="feedback-box-label">Manager feedback</div>
        <div class="feedback-box-body">${escapeHtml(report.manager_feedback)}</div>
      </div>
    ` : ''}
    ${canGiveFeedback ? `
      <div class="divider"></div>
      <div class="section-title">Add feedback</div>
      <div class="field"><textarea id="feedbackText" rows="3" placeholder="Your feedback for this report…"></textarea></div>
      <div class="form-actions">
        <button type="button" class="btn btn--primary btn--sm" id="submitFeedbackBtn">Submit feedback</button>
      </div>
    ` : ''}
  `;

  if (canEdit) {
    document.getElementById('editReportBtn').addEventListener('click', () => {
      document.getElementById('detailNotesView').style.display = 'none';
      document.getElementById('detailEditForm').style.display = 'block';
    });
    document.getElementById('cancelEditBtn').addEventListener('click', () => {
      document.getElementById('detailNotesView').style.display = 'block';
      document.getElementById('detailEditForm').style.display = 'none';
    });
    document.getElementById('saveEditBtn').addEventListener('click', async () => {
      const alertBox = document.getElementById('detailAlert');
      const newNotes = document.getElementById('editNotes').value.trim();
      if (!newNotes) { alertBox.textContent = 'Notes are required.'; alertBox.classList.add('show'); return; }
      const subjectField = document.getElementById('editSubject');
      try {
        await api(`/farms/${farmId}/field-reports/${reportId}`, {
          method: 'PATCH',
          body: { notes: newNotes, subject: subjectField ? (subjectField.value.trim() || null) : undefined },
        });
        closeModal('detailModal');
        await loadReports();
      } catch (err) {
        alertBox.textContent = err.message;
        alertBox.classList.add('show');
      }
    });
    document.getElementById('deleteReportBtn').addEventListener('click', async () => {
      if (!confirm('Delete this report? This cannot be undone.')) return;
      try {
        await api(`/farms/${farmId}/field-reports/${reportId}`, { method: 'DELETE' });
        closeModal('detailModal');
        await loadReports();
      } catch (err) {
        document.getElementById('detailAlert').textContent = err.message;
        document.getElementById('detailAlert').classList.add('show');
      }
    });
  }

  if (canGiveFeedback) {
    document.getElementById('submitFeedbackBtn').addEventListener('click', async () => {
      const alertBox = document.getElementById('detailAlert');
      const feedback = document.getElementById('feedbackText').value.trim();
      if (!feedback) { alertBox.textContent = 'Enter your feedback.'; alertBox.classList.add('show'); return; }
      try {
        await api(`/farms/${farmId}/field-reports/${reportId}/feedback`, { method: 'POST', body: { feedback } });
        closeModal('detailModal');
        await loadReports();
      } catch (err) {
        alertBox.textContent = err.message;
        alertBox.classList.add('show');
      }
    });
  }

  openModal('detailModal');
}

// ── Init ─────────────────────────────────────────────────────────────

async function init() {
  token = getToken();
  if (!token) { window.location.href = '/login'; return; }

  try {
    const [me, farms] = await Promise.all([api('/auth/me'), api('/farms')]);
    currentUserId = me.id;
    document.getElementById('userGreeting').textContent = `Welcome back, ${me.full_name.split(' ')[0]}`;

    if (farms.length === 0) {
      document.getElementById('farmName').textContent = 'No farm yet';
      document.getElementById('noFarmState').style.display = 'block';
      return;
    }

    const savedFarmId = localStorage.getItem('farmwise_active_farm_id');
    const activeFarm = farms.find(f => f.id === savedFarmId) || farms[0];
    farmId = activeFarm.id;
    currentRole = activeFarm.my_role || null;
    isManager = MANAGE_ROLES.includes(currentRole);
    renderFarmSwitcher(farms, activeFarm);
    document.getElementById('roleBadge').textContent = currentRole || '';
    document.getElementById('pageTitle').textContent = isManager ? 'Team Reports' : 'My Reports';

    batches = await api(`/farms/${farmId}/animals/batches`);
    const batchSelect = document.getElementById('reportBatch');
    batches.forEach(b => {
      const opt = document.createElement('option');
      opt.value = b.id; opt.textContent = b.batch_name;
      batchSelect.appendChild(opt);
    });

    await loadReports();
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

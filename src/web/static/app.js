/**
 * AI-Invoice-Auditor Front-end Controller
 * Handles drag-and-drop file ingestion, engine selection, async auditing,
 * real-time UI rendering, and SQLite historical tracking.
 */

let selectedFiles = [];
let currentSession = null;
let serverHasKey = false;
let activePreviewObjectUrl = null;

function getAuthToken() {
  return localStorage.getItem('auth_token');
}

async function authFetch(url, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = getAuthToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    localStorage.removeItem('auth_token');
    currentUser = null;
    renderAuthUI();
    openLoginModal();
  }
  return response;
}

async function downloadBlobWithAuth(url, fallbackFilename) {
  const res = await authFetch(url);
  if (!res.ok) {
    let detail = '下載失敗';
    try {
      const err = await res.json();
      detail = err.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = objectUrl;
  a.download = fallbackFilename || 'download';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
}

async function handleProtectedDownload(event, anchorEl) {
  if (event) event.preventDefault();
  const targetUrl = anchorEl.dataset.url || anchorEl.getAttribute('href');
  if (!targetUrl || targetUrl === '#') return false;
  try {
    await downloadBlobWithAuth(targetUrl, anchorEl.dataset.filename || 'report');
  } catch (err) {
    alert(`檔案下載失敗: ${err.message}`);
  }
  return false;
}

// Initialization
document.addEventListener('DOMContentLoaded', () => {
  // Load saved API Key from localStorage
  const savedKey = localStorage.getItem('gemini_api_key');
  if (savedKey) {
    const keyInput = document.getElementById('api-key-input');
    if (keyInput) keyInput.value = savedKey;
  }

  setupDropzone();
  loadServerConfig();
  loadLiveRates();
  checkCurrentUser();
  loadApprovalSummaryBadge();
});

// Load Live Exchange Rates from Bank of Taiwan / Taifex
async function loadLiveRates() {
  try {
    const res = await fetch('/api/rates');
    if (!res.ok) return;
    const data = await res.json();
    const label = document.getElementById('fx-status-label');
    const indicator = document.getElementById('fx-status-indicator');
    if (label && data.rates) {
      const usd = data.rates.USD ? `USD $${data.rates.USD}` : '';
      const eur = data.rates.EUR ? `EUR $${data.rates.EUR}` : '';
      label.textContent = `台銀即時匯率 (${usd})`;
      if (indicator) {
        indicator.title = `${data.source}\n更新時間：${data.updated_at}\nUSD: ${data.rates.USD} | EUR: ${data.rates.EUR} | RMB: ${data.rates.RMB} | JPY: ${data.rates.JPY} | THB: ${data.rates.THB}`;
      }
    }
  } catch (e) {
    console.warn('FX rate load error:', e);
  }
}

// ==========================================================================
// Enterprise Authentication & User Context
// ==========================================================================
let currentUser = null;

async function checkCurrentUser() {
  const token = getAuthToken();
  if (!token) {
    currentUser = null;
    renderAuthUI();
    return;
  }

  try {
    const res = await fetch('/api/auth/me', {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (res.ok) {
      currentUser = await res.json();
    } else {
      localStorage.removeItem('auth_token');
      currentUser = null;
    }
  } catch (e) {
    console.warn('Auth check error:', e);
    currentUser = null;
  }
  renderAuthUI();
  if (currentUser) {
    await loadFilterUsersIfPrivileged();
    loadHistoryStats();
  }
}

function renderAuthUI() {
  const btnOpen = document.getElementById('btn-open-login');
  const badge = document.getElementById('user-profile-badge');
  const roleTag = document.getElementById('user-role-tag');
  const nameText = document.getElementById('user-name-display');
  const adminLink = document.getElementById('link-admin-panel');

  if (currentUser) {
    if (btnOpen) btnOpen.classList.add('hidden');
    if (badge) badge.classList.remove('hidden');
    if (roleTag) {
      roleTag.textContent = currentUser.role.toUpperCase();
      if (currentUser.role === 'admin') {
        roleTag.style.background = '#EDE9FE';
        roleTag.style.color = '#6D28D9';
      } else if (currentUser.role === 'accountant') {
        roleTag.style.background = '#D1FAE5';
        roleTag.style.color = '#047857';
      } else {
        roleTag.style.background = '#DBEAFE';
        roleTag.style.color = '#1D4ED8';
      }
    }
    if (nameText) {
      nameText.textContent = `${currentUser.full_name} (${currentUser.department})`;
    }
    if (adminLink) {
      if (['admin', 'accountant', 'manager'].includes(currentUser.role)) {
        adminLink.classList.remove('hidden');
      } else {
        adminLink.classList.add('hidden');
      }
    }
  } else {
    if (btnOpen) btnOpen.classList.remove('hidden');
    if (badge) badge.classList.add('hidden');
    if (adminLink) adminLink.classList.add('hidden');
  }
}

function openLoginModal() {
  const modal = document.getElementById('auth-modal');
  const err = document.getElementById('login-error-msg');
  if (err) err.classList.add('hidden');
  if (modal) modal.classList.remove('hidden');
}

function closeLoginModal() {
  const modal = document.getElementById('auth-modal');
  if (modal) modal.classList.add('hidden');
}

function quickFillLogin(role) {
  const emailInput = document.getElementById('login-email');
  const passInput = document.getElementById('login-password');
  const err = document.getElementById('login-error-msg');
  if (err) err.classList.add('hidden');

  if (role === 'admin') {
    if (emailInput) emailInput.value = 'admin@company.local';
    if (passInput) passInput.value = 'Admin1234!';
  } else if (role === 'finance') {
    if (emailInput) emailInput.value = 'finance@company.local';
    if (passInput) passInput.value = 'Finance1234!';
  } else if (role === 'employee') {
    if (emailInput) emailInput.value = 'employee@company.local';
    if (passInput) passInput.value = 'User1234!';
  }
}

async function handleLoginSubmit(event) {
  event.preventDefault();
  const email = document.getElementById('login-email').value;
  const password = document.getElementById('login-password').value;
  const err = document.getElementById('login-error-msg');
  const btnSubmit = document.getElementById('btn-login-submit');

  if (btnSubmit) btnSubmit.disabled = true;
  if (err) err.classList.add('hidden');

  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password })
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || '登入失敗，請檢查帳號密碼');
    }

    localStorage.setItem('auth_token', data.access_token);
    currentUser = data.user;
    renderAuthUI();
    closeLoginModal();
    await loadFilterUsersIfPrivileged();
    loadHistoryStats();
    const histPane = document.getElementById('tab-history');
    if (histPane && histPane.classList.contains('active')) {
      loadHistory();
    }
  } catch (e) {
    if (err) {
      err.textContent = e.message;
      err.classList.remove('hidden');
    }
  } finally {
    if (btnSubmit) btnSubmit.disabled = false;
  }
}

function logoutUser() {
  localStorage.removeItem('auth_token');
  currentUser = null;
  const userSelect = document.getElementById('history-filter-user');
  if (userSelect) {
    userSelect.innerHTML = '<option value="">全部同仁</option>';
    userSelect.classList.add('hidden');
  }
  renderAuthUI();
  const tbody = document.getElementById('history-table-body');
  if (tbody) {
    tbody.innerHTML = '<tr><td colspan="12" class="text-center" style="padding: 2rem;">請先登入系統後查看歷史單據記錄</td></tr>';
  }
}

async function loadFilterUsersIfPrivileged() {
  const userSelect = document.getElementById('history-filter-user');
  if (!userSelect) return;
  if (!currentUser || !['admin', 'accountant', 'manager'].includes(currentUser.role)) {
    userSelect.classList.add('hidden');
    return;
  }
  try {
    const res = await authFetch('/api/auth/users');
    if (!res.ok) return;
    const users = await res.json();
    userSelect.innerHTML = '<option value="">全部同仁</option>' + users.map(u => (
      `<option value="${u.id}">${escapeHtml(u.full_name)} (${escapeHtml(u.email)})</option>`
    )).join('');
    userSelect.classList.remove('hidden');
  } catch (e) {
    console.warn('Failed to load filter users:', e);
  }
}

function openChangePasswordModal() {
  const modal = document.getElementById('change-password-modal');
  const form = document.getElementById('change-password-form');
  const err = document.getElementById('cp-error-msg');
  if (form) form.reset();
  if (err) err.classList.add('hidden');
  if (modal) modal.classList.remove('hidden');
}

function closeChangePasswordModal() {
  const modal = document.getElementById('change-password-modal');
  if (modal) modal.classList.add('hidden');
}

async function handleChangePasswordSubmit(event) {
  event.preventDefault();
  const currentPassword = document.getElementById('cp-current-password').value;
  const newPassword = document.getElementById('cp-new-password').value;
  const confirmPassword = document.getElementById('cp-confirm-password').value;
  const err = document.getElementById('cp-error-msg');
  const btn = document.getElementById('btn-cp-submit');

  if (err) err.classList.add('hidden');
  if (newPassword !== confirmPassword) {
    if (err) {
      err.textContent = '兩次輸入的新密碼不一致';
      err.classList.remove('hidden');
    }
    return;
  }

  if (btn) btn.disabled = true;
  try {
    const res = await authFetch('/api/auth/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword })
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || '修改密碼失敗');
    }
    closeChangePasswordModal();
    alert(data.message || '密碼已更新');
  } catch (e) {
    if (err) {
      err.textContent = e.message;
      err.classList.remove('hidden');
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}



// Load Server Configuration (Checks if GEMINI_API_KEY is preloaded in backend)
async function loadServerConfig() {
  try {
    const res = await fetch('/api/config');
    if (!res.ok) return;
    const cfg = await res.json();
    serverHasKey = cfg.has_server_api_key;
    const serverBadge = document.getElementById('server-key-badge');
    const apiKeyInput = document.getElementById('api-key-input');
    const apiKeyHint = document.getElementById('api-key-hint');
    const badgeLabel = document.getElementById('engine-badge-label');

    if (serverHasKey) {
      if (serverBadge) serverBadge.classList.remove('hidden');
      if (apiKeyInput) apiKeyInput.placeholder = '伺服器已預設 API Key（免手動填寫，可直接辨識）';
      if (apiKeyHint) apiKeyHint.textContent = '🟢 伺服器端已連線 GEMINI_API_KEY，同仁免輸金鑰。';
      if (badgeLabel) badgeLabel.textContent = 'Gemini 3.7 Flash 雲端';
    } else {
      if (serverBadge) serverBadge.classList.add('hidden');
      if (badgeLabel) badgeLabel.textContent = 'Gemini 3.7 Flash 雲端';
    }
  } catch (e) {
    console.error("Failed to load server config", e);
  }
}

// Tab Switching
function switchTab(tabName) {
  document.querySelectorAll('.nav-tab').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));

  // Auto-reset viewports to top
  document.querySelectorAll('.table-responsive-viewport').forEach(el => {
    el.scrollTop = 0;
  });

  if (tabName === 'audit') {
    document.getElementById('tab-audit-btn').classList.add('active');
    document.getElementById('tab-audit').classList.add('active');
  } else if (tabName === 'approval') {
    document.getElementById('tab-approval-btn').classList.add('active');
    document.getElementById('tab-approval').classList.add('active');
    loadApprovalData();
  } else if (tabName === 'analytics') {
    document.getElementById('tab-analytics-btn').classList.add('active');
    document.getElementById('tab-analytics').classList.add('active');
    initAnalyticsDashboard();
    loadStorageStats();
  } else if (tabName === 'history') {
    document.getElementById('tab-history-btn').classList.add('active');
    document.getElementById('tab-history').classList.add('active');
    loadHistory();
  }
}

// Engine Change Handler
function onEngineChange() {
  const engine = document.getElementById('engine-select').value;
  const keyGroup = document.getElementById('gemini-key-group');
  const engineHint = document.getElementById('engine-hint');
  const badgeLabel = document.getElementById('engine-badge-label');

  if (engine === 'gemini') {
    keyGroup.classList.remove('hidden');
    engineHint.textContent = '採用 Google 雲端多模態視覺模型，辨識速度 1~2 秒，跨國多幣別理解力最強。';
    badgeLabel.textContent = 'Gemini 3.7 Flash 雲端';
  } else {
    keyGroup.classList.add('hidden');
    engineHint.textContent = '無需 API Key，支援示範中國發票、泰國 Grab、歐洲飯店與高鐵票。';
    badgeLabel.textContent = 'Mock 離線演示';
  }
}

// Password Visibility Toggle
function toggleKeyVisibility() {
  const input = document.getElementById('api-key-input');
  input.type = input.type === 'password' ? 'text' : 'password';
}

// Dropzone Setup
function setupDropzone() {
  const dropzone = document.getElementById('dropzone');

  ['dragenter', 'dragover'].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add('dragover');
    }, false);
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove('dragover');
    }, false);
  });

  dropzone.addEventListener('drop', (e) => {
    const dt = e.dataTransfer;
    const files = dt.files;
    addFiles(files);
  });
}

function triggerCameraInput(e) {
  if (e) e.stopPropagation();
  const cam = document.getElementById('camera-input');
  if (cam) cam.click();
  else document.getElementById('file-input').click();
}

function triggerFileInput(e) {
  if (e) e.stopPropagation();
  document.getElementById('file-input').click();
}

function handleFileSelect(e) {
  const files = e.target.files;
  addFiles(files);
}

function addFiles(files) {
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    // Avoid duplicate filenames in queue
    if (!selectedFiles.some(f => f.name === file.name)) {
      selectedFiles.push(file);
    }
  }
  renderQueue();
}

function removeFile(index) {
  selectedFiles.splice(index, 1);
  renderQueue();
}

function clearQueue() {
  selectedFiles = [];
  renderQueue();
  document.getElementById('file-input').value = '';
  const cam = document.getElementById('camera-input');
  if (cam) cam.value = '';
}

function renderQueue() {
  const container = document.getElementById('file-queue-container');
  const list = document.getElementById('file-queue-list');
  const countSpan = document.getElementById('queue-count');
  const startBtn = document.getElementById('btn-start-audit');

  if (selectedFiles.length === 0) {
    container.classList.add('hidden');
    startBtn.disabled = true;
    return;
  }

  container.classList.remove('hidden');
  countSpan.textContent = selectedFiles.length;
  startBtn.disabled = false;

  list.innerHTML = selectedFiles.map((file, idx) => `
    <div class="queue-chip">
      <span class="queue-chip-name" title="${file.name}">📄 ${file.name} <small style="color:#64748B;">(${formatBytes(file.size)})</small></span>
      <button type="button" class="queue-chip-del" onclick="removeFile(${idx})" title="移除">✕</button>
    </div>
  `).join('');
}

function formatBytes(bytes, decimals = 1) {
  if (!+bytes) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}

// Load Demo Fixtures (Quick Experience)
async function loadSampleFixtures() {
  const sampleNames = [
    'sample_china_vat_invoice.json',
    'sample_thailand_grab.json',
    'sample_overseas_hotel.json',
    'sample_thsr_ticket.png',
    'sample_taxi_receipt.png',
    'sample_invoice_tw_b2b.json',
    'sample_invoice_error_math.json'
  ];

  selectedFiles = [];
  for (const name of sampleNames) {
    // Create dummy files with these names so backend's Mock/sample loader detects them
    const file = new File(["{}"], name, { type: "application/json" });
    selectedFiles.push(file);
  }
  renderQueue();
}

// Start Audit Flow
async function startAudit() {
  if (selectedFiles.length === 0) return;

  if (!currentUser || !getAuthToken()) {
    alert('請先登入公司帳號後再上傳單據。');
    openLoginModal();
    return;
  }

  const engine = document.getElementById('engine-select').value;
  const apiKey = document.getElementById('api-key-input').value.trim();
  const applyTemplate = document.getElementById('apply-template-cb').checked;

  if (engine === 'gemini') {
    if (!apiKey && !serverHasKey) {
      alert('請輸入 Gemini API Key，或於伺服器端環境設定 GEMINI_API_KEY！\n（若想快速測試，可切換至「離線展示模式」）');
      document.getElementById('api-key-input').focus();
      return;
    }
    if (apiKey) localStorage.setItem('gemini_api_key', apiKey);
  }

  // UI State: Loading
  document.getElementById('btn-start-audit').disabled = true;
  document.getElementById('audit-empty').classList.add('hidden');
  document.getElementById('audit-table-wrapper').classList.add('hidden');
  document.getElementById('download-banner').classList.add('hidden');
  document.getElementById('currency-bar').classList.add('hidden');
  const statsStrip = document.getElementById('audit-stats-strip');
  if (statsStrip) statsStrip.classList.add('hidden');
  document.getElementById('audit-loading').classList.remove('hidden');

  const formData = new FormData();
  selectedFiles.forEach(file => {
    formData.append('files', file);
  });
  formData.append('engine', engine);
  if (apiKey) formData.append('api_key', apiKey);
  formData.append('apply_template', applyTemplate ? 'true' : 'false');

  try {
    const response = await authFetch('/api/audit', {
      method: 'POST',
      body: formData
    });

    if (!response.ok) {
      const err = await response.json();
      throw new Error(err.detail || '審核請求失敗');
    }

    const data = await response.json();
    currentSession = data;
    renderAuditResults(data);
    loadHistoryStats(); // Refresh SQLite stats

  } catch (error) {
    alert(`執行審核發生錯誤: ${error.message}`);
    document.getElementById('audit-empty').classList.remove('hidden');
  } finally {
    document.getElementById('audit-loading').classList.add('hidden');
    document.getElementById('btn-start-audit').disabled = false;
  }
}

// Render Results Dashboard
function renderAuditResults(data) {
  // Update KPI Cards
  document.getElementById('stat-total').textContent = data.total;
  document.getElementById('stat-approved').textContent = data.approved;
  document.getElementById('stat-warning').textContent = data.warning;
  document.getElementById('stat-rejected').textContent = data.rejected;

  const statsStrip = document.getElementById('audit-stats-strip');
  if (statsStrip) statsStrip.classList.remove('hidden');

  // Currency breakdown
  const currencyTotals = {};
  data.reports.forEach(r => {
    const cur = r.data.currency || 'NT$';
    const amt = r.data.total_amount || 0;
    currencyTotals[cur] = (currencyTotals[cur] || 0) + amt;
  });

  const currencyBar = document.getElementById('currency-bar');
  const currencyBadges = document.getElementById('currency-badges');
  const currencies = Object.keys(currencyTotals);

  if (currencies.length > 0) {
    currencyBadges.innerHTML = currencies.map(c => `
      <span class="curr-pill">
        ${c} ${currencyTotals[c].toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
      </span>
    `).join('');
    currencyBar.classList.remove('hidden');
  }

  // Update Download Links (protected via authFetch Blob downloads)
  if (data.download_urls) {
    const banner = document.getElementById('download-banner');
    const btnTemplate = document.getElementById('btn-download-template');
    const btnExcel = document.getElementById('btn-download-excel');
    const btnJson = document.getElementById('btn-download-json');

    if (data.download_urls.template) {
      btnTemplate.dataset.url = data.download_urls.template;
      btnTemplate.href = data.download_urls.template;
      btnTemplate.classList.remove('hidden');
    } else {
      btnTemplate.dataset.url = '';
      btnTemplate.classList.add('hidden');
    }

    if (data.download_urls.excel) {
      btnExcel.dataset.url = data.download_urls.excel;
      btnExcel.href = data.download_urls.excel;
    }

    if (btnJson && data.download_urls.json) {
      btnJson.dataset.url = data.download_urls.json;
      btnJson.href = data.download_urls.json;
    }

    const btnErp = document.getElementById('btn-download-erp');
    if (btnErp && data.download_urls.erp) {
      btnErp.dataset.url = data.download_urls.erp;
      btnErp.href = data.download_urls.erp;
      btnErp.classList.remove('hidden');
    }

    banner.classList.remove('hidden');
  }

  // Show table container
  document.getElementById('audit-table-wrapper').classList.remove('hidden');

  // Populate Table Rows
  const tbody = document.getElementById('audit-table-body');
  tbody.innerHTML = data.reports.map((report, idx) => {
    const inv = report.data;
    const status = report.status;
    let statusBadge = '';
    if (status === 'APPROVED') statusBadge = '<span class="badge badge-approved">合規 APPROVED</span>';
    else if (status === 'WARNING') statusBadge = '<span class="badge badge-warning">警示 WARNING</span>';
    else statusBadge = '<span class="badge badge-rejected">違規 REJECTED</span>';

    // Category badge
    const cat = inv.expense_category || 'E';
    const catClass = `badge-${cat.toLowerCase()}`;
    const catLabels = { 'T': '交通 T', 'F': '餐飲 F', 'H': '住宿 H', 'S': '社交 S', 'I': '保險 I', 'E': '雜支 E' };
    const catText = catLabels[cat] || cat;

    // Issues list
    const issuesHtml = (report.issues || []).map(issue => `
      <span class="issue-pill ${issue.level.toLowerCase()}">
        ↳ [${issue.code}] ${escapeHtml(issue.message)}
      </span>
    `).join('');

    const formattedAmt = (inv.total_amount || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    const safeInvNum = escapeHtml(inv.invoice_number || '-');
    const invDisplay = report.preview_url
      ? `<a class="btn-preview-link" onclick="openInvoicePreview('${report.preview_url}', '${escapeQuotes(report.file_path)}', '${escapeQuotes(inv.invoice_number || '單據')}')" title="點擊以 15 分鐘安全預簽署連結預覽憑證">👁️ ${safeInvNum}</a>`
      : `<strong>${safeInvNum}</strong>`;

    return `
      <tr>
        <td data-label="狀態">${statusBadge}</td>
        <td data-label="類別"><span class="badge ${catClass}">${catText}</span></td>
        <td data-label="單據號碼" class="tabular-nums">${invDisplay}</td>
        <td data-label="開立日期" class="tabular-nums">${escapeHtml(inv.invoice_date || '-')}</td>
        <td data-label="原幣金額" class="tabular-nums" style="font-weight: 700; text-align: right;">${formattedAmt}</td>
        <td data-label="幣別"><span class="badge" style="background: #F1F5F9; color: #334155;">${escapeHtml(inv.currency || 'NT$')}</span></td>
        <td data-label="事由說明">${escapeHtml(inv.purpose || inv.notes || '-')}</td>
        <td data-label="開立商家">${escapeHtml(inv.seller_name || '-')}</td>
        <td data-label="稽核結果">${issuesHtml || '<span style="color: #059669; font-size: 0.875rem; font-weight: 500;">✓ 統編與金額檢核通過</span>'}</td>
      </tr>
    `;
  }).join('');

  document.getElementById('audit-table-wrapper').classList.remove('hidden');
}

function buildHistoryFilterQuery() {
  const params = new URLSearchParams();
  const userFilter = document.getElementById('history-filter-user');
  const statusFilter = document.getElementById('history-filter-status');
  const fromFilter = document.getElementById('history-filter-from');
  const toFilter = document.getElementById('history-filter-to');

  if (userFilter && userFilter.value) params.set('user_id', userFilter.value);
  if (statusFilter && statusFilter.value) params.set('status', statusFilter.value);
  if (fromFilter && fromFilter.value) params.set('date_from', fromFilter.value);
  if (toFilter && toFilter.value) params.set('date_to', toFilter.value);
  return params;
}

// Load History from SQLite
async function loadHistory() {
  const tbody = document.getElementById('history-table-body');
  if (!getAuthToken()) {
    tbody.innerHTML = '<tr><td colspan="12" class="text-center" style="padding: 2rem;">請先登入系統後查看歷史單據記錄</td></tr>';
    return;
  }

  tbody.innerHTML = '<tr><td colspan="12" class="text-center">讀取歷史記錄中...</td></tr>';

  try {
    const params = buildHistoryFilterQuery();
    params.set('limit', '100');
    const response = await authFetch(`/api/history?${params.toString()}`);
    if (!response.ok) throw new Error('無法取得歷史記錄');
    const data = await response.json();

    if (!data.history || data.history.length === 0) {
      tbody.innerHTML = '<tr><td colspan="12" class="text-center" style="padding: 2rem;">查無符合條件的報銷發票紀錄</td></tr>';
      return;
    }

    tbody.innerHTML = data.history.map(item => {
      let statusBadge = '';
      if (item.audit_status === 'APPROVED') statusBadge = '<span class="badge badge-approved">APPROVED</span>';
      else if (item.audit_status === 'WARNING') statusBadge = '<span class="badge badge-warning">WARNING</span>';
      else statusBadge = '<span class="badge badge-rejected">REJECTED</span>';

      const cat = item.expense_category || 'E';
      const catClass = `badge-${cat.toLowerCase()}`;
      const amt = (item.total_amount || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      const uploaderDisplay = escapeHtml(item.uploader_name || item.uploader_email || '-');
      const previewBtn = item.has_file
        ? `<button type="button" class="btn-xs-preview" onclick="openReceiptPreview(${item.id}, ${JSON.stringify(item.file_name || 'receipt')}, ${JSON.stringify(item.file_mime_type || '')})">查看原圖</button>`
        : '<span style="color: var(--text-subtle); font-size: 0.8125rem;">無原檔</span>';

      return `
        <tr>
          <td data-label="ID" class="tabular-nums">#${item.id}</td>
          <td data-label="狀態">${statusBadge}</td>
          <td data-label="單據號碼" class="tabular-nums"><strong>${escapeHtml(item.invoice_number || '-')}</strong></td>
          <td data-label="單據日期" class="tabular-nums">${escapeHtml(item.invoice_date || '-')}</td>
          <td data-label="原幣金額" class="tabular-nums" style="font-weight: 700; text-align: right;">${amt}</td>
          <td data-label="幣別">${escapeHtml(item.currency || 'NT$')}</td>
          <td data-label="類別"><span class="badge ${catClass}">${cat}</span></td>
          <td data-label="開立商家">${escapeHtml(item.seller_name || '-')}</td>
          <td data-label="出差用途">${escapeHtml(item.purpose || '-')}</td>
          <td data-label="上傳同仁">${uploaderDisplay}</td>
          <td data-label="原圖/明細">${previewBtn}</td>
          <td data-label="審核時間" class="tabular-nums" style="color: var(--text-subtle); font-size: 0.8125rem;">${escapeHtml(item.created_at || '-')}</td>
        </tr>
      `;
    }).join('');

  } catch (error) {
    tbody.innerHTML = `<tr><td colspan="12" class="text-center text-red">載入失敗: ${escapeHtml(error.message)}</td></tr>`;
  }

  loadHistoryStats();
}

async function exportFilteredHistoryExcel() {
  if (!getAuthToken()) {
    openLoginModal();
    return;
  }
  const params = buildHistoryFilterQuery();
  const qs = params.toString();
  const url = qs ? `/api/history/export?${qs}` : '/api/history/export';
  try {
    await downloadBlobWithAuth(url, 'Travel_Expenses_Report_Filtered.xlsx');
  } catch (err) {
    alert(`匯出失敗: ${err.message}`);
  }
}

async function openReceiptPreview(recordId, fileName, mimeType) {
  const modal = document.getElementById('receipt-preview-modal');
  const titleEl = document.getElementById('receipt-preview-title');
  const viewport = document.getElementById('receipt-preview-viewport');
  const downloadBtn = document.getElementById('btn-download-original-receipt');

  if (activePreviewObjectUrl) {
    URL.revokeObjectURL(activePreviewObjectUrl);
    activePreviewObjectUrl = null;
  }

  if (titleEl) titleEl.textContent = `憑證原圖預覽 — ${fileName || '#' + recordId}`;
  if (viewport) viewport.innerHTML = '<div class="text-center">載入憑證中...</div>';
  if (modal) modal.classList.remove('hidden');

  try {
    const res = await authFetch(`/api/records/${recordId}/file`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || '無法讀取原始憑證');
    }
    const blob = await res.blob();
    const resolvedMime = mimeType || blob.type || '';
    activePreviewObjectUrl = URL.createObjectURL(blob);

    if (downloadBtn) {
      downloadBtn.href = activePreviewObjectUrl;
      downloadBtn.download = fileName || `receipt_${recordId}`;
    }

    if (resolvedMime.includes('pdf') || (fileName && fileName.toLowerCase().endsWith('.pdf'))) {
      viewport.innerHTML = `<iframe src="${activePreviewObjectUrl}" title="PDF Preview"></iframe>`;
    } else if (resolvedMime.startsWith('image/')) {
      viewport.innerHTML = `<img src="${activePreviewObjectUrl}" alt="${escapeHtml(fileName)}" />`;
    } else {
      const textPreview = await blob.text();
      viewport.innerHTML = `<pre style="color:#1E293B;font-size:0.875rem;white-space:pre-wrap;width:100%;">${escapeHtml(textPreview.slice(0, 4000))}</pre>`;
    }
  } catch (err) {
    if (viewport) {
      viewport.innerHTML = `<div class="text-red">${escapeHtml(err.message)}</div>`;
    }
  }
}

function closeReceiptPreviewModal() {
  const modal = document.getElementById('receipt-preview-modal');
  const viewport = document.getElementById('receipt-preview-viewport');
  if (modal) modal.classList.add('hidden');
  if (viewport) viewport.innerHTML = '';
  if (activePreviewObjectUrl) {
    URL.revokeObjectURL(activePreviewObjectUrl);
    activePreviewObjectUrl = null;
  }
}

async function loadHistoryStats() {
  if (!getAuthToken()) return;
  try {
    const res = await authFetch('/api/stats');
    if (!res.ok) return;
    const stats = await res.json();

    const histTotal = document.getElementById('hist-total');
    if (histTotal) histTotal.textContent = stats.total_invoices || 0;

    const approvedCount = (stats.status_counts && stats.status_counts.APPROVED) || 0;
    const totalCount = stats.total_invoices || 0;
    const rate = totalCount > 0 ? Math.round((approvedCount / totalCount) * 100) : 100;
    
    const histRate = document.getElementById('hist-approved-rate');
    if (histRate) histRate.textContent = `${rate}%`;

    const currCount = Object.keys(stats.currency_totals || {}).length;
    const histCurr = document.getElementById('hist-currencies-count');
    if (histCurr) histCurr.textContent = currCount;

  } catch (e) {
    console.error("Failed to load history stats", e);
  }
}

function escapeHtml(text) {
  if (!text) return '';
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

/* ==========================================================================
   Google Stitch SaaS Analytics: Count-Up Animation & Smooth Canvas Chart
   ========================================================================== */

let analyticsInitialized = false;
let currentChartRange = '7D';
let isSkeletonActive = false;

const chartDatasets = {
  '7D': [
    { date: '08/27', count: 82, tw: 52, over: 30, rate: 98.8 },
    { date: '08/28', count: 114, tw: 74, over: 40, rate: 99.1 },
    { date: '08/29', count: 96, tw: 60, over: 36, rate: 100.0 },
    { date: '08/30', count: 148, tw: 98, over: 50, rate: 99.3 },
    { date: '08/31', count: 188, tw: 120, over: 68, rate: 99.5 },
    { date: '09/01', count: 242, tw: 160, over: 82, rate: 99.6 },
    { date: '09/02', count: 215, tw: 135, over: 80, rate: 99.8 }
  ],
  '30D': [
    { date: '08/04', count: 420, tw: 290, over: 130, rate: 99.0 },
    { date: '08/11', count: 560, tw: 380, over: 180, rate: 99.2 },
    { date: '08/18', count: 680, tw: 440, over: 240, rate: 99.5 },
    { date: '08/25', count: 790, tw: 510, over: 280, rate: 99.7 },
    { date: '09/01', count: 865, tw: 560, over: 305, rate: 99.8 }
  ],
  '90D': [
    { date: '2026-Q1', count: 2450, tw: 1680, over: 770, rate: 98.9 },
    { date: '2026-Q2', count: 3200, tw: 2150, over: 1050, rate: 99.4 },
    { date: '2026-Q3 (累計)', count: 4120, tw: 2780, over: 1340, rate: 99.8 }
  ]
};

function initAnalyticsDashboard() {
  if (analyticsInitialized) {
    renderTrendChart();
    return;
  }
  analyticsInitialized = true;

  // Run animated number incrementing (Stitch Count-Up)
  animateCountUp('counter-docs', 2845, 1200, 0);
  animateCountUp('counter-amount', 842500, 1400, 0);
  animateCountUp('counter-latency', 1.38, 1000, 2);
  animateCountUp('counter-rate', 99.8, 1100, 1);

  // Setup canvas chart
  setTimeout(() => {
    renderTrendChart();
    setupChartHover();
  }, 100);
}

// Framer-Motion style spring/ease count-up animation
function animateCountUp(elementId, targetVal, duration = 1200, decimals = 0) {
  const el = document.getElementById(elementId);
  if (!el) return;

  let start = 0;
  const startTime = performance.now();

  function update(currentTime) {
    const elapsed = currentTime - startTime;
    const progress = Math.min(elapsed / duration, 1);
    
    // Ease-out cubic: 1 - pow(1 - progress, 3)
    const eased = 1 - Math.pow(1 - progress, 3);
    const current = start + (targetVal - start) * eased;

    el.textContent = current.toLocaleString(undefined, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals
    });

    if (progress < 1) {
      requestAnimationFrame(update);
    } else {
      el.textContent = targetVal.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
      });
    }
  }

  requestAnimationFrame(update);
}

// Toggle custom skeleton loader
function toggleSkeletonView() {
  isSkeletonActive = !isSkeletonActive;
  const skeletonEl = document.getElementById('analytics-skeleton-view');
  const liveEl = document.getElementById('analytics-live-view');
  const btnText = document.getElementById('skeleton-btn-text');

  if (isSkeletonActive) {
    skeletonEl.classList.remove('hidden');
    liveEl.classList.add('hidden');
    btnText.textContent = '返回即時數據畫面';
  } else {
    skeletonEl.classList.add('hidden');
    liveEl.classList.remove('hidden');
    btnText.textContent = '預覽骨架屏 (Skeleton)';
    renderTrendChart();
  }
}

// Change chart time range
function setChartRange(range) {
  currentChartRange = range;
  
  document.querySelectorAll('.range-btn').forEach(btn => btn.classList.remove('active'));
  const activeBtn = document.getElementById(`range-btn-${range.toLowerCase()}`);
  if (activeBtn) activeBtn.classList.add('active');

  const data = chartDatasets[range];
  const total = data.reduce((sum, item) => sum + item.count, 0);
  const countEl = document.getElementById('chart-total-count');
  if (countEl) countEl.textContent = total.toLocaleString();

  renderTrendChart();
}

// Render high-DPI Smooth Bezier Curve Canvas Chart
let chartPointsCache = [];

function renderTrendChart() {
  const canvas = document.getElementById('trendChartCanvas');
  if (!canvas) return;

  const ctx = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();

  canvas.width = rect.width * dpr;
  canvas.height = 250 * dpr;
  ctx.scale(dpr, dpr);

  const width = rect.width;
  const height = 250;
  const padding = { top: 25, right: 25, bottom: 40, left: 45 };

  ctx.clearRect(0, 0, width, height);

  const data = chartDatasets[currentChartRange] || chartDatasets['7D'];
  const maxVal = Math.max(...data.map(d => d.count)) * 1.15;
  const minVal = 0;

  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  // 1. Draw horizontal grid lines & Y-axis labels
  ctx.strokeStyle = 'rgba(226, 232, 240, 0.9)';
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  ctx.fillStyle = '#64748B';
  ctx.font = '12px Inter, sans-serif';
  ctx.textAlign = 'right';

  const gridSteps = 4;
  for (let i = 0; i <= gridSteps; i++) {
    const y = padding.top + (chartH / gridSteps) * i;
    const val = Math.round(maxVal - (maxVal / gridSteps) * i);
    
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(width - padding.right, y);
    ctx.stroke();

    ctx.fillText(val.toString(), padding.left - 10, y + 4);
  }
  ctx.setLineDash([]);

  // 2. Compute point coordinates
  const stepX = chartW / (data.length - 1);
  const points = data.map((d, index) => {
    const x = padding.left + index * stepX;
    const y = padding.top + chartH - (d.count / maxVal) * chartH;
    return { x, y, data: d };
  });
  chartPointsCache = points;

  // 3. Draw X-axis labels
  ctx.textAlign = 'center';
  ctx.fillStyle = '#64748B';
  points.forEach(p => {
    ctx.fillText(p.data.date, p.x, height - 12);
  });

  if (points.length < 2) return;

  // 4. Draw Soft Gradient Fill Area (using smooth monotone cubic Bezier)
  const grad = ctx.createLinearGradient(0, padding.top, 0, padding.top + chartH);
  grad.addColorStop(0, 'rgba(37, 99, 235, 0.16)');
  grad.addColorStop(0.6, 'rgba(37, 99, 235, 0.04)');
  grad.addColorStop(1, 'rgba(37, 99, 235, 0.0)');

  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);

  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i === 0 ? 0 : i - 1];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] || p2;

    const cp1x = p1.x + (p2.x - p0.x) / 6;
    const cp1y = p1.y + (p2.y - p0.y) / 6;
    const cp2x = p2.x - (p3.x - p1.x) / 6;
    const cp2y = p2.y - (p3.y - p1.y) / 6;

    ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p2.x, p2.y);
  }

  // Close area path for fill
  ctx.lineTo(points[points.length - 1].x, padding.top + chartH);
  ctx.lineTo(points[0].x, padding.top + chartH);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // 5. Draw the Stroke Line with clean SaaS accent
  const strokeGrad = ctx.createLinearGradient(padding.left, 0, width - padding.right, 0);
  strokeGrad.addColorStop(0, '#3B82F6');
  strokeGrad.addColorStop(0.5, '#2563EB');
  strokeGrad.addColorStop(1, '#4F46E5');

  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);

  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i === 0 ? 0 : i - 1];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] || p2;

    const cp1x = p1.x + (p2.x - p0.x) / 6;
    const cp1y = p1.y + (p2.y - p0.y) / 6;
    const cp2x = p2.x - (p3.x - p1.x) / 6;
    const cp2y = p2.y - (p3.y - p1.y) / 6;

    ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, p2.x, p2.y);
  }

  ctx.strokeStyle = strokeGrad;
  ctx.lineWidth = 2.5;
  ctx.shadowColor = 'rgba(37, 99, 235, 0.15)';
  ctx.shadowBlur = 4;
  ctx.stroke();
  ctx.shadowBlur = 0; // reset shadow

  // 6. Draw subtle node dots
  points.forEach(p => {
    ctx.beginPath();
    ctx.arc(p.x, p.y, 3.5, 0, Math.PI * 2);
    ctx.fillStyle = '#FFFFFF';
    ctx.fill();
    ctx.strokeStyle = '#2563EB';
    ctx.lineWidth = 2;
    ctx.stroke();
  });
}

// Setup interactive tooltip on Canvas mousemove
function setupChartHover() {
  const canvas = document.getElementById('trendChartCanvas');
  const tooltip = document.getElementById('chart-custom-tooltip');
  if (!canvas || !tooltip) return;

  canvas.addEventListener('mousemove', (e) => {
    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    
    // Find closest point
    if (!chartPointsCache.length) return;

    let closest = chartPointsCache[0];
    let minDiff = Math.abs(mouseX - closest.x);

    for (let p of chartPointsCache) {
      const diff = Math.abs(mouseX - p.x);
      if (diff < minDiff) {
        minDiff = diff;
        closest = p;
      }
    }

    if (minDiff < 45) {
      const d = closest.data;
      tooltip.innerHTML = `
        <div class="tooltip-date">📅 ${d.date}</div>
        <div class="tooltip-row main">
          <span>辨識單據總數</span>
          <span class="tabular-nums">${d.count} 份</span>
        </div>
        <div class="tooltip-row sub">
          <span>↳ 台灣發票/高鐵</span>
          <span class="tabular-nums">${d.tw} 份</span>
        </div>
        <div class="tooltip-row sub">
          <span>↳ 海外單據(中/泰/歐)</span>
          <span class="tabular-nums">${d.over} 份</span>
        </div>
        <div class="tooltip-row rate">
          <span>合規率</span>
          <span class="tabular-nums">${d.rate}%</span>
        </div>
      `;
      tooltip.style.left = `${closest.x}px`;
      tooltip.style.top = `${closest.y}px`;
      tooltip.classList.remove('hidden');
    } else {
      tooltip.classList.add('hidden');
    }
  });

  canvas.addEventListener('mouseleave', () => {
    tooltip.classList.add('hidden');
  });

  let resizeDebounceTimer = null;
  window.addEventListener('resize', () => {
    if (isSkeletonActive) return;
    clearTimeout(resizeDebounceTimer);
    resizeDebounceTimer = setTimeout(() => {
      renderTrendChart();
    }, 100);
  });
}

// ==========================================================================
// SMALL-TEAM FINANCIAL APPROVAL WORKFLOW & STATE MACHINE CONTROLLER (方案 A)
// ==========================================================================

let currentApprovalFilter = 'ALL';

async function loadApprovalSummaryBadge() {
  try {
    const res = await fetch('/api/approval/summary');
    if (!res.ok) return;
    const data = await res.json();
    
    // Update Nav Tab Badge
    const navBadge = document.getElementById('nav-pending-badge');
    if (navBadge) {
      if (data.submitted_count > 0) {
        navBadge.textContent = data.submitted_count;
        navBadge.classList.remove('hidden');
      } else {
        navBadge.classList.add('hidden');
      }
    }

    // Update KPI summary cards
    const subCount = document.getElementById('appr-kpi-submitted-count');
    const subAmt = document.getElementById('appr-kpi-submitted-amt');
    const appCount = document.getElementById('appr-kpi-approved-count');
    const appAmt = document.getElementById('appr-kpi-approved-amt');
    const retCount = document.getElementById('appr-kpi-returned-count');
    const paidCount = document.getElementById('appr-kpi-paid-count');
    const paidAmt = document.getElementById('appr-kpi-paid-amt');

    if (subCount) subCount.textContent = data.submitted_count;
    if (subAmt) subAmt.textContent = Number(data.submitted_amount).toLocaleString();
    if (appCount) appCount.textContent = data.approved_count;
    if (appAmt) appAmt.textContent = Number(data.approved_amount).toLocaleString();
    if (retCount) retCount.textContent = data.returned_count;
    if (paidCount) paidCount.textContent = data.paid_count;
    if (paidAmt) paidAmt.textContent = Number(data.paid_amount).toLocaleString();
  } catch (e) {
    console.warn('Failed to load approval summary badge:', e);
  }
}

async function loadApprovalData() {
  await loadApprovalSummaryBadge();
  const tbody = document.getElementById('approval-table-body');
  const countLabel = document.getElementById('appr-table-count-label');
  if (!tbody) return;

  tbody.innerHTML = '<tr><td colspan="9" class="text-center" style="padding: 2rem;"><div class="spinner" style="margin: 0 auto 0.5rem;"></div>載入審批單據中...</td></tr>';

  try {
    const res = await fetch(`/api/approval/list?status=${encodeURIComponent(currentApprovalFilter)}`);
    if (!res.ok) throw new Error('API request failed');
    const data = await res.json();
    const records = data.records || [];

    if (countLabel) {
      countLabel.textContent = `共 ${records.length} 筆單據 (篩選：${currentApprovalFilter})`;
    }

    if (records.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="9" class="text-center" style="padding: 2.5rem; color: var(--text-subtle);">
            <div style="font-size: 1.5rem; margin-bottom: 0.5rem;">📭</div>
            目前無符合【${currentApprovalFilter}】條件的報銷單據
          </td>
        </tr>
      `;
      return;
    }

    tbody.innerHTML = records.map(r => {
      const status = (r.approval_status || 'SUBMITTED').toUpperCase();
      let statusBadge = '';
      if (status === 'SUBMITTED') {
        statusBadge = '<span class="badge-appr badge-appr-submitted">待審核</span>';
      } else if (status === 'APPROVED') {
        statusBadge = '<span class="badge-appr badge-appr-approved">已核准 · 待撥款</span>';
      } else if (status === 'RETURNED') {
        statusBadge = '<span class="badge-appr badge-appr-returned">退件補件</span>';
      } else if (status === 'REJECTED') {
        statusBadge = '<span class="badge-appr badge-appr-rejected">已駁回</span>';
      } else if (status === 'PAID') {
        statusBadge = '<span class="badge-appr badge-appr-paid">已撥款結案</span>';
      }

      const auditStatus = (r.audit_status || 'APPROVED').toUpperCase();
      let auditBadge = '';
      if (auditStatus === 'APPROVED') {
        auditBadge = '<span class="badge-status badge-approved" style="font-size:0.7rem;">通過</span>';
      } else if (auditStatus === 'WARNING') {
        auditBadge = '<span class="badge-status badge-warning" style="font-size:0.7rem;">警示</span>';
      } else {
        auditBadge = '<span class="badge-status badge-rejected" style="font-size:0.7rem;">違規</span>';
      }

      // Actions Group
      let actionButtons = '';
      const safeInv = escapeHtml(r.invoice_number || '無號碼');
      const safeApplicant = escapeHtml(r.applicant_name || '林專員');
      const safeSeller = escapeHtml(r.seller_name || '-');
      const safeCurrency = escapeHtml(r.currency || 'NT$');

      if (status === 'SUBMITTED') {
        actionButtons = `
          <div class="appr-action-group">
            <button class="btn-appr btn-appr-approve" onclick="quickApprove(${r.id})" title="快速核准通過">核准</button>
            <button class="btn-appr btn-appr-return" onclick="openReviewModal(${r.id}, '${safeInv}', ${r.total_amount}, '${safeCurrency}', '${safeApplicant}', '${safeSeller}')" title="填寫審核意見或退件原因">審核/退件</button>
          </div>
        `;
      } else if (status === 'APPROVED') {
        actionButtons = `
          <div class="appr-action-group">
            <button class="btn-appr btn-appr-pay" onclick="markAsPaidDirect(${r.id})" title="標記出納已完成匯款撥款">標記已撥款</button>
          </div>
        `;
      } else if (status === 'RETURNED') {
        actionButtons = `
          <div class="appr-action-group">
            <button class="btn-appr btn-appr-resubmit" onclick="resubmitDirect(${r.id})" title="同仁補件後重送">補件重送</button>
          </div>
        `;
      } else if (status === 'PAID') {
        actionButtons = `<span class="text-xs text-subtle" title="經辦人：${escapeHtml(r.paid_by || '出納')}">✅ ${escapeHtml(r.paid_by || '出納撥款')}</span>`;
      } else if (status === 'REJECTED') {
        actionButtons = `<span class="text-xs text-rose">🚫 審核不合規</span>`;
      }

      const notesDisplay = r.review_notes ? escapeHtml(r.review_notes) : (r.reviewed_at ? '<span class="text-subtle text-xs">審核合規通過</span>' : '<span class="text-subtle text-xs">-</span>');

      return `
        <tr>
          <td data-label="序號"><span class="text-xs tabular-nums text-subtle">#${r.id}</span></td>
          <td data-label="審批狀態">${statusBadge}</td>
          <td data-label="AI 稽核">${auditBadge}</td>
          <td data-label="申請人">
            <div style="font-weight: 600; font-size: 0.875rem;">${escapeHtml(r.applicant_name || '林專員')}</div>
            <div class="text-xs text-subtle">${escapeHtml(r.applicant_email || '')}</div>
          </td>
          <td data-label="單據號碼">
            <div style="font-weight: 600; color: #2563EB;">${escapeHtml(r.invoice_number || '無號碼')}</div>
            <div class="text-xs text-subtle">${escapeHtml(r.document_type || '發票')} · ${escapeHtml(r.invoice_date || '')}</div>
          </td>
          <td data-label="開立廠商">
            <div>${escapeHtml(r.seller_name || '-')}</div>
            <div class="text-xs text-subtle">${escapeHtml(r.purpose || '-')}</div>
          </td>
          <td data-label="報銷金額">
            <div style="font-weight: 700; font-family: 'Inter', sans-serif;">${safeCurrency} ${Number(r.total_amount || 0).toLocaleString()}</div>
          </td>
          <td data-label="審核備註">
            <div style="font-size: 0.8125rem; max-width: 240px; word-break: break-word;">${notesDisplay}</div>
          </td>
          <td data-label="審批操作">${actionButtons}</td>
        </tr>
      `;
    }).join('');

  } catch (e) {
    console.error('Failed to load approval records:', e);
    tbody.innerHTML = `<tr><td colspan="9" class="text-center text-rose" style="padding: 1.5rem;">載入失敗: ${escapeHtml(e.message)}</td></tr>`;
  }
}

function setApprovalFilter(status) {
  currentApprovalFilter = status;
  document.querySelectorAll('.filter-pill').forEach(btn => btn.classList.remove('active'));
  const btnId = `appr-filter-${status.toLowerCase()}`;
  const targetBtn = document.getElementById(btnId);
  if (targetBtn) targetBtn.classList.add('active');
  loadApprovalData();
}

// Quick Actions
async function quickApprove(recordId) {
  const token = localStorage.getItem('auth_token');
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  try {
    const res = await fetch(`/api/approval/${recordId}/review`, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({ action: 'approve', notes: '主管/財務審核通過' })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`核准失敗: ${err.detail || '未知錯誤'}`);
      return;
    }
    await loadApprovalData();
  } catch (e) {
    alert(`操作錯誤: ${e.message}`);
  }
}

async function markAsPaidDirect(recordId) {
  const payerName = prompt('請輸入出納撥款備註或匯款資訊 (選填)：', '台新銀行企業網銀轉帳');
  if (payerName === null) return;

  const token = localStorage.getItem('auth_token');
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  try {
    const res = await fetch(`/api/approval/${recordId}/mark-paid`, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({ paid_by: payerName || '出納處已撥款' })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`標記撥款失敗: ${err.detail || '未知錯誤'}`);
      return;
    }
    await loadApprovalData();
  } catch (e) {
    alert(`操作錯誤: ${e.message}`);
  }
}

async function resubmitDirect(recordId) {
  const note = prompt('請輸入補件重送說明：', '已補齊憑證與相關證明');
  if (note === null) return;

  const token = localStorage.getItem('auth_token');
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  try {
    const res = await fetch(`/api/approval/${recordId}/resubmit`, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({ notes: note })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`重送失敗: ${err.detail || '未知錯誤'}`);
      return;
    }
    await loadApprovalData();
  } catch (e) {
    alert(`操作錯誤: ${e.message}`);
  }
}

// Review Modal
function openReviewModal(recordId, invNum, amt, curr, applicant, seller) {
  const modal = document.getElementById('review-modal');
  const recInput = document.getElementById('review-record-id');
  const summaryBox = document.getElementById('review-target-summary');
  const notesArea = document.getElementById('review-notes');

  if (recInput) recInput.value = recordId;
  if (notesArea) notesArea.value = '';

  if (summaryBox) {
    summaryBox.innerHTML = `
      <div><strong>單據序號：</strong> #${recordId} (${invNum})</div>
      <div><strong>報銷金額：</strong> <span class="text-accent" style="font-weight:700;">${curr} ${Number(amt).toLocaleString()}</span></div>
      <div><strong>申請人：</strong> ${applicant} ｜ <strong>開立商家：</strong> ${seller}</div>
    `;
  }

  // Reset radio to approve
  const approveRadio = document.querySelector('input[name="review_action"][value="approve"]');
  if (approveRadio) approveRadio.checked = true;
  onReviewActionChange();

  if (modal) modal.classList.remove('hidden');
}

function closeReviewModal() {
  const modal = document.getElementById('review-modal');
  if (modal) modal.classList.add('hidden');
}

function onReviewActionChange() {
  const selected = document.querySelector('input[name="review_action"]:checked')?.value;
  const presets = document.getElementById('quick-return-presets');
  const reqLabel = document.getElementById('review-notes-required');
  const notesArea = document.getElementById('review-notes');

  if (selected === 'return') {
    if (presets) presets.classList.remove('hidden');
    if (reqLabel) reqLabel.classList.remove('hidden');
    if (notesArea && !notesArea.value) notesArea.placeholder = '請務必填寫退件補件原因（例如：請補附高鐵車票證明）...';
  } else if (selected === 'reject') {
    if (presets) presets.classList.add('hidden');
    if (reqLabel) reqLabel.classList.add('hidden');
    if (notesArea && !notesArea.value) notesArea.placeholder = '請填寫駁回不予核銷原因...';
  } else {
    if (presets) presets.classList.add('hidden');
    if (reqLabel) reqLabel.classList.add('hidden');
    if (notesArea && !notesArea.value) notesArea.placeholder = '審核合規通過備註 (選填)...';
  }
}

function applyPresetReason(reason) {
  const notesArea = document.getElementById('review-notes');
  if (notesArea) {
    notesArea.value = reason;
    notesArea.focus();
  }
}

async function handleReviewSubmit(e) {
  e.preventDefault();
  const recordId = document.getElementById('review-record-id')?.value;
  const action = document.querySelector('input[name="review_action"]:checked')?.value;
  const notes = document.getElementById('review-notes')?.value;

  if (action === 'return' && (!notes || !notes.trim())) {
    alert('退件補件時必須填寫具體退件原因！');
    return;
  }

  const token = localStorage.getItem('auth_token');
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  try {
    const res = await fetch(`/api/approval/${recordId}/review`, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({ action: action, notes: notes })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`審核提交失敗: ${err.detail || '未知錯誤'}`);
      return;
    }
    closeReviewModal();
    await loadApprovalData();
  } catch (err) {
    alert(`送出失敗: ${err.message}`);
  }
}

function escapeQuotes(str) {
  if (!str) return '';
  return String(str)
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '&quot;');
}

// ==========================================================================
// STORAGE FOOTPRINT & TTL RETENTION POLICY CONTROLLER (面向四)
// ==========================================================================

async function loadStorageStats() {
  try {
    const res = await fetch('/api/storage/stats');
    if (!res.ok) return;
    const stats = await res.json();

    const totalMb = document.getElementById('storage-stat-total-mb');
    const sessCount = document.getElementById('storage-stat-sessions-count');
    const filesCount = document.getElementById('storage-stat-files-count');
    const dbMb = document.getElementById('storage-stat-db-mb');
    const badge = document.getElementById('storage-provider-badge');

    if (totalMb) totalMb.textContent = stats.total_mb || '0.0';
    if (sessCount) sessCount.textContent = stats.sessions_folder_count || '0';
    if (filesCount) filesCount.textContent = stats.sessions_files_count || '0';
    if (dbMb) dbMb.textContent = stats.database_storage_mb || '0.0';
    if (badge) badge.textContent = stats.provider_type || 'LocalStorage';
  } catch (e) {
    console.warn('Failed to load storage stats:', e);
  }
}

async function triggerStorageCleanup() {
  const hours = prompt('請輸入要清理多久以前的未送審暫存檔 (小時)：', '72');
  if (hours === null) return;
  const numHours = parseInt(hours, 10) || 72;

  const token = localStorage.getItem('auth_token');
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  try {
    const res = await fetch('/api/storage/cleanup', {
      method: 'POST',
      headers: headers,
      body: JSON.stringify({ max_age_hours: numHours })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`清理失敗: ${err.detail || '未知錯誤'}`);
      return;
    }
    const data = await res.json();
    alert(data.message || '清理完成！');
    await loadStorageStats();
  } catch (e) {
    alert(`清理操作失敗: ${e.message}`);
  }
}

// ==========================================================================
// SECURE INVOICE PREVIEW MODAL (15-MIN PRESIGNED URL)
// ==========================================================================

function openInvoicePreview(previewUrl, filePath, title) {
  const modal = document.getElementById('invoice-preview-modal');
  const titleEl = document.getElementById('preview-modal-title');
  const metaEl = document.getElementById('preview-meta-filename');
  const container = document.getElementById('preview-media-container');
  const dlBtn = document.getElementById('btn-preview-download-raw');

  if (!modal || !container) return;

  const fileName = filePath ? filePath.split('/').pop() : '憑證檔案';
  if (titleEl) titleEl.textContent = `單據憑證安全預覽：${title || fileName}`;
  if (metaEl) metaEl.textContent = `檔案名稱：${fileName} ｜ 安全通道：HMAC-SHA256 Signed Token`;
  if (dlBtn) dlBtn.href = previewUrl;

  const isPdf = fileName.toLowerCase().endsWith('.pdf');

  if (isPdf) {
    container.innerHTML = `<iframe src="${previewUrl}" title="PDF 憑證預覽"></iframe>`;
  } else {
    container.innerHTML = `
      <img src="${previewUrl}" alt="發票收據憑證" onerror="this.onerror=null; this.parentElement.innerHTML='<div style=\\'color:#475569; padding:2rem; text-align:center;\\'>⚠️ 暫存憑證圖檔已過期或非圖檔格式，請重新送審</div>';" />
    `;
  }

  modal.classList.remove('hidden');
}

function closeInvoicePreview() {
  const modal = document.getElementById('invoice-preview-modal');
  const container = document.getElementById('preview-media-container');
  if (container) container.innerHTML = '';
  if (modal) modal.classList.add('hidden');
}




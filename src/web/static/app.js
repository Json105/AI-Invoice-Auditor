/**
 * AI-Invoice-Auditor Front-end Controller
 * Handles drag-and-drop file ingestion, engine selection, async auditing,
 * real-time UI rendering, and SQLite historical tracking.
 */

let selectedFiles = [];
let currentSession = null;
let serverHasKey = false;
let serverHasOpenRouterKey = false;
let serverAiPolicy = null;
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
    if (!document.getElementById('auth-gate-screen')) {
      openLoginModal();
    }
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
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
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
// Enterprise Authentication, Auth Gate & RBAC Role Views
// ==========================================================================
let currentUser = null;

function applyRolePermissions() {
  const gate = document.getElementById('auth-gate-screen');
  const shell = document.getElementById('app-shell');
  const approvalTabBtn = document.getElementById('tab-approval-btn');
  const analyticsTabBtn = document.getElementById('tab-analytics-btn');
  const adminLink = document.getElementById('link-admin-panel');

  if (!currentUser) {
    if (gate) gate.classList.remove('hidden');
    if (shell) shell.classList.add('auth-locked');
    return;
  }

  if (gate) gate.classList.add('hidden');
  if (shell) shell.classList.remove('auth-locked');

  const privileged = isPrivilegedRole();
  if (approvalTabBtn) approvalTabBtn.classList.toggle('hidden', !privileged);
  if (analyticsTabBtn) analyticsTabBtn.classList.toggle('hidden', !privileged);
  if (adminLink) adminLink.classList.toggle('hidden', !privileged);

  if (!privileged) {
    const activeProtected =
      document.getElementById('tab-approval')?.classList.contains('active') ||
      document.getElementById('tab-analytics')?.classList.contains('active');
    if (activeProtected) {
      switchTab('audit');
    }
  }
  applyAiPolicyUI();
}

async function onAuthenticatedSessionReady() {
  await loadFilterUsersIfPrivileged();
  await loadTripOptions();
  loadHistoryStats();
  loadWatchFolderStatus();
  updateTripsFilterBadge();
  updateApprovalFilterBadge();
  updateHistoryFilterBadge();
  if (isPrivilegedRole()) {
    loadApprovalSummaryBadge();
  }
  const histPane = document.getElementById('tab-history');
  if (histPane && histPane.classList.contains('active')) {
    loadHistory();
  }
  const tripsPane = document.getElementById('tab-trips');
  if (tripsPane && tripsPane.classList.contains('active')) {
    loadTrips();
  }
  const apprPane = document.getElementById('tab-approval');
  if (apprPane && apprPane.classList.contains('active')) {
    loadApprovalData();
  }
}

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
    await onAuthenticatedSessionReady();
  }
}

function renderAuthUI() {
  const btnOpen = document.getElementById('btn-open-login');
  const badge = document.getElementById('user-profile-badge');
  const roleTag = document.getElementById('user-role-tag');
  const nameText = document.getElementById('user-name-display');

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
  } else {
    if (btnOpen) btnOpen.classList.remove('hidden');
    if (badge) badge.classList.add('hidden');
  }
  applyRolePermissions();
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

function _applyDemoCredentials(role, emailInput, passInput, errEl) {
  if (errEl) errEl.classList.add('hidden');
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

function quickFillLogin(role) {
  _applyDemoCredentials(
    role,
    document.getElementById('login-email'),
    document.getElementById('login-password'),
    document.getElementById('login-error-msg')
  );
}

function quickFillGateLogin(role) {
  _applyDemoCredentials(
    role,
    document.getElementById('gate-login-email'),
    document.getElementById('gate-login-password'),
    document.getElementById('gate-login-error')
  );
}

async function _performLogin(email, password, errEl, btnSubmit, onSuccessClose) {
  if (btnSubmit) btnSubmit.disabled = true;
  if (errEl) errEl.classList.add('hidden');

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
    if (onSuccessClose) onSuccessClose();
    await onAuthenticatedSessionReady();
  } catch (e) {
    if (errEl) {
      errEl.textContent = e.message;
      errEl.classList.remove('hidden');
    }
  } finally {
    if (btnSubmit) btnSubmit.disabled = false;
  }
}

async function handleLoginSubmit(event) {
  event.preventDefault();
  await _performLogin(
    document.getElementById('login-email').value,
    document.getElementById('login-password').value,
    document.getElementById('login-error-msg'),
    document.getElementById('btn-login-submit'),
    closeLoginModal
  );
}

async function handleGateLoginSubmit(event) {
  event.preventDefault();
  await _performLogin(
    document.getElementById('gate-login-email').value,
    document.getElementById('gate-login-password').value,
    document.getElementById('gate-login-error'),
    document.getElementById('btn-gate-submit'),
    () => {
      const pw = document.getElementById('gate-login-password');
      if (pw) pw.value = '';
    }
  );
}

function logoutUser() {
  localStorage.removeItem('auth_token');
  currentUser = null;
  const userMenu = document.getElementById('user-profile-badge');
  if (userMenu) userMenu.removeAttribute('open');
  closeLoginModal();
  closeTripDetailModal();
  closeTripModal();
  closeReviewModal();
  closeFxEditModal();
  switchTab('audit');

  const userSelect = document.getElementById('history-filter-user');
  if (userSelect) {
    userSelect.innerHTML = '<option value="">全部同仁</option>';
    userSelect.classList.add('hidden');
  }
  const tripsUserSelect = document.getElementById('trips-filter-user');
  if (tripsUserSelect) {
    tripsUserSelect.innerHTML = '<option value="">全部同仁</option>';
    tripsUserSelect.classList.add('hidden');
  }
  const uploadTripSelect = document.getElementById('upload-trip-select');
  if (uploadTripSelect) {
    uploadTripSelect.innerHTML = '<option value="">不指定專案（單次獨立送審）</option>';
  }
  const histTripSelect = document.getElementById('history-filter-trip');
  if (histTripSelect) {
    histTripSelect.innerHTML = '<option value="">全部出差專案</option>';
  }
  onUploadTripChange();
  renderAuthUI();
  const tbody = document.getElementById('history-table-body');
  if (tbody) {
    tbody.innerHTML = '<tr><td colspan="12" class="text-center" style="padding: 2rem;">請先登入系統後查看歷史單據記錄</td></tr>';
  }
}

async function loadFilterUsersIfPrivileged() {
  const userSelect = document.getElementById('history-filter-user');
  const tripsUserSelect = document.getElementById('trips-filter-user');
  if (!currentUser || !['admin', 'accountant', 'manager'].includes(currentUser.role)) {
    if (userSelect) userSelect.classList.add('hidden');
    if (tripsUserSelect) tripsUserSelect.classList.add('hidden');
    return;
  }
  try {
    const res = await authFetch('/api/auth/users');
    if (!res.ok) return;
    const users = await res.json();
    const optionsHtml = '<option value="">全部同仁</option>' + users.map(u => (
      `<option value="${u.id}">${escapeHtml(u.full_name)} (${escapeHtml(u.email)})</option>`
    )).join('');
    if (userSelect) {
      userSelect.innerHTML = optionsHtml;
      userSelect.classList.remove('hidden');
    }
    if (tripsUserSelect) {
      tripsUserSelect.innerHTML = optionsHtml;
      tripsUserSelect.classList.remove('hidden');
    }
  } catch (e) {
    console.warn('Failed to load filter users:', e);
  }
}

function openChangePasswordModal() {
  const userMenu = document.getElementById('user-profile-badge');
  if (userMenu) userMenu.removeAttribute('open');
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



// Load Server Configuration & Admin AI Policy
function isAdminRole() {
  return Boolean(currentUser && currentUser.role === 'admin');
}

function isModelLockedForCurrentUser() {
  if (!serverAiPolicy) return false;
  return Boolean(serverAiPolicy.lock_model_for_non_admin) && !isAdminRole();
}

function showQuotaExceededNotice(message) {
  const banner = document.getElementById('quota-exceeded-banner');
  const textEl = document.getElementById('quota-exceeded-text');
  if (textEl && message) {
    textEl.textContent = `⚠️ ${message}`;
  }
  if (banner) banner.classList.remove('hidden');
}

function hideQuotaExceededNotice() {
  const banner = document.getElementById('quota-exceeded-banner');
  if (banner) banner.classList.add('hidden');
}

function applyAiPolicyUI() {
  const adminControls = document.getElementById('admin-engine-controls');
  const managedBanner = document.getElementById('managed-ai-policy-banner');
  const engineHint = document.getElementById('engine-hint');
  const badgeLabel = document.getElementById('engine-badge-label');
  const advTitle = document.getElementById('advanced-settings-title');
  const engineSelect = document.getElementById('engine-select');
  const orSelect = document.getElementById('openrouter-model-select');

  if (serverAiPolicy && engineSelect) {
    const policyEngine = serverAiPolicy.active_engine;
    if (policyEngine && Array.from(engineSelect.options).some(o => o.value === policyEngine)) {
      engineSelect.value = policyEngine;
    }
    if (orSelect && policyEngine === 'openrouter' && serverAiPolicy.active_model) {
      if (Array.from(orSelect.options).some(o => o.value === serverAiPolicy.active_model)) {
        orSelect.value = serverAiPolicy.active_model;
      }
    }
  } else if (engineSelect && serverHasOpenRouterKey && !serverHasKey) {
    engineSelect.value = 'openrouter';
  }

  onEngineChange();

  // Keep non-admin UX completely unaware of model internals; notify only when quota is exhausted
  if (managedBanner) managedBanner.classList.add('hidden');

  if (isModelLockedForCurrentUser()) {
    if (adminControls) adminControls.classList.add('hidden');
    if (engineHint) engineHint.classList.add('hidden');
    if (badgeLabel) badgeLabel.classList.add('hidden');
    if (advTitle) advTitle.textContent = '⚙️ 進階設定：匯率模式 · 批次拆頁 · 事務機監控資料夾';
  } else {
    if (adminControls) adminControls.classList.remove('hidden');
    if (engineHint) engineHint.classList.remove('hidden');
    if (badgeLabel) badgeLabel.classList.remove('hidden');
    if (advTitle) advTitle.textContent = '⚙️ 進階設定：匯率模式 · 批次拆頁 · AI 引擎 · 事務機監控資料夾';
  }
}

async function loadServerConfig() {
  try {
    const res = await fetch('/api/config');
    if (!res.ok) return;
    const cfg = await res.json();
    serverHasKey = Boolean(cfg.has_server_api_key);
    serverHasOpenRouterKey = Boolean(cfg.has_openrouter_api_key);
    serverAiPolicy = cfg.ai_policy || null;

    const orSelect = document.getElementById('openrouter-model-select');
    const govOrSelect = document.getElementById('gov-openrouter-model-select');
    if (Array.isArray(cfg.openrouter_models) && cfg.openrouter_models.length > 0) {
      const optionsHtml = cfg.openrouter_models.map(m => (
        `<option value="${escapeHtml(m.id)}" ${m.id === cfg.default_openrouter_model ? 'selected' : ''}>${escapeHtml(m.label)}</option>`
      )).join('');
      if (orSelect) orSelect.innerHTML = optionsHtml;
      if (govOrSelect) govOrSelect.innerHTML = optionsHtml;
    }
    applyAiPolicyUI();
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
  } else if (tabName === 'trips') {
    document.getElementById('tab-trips-btn').classList.add('active');
    document.getElementById('tab-trips').classList.add('active');
    loadTrips();
  } else if (tabName === 'approval') {
    document.getElementById('tab-approval-btn').classList.add('active');
    document.getElementById('tab-approval').classList.add('active');
    loadApprovalData();
  } else if (tabName === 'analytics') {
    document.getElementById('tab-analytics-btn').classList.add('active');
    document.getElementById('tab-analytics').classList.add('active');
    initAnalyticsDashboard();
    loadStorageStats();
    loadAdminAiGovernance();
  } else if (tabName === 'history') {
    document.getElementById('tab-history-btn').classList.add('active');
    document.getElementById('tab-history').classList.add('active');
    loadTripOptions();
    loadHistory();
  }
}

function onFxModeChange() {
  const mode = document.getElementById('fx-mode-select')?.value || 'auto_bot';
  const rateInput = document.getElementById('manual-fx-rate-input');
  if (rateInput) {
    rateInput.classList.toggle('hidden', mode !== 'manual');
  }
}

async function loadWatchFolderStatus() {
  const label = document.getElementById('watch-folder-status-text');
  if (!label || !getAuthToken()) return;
  try {
    const res = await authFetch('/api/watch-folder/status');
    if (!res.ok) return;
    const data = await res.json();
    label.textContent = `📁 事務機監控夾：${data.pending_count || 0} 份待匯入 (${escapeHtml(data.watch_dir || 'data/watch_inbox')})`;
  } catch (e) {
    console.warn('Watch folder status error:', e);
  }
}

async function scanWatchFolder() {
  if (!currentUser || !getAuthToken()) {
    openLoginModal();
    return;
  }
  const btn = document.getElementById('btn-watch-scan');
  const lockedByPolicy = isModelLockedForCurrentUser();
  const engine = lockedByPolicy
    ? (serverAiPolicy?.active_engine || 'openrouter')
    : (document.getElementById('engine-select')?.value || 'mock');
  const apiKey = lockedByPolicy ? null : (document.getElementById('api-key-input')?.value?.trim() || null);
  const selectedModel = lockedByPolicy
    ? (serverAiPolicy?.active_model || null)
    : (engine === 'openrouter'
      ? (document.getElementById('openrouter-model-select')?.value || 'google/gemma-4-31b-it:free')
      : null);
  const splitPdf = document.getElementById('split-pdf-cb')?.checked ?? true;
  const multiReceipt = document.getElementById('multi-receipt-cb')?.checked ?? false;
  const fxMode = document.getElementById('fx-mode-select')?.value || 'auto_bot';
  const manualRateRaw = document.getElementById('manual-fx-rate-input')?.value;
  const manualRate = fxMode === 'manual' && manualRateRaw ? parseFloat(manualRateRaw) : null;
  const tripIdRaw = document.getElementById('upload-trip-select')?.value || '';
  const tripId = tripIdRaw ? parseInt(tripIdRaw, 10) : null;

  if (btn) btn.disabled = true;
  hideQuotaExceededNotice();
  try {
    const res = await authFetch('/api/watch-folder/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        engine,
        api_key: apiKey,
        ...(selectedModel ? { model: selectedModel } : {}),
        split_pdf: splitPdf,
        multi_receipt: multiReceipt,
        fx_mode: fxMode,
        manual_exchange_rate: manualRate,
        trip_id: tripId
      })
    });
    const data = await res.json();
    if (!res.ok) {
      if (res.status === 429) {
        const quotaMsg = data.detail || '目前辨識額度已用完，請稍後再試或聯繫系統管理員處理。';
        showQuotaExceededNotice(quotaMsg);
        alert(`⚠️ ${quotaMsg}`);
        return;
      }
      throw new Error(data.detail || '掃描失敗');
    }
    await loadWatchFolderStatus();
    if ((data.total || 0) === 0) {
      alert(`目前監控資料夾（${data.watch_dir}）沒有待處理的新檔案。`);
      return;
    }
    renderAuditResults({
      session_id: 'watch_inbox',
      trip_id: data.trip_id,
      source_files_count: data.scanned_files_count,
      expanded_pages_count: data.expanded_pages_count,
      qr_fast_decoded_count: data.qr_fast_decoded_count,
      total: data.total,
      approved: data.approved,
      warning: data.warning,
      rejected: data.rejected,
      reports: data.reports || []
    });
    document.getElementById('audit-empty')?.classList.add('hidden');
    loadHistoryStats();
    loadTripOptions(tripId ? String(tripId) : undefined);
    alert(`已從監控資料夾匯入 ${data.scanned_files_count} 份檔案（共 ${data.total} 筆單據）。`);
  } catch (e) {
    alert(`事務機資料夾掃描失敗: ${e.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Engine Change Handler
function onEngineChange() {
  const engine = document.getElementById('engine-select').value;
  const keyGroup = document.getElementById('gemini-key-group');
  const orModelGroup = document.getElementById('openrouter-model-group');
  const serverBadge = document.getElementById('server-key-badge');
  const apiKeyInput = document.getElementById('api-key-input');
  const engineHint = document.getElementById('engine-hint');
  const badgeLabel = document.getElementById('engine-badge-label');

  if (engine === 'gemini') {
    if (orModelGroup) orModelGroup.classList.add('hidden');
    if (keyGroup) keyGroup.classList.remove('hidden');
    if (serverBadge) serverBadge.classList.toggle('hidden', !serverHasKey);
    if (apiKeyInput) {
      apiKeyInput.value = localStorage.getItem('gemini_api_key') || '';
      apiKeyInput.placeholder = serverHasKey
        ? '伺服器已預設 API Key（免手動填寫，可直接辨識）'
        : '輸入 Gemini API Key (選填)...';
    }
    if (engineHint) engineHint.textContent = '採用 Google 雲端多模態視覺模型，辨識速度 1~2 秒，跨國多幣別理解力最強。';
    if (badgeLabel) badgeLabel.textContent = 'Gemini 雲端';
  } else if (engine === 'openrouter') {
    if (orModelGroup) orModelGroup.classList.remove('hidden');
    if (keyGroup) keyGroup.classList.remove('hidden');
    if (serverBadge) serverBadge.classList.toggle('hidden', !serverHasOpenRouterKey);
    if (apiKeyInput) {
      apiKeyInput.value = localStorage.getItem('openrouter_api_key') || '';
      apiKeyInput.placeholder = serverHasOpenRouterKey
        ? '伺服器已預設 OpenRouter Key（可直接辨識）'
        : '輸入 OpenRouter API Key (sk-or-...)';
    }
    if (engineHint) engineHint.textContent = '免費視覺模型 POC 模式：首選 Gemma 4 31B，遇到 HTTP 429 限流自動切換備援模型。';
    if (badgeLabel) badgeLabel.textContent = 'OpenRouter 免費視覺';
  } else if (engine === 'ollama') {
    if (orModelGroup) orModelGroup.classList.add('hidden');
    if (keyGroup) keyGroup.classList.add('hidden');
    if (serverBadge) serverBadge.classList.add('hidden');
    if (engineHint) engineHint.textContent = '連線本機 Ollama 視覺模型 (llama3.2-vision)，單據完全不出企業內網。';
    if (badgeLabel) badgeLabel.textContent = 'Ollama 地端離線';
  } else {
    if (orModelGroup) orModelGroup.classList.add('hidden');
    if (keyGroup) keyGroup.classList.add('hidden');
    if (serverBadge) serverBadge.classList.add('hidden');
    if (engineHint) engineHint.textContent = '無需 API Key，支援示範中國發票、泰國 Grab、歐洲飯店與高鐵票。';
    if (badgeLabel) badgeLabel.textContent = 'Mock 離線演示';
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

let lastUploadedTripId = null;
const BATCH_CHUNK_SIZE = 5;

// Start Audit Flow (supports Phase 2 automatic chunked batch upload + trip binding)
async function startAudit() {
  if (selectedFiles.length === 0) return;

  if (!currentUser || !getAuthToken()) {
    alert('請先登入公司帳號後再上傳單據。');
    openLoginModal();
    return;
  }

  const lockedByPolicy = isModelLockedForCurrentUser();
  const engine = lockedByPolicy
    ? (serverAiPolicy?.active_engine || 'openrouter')
    : document.getElementById('engine-select').value;
  const apiKey = lockedByPolicy ? '' : document.getElementById('api-key-input').value.trim();
  const applyTemplate = document.getElementById('apply-template-cb').checked;
  const splitPdf = document.getElementById('split-pdf-cb')?.checked ?? true;
  const multiReceipt = document.getElementById('multi-receipt-cb')?.checked ?? false;
  const fxMode = document.getElementById('fx-mode-select')?.value || 'auto_bot';
  const manualRateRaw = document.getElementById('manual-fx-rate-input')?.value?.trim() || '';
  const manualRate = fxMode === 'manual' ? parseFloat(manualRateRaw) : null;
  const tripIdRaw = document.getElementById('upload-trip-select')?.value || '';
  const tripId = tripIdRaw ? parseInt(tripIdRaw, 10) : null;

  if (fxMode === 'manual' && (!manualRate || manualRate <= 0)) {
    alert('選擇「員工自訂統一匯率」時，請輸入大於 0 的匯率數值。');
    document.getElementById('manual-fx-rate-input')?.focus();
    return;
  }

  if (!lockedByPolicy) {
    if (engine === 'gemini') {
      if (!apiKey && !serverHasKey) {
        alert('請輸入 Gemini API Key，或於伺服器端環境設定 GEMINI_API_KEY！\n（若想快速測試，可切換至「OpenRouter 免費視覺模型」或「離線展示模式」）');
        document.getElementById('api-key-input').focus();
        return;
      }
      if (apiKey) localStorage.setItem('gemini_api_key', apiKey);
    } else if (engine === 'openrouter') {
      if (!apiKey && !serverHasOpenRouterKey) {
        alert('請輸入 OpenRouter API Key (sk-or-...)，或於伺服器端設定 OPENROUTER_API_KEY！');
        document.getElementById('api-key-input').focus();
        return;
      }
      if (apiKey) localStorage.setItem('openrouter_api_key', apiKey);
    }
  }
  const selectedModel = lockedByPolicy
    ? (serverAiPolicy?.active_model || '')
    : (engine === 'openrouter'
      ? (document.getElementById('openrouter-model-select')?.value || 'google/gemma-4-31b-it:free')
      : '');

  // Prepare file chunks so large 50-200+ uploads never hit HTTP timeouts
  const chunks = [];
  for (let i = 0; i < selectedFiles.length; i += BATCH_CHUNK_SIZE) {
    chunks.push(selectedFiles.slice(i, i + BATCH_CHUNK_SIZE));
  }

  // UI State: Loading
  hideQuotaExceededNotice();
  document.getElementById('btn-start-audit').disabled = true;
  document.getElementById('audit-empty').classList.add('hidden');
  document.getElementById('audit-table-wrapper').classList.add('hidden');
  document.getElementById('download-banner').classList.add('hidden');
  document.getElementById('currency-bar').classList.add('hidden');
  const statsStrip = document.getElementById('audit-stats-strip');
  if (statsStrip) statsStrip.classList.add('hidden');
  document.getElementById('audit-loading').classList.remove('hidden');

  const progressWrap = document.getElementById('batch-progress-wrap');
  const progressFill = document.getElementById('batch-progress-fill');
  const progressText = document.getElementById('batch-progress-text');
  if (progressWrap) {
    if (chunks.length > 1) {
      progressWrap.classList.remove('hidden');
      if (progressFill) progressFill.style.width = '5%';
      if (progressText) progressText.textContent = `準備分批上傳 ${selectedFiles.length} 份單據（共 ${chunks.length} 批）...`;
    } else {
      progressWrap.classList.add('hidden');
    }
  }

  try {
    const mergedReports = [];
    let totalApproved = 0;
    let totalWarning = 0;
    let totalRejected = 0;
    let totalSourceFiles = 0;
    let totalExpandedPages = 0;
    let totalQrDecoded = 0;
    let lastData = null;
    let processedCount = 0;

    for (let idx = 0; idx < chunks.length; idx++) {
      const chunk = chunks[idx];
      const isLast = idx === chunks.length - 1;

      if (chunks.length > 1 && progressText) {
        progressText.textContent = `正在處理第 ${idx + 1} / ${chunks.length} 批（已完成 ${processedCount} / ${selectedFiles.length} 份單據）`;
      }

      const formData = new FormData();
      chunk.forEach(file => {
        formData.append('files', file);
      });
      formData.append('engine', engine);
      if (apiKey) formData.append('api_key', apiKey);
      if (selectedModel) formData.append('model', selectedModel);
      formData.append('apply_template', applyTemplate ? 'true' : 'false');
      formData.append('split_pdf', splitPdf ? 'true' : 'false');
      formData.append('multi_receipt', multiReceipt ? 'true' : 'false');
      formData.append('fx_mode', fxMode);
      if (manualRate && manualRate > 0) formData.append('manual_exchange_rate', String(manualRate));
      if (tripId) formData.append('trip_id', String(tripId));
      // Intermediate batches skip intermediate Excel generation for throughput
      if (!isLast && chunks.length > 1) {
        formData.append('skip_exports', 'true');
      }

      const response = await authFetch('/api/audit', {
        method: 'POST',
        body: formData
      });

      if (!response.ok) {
        const err = await response.json();
        if (response.status === 429) {
          const quotaMsg = err.detail || '目前辨識額度已用完，請稍後再試或聯繫系統管理員處理。';
          showQuotaExceededNotice(quotaMsg);
          const quotaErr = new Error(quotaMsg);
          quotaErr.isQuotaExceeded = true;
          throw quotaErr;
        }
        throw new Error(err.detail || '審核請求失敗');
      }

      const data = await response.json();
      lastData = data;
      mergedReports.push(...(data.reports || []));
      totalApproved += data.approved || 0;
      totalWarning += data.warning || 0;
      totalRejected += data.rejected || 0;
      totalSourceFiles += data.source_files_count || chunk.length;
      totalExpandedPages += data.expanded_pages_count || chunk.length;
      totalQrDecoded += data.qr_fast_decoded_count || 0;
      processedCount += chunk.length;

      if (chunks.length > 1 && progressFill) {
        const pct = Math.round((processedCount / selectedFiles.length) * 100);
        progressFill.style.width = `${pct}%`;
      }
    }

    lastUploadedTripId = tripId;
    const downloadUrls = { ...(lastData?.download_urls || {}) };
    // When bound to a trip bundle, export URLs always generate the unified multi-day Excel/ERP workbook
    if (tripId) {
      downloadUrls.template = `/api/trips/${tripId}/export?format=template`;
      downloadUrls.excel = `/api/trips/${tripId}/export?format=excel`;
      downloadUrls.erp = `/api/trips/${tripId}/export?format=erp`;
      downloadUrls.json = `/api/trips/${tripId}/export?format=json`;
    }

    const combinedResult = {
      session_id: lastData?.session_id,
      trip_id: tripId,
      chunk_count: chunks.length,
      source_files_count: totalSourceFiles,
      expanded_pages_count: totalExpandedPages,
      qr_fast_decoded_count: totalQrDecoded,
      total: mergedReports.length,
      approved: totalApproved,
      warning: totalWarning,
      rejected: totalRejected,
      download_urls: downloadUrls,
      reports: mergedReports
    };

    currentSession = combinedResult;
    renderAuditResults(combinedResult);
    loadHistoryStats();
    loadTripOptions(tripId ? String(tripId) : undefined);
    if (isPrivilegedRole()) loadApprovalSummaryBadge();

  } catch (error) {
    if (error && error.isQuotaExceeded) {
      alert(`⚠️ ${error.message}`);
    } else {
      alert(`執行審核發生錯誤: ${error.message}`);
    }
    document.getElementById('audit-empty').classList.remove('hidden');
  } finally {
    document.getElementById('audit-loading').classList.add('hidden');
    if (progressWrap) progressWrap.classList.add('hidden');
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

  // Banner messaging for trip bundle vs standalone batch (including PDF split / multi-receipt info)
  const heroTitle = document.getElementById('download-hero-title-text');
  const heroDesc = document.getElementById('download-hero-desc-text');
  const gotoTripBtn = document.getElementById('btn-goto-trip-detail');
  const expandedPages = data.expanded_pages_count || data.total;
  const sourceFiles = data.source_files_count || data.total;
  const qrNote = data.qr_fast_decoded_count > 0 ? `（含 ${data.qr_fast_decoded_count} 份台灣電子發票 QR 高速解碼）` : '';
  const expandSummary = (expandedPages > sourceFiles || data.total > expandedPages)
    ? `已自 ${sourceFiles} 份檔案展開 ${expandedPages} 頁並拆出 ${data.total} 筆單據明細${qrNote}；`
    : `本次新增 ${data.total} 份單據${qrNote}；`;

  if (data.trip_id) {
    if (heroTitle) heroTitle.textContent = '已加入出差專案！整趟出差報銷單已更新';
    if (heroDesc) heroDesc.textContent = `${expandSummary}下方下載將直接匯出該出差專案之完整合併 Excel 與 ERP 傳票。`;
    if (gotoTripBtn) gotoTripBtn.classList.remove('hidden');
  } else {
    if (heroTitle) heroTitle.textContent = '稽核完成！企業標準出差報銷單已填妥';
    if (heroDesc) {
      heroDesc.textContent = (data.chunk_count && data.chunk_count > 1)
        ? `${expandSummary}已分 ${data.chunk_count} 批完成入庫；可於「歷史單據記錄」篩選匯出完整清冊。`
        : `${expandSummary}已自動完成金額加總、匯率折算與費用分類，可直接下載報銷。`;
    }
    if (gotoTripBtn) gotoTripBtn.classList.add('hidden');
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
    const currUpper = (inv.currency || 'NT$').toUpperCase();
    const isForeign = !['NT$', 'TWD', 'NTD', 'NT'].includes(currUpper);
    const fxChip = isForeign && inv.exchange_rate
      ? `<div class="fx-rate-chip tabular-nums" title="${escapeHtml(inv.exchange_rate_source || '')}">× ${Number(inv.exchange_rate).toFixed(4)} ≈ NT$ ${Number(inv.ntd_amount || (inv.total_amount * inv.exchange_rate)).toLocaleString()}</div>`
      : '';

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
        <td data-label="原幣金額" class="tabular-nums" style="font-weight: 700; text-align: right;">${formattedAmt}${fxChip}</td>
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
  const tripFilter = document.getElementById('history-filter-trip');
  const statusFilter = document.getElementById('history-filter-status');
  const fromFilter = document.getElementById('history-filter-from');
  const toFilter = document.getElementById('history-filter-to');

  if (userFilter && userFilter.value) params.set('user_id', userFilter.value);
  if (tripFilter && tripFilter.value) params.set('trip_id', tripFilter.value);
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
      const currUpper = (item.currency || 'NT$').toUpperCase();
      const isForeign = !['NT$', 'TWD', 'NTD', 'NT'].includes(currUpper);
      const fxChip = isForeign && item.exchange_rate
        ? `<div class="fx-rate-chip tabular-nums" title="${escapeHtml(item.exchange_rate_source || '')}">× ${Number(item.exchange_rate).toFixed(4)} ≈ NT$ ${Number(item.ntd_amount || (item.total_amount * item.exchange_rate)).toLocaleString()}</div>`
        : '';
      const uploaderDisplay = escapeHtml(item.uploader_name || item.uploader_email || '-');
      const tripBadge = item.trip_code
        ? `<div><span class="trip-code-pill">${escapeHtml(item.trip_code)}</span></div>`
        : '';
      const previewBtn = item.has_file
        ? `<button type="button" class="btn-xs-preview" onclick="openReceiptPreview(${item.id}, '${escapeQuotes(item.file_name || 'receipt')}', '${escapeQuotes(item.file_mime_type || '')}')">原圖</button>`
        : '<span style="color: var(--text-subtle); font-size: 0.78rem;">無原檔</span>';
      const fxBtn = isForeign
        ? `<button type="button" class="fx-edit-btn" onclick="openFxEditModal(${item.id}, '${escapeQuotes(item.invoice_number || '#' + item.id)}', '${escapeQuotes(item.currency || 'USD')}', ${Number(item.total_amount || 0)}, '${escapeQuotes(item.invoice_date || '')}', ${item.exchange_rate ? Number(item.exchange_rate) : 'null'}, '${escapeQuotes(item.exchange_rate_source || '')}', '${escapeQuotes(item.exchange_rate_date || '')}')">匯率</button>`
        : '';

      return `
        <tr>
          <td data-label="ID" class="tabular-nums">#${item.id}</td>
          <td data-label="狀態">${statusBadge}</td>
          <td data-label="單據號碼" class="tabular-nums"><strong>${escapeHtml(item.invoice_number || '-')}</strong>${tripBadge}</td>
          <td data-label="單據日期" class="tabular-nums">${escapeHtml(item.invoice_date || '-')}</td>
          <td data-label="原幣金額" class="tabular-nums" style="font-weight: 700; text-align: right;">${amt}${fxChip}</td>
          <td data-label="幣別">${escapeHtml(item.currency || 'NT$')}</td>
          <td data-label="類別"><span class="badge ${catClass}">${cat}</span></td>
          <td data-label="開立商家">${escapeHtml(item.seller_name || '-')}</td>
          <td data-label="出差用途">${escapeHtml(item.purpose || '-')}</td>
          <td data-label="上傳同仁">${uploaderDisplay}</td>
          <td data-label="操作 / 原圖">${previewBtn}${fxBtn}</td>
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
let currentApprovalView = 'trips';

function switchApprovalView(mode) {
  currentApprovalView = mode === 'records' ? 'records' : 'trips';
  const btnTrips = document.getElementById('appr-mode-trips');
  const btnRecs = document.getElementById('appr-mode-records');
  const tripsBox = document.getElementById('approval-trips-container');
  const recsBox = document.getElementById('approval-records-container');

  if (btnTrips) btnTrips.classList.toggle('active', currentApprovalView === 'trips');
  if (btnRecs) btnRecs.classList.toggle('active', currentApprovalView === 'records');
  if (tripsBox) tripsBox.classList.toggle('hidden', currentApprovalView !== 'trips');
  if (recsBox) recsBox.classList.toggle('hidden', currentApprovalView !== 'records');

  loadApprovalData();
}

async function loadApprovalSummaryBadge() {
  if (!getAuthToken()) return;
  try {
    const res = await authFetch('/api/approval/summary');
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
  if (!getAuthToken()) return;
  await loadApprovalSummaryBadge();
  if (currentApprovalView === 'trips') {
    await loadApprovalTrips();
    return;
  }

  const tbody = document.getElementById('approval-table-body');
  const countLabel = document.getElementById('appr-table-count-label');
  if (!tbody) return;

  tbody.innerHTML = '<tr><td colspan="9" class="text-center" style="padding: 2rem;"><div class="spinner" style="margin: 0 auto 0.5rem;"></div>載入審批單據中...</td></tr>';

  try {
    const res = await authFetch(`/api/approval/list?status=${encodeURIComponent(currentApprovalFilter)}`);
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
      const statusBadge = getApprovalBadgeHtml(status);

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
            <button class="btn-appr btn-appr-return" onclick="openReviewModal(${r.id}, '${escapeQuotes(r.invoice_number || '無號碼')}', ${r.total_amount}, '${escapeQuotes(r.currency || 'NT$')}', '${escapeQuotes(r.applicant_name || '林專員')}', '${escapeQuotes(r.seller_name || '-')}')" title="填寫審核意見或退件原因">審核/退件</button>
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

      const tripTag = r.trip_code
        ? `<div><button type="button" class="btn-link-inline" onclick="openTripDetail(${r.trip_id})">${escapeHtml(r.trip_code)}</button></div>`
        : '';
      const notesDisplay = r.review_notes ? escapeHtml(r.review_notes) : (r.reviewed_at ? '<span class="text-subtle text-xs">審核合規通過</span>' : '<span class="text-subtle text-xs">-</span>');

      return `
        <tr>
          <td data-label="序號"><span class="text-xs tabular-nums text-subtle">#${r.id}</span></td>
          <td data-label="審批狀態">${statusBadge}</td>
          <td data-label="AI 稽核">${auditBadge}</td>
          <td data-label="申請人">
            <div style="font-weight: 600; font-size: 0.875rem;">${safeApplicant}</div>
            <div class="text-xs text-subtle">${escapeHtml(r.applicant_email || '')}</div>
          </td>
          <td data-label="單據號碼">
            <div style="font-weight: 600; color: #2563EB;">${safeInv}</div>
            ${tripTag}
            <div class="text-xs text-subtle">${escapeHtml(r.document_type || '發票')} · ${escapeHtml(r.invoice_date || '')}</div>
          </td>
          <td data-label="開立廠商">
            <div>${safeSeller}</div>
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

async function loadApprovalTrips() {
  const container = document.getElementById('approval-trips-container');
  const countLabel = document.getElementById('appr-table-count-label');
  if (!container) return;

  if (!getAuthToken()) {
    if (countLabel) countLabel.textContent = '需登入檢視出差專案';
    container.innerHTML = `
      <div class="empty-state" style="padding: 2rem;">
        <div class="empty-title">請先登入公司帳號後檢視出差專案整案審批</div>
        <div class="empty-sub" style="margin-bottom: 1rem;">或可切換右上角「單筆發票明細」檢視全部單據狀態。</div>
        <button type="button" class="btn btn-primary btn-sm" onclick="openLoginModal()">立即登入</button>
      </div>
    `;
    return;
  }

  container.innerHTML = '<div class="text-center" style="padding: 2rem;">載入出差專案審批中...</div>';
  try {
    const qs = currentApprovalFilter === 'ALL' ? '' : `?status=${encodeURIComponent(currentApprovalFilter)}`;
    const res = await authFetch(`/api/trips${qs}`);
    if (!res.ok) throw new Error('無法載入出差專案');
    const data = await res.json();
    let trips = data.trips || [];
    if (currentApprovalFilter === 'ALL') {
      trips = trips.filter(t => (t.status || 'DRAFT').toUpperCase() !== 'DRAFT');
    }

    if (countLabel) {
      countLabel.textContent = `共 ${trips.length} 個出差專案 (篩選：${currentApprovalFilter})`;
    }

    if (trips.length === 0) {
      container.innerHTML = `
        <div class="empty-state" style="padding: 2rem;">
          <div class="empty-title">目前無符合【${escapeHtml(currentApprovalFilter)}】的出差專案</div>
          <div class="empty-sub">員工送審整趟出差專案後將集中顯示於此，或可切換為「單筆發票明細」檢視零星單據。</div>
        </div>
      `;
      return;
    }

    const privileged = isPrivilegedRole();
    container.innerHTML = trips.map(t => {
      const st = (t.status || 'SUBMITTED').toUpperCase();
      const statusBadge = getApprovalBadgeHtml(st);
      const period = [t.start_date, t.end_date].filter(Boolean).join(' ~ ') || '未指定日期';
      const issueWarn = (t.warning_audit_count || 0) + (t.rejected_audit_count || 0);
      const issueHtml = issueWarn > 0
        ? `<span class="badge badge-warning">異常警示 ${issueWarn} 張</span>`
        : `<span class="badge badge-approved">AI 稽核全過</span>`;

      let actionHtml = `
        <button type="button" class="btn btn-secondary btn-sm" onclick="openTripDetail(${t.id})">展開明細 (${t.receipt_count || 0})</button>
        <button type="button" class="btn btn-secondary btn-sm" onclick="exportTripReport(${t.id}, 'template')">整趟 Excel</button>
      `;
      if (privileged && (st === 'SUBMITTED' || st === 'RETURNED')) {
        actionHtml += `
          <button type="button" class="btn-appr btn-appr-approve" onclick="reviewTrip(${t.id}, 'approve')">整案核准</button>
          <button type="button" class="btn-appr btn-appr-return" onclick="reviewTrip(${t.id}, 'return')">整案退件</button>
        `;
      } else if (privileged && st === 'APPROVED') {
        actionHtml += `
          <button type="button" class="btn-appr btn-appr-pay" onclick="markTripPaid(${t.id})">標記整案撥款</button>
        `;
      } else if (!privileged && st === 'RETURNED') {
        actionHtml += `
          <button type="button" class="btn-appr btn-appr-resubmit" onclick="submitTrip(${t.id})">補件重送整案</button>
        `;
      }

      return `
        <div class="approval-trip-rowcard">
          <div class="approval-trip-info">
            <div class="flex-align-center gap-2" style="flex-wrap: wrap;">
              <span class="trip-code-pill">${escapeHtml(t.trip_code)}</span>
              ${statusBadge}
              ${issueHtml}
            </div>
            <div style="font-size: 1rem; font-weight: 700; color: var(--text-main);">${escapeHtml(t.title)}</div>
            <div class="approval-trip-stats">
              <span>申請人：<strong>${escapeHtml(t.applicant_name || '-')}</strong></span>
              <span>期間：${escapeHtml(period)}</span>
              <span>地點：${escapeHtml(t.destination || '-')}</span>
              <span>單據：<strong>${t.receipt_count || 0}</strong> 張</span>
              <span>折合：<strong style="color: var(--primary);">NT$ ${Number(t.estimated_total_ntd || 0).toLocaleString()}</strong></span>
            </div>
          </div>
          <div class="appr-action-group">${actionHtml}</div>
        </div>
      `;
    }).join('');
  } catch (e) {
    container.innerHTML = `<div class="text-center text-rose" style="padding: 1.5rem;">載入失敗: ${escapeHtml(e.message)}</div>`;
  }
}

function setApprovalFilter(status) {
  currentApprovalFilter = status;
  document.querySelectorAll('#tab-approval .filter-pill').forEach(btn => btn.classList.remove('active'));
  const btnId = `appr-filter-${status.toLowerCase()}`;
  const targetBtn = document.getElementById(btnId);
  if (targetBtn) targetBtn.classList.add('active');
  updateApprovalFilterBadge();
  loadApprovalData();
}

// Quick Actions
async function quickApprove(recordId) {
  try {
    const res = await authFetch(`/api/approval/${recordId}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'approve', notes: '主管/財務審核通過' })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`核准失敗: ${err.detail || '未知錯誤'}`);
      return;
    }
    await loadApprovalData();
    if (activeTripDetailId) await openTripDetail(activeTripDetailId);
  } catch (e) {
    alert(`操作錯誤: ${e.message}`);
  }
}

async function markAsPaidDirect(recordId) {
  const payerName = prompt('請輸入出納撥款備註或匯款資訊 (選填)：', '台新銀行企業網銀轉帳');
  if (payerName === null) return;

  try {
    const res = await authFetch(`/api/approval/${recordId}/mark-paid`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paid_by: payerName || '出納處已撥款' })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`標記撥款失敗: ${err.detail || '未知錯誤'}`);
      return;
    }
    await loadApprovalData();
    if (activeTripDetailId) await openTripDetail(activeTripDetailId);
  } catch (e) {
    alert(`操作錯誤: ${e.message}`);
  }
}

async function resubmitDirect(recordId) {
  const note = prompt('請輸入補件重送說明：', '已補齊憑證與相關證明');
  if (note === null) return;

  try {
    const res = await authFetch(`/api/approval/${recordId}/resubmit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notes: note })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`重送失敗: ${err.detail || '未知錯誤'}`);
      return;
    }
    await loadApprovalData();
    if (activeTripDetailId) await openTripDetail(activeTripDetailId);
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
      <div><strong>單據序號：</strong> #${recordId} (${escapeHtml(invNum)})</div>
      <div><strong>報銷金額：</strong> <span class="text-accent" style="font-weight:700;">${escapeHtml(curr)} ${Number(amt).toLocaleString()}</span></div>
      <div><strong>申請人：</strong> ${escapeHtml(applicant)} ｜ <strong>開立商家：</strong> ${escapeHtml(seller)}</div>
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

  try {
    const res = await authFetch(`/api/approval/${recordId}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: action, notes: notes })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`審核提交失敗: ${err.detail || '未知錯誤'}`);
      return;
    }
    closeReviewModal();
    await loadApprovalData();
    if (activeTripDetailId) await openTripDetail(activeTripDetailId);
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
  if (!getAuthToken() || !isPrivilegedRole()) return;
  try {
    const res = await authFetch('/api/storage/stats');
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
  if (!getAuthToken() || !isPrivilegedRole()) {
    alert('僅財務主管或系統管理員可執行暫存清理');
    return;
  }
  const hours = prompt('請輸入要清理多久以前的未送審暫存檔 (小時)：', '72');
  if (hours === null) return;
  const numHours = parseInt(hours, 10) || 72;

  try {
    const res = await authFetch('/api/storage/cleanup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
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

// ==========================================================================
// BUSINESS TRIP BUNDLE MANAGEMENT CONTROLLER (出差報銷專案)
// ==========================================================================

let tripsCache = [];
let currentTripFilter = 'ALL';
let activeTripDetailId = null;

function isPrivilegedRole() {
  return Boolean(currentUser && ['admin', 'accountant', 'manager'].includes(currentUser.role));
}

function getApprovalBadgeHtml(status) {
  const st = (status || 'SUBMITTED').toUpperCase();
  if (st === 'DRAFT') return '<span class="badge-appr badge-trip-draft">草稿收集中</span>';
  if (st === 'SUBMITTED') return '<span class="badge-appr badge-appr-submitted">待審核</span>';
  if (st === 'APPROVED') return '<span class="badge-appr badge-appr-approved">已核准 · 待撥款</span>';
  if (st === 'RETURNED') return '<span class="badge-appr badge-appr-returned">退件補件</span>';
  if (st === 'REJECTED') return '<span class="badge-appr badge-appr-rejected">已駁回</span>';
  if (st === 'PAID') return '<span class="badge-appr badge-appr-paid">已撥款結案</span>';
  return `<span class="badge-appr badge-trip-draft">${escapeHtml(st)}</span>`;
}

async function loadTripOptions(preferredTripId) {
  const uploadSel = document.getElementById('upload-trip-select');
  const histSel = document.getElementById('history-filter-trip');
  if (!getAuthToken()) return;

  try {
    const res = await authFetch('/api/trips?limit=200');
    if (!res.ok) return;
    const data = await res.json();
    tripsCache = data.trips || [];

    if (uploadSel) {
      const prevVal = preferredTripId !== undefined ? preferredTripId : uploadSel.value;
      const openTrips = tripsCache.filter(t => !['PAID', 'REJECTED'].includes((t.status || '').toUpperCase()));
      uploadSel.innerHTML = '<option value="">不指定專案（單次獨立送審）</option>' + openTrips.map(t => (
        `<option value="${t.id}">[${escapeHtml(t.trip_code)}] ${escapeHtml(t.title)} (${t.receipt_count || 0}張)</option>`
      )).join('');
      if (prevVal && openTrips.some(t => String(t.id) === String(prevVal))) {
        uploadSel.value = String(prevVal);
      }
      onUploadTripChange();
    }

    if (histSel) {
      const prevHist = histSel.value;
      histSel.innerHTML = '<option value="">全部出差專案</option>' + tripsCache.map(t => (
        `<option value="${t.id}">[${escapeHtml(t.trip_code)}] ${escapeHtml(t.title)}</option>`
      )).join('');
      if (prevHist && tripsCache.some(t => String(t.id) === String(prevHist))) {
        histSel.value = prevHist;
      }
    }
  } catch (e) {
    console.warn('Failed to load trip options:', e);
  }
}

function onUploadTripChange() {
  const uploadSel = document.getElementById('upload-trip-select');
  const pill = document.getElementById('upload-trip-pill');
  const textEl = document.getElementById('upload-trip-summary-text');
  if (!uploadSel || !pill || !textEl) return;

  const val = uploadSel.value;
  if (!val) {
    pill.classList.add('hidden');
    return;
  }
  const trip = tripsCache.find(t => String(t.id) === String(val));
  if (!trip) {
    pill.classList.add('hidden');
    return;
  }
  textEl.textContent = `${trip.trip_code} · 目前已有 ${trip.receipt_count || 0} 張單據 (NT$ ${Number(trip.estimated_total_ntd || 0).toLocaleString()})`;
  pill.classList.remove('hidden');
}

function openSelectedUploadTrip() {
  const val = document.getElementById('upload-trip-select')?.value;
  if (val) openTripDetail(parseInt(val, 10));
}

function openLastUploadedTrip() {
  if (lastUploadedTripId) openTripDetail(lastUploadedTripId);
}

function setTripFilter(status) {
  currentTripFilter = status;
  document.querySelectorAll('#tab-trips .trip-filter-pill').forEach(btn => btn.classList.remove('active'));
  const target = document.getElementById(`trip-filter-${status.toLowerCase()}`);
  if (target) target.classList.add('active');
  updateTripsFilterBadge();
  loadTrips();
}

async function loadTrips() {
  const grid = document.getElementById('trips-card-grid');
  const countLabel = document.getElementById('trips-count-label');
  if (!grid) return;

  if (!getAuthToken()) {
    if (countLabel) countLabel.textContent = '請先登入系統';
    grid.innerHTML = `
      <div class="empty-state" style="grid-column: 1 / -1;">
        <div class="empty-title">請先登入公司帳號後管理出差報銷專案</div>
        <button type="button" class="btn btn-primary btn-sm" style="margin-top: 0.75rem;" onclick="openLoginModal()">立即登入</button>
      </div>
    `;
    return;
  }

  grid.innerHTML = '<div class="text-center" style="grid-column: 1 / -1; padding: 2rem;">載入出差專案中...</div>';
  try {
    const params = new URLSearchParams();
    if (currentTripFilter !== 'ALL') params.set('status', currentTripFilter);
    const userFilter = document.getElementById('trips-filter-user');
    if (userFilter && userFilter.value) params.set('user_id', userFilter.value);
    const qs = params.toString();

    const res = await authFetch(qs ? `/api/trips?${qs}` : '/api/trips');
    if (!res.ok) throw new Error('無法取得出差專案清單');
    const data = await res.json();
    const trips = data.trips || [];

    if (countLabel) countLabel.textContent = `共 ${trips.length} 個出差專案`;

    if (trips.length === 0) {
      grid.innerHTML = `
        <div class="empty-state" style="grid-column: 1 / -1;">
          <div class="empty-title">尚無符合條件的出差報銷專案</div>
          <div class="empty-sub">點擊右上角「+ 建立出差專案」，即可開始把多日差旅的所有發票集中至同一個報銷組合！</div>
        </div>
      `;
      return;
    }

    grid.innerHTML = trips.map(t => {
      const st = (t.status || 'DRAFT').toUpperCase();
      const statusBadge = getApprovalBadgeHtml(st);
      const period = [t.start_date, t.end_date].filter(Boolean).join(' ~ ') || '未填日期';
      const canContinueUpload = !['PAID', 'REJECTED'].includes(st);
      const canSubmit = (st === 'DRAFT' || st === 'RETURNED') && (t.receipt_count || 0) > 0;
      const canDelete = (st === 'DRAFT') || (isPrivilegedRole() && st !== 'PAID');

      return `
        <div class="trip-card">
          <div class="trip-card-top">
            <div>
              <span class="trip-code-pill">${escapeHtml(t.trip_code)}</span>
              <div class="trip-card-title">${escapeHtml(t.title)}</div>
            </div>
            ${statusBadge}
          </div>
          <div class="trip-card-meta">
            <span>📅 ${escapeHtml(period)}</span>
            <span>📍 ${escapeHtml(t.destination || '未填地點')}</span>
            <span>👤 ${escapeHtml(t.applicant_name || '-')}</span>
          </div>
          <div class="trip-card-kpis">
            <div>
              <div class="trip-kpi-label">已收單據數</div>
              <div class="trip-kpi-val">${t.receipt_count || 0} 張</div>
            </div>
            <div>
              <div class="trip-kpi-label">折合台幣總額</div>
              <div class="trip-kpi-val" style="color: var(--primary);">NT$ ${Number(t.estimated_total_ntd || 0).toLocaleString()}</div>
            </div>
          </div>
          <div class="trip-card-actions">
            <button type="button" class="btn btn-secondary btn-sm" onclick="openTripDetail(${t.id})">檢視明細</button>
            ${canContinueUpload ? `<button type="button" class="btn btn-primary btn-sm" onclick="continueUploadForTrip(${t.id})">拍照/補傳</button>` : ''}
            ${(t.receipt_count || 0) > 0 ? `<button type="button" class="btn btn-secondary btn-sm" onclick="exportTripReport(${t.id}, 'template')">匯出 Excel</button>` : ''}
            ${canSubmit ? `<button type="button" class="btn btn-emerald btn-sm" onclick="submitTrip(${t.id})">送審整案</button>` : ''}
            ${canDelete ? `<button type="button" class="btn btn-secondary btn-sm" style="color: var(--rose);" onclick="deleteTrip(${t.id})">刪除</button>` : ''}
          </div>
        </div>
      `;
    }).join('');
  } catch (e) {
    grid.innerHTML = `<div class="text-center text-rose" style="grid-column: 1 / -1; padding: 2rem;">載入失敗: ${escapeHtml(e.message)}</div>`;
  }
}

function openTripModal(tripObj = null, autoSelectForUpload = false) {
  if (!currentUser || !getAuthToken()) {
    openLoginModal();
    return;
  }
  const modal = document.getElementById('trip-form-modal');
  const titleEl = document.getElementById('trip-form-modal-title');
  const errEl = document.getElementById('trip-form-error');
  document.getElementById('trip-form-id').value = tripObj ? String(tripObj.id) : '';
  document.getElementById('trip-form-auto-select').value = autoSelectForUpload ? '1' : '0';
  document.getElementById('trip-input-title').value = tripObj?.title || '';
  document.getElementById('trip-input-destination').value = tripObj?.destination || '';
  document.getElementById('trip-input-advance').value = tripObj?.advance_payment ?? 0;
  document.getElementById('trip-input-start-date').value = tripObj?.start_date || '';
  document.getElementById('trip-input-end-date').value = tripObj?.end_date || '';
  document.getElementById('trip-input-purpose').value = tripObj?.purpose || '';
  if (titleEl) titleEl.textContent = tripObj ? `編輯出差專案 (${tripObj.trip_code})` : '建立新出差報銷專案';
  if (errEl) errEl.classList.add('hidden');
  if (modal) modal.classList.remove('hidden');
}

function closeTripModal() {
  const modal = document.getElementById('trip-form-modal');
  if (modal) modal.classList.add('hidden');
}

async function handleTripFormSubmit(e) {
  e.preventDefault();
  const editId = document.getElementById('trip-form-id')?.value;
  const autoSelect = document.getElementById('trip-form-auto-select')?.value === '1';
  const errEl = document.getElementById('trip-form-error');
  const submitBtn = document.getElementById('btn-trip-form-submit');

  const payload = {
    title: document.getElementById('trip-input-title').value.trim(),
    destination: document.getElementById('trip-input-destination').value.trim() || null,
    advance_payment: parseFloat(document.getElementById('trip-input-advance').value || '0') || 0,
    start_date: document.getElementById('trip-input-start-date').value || null,
    end_date: document.getElementById('trip-input-end-date').value || null,
    purpose: document.getElementById('trip-input-purpose').value.trim() || null
  };

  if (errEl) errEl.classList.add('hidden');
  if (submitBtn) submitBtn.disabled = true;

  try {
    const url = editId ? `/api/trips/${editId}` : '/api/trips';
    const method = editId ? 'PATCH' : 'POST';
    const res = await authFetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '儲存失敗');

    const createdId = data.trip?.id;
    closeTripModal();
    await loadTripOptions(autoSelect && createdId ? String(createdId) : undefined);
    const tripsPane = document.getElementById('tab-trips');
    if (tripsPane && tripsPane.classList.contains('active')) {
      await loadTrips();
    }
    if (editId && activeTripDetailId && String(activeTripDetailId) === String(editId)) {
      await openTripDetail(parseInt(editId, 10));
    }
  } catch (err) {
    if (errEl) {
      errEl.textContent = err.message;
      errEl.classList.remove('hidden');
    }
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

async function openTripDetail(tripId) {
  if (!getAuthToken()) {
    openLoginModal();
    return;
  }
  const modal = document.getElementById('trip-detail-modal');
  const recordsBody = document.getElementById('trip-detail-records-body');
  activeTripDetailId = tripId;
  if (recordsBody) recordsBody.innerHTML = '<tr><td colspan="8" class="text-center" style="padding: 2rem;">載入專案明細中...</td></tr>';
  if (modal) modal.classList.remove('hidden');

  try {
    const res = await authFetch(`/api/trips/${tripId}`);
    const t = await res.json();
    if (!res.ok) throw new Error(t.detail || '讀取專案失敗');

    const st = (t.status || 'DRAFT').toUpperCase();
    const headerTitle = document.getElementById('trip-detail-header-title');
    const statusBadgeWrap = document.getElementById('trip-detail-status-badge');
    const metaBar = document.getElementById('trip-detail-meta-bar');
    const summaryGrid = document.getElementById('trip-detail-summary-grid');
    const actionsBar = document.getElementById('trip-detail-actions');

    if (headerTitle) headerTitle.textContent = `[${t.trip_code}] ${t.title}`;
    if (statusBadgeWrap) statusBadgeWrap.outerHTML = `<span id="trip-detail-status-badge">${getApprovalBadgeHtml(st)}</span>`;

    const period = [t.start_date, t.end_date].filter(Boolean).join(' ~ ') || '未填寫';
    if (metaBar) {
      metaBar.innerHTML = `
        <div><strong>申請人：</strong>${escapeHtml(t.applicant_name || '-')} (${escapeHtml(t.applicant_email || '')}) ｜ <strong>出差地點：</strong>${escapeHtml(t.destination || '-')} ｜ <strong>期間：</strong>${escapeHtml(period)}</div>
        ${t.purpose ? `<div><strong>出差事由：</strong>${escapeHtml(t.purpose)}</div>` : ''}
        ${t.review_notes ? `<div><strong>審核意見：</strong><span class="text-rose">${escapeHtml(t.review_notes)}</span></div>` : ''}
      `;
    }

    const currencyBreakdown = Object.entries(t.currency_totals || {})
      .map(([c, amt]) => `${c} ${Number(amt).toLocaleString()}`)
      .join(' · ') || 'NT$ 0';

    if (summaryGrid) {
      summaryGrid.innerHTML = `
        <div class="trip-detail-stat">
          <div class="trip-kpi-label">單據總張數</div>
          <div class="trip-kpi-val">${t.receipt_count || 0} 張</div>
        </div>
        <div class="trip-detail-stat">
          <div class="trip-kpi-label">折合台幣估算</div>
          <div class="trip-kpi-val" style="color: var(--primary);">NT$ ${Number(t.estimated_total_ntd || 0).toLocaleString()}</div>
        </div>
        <div class="trip-detail-stat">
          <div class="trip-kpi-label">預支旅費</div>
          <div class="trip-kpi-val">NT$ ${Number(t.advance_payment || 0).toLocaleString()}</div>
        </div>
        <div class="trip-detail-stat">
          <div class="trip-kpi-label">各幣別明細加總</div>
          <div class="trip-kpi-val" style="font-size: 0.85rem;">${escapeHtml(currencyBreakdown)}</div>
        </div>
      `;
    }

    if (actionsBar) {
      const privileged = isPrivilegedRole();
      const canEdit = !['PAID', 'REJECTED'].includes(st);
      const canSubmit = (st === 'DRAFT' || st === 'RETURNED') && (t.receipt_count || 0) > 0;
      const tripPayloadJson = escapeQuotes(JSON.stringify({
        id: t.id,
        trip_code: t.trip_code,
        title: t.title,
        destination: t.destination,
        advance_payment: t.advance_payment,
        start_date: t.start_date,
        end_date: t.end_date,
        purpose: t.purpose
      }));

      actionsBar.innerHTML = `
        <div class="flex-align-center gap-2" style="flex-wrap: wrap;">
          ${canEdit ? `<button type="button" class="btn btn-primary btn-sm" onclick="continueUploadForTrip(${t.id}); closeTripDetailModal();">繼續拍照/上傳單據</button>` : ''}
          ${canEdit ? `<button type="button" class="btn btn-secondary btn-sm" onclick="openTripModal(${tripPayloadJson})">編輯專案資訊</button>` : ''}
          ${canSubmit ? `<button type="button" class="btn btn-emerald btn-sm" onclick="submitTrip(${t.id})">送審整筆出差</button>` : ''}
          ${privileged && (st === 'SUBMITTED' || st === 'RETURNED') ? `<button type="button" class="btn-appr btn-appr-approve" onclick="reviewTrip(${t.id}, 'approve')">整案核准</button><button type="button" class="btn-appr btn-appr-return" onclick="reviewTrip(${t.id}, 'return')">整案退件</button>` : ''}
          ${privileged && st === 'APPROVED' ? `<button type="button" class="btn-appr btn-appr-pay" onclick="markTripPaid(${t.id})">標記整案已撥款</button>` : ''}
        </div>
        <div class="flex-align-center gap-2" style="flex-wrap: wrap;">
          ${(t.receipt_count || 0) > 0 ? `
            <button type="button" class="btn btn-emerald btn-sm" onclick="exportTripReport(${t.id}, 'template')">匯出出差報銷單 (Excel)</button>
            <button type="button" class="btn btn-secondary btn-sm" onclick="exportTripReport(${t.id}, 'erp')">ERP 傳票 (CSV)</button>
            <button type="button" class="btn btn-secondary btn-sm" onclick="exportTripReport(${t.id}, 'json')">JSON</button>
          ` : ''}
        </div>
      `;
    }

    const records = t.records || [];
    if (recordsBody) {
      if (records.length === 0) {
        recordsBody.innerHTML = '<tr><td colspan="8" class="text-center" style="padding: 2rem;">此出差專案尚未加入任何發票單據，點擊「繼續拍照/上傳單據」即可新增！</td></tr>';
      } else {
        const privileged = isPrivilegedRole();
        recordsBody.innerHTML = records.map(r => {
          const recStatus = (r.approval_status || 'SUBMITTED').toUpperCase();
          const apprBadge = getApprovalBadgeHtml(recStatus);
          const auditBadge = r.audit_status === 'APPROVED'
            ? '<span class="badge badge-approved">通過</span>'
            : (r.audit_status === 'WARNING' ? '<span class="badge badge-warning">警示</span>' : '<span class="badge badge-rejected">違規</span>');
          const previewHtml = r.preview_url
            ? `<button type="button" class="btn-xs-preview" onclick="openInvoicePreview('${r.preview_url}', '${escapeQuotes(r.stored_file_path || r.file_name)}', '${escapeQuotes(r.invoice_number || '憑證')}')">原圖</button>`
            : (r.has_file ? `<button type="button" class="btn-xs-preview" onclick="openReceiptPreview(${r.id}, '${escapeQuotes(r.file_name || 'receipt')}', '${escapeQuotes(r.file_mime_type || '')}')">原圖</button>` : '');

          const currUpper = (r.currency || 'NT$').toUpperCase();
          const isForeign = !['NT$', 'TWD', 'NTD', 'NT'].includes(currUpper);
          const fxChip = isForeign && r.exchange_rate
            ? `<div class="fx-rate-chip tabular-nums" title="${escapeHtml(r.exchange_rate_source || '')}">× ${Number(r.exchange_rate).toFixed(4)} ≈ NT$ ${Number(r.ntd_amount || (r.total_amount * r.exchange_rate)).toLocaleString()}</div>`
            : '';

          let rowBtns = previewHtml;
          if (isForeign && (privileged || !['PAID', 'REJECTED'].includes(st))) {
            rowBtns += ` <button type="button" class="fx-edit-btn" onclick="openFxEditModal(${r.id}, '${escapeQuotes(r.invoice_number || '#' + r.id)}', '${escapeQuotes(r.currency || 'USD')}', ${Number(r.total_amount || 0)}, '${escapeQuotes(r.invoice_date || '')}', ${r.exchange_rate ? Number(r.exchange_rate) : 'null'}, '${escapeQuotes(r.exchange_rate_source || '')}', '${escapeQuotes(r.exchange_rate_date || '')}')">匯率</button>`;
          }
          if (privileged && recStatus === 'SUBMITTED') {
            rowBtns += ` <button type="button" class="btn-appr btn-appr-return" onclick="openReviewModal(${r.id}, '${escapeQuotes(r.invoice_number || '無號碼')}', ${r.total_amount || 0}, '${escapeQuotes(r.currency || 'NT$')}', '${escapeQuotes(r.applicant_name || '')}', '${escapeQuotes(r.seller_name || '-')}')">退件/審核</button>`;
          }
          if (recStatus === 'RETURNED') {
            rowBtns += ` <button type="button" class="btn-appr btn-appr-resubmit" onclick="resubmitDirect(${r.id})">補件重送</button>`;
          }
          if (st === 'DRAFT') {
            rowBtns += ` <button type="button" class="btn-appr" style="background: var(--bg-subtle); border-color: var(--border-input);" onclick="detachRecordFromTrip(${t.id}, ${r.id})">移出</button>`;
          }

          return `
            <tr>
              <td data-label="序號" class="tabular-nums">#${r.id}</td>
              <td data-label="審批狀態">${apprBadge}</td>
              <td data-label="AI 稽核">${auditBadge}</td>
              <td data-label="單據號碼">
                <div style="font-weight: 600;">${escapeHtml(r.invoice_number || '-')}</div>
                <div class="text-xs text-subtle">${escapeHtml(r.invoice_date || '-')}</div>
              </td>
              <td data-label="商家/用途">
                <div>${escapeHtml(r.seller_name || '-')}</div>
                <div class="text-xs text-subtle">${escapeHtml(r.purpose || '-')}</div>
              </td>
              <td data-label="金額" class="tabular-nums" style="font-weight: 700;">${escapeHtml(r.currency || 'NT$')} ${Number(r.total_amount || 0).toLocaleString()}${fxChip}</td>
              <td data-label="意見" style="font-size: 0.8rem;">${escapeHtml(r.review_notes || '-')}</td>
              <td data-label="操作"><div class="appr-action-group">${rowBtns}</div></td>
            </tr>
          `;
        }).join('');
      }
    }
  } catch (e) {
    if (recordsBody) recordsBody.innerHTML = `<tr><td colspan="8" class="text-center text-rose">${escapeHtml(e.message)}</td></tr>`;
  }
}

function closeTripDetailModal() {
  const modal = document.getElementById('trip-detail-modal');
  if (modal) modal.classList.add('hidden');
  activeTripDetailId = null;
}

function continueUploadForTrip(tripId) {
  const sel = document.getElementById('upload-trip-select');
  if (sel) {
    sel.value = String(tripId);
    onUploadTripChange();
  }
  switchTab('audit');
}

async function submitTrip(tripId) {
  const notes = prompt('請確認是否將此出差專案底下所有單據送交財務審核？可填寫補充說明 (選填)：', '');
  if (notes === null) return;
  try {
    const res = await authFetch(`/api/trips/${tripId}/submit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notes: notes || null })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '送審失敗');
    alert(data.message || '已送交財務審核');
    await loadTripOptions(String(tripId));
    await loadApprovalSummaryBadge();
    if (activeTripDetailId === tripId) await openTripDetail(tripId);
    if (document.getElementById('tab-trips')?.classList.contains('active')) await loadTrips();
    if (document.getElementById('tab-approval')?.classList.contains('active')) await loadApprovalData();
  } catch (e) {
    alert(`送審失敗: ${e.message}`);
  }
}

async function reviewTrip(tripId, action) {
  let notes = null;
  if (action === 'return') {
    notes = prompt('請填寫整案退件補件原因（必填）：', '');
    if (notes === null) return;
    if (!notes.trim()) {
      alert('退件時必須填寫退件原因！');
      return;
    }
  } else if (action === 'approve') {
    notes = prompt('整案核准備註 (選填)：', '整趟出差單據複核無誤通過');
    if (notes === null) return;
  }

  try {
    const res = await authFetch(`/api/trips/${tripId}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, notes })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '審核操作失敗');
    alert(data.message || '審核已更新');
    await loadTripOptions();
    await loadApprovalData();
    if (activeTripDetailId === tripId) await openTripDetail(tripId);
    if (document.getElementById('tab-trips')?.classList.contains('active')) await loadTrips();
  } catch (e) {
    alert(`審核失敗: ${e.message}`);
  }
}

async function markTripPaid(tripId) {
  const paidBy = prompt('請輸入撥款經辦或轉帳方式：', '出納銀行匯款');
  if (paidBy === null) return;
  try {
    const res = await authFetch(`/api/trips/${tripId}/mark-paid`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paid_by: paidBy || '出納銀行匯款' })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '撥款更新失敗');
    alert(data.message || '已完成整案撥款結案');
    await loadTripOptions();
    await loadApprovalData();
    if (activeTripDetailId === tripId) await openTripDetail(tripId);
    if (document.getElementById('tab-trips')?.classList.contains('active')) await loadTrips();
  } catch (e) {
    alert(`撥款更新失敗: ${e.message}`);
  }
}

async function deleteTrip(tripId) {
  if (!confirm('確定刪除此出差專案？\n（註：關聯發票會安全保留於歷史資料庫，不會被刪除）')) return;
  try {
    const res = await authFetch(`/api/trips/${tripId}`, { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '刪除失敗');
    if (activeTripDetailId === tripId) closeTripDetailModal();
    await loadTripOptions();
    await loadTrips();
  } catch (e) {
    alert(`刪除失敗: ${e.message}`);
  }
}

async function detachRecordFromTrip(tripId, recordId) {
  try {
    const res = await authFetch(`/api/trips/${tripId}/assign`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ record_ids: [recordId], action: 'remove' })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '移出失敗');
    await openTripDetail(tripId);
    await loadTripOptions();
    if (document.getElementById('tab-trips')?.classList.contains('active')) await loadTrips();
  } catch (e) {
    alert(`移出失敗: ${e.message}`);
  }
}

async function exportTripReport(tripId, format = 'template') {
  const extMap = { template: 'Travel_Expenses_Report.xlsx', excel: 'expense_claim.xlsx', erp: 'erp_journal_entry.csv', json: 'audit_trail.json' };
  const trip = tripsCache.find(t => t.id === tripId);
  const prefix = trip?.trip_code ? `${trip.trip_code}_` : `TRIP_${tripId}_`;
  try {
    await downloadBlobWithAuth(`/api/trips/${tripId}/export?format=${encodeURIComponent(format)}`, `${prefix}${extMap[format] || 'export'}`);
  } catch (e) {
    alert(`匯出失敗: ${e.message}`);
  }
}

// ==========================================================================
// COLLAPSIBLE FILTER PANELS & INTERACTIVE FX RATE EDITOR
// ==========================================================================

function toggleFilterPanel(panelId) {
  const el = document.getElementById(panelId);
  if (el) el.classList.toggle('hidden');
}

function _setBadgeCount(badgeId, count) {
  const el = document.getElementById(badgeId);
  if (!el) return;
  if (count > 0) {
    el.textContent = String(count);
    el.classList.remove('hidden');
  } else {
    el.classList.add('hidden');
  }
}

function updateTripsFilterBadge() {
  let count = currentTripFilter !== 'ALL' ? 1 : 0;
  if (document.getElementById('trips-filter-user')?.value) count += 1;
  _setBadgeCount('trips-filter-badge', count);
}

function clearTripsFilters() {
  const userSel = document.getElementById('trips-filter-user');
  if (userSel) userSel.value = '';
  setTripFilter('ALL');
}

function updateApprovalFilterBadge() {
  _setBadgeCount('approval-filter-badge', currentApprovalFilter !== 'ALL' ? 1 : 0);
}

function clearApprovalFilters() {
  setApprovalFilter('ALL');
}

function updateHistoryFilterBadge() {
  const ids = [
    'history-filter-user',
    'history-filter-trip',
    'history-filter-status',
    'history-filter-from',
    'history-filter-to'
  ];
  const count = ids.filter(id => Boolean(document.getElementById(id)?.value)).length;
  _setBadgeCount('history-filter-badge', count);
}

function clearHistoryFilters() {
  ['history-filter-user', 'history-filter-trip', 'history-filter-status', 'history-filter-from', 'history-filter-to'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  updateHistoryFilterBadge();
  loadHistory();
}

function openFxEditModal(recordId, invNum, currency, origAmount, invoiceDate, currentRate, currentSource, currentRateDate) {
  const modal = document.getElementById('fx-edit-modal');
  if (!modal) return;

  document.getElementById('fx-edit-record-id').value = String(recordId);
  document.getElementById('fx-edit-currency').value = currency || 'USD';
  document.getElementById('fx-edit-orig-amount').value = String(origAmount || 0);
  document.getElementById('fx-edit-curr-label').textContent = currency || '外幣';

  const summary = document.getElementById('fx-edit-summary');
  if (summary) {
    summary.innerHTML = `
      <div><strong>單據：</strong> ${escapeHtml(invNum || '#' + recordId)} ｜ <strong>原始金額：</strong> ${escapeHtml(currency || 'USD')} ${Number(origAmount || 0).toLocaleString()}</div>
      <div class="text-xs text-muted">目前匯率來源：${escapeHtml(currentSource || '尚未鎖定')}${currentRate ? ` (× ${Number(currentRate).toFixed(4)})` : ''}</div>
    `;
  }

  const normalizedDate = (currentRateDate || invoiceDate || new Date().toISOString().slice(0, 10)).replace(/\//g, '-').slice(0, 10);
  const dateInput = document.getElementById('fx-edit-rate-date');
  if (dateInput) dateInput.value = normalizedDate;

  const rateInput = document.getElementById('fx-edit-manual-rate');
  if (rateInput) rateInput.value = currentRate && currentRate > 0 ? String(currentRate) : '1';

  const infoEl = document.getElementById('fx-edit-lookup-info');
  if (infoEl) infoEl.textContent = '';
  const errEl = document.getElementById('fx-edit-error');
  if (errEl) errEl.classList.add('hidden');

  const isManual = currentSource && currentSource.includes('自訂');
  const targetRadio = document.querySelector(`input[name="fx_edit_mode"][value="${isManual ? 'manual' : 'bot_date'}"]`);
  if (targetRadio) targetRadio.checked = true;
  onFxEditModeChange();
  updateFxNtdPreview();

  modal.classList.remove('hidden');
}

function closeFxEditModal() {
  const modal = document.getElementById('fx-edit-modal');
  if (modal) modal.classList.add('hidden');
}

function onFxEditModeChange() {
  const mode = document.querySelector('input[name="fx_edit_mode"]:checked')?.value || 'bot_date';
  const botPanel = document.getElementById('fx-edit-bot-panel');
  if (botPanel) botPanel.classList.toggle('hidden', mode !== 'bot_date');
}

function updateFxNtdPreview() {
  const origAmt = parseFloat(document.getElementById('fx-edit-orig-amount')?.value || '0') || 0;
  const rate = parseFloat(document.getElementById('fx-edit-manual-rate')?.value || '0') || 0;
  const preview = document.getElementById('fx-edit-ntd-preview');
  if (!preview) return;
  const ntd = Math.round(origAmt * rate * 100) / 100;
  preview.textContent = `換算新台幣金額：NT$ ${ntd.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

async function previewBotRateLookup() {
  const currency = document.getElementById('fx-edit-currency')?.value || 'USD';
  const rateDate = document.getElementById('fx-edit-rate-date')?.value || '';
  const rateType = document.getElementById('fx-edit-rate-type')?.value || 'spot_sell';
  const infoEl = document.getElementById('fx-edit-lookup-info');
  const btn = document.getElementById('btn-fx-lookup');

  if (btn) btn.disabled = true;
  if (infoEl) infoEl.textContent = '查詢臺灣銀行牌告中...';
  try {
    const qs = new URLSearchParams({ currency, date: rateDate, rate_type: rateType });
    const res = await fetch(`/api/rates/lookup?${qs.toString()}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '查詢失敗');
    const rateInput = document.getElementById('fx-edit-manual-rate');
    if (rateInput && data.rate) rateInput.value = String(data.rate);
    if (infoEl) infoEl.textContent = `✅ ${data.source || '臺灣銀行牌告'}（匯率 ${data.rate}）`;
    updateFxNtdPreview();
  } catch (e) {
    if (infoEl) infoEl.textContent = `❌ ${e.message}`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handleFxEditSubmit(event) {
  event.preventDefault();
  const recordId = document.getElementById('fx-edit-record-id')?.value;
  const mode = document.querySelector('input[name="fx_edit_mode"]:checked')?.value || 'bot_date';
  const rateVal = parseFloat(document.getElementById('fx-edit-manual-rate')?.value || '0');
  const rateDate = document.getElementById('fx-edit-rate-date')?.value || null;
  const rateType = document.getElementById('fx-edit-rate-type')?.value || 'spot_sell';
  const errEl = document.getElementById('fx-edit-error');
  const submitBtn = document.getElementById('btn-fx-edit-submit');

  if (errEl) errEl.classList.add('hidden');
  if (mode === 'manual' && (!rateVal || rateVal <= 0)) {
    if (errEl) {
      errEl.textContent = '請輸入大於 0 的匯率數值';
      errEl.classList.remove('hidden');
    }
    return;
  }

  if (submitBtn) submitBtn.disabled = true;
  try {
    const res = await authFetch(`/api/records/${recordId}/exchange-rate`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mode,
        exchange_rate: mode === 'manual' ? rateVal : null,
        rate_date: rateDate,
        rate_type: rateType
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '更新匯率失敗');
    closeFxEditModal();
    if (activeTripDetailId) await openTripDetail(activeTripDetailId);
    if (document.getElementById('tab-history')?.classList.contains('active')) await loadHistory();
    if (document.getElementById('tab-trips')?.classList.contains('active')) await loadTrips();
    await loadTripOptions();
  } catch (e) {
    if (errEl) {
      errEl.textContent = e.message;
      errEl.classList.remove('hidden');
    }
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

// ==========================================================================
// ADMIN AI GOVERNANCE & QUOTA USAGE CONTROLLER
// ==========================================================================

function onGovEngineChange() {
  const engine = document.getElementById('gov-engine-select')?.value || 'openrouter';
  const orWrap = document.getElementById('gov-openrouter-model-wrap');
  const customWrap = document.getElementById('gov-custom-model-wrap');
  const customInput = document.getElementById('gov-custom-model-input');
  if (engine === 'openrouter') {
    if (orWrap) orWrap.classList.remove('hidden');
    if (customWrap) customWrap.classList.add('hidden');
  } else {
    if (orWrap) orWrap.classList.add('hidden');
    if (customWrap) customWrap.classList.remove('hidden');
    if (customInput && !customInput.value.trim()) {
      if (engine === 'gemini') customInput.value = 'gemini-3.7-flash';
      else if (engine === 'ollama') customInput.value = 'llama3.2-vision:latest';
      else customInput.value = 'mock';
    }
  }
}

async function loadAdminAiGovernance() {
  if (!getAuthToken() || !isPrivilegedRole()) return;
  try {
    const [resPolicy, resUsage] = await Promise.all([
      authFetch('/api/admin/ai-policy'),
      authFetch('/api/admin/ai-usage')
    ]);
    if (resPolicy.ok) {
      const pData = await resPolicy.json();
      const policy = pData.policy || {};
      serverAiPolicy = policy;
      const canEdit = Boolean(pData.can_edit);

      const engEl = document.getElementById('gov-engine-select');
      const orSel = document.getElementById('gov-openrouter-model-select');
      const custIn = document.getElementById('gov-custom-model-input');
      const dailyIn = document.getElementById('gov-daily-limit');
      const monthIn = document.getElementById('gov-monthly-limit');
      const lockCb = document.getElementById('gov-lock-non-admin');
      const fbCb = document.getElementById('gov-enable-fallback');
      const lockBadge = document.getElementById('gov-lock-badge');
      const saveBtn = document.getElementById('btn-save-ai-policy');
      const roHint = document.getElementById('gov-readonly-hint');

      if (engEl && policy.active_engine) engEl.value = policy.active_engine;
      if (orSel && Array.isArray(pData.openrouter_models)) {
        orSel.innerHTML = pData.openrouter_models.map(m => (
          `<option value="${escapeHtml(m.id)}">${escapeHtml(m.label)}</option>`
        )).join('');
        if (policy.active_engine === 'openrouter' && policy.active_model) {
          orSel.value = policy.active_model;
        }
      }
      if (custIn && policy.active_engine !== 'openrouter') {
        custIn.value = policy.active_model || '';
      }
      onGovEngineChange();
      if (dailyIn) dailyIn.value = Number(policy.daily_limit_per_user || 0);
      if (monthIn) monthIn.value = Number(policy.monthly_limit_total || 0);
      if (lockCb) lockCb.checked = Boolean(policy.lock_model_for_non_admin);
      if (fbCb) fbCb.checked = Boolean(policy.enable_fallback);
      if (lockBadge) {
        lockBadge.textContent = policy.lock_model_for_non_admin ? '已鎖定非 Admin 自選' : '允許同仁自選模型';
      }

      [engEl, orSel, custIn, dailyIn, monthIn, lockCb, fbCb].forEach(el => {
        if (el) el.disabled = !canEdit;
      });
      if (saveBtn) saveBtn.classList.toggle('hidden', !canEdit);
      if (roHint) roHint.classList.toggle('hidden', canEdit);
      applyAiPolicyUI();
    }

    if (resUsage.ok) {
      const uData = await resUsage.json();
      const tCalls = document.getElementById('gov-kpi-today-calls');
      const mCalls = document.getElementById('gov-kpi-month-calls');
      const qrSaved = document.getElementById('gov-kpi-qr-saved');
      const activeMod = document.getElementById('gov-kpi-active-model');
      if (tCalls) tCalls.textContent = String(uData.today_ai_calls || 0);
      if (mCalls) mCalls.textContent = String(uData.month_ai_calls || 0);
      if (qrSaved) qrSaved.textContent = String(uData.total_qr_saved || 0);
      if (activeMod && uData.policy) {
        activeMod.textContent = `${uData.policy.active_engine} / ${uData.policy.active_model}`;
      }

      const byModelEl = document.getElementById('gov-usage-by-model');
      if (byModelEl) {
        const items = uData.by_model || [];
        byModelEl.innerHTML = items.length === 0
          ? '<span class="text-subtle">尚無 AI 辨識紀錄</span>'
          : items.map(m => (
              `<div style="display:flex; justify-content:space-between; padding:0.25rem 0; border-bottom:1px dashed #E2E8F0;">` +
              `<span><code>${escapeHtml(m.engine)}</code> · ${escapeHtml(m.model)}</span>` +
              `<strong>${m.ai_calls} 次 (${m.documents} 份)</strong></div>`
            )).join('');
      }

      const byUserEl = document.getElementById('gov-usage-by-user');
      if (byUserEl) {
        const users = uData.by_user || [];
        byUserEl.innerHTML = users.length === 0
          ? '<span class="text-subtle">尚無同仁呼叫紀錄</span>'
          : users.map(u => (
              `<div style="display:flex; justify-content:space-between; padding:0.25rem 0; border-bottom:1px dashed #E2E8F0;">` +
              `<span>${escapeHtml(u.user_name)} <small>(${escapeHtml(u.user_email)})</small></span>` +
              `<strong>本月 ${u.month_calls} / 今日 ${u.today_calls} 次</strong></div>`
            )).join('');
      }
    }
  } catch (e) {
    console.warn('Failed to load admin AI governance:', e);
  }
}

async function saveAdminAiPolicy(event) {
  if (event) event.preventDefault();
  if (!isAdminRole()) {
    alert('僅系統管理員 (admin) 可修改全系統 AI 模型與配額設定');
    return false;
  }

  const engine = document.getElementById('gov-engine-select')?.value || 'openrouter';
  const model = engine === 'openrouter'
    ? (document.getElementById('gov-openrouter-model-select')?.value || 'google/gemma-4-31b-it:free')
    : (document.getElementById('gov-custom-model-input')?.value?.trim() || '');
  const dailyLimit = parseInt(document.getElementById('gov-daily-limit')?.value || '0', 10) || 0;
  const monthlyLimit = parseInt(document.getElementById('gov-monthly-limit')?.value || '0', 10) || 0;
  const lockNonAdmin = Boolean(document.getElementById('gov-lock-non-admin')?.checked);
  const enableFallback = Boolean(document.getElementById('gov-enable-fallback')?.checked);
  const btn = document.getElementById('btn-save-ai-policy');

  if (btn) btn.disabled = true;
  try {
    const res = await authFetch('/api/admin/ai-policy', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        active_engine: engine,
        active_model: model,
        enable_fallback: enableFallback,
        lock_model_for_non_admin: lockNonAdmin,
        daily_limit_per_user: Math.max(0, dailyLimit),
        monthly_limit_total: Math.max(0, monthlyLimit)
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '更新失敗');
    serverAiPolicy = data.policy;
    applyAiPolicyUI();
    await loadAdminAiGovernance();
    alert(`已儲存全系統 AI 策略：${serverAiPolicy.active_engine} / ${serverAiPolicy.active_model}`);
  } catch (e) {
    alert(`儲存 AI 策略失敗: ${e.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
  return false;
}





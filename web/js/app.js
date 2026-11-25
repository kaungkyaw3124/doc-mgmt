const API_BASE = '/api';

// ---------- stale-response guarding ----------
// If you click refresh (or switch tabs, or re-trigger a load some other
// way) before a previous fetch for that same list has finished, an older
// slower response could otherwise land AFTER a newer one and overwrite it
// with stale data. Each list gets a counter: every load bumps it and
// captures its own number, then only applies its result if no newer load
// has started in the meantime — otherwise it just discards itself.
const loadSeq = { documents: 0, products: 0, customers: 0, projects: 0, companies: 0, groupMembers: 0, groupRoles: 0, backups: 0 };
function startLoad(key) {
  loadSeq[key] += 1;
  return loadSeq[key];
}
function isStaleLoad(key, mySeq) {
  return mySeq !== loadSeq[key];
}
// Access token lives ONLY in this in-memory variable — never persisted to
// localStorage — so it disappears on page reload/close rather than sitting
// around indefinitely for an XSS payload to read at any later time. A page
// load re-establishes it (if there's a still-valid session) via the
// HttpOnly refresh cookie instead — see bootstrap() below.
let token = null;
let refreshPromise = null;

// ---------- helpers ----------
function fmtMoney(v) {
  if (v === null || v === undefined) return '—';
  return '$' + Number(v).toFixed(2);
}
function fmtDate(v) {
  if (!v) return '—';
  return new Date(v).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}
function escapeHtml(s) {
  if (s === null || s === undefined) return '';
  return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
// Units (loop iteration 30). The server normalizes spellings (meter -> M);
// displayUnit is the same rule as document-service's app/core/units.py:
// "No"/"Nos"/blank prints No for quantity 1, Nos otherwise; others as stored.
const UNITS = ['No', 'M', 'Set', 'Pcs', 'Roll', 'Box', 'Lot', 'Kg', 'Ft'];
function displayUnit(unit, qty) {
  const u = String(unit || '').trim();
  if (/^(nos?)?$/i.test(u)) return Number(qty) === 1 ? 'No' : 'Nos';
  return u;
}
// A .unit-select plus its "Other…" text box (the next .unit-other sibling).
function setUnitControl(select, unit) {
  const other = select.parentNode.querySelector('.unit-other');
  const known = UNITS.includes(unit || 'No');
  select.value = known ? (unit || 'No') : '__other';
  other.value = known ? '' : unit;
  other.style.display = known ? 'none' : '';
}
function readUnitControl(select) {
  return select.value === '__other' ? select.parentNode.querySelector('.unit-other').value.trim() : select.value;
}
document.querySelectorAll('.unit-select').forEach(select => {
  select.innerHTML = UNITS.map(u => `<option value="${u}">${u}</option>`).join('') + '<option value="__other">Other…</option>';
  select.addEventListener('change', () => {
    const other = select.parentNode.querySelector('.unit-other');
    other.style.display = select.value === '__other' ? '' : 'none';
    if (select.value === '__other') other.focus();
  });
});

function skeletonRows(colspan) {
  const widths = [80, 55, 70];
  return widths.map(w =>
    `<tr class="empty-row skeleton-row"><td colspan="${colspan}"><span class="skeleton-bar" style="width:${w}%"></span></td></tr>`
  ).join('');
}
function stampClass(value) {
  return 'stamp stamp-' + String(value || '').toLowerCase();
}
// Saved customer's name, or the one-time customer typed on the document.
function docCustomerName(doc) {
  const customer = cachedCustomers.find(c => c.id === doc.customer_id);
  return customer ? customer.name : (doc.end_user_name || '—');
}

function projectLabel(projectId) {
  if (!projectId) return '—';
  const project = cachedProjects.find(p => p.id === projectId);
  if (!project) return '—';
  const yearPart = project.budget_year ? ` <span class="mono" style="color:var(--text-dim); font-size:12px;">(${escapeHtml(project.budget_year)})</span>` : '';
  return `<button class="link-btn-inline" data-goto-project-id="${project.id}">${escapeHtml(project.name)}</button>${yearPart}`;
}

function gotoProject(projectId) {
  const project = cachedProjects.find(p => p.id === projectId);
  if (!project) return;
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelector('.nav-item[data-view="projects"]').classList.add('active');
  document.getElementById('view-projects').classList.add('active');
  viewProjectDocuments(project);
}

function formatApiErrorDetail(detail) {
  if (typeof detail === 'string') return detail;
  if (detail && detail.message) {
    // duplicate-product 409: {message, existing: {id, sku, name} | null}
    return detail.existing ? `${detail.message}: ${detail.existing.sku} ${detail.existing.name}` : detail.message;
  }
  if (Array.isArray(detail)) {
    // FastAPI/Pydantic validation errors: [{type, loc: [...], msg, input}, ...]
    return detail.map(err => {
      if (typeof err === 'string') return err;
      const loc = Array.isArray(err.loc) ? err.loc.filter(p => p !== 'body') : [];
      const field = loc.length ? loc.join(' → ') : 'value';
      return `${field}: ${err.msg || 'invalid value'}`;
    }).join('; ');
  }
  return null;
}

// Silently mints a new access token from the HttpOnly refresh cookie.
// Concurrent callers share one in-flight request (not one each) so a
// burst of 401s from several parallel API calls doesn't rotate the
// refresh token multiple times and invalidate itself mid-burst.
// Never refreshes for an idle user (see "session" below): that is what
// lets an unattended tab's session end.
function refreshAccessToken() {
  if (isIdle()) return Promise.reject(new Error('idle'));
  if (!refreshPromise) {
    refreshPromise = (async () => {
      // Tabs share the refresh cookie and every refresh rotates it, so two
      // tabs refreshing at the same moment would present the same token and
      // trip replay detection (which ends every session). Stagger them.
      let since = Date.now() - Number(localStorage.getItem(REFRESH_AT_KEY) || 0);
      if (since >= 0 && since < 3000) await new Promise(r => setTimeout(r, 3000 - since));
      try { localStorage.setItem(REFRESH_AT_KEY, String(Date.now())); } catch (e) {}
      const res = await fetch(API_BASE + '/auth/refresh', { method: 'POST', credentials: 'include' });
      if (!res.ok) { res.text().catch(() => {}); throw new Error('refresh failed'); }  // drain: an unread no-store body keeps its connection busy
      applySession(await res.json());
      return token;
    })().finally(() => { refreshPromise = null; });
  }
  return refreshPromise;
}

async function apiFetch(path, options = {}, _retriedAfterRefresh = false) {
  const headers = Object.assign({}, options.headers, { 'Authorization': 'Bearer ' + token });
  if (options.body) headers['Content-Type'] = 'application/json';
  const res = await fetch(API_BASE + path, Object.assign({}, options, { headers }));
  if (res.status === 401) {
    // 401 here means the access token itself is invalid/expired (Nginx's
    // auth_request returns 403, not 401, for a valid-token-but-no-access
    // case — see infra/nginx/nginx.conf.template) — so it's always safe
    // to try a silent refresh-and-retry once before giving up.
    if (!_retriedAfterRefresh) {
      try {
        await refreshAccessToken();
        return apiFetch(path, options, true);
      } catch (e) {
        doLogout(isIdle() ? idleLogoutMessage() : EXPIRED_MESSAGE);
        throw new Error('Session expired — please sign in again.');
      }
    }
    doLogout(EXPIRED_MESSAGE);
    throw new Error('Session expired — please sign in again.');
  }
  if (!res.ok) {
    let detail = 'Something went wrong.';
    let rawDetail = null;
    try {
      const body = await res.json();
      rawDetail = body.detail;
      detail = formatApiErrorDetail(body.detail) || detail;
    } catch (e) {}
    if (res.status === 403) detail = "You don't have access to this. (" + detail + ")";
    const error = new Error(detail);
    error.detail = rawDetail;  // e.g. the duplicate-product 409's {message, existing}
    error.status = res.status;
    throw error;
  }
  if (res.status === 204) return null;
  return res.json();
}

// ---------- safe simultaneous use (loop iteration 21) ----------
// The server's answer when an edit was made on an older copy of the record.
const CONFLICT_MESSAGE = 'Someone else changed this. Reload to see their changes.';
const editingVersions = {};  // form -> the version of the record it was opened with
const idempotencyKeys = {};  // form -> Idempotency-Key, kept until that form's create succeeds
function newRequestKey() {  // crypto.randomUUID needs https; getRandomValues works on plain-http LAN too
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
}
// A repeated create (double click, retry after a lost answer) gets the first result back.
function idempotencyHeader(form) { return { 'Idempotency-Key': idempotencyKeys[form] ||= newRequestKey() }; }
function idempotencyDone(form) { delete idempotencyKeys[form]; }
function setBusy(btn, busy) {
  btn.disabled = busy;
  btn.setAttribute('aria-busy', String(busy));
  if (busy) { btn.dataset.label = btn.textContent; btn.textContent = 'Saving…'; }
  else if (btn.textContent === 'Saving…') btn.textContent = btn.dataset.label;  // a success path may already have set the next label
}
// Save failed: a version conflict gets a Reload button (the typed text stays
// in the form until the user clicks it); anything else is a plain banner.
function showSaveError(err, bannerId, reload) {
  if (err.status === 409 && err.message === CONFLICT_MESSAGE && reload) {
    const el = document.getElementById(bannerId);
    el.innerHTML = `<div class="banner banner-error conflict-banner">${escapeHtml(CONFLICT_MESSAGE)} <button type="button" class="btn btn-secondary conflict-reload-btn">Reload</button></div>`;
    el.querySelector('.conflict-reload-btn').addEventListener('click', async () => {
      try { await reload(); showBanner(bannerId, ''); } catch (e) { showBanner(bannerId, e.message); }
    });
    el.scrollIntoView({ block: 'center' });  // Save sits at the bottom of a long modal; the banner is at the top, under a sticky header
  } else {
    showBanner(bannerId, err.message);
  }
  showErrorCross(err.message);
}

function showBanner(elId, message, type = 'error') {
  const el = document.getElementById(elId);
  el.innerHTML = message ? `<div class="banner banner-${type}">${escapeHtml(message)}</div>` : '';
}

// Animated tick (success) / cross (failure) toast — CSS-drawn, no sound.
function showTick(ok, message) {
  const el = document.createElement('div');
  el.className = 'tick-toast ' + (ok ? 'tick-ok' : 'tick-err');
  el.setAttribute('role', 'status');
  el.innerHTML = `<svg viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="23" pathLength="100"/><path d="${ok ? 'M15 27l7 7 15-15' : 'M18 18l16 16M34 18L18 34'}" pathLength="100"/></svg>`;
  if (message) el.append(message);
  document.body.appendChild(el);
  setTimeout(() => el.remove(), ok ? 1600 : 2600);
}
const showSuccessTick = (message) => showTick(true, message);
const showErrorCross = (message) => showTick(false, message);

// Resolves once each saved image (by its *-url endpoint) loads back from storage.
async function verifySavedImages(urlPaths) {
  const urls = await Promise.all(urlPaths.map(p => apiFetch(p).then(d => d.url)));
  await Promise.all(urls.map(src => new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = resolve;
    img.onerror = () => reject(new Error('Saved, but the uploaded image could not be loaded back.'));
    img.src = src;
  })));
}

// ---------- auth ----------
document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const username = document.getElementById('login-username').value;
  const password = document.getElementById('login-password').value;
  document.getElementById('login-error').innerHTML = '';
  try {
    const res = await fetch(API_BASE + '/auth/login', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'Invalid username or password.');
    }
    const data = await res.json();
    // Kept in memory only — the server also set an HttpOnly refresh
    // cookie, which is what survives a page reload, not this variable.
    recordActivity(true);
    applySession(data);
    enterApp();
  } catch (err) {
    document.getElementById('login-error').innerHTML =
      `<div class="error-msg">${escapeHtml(err.message)}</div>`;
  }
});

document.getElementById('show-register-btn').addEventListener('click', () => {
  document.getElementById('auth-card-title').textContent = 'Create an account';
  document.getElementById('auth-card-sub').textContent = 'Register to request access — an administrator will need to approve you before you can sign in.';
  document.getElementById('login-form').style.display = 'none';
  document.getElementById('register-form').style.display = 'block';
  document.getElementById('show-register-btn').parentElement.style.display = 'none';
  document.getElementById('show-login-wrap').style.display = 'block';
  document.getElementById('login-error').innerHTML = '';
});

document.getElementById('show-login-btn').addEventListener('click', () => {
  document.getElementById('auth-card-title').textContent = 'Ledger';
  document.getElementById('auth-card-sub').textContent = 'Sign in to manage documents, products, and search.';
  document.getElementById('login-form').style.display = 'block';
  document.getElementById('register-form').style.display = 'none';
  document.getElementById('show-register-btn').parentElement.style.display = 'block';
  document.getElementById('show-login-wrap').style.display = 'none';
  document.getElementById('login-error').innerHTML = '';
});

document.getElementById('register-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const username = document.getElementById('register-username').value;
  const password = document.getElementById('register-password').value;
  const requestedGroupId = document.getElementById('register-group').value || null;
  document.getElementById('login-error').innerHTML = '';
  try {
    const res = await fetch(API_BASE + '/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, requested_group_id: requestedGroupId })
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || 'Could not register.');
    document.getElementById('register-form').reset();
    document.getElementById('show-login-btn').click();
    document.getElementById('login-error').innerHTML =
      `<div class="banner banner-info">${escapeHtml(body.message || 'Registered — waiting for admin approval.')}</div>`;
  } catch (err) {
    document.getElementById('login-error').innerHTML =
      `<div class="error-msg">${escapeHtml(err.message)}</div>`;
  }
});

document.getElementById('logout-btn').addEventListener('click', () => doLogout());

// message: shown on the login screen (why the user was logged out).
// broadcast: tell the other tabs; false when this logout came from one.
// Every logout ends in a page reload: hiding #app used to leave the
// previous user's caches, permission flags, active view and rendered
// tables in place for whoever logged in next on the same page (an Editor
// saw the admin's Admin page). The reason is shown after the reload.
const LOGOUT_MESSAGE_KEY = 'ledger_logout_message';
let loggingOut = false;
async function doLogout(message = '', broadcast = true) {
  if (loggingOut) return;
  loggingOut = true;
  clearInterval(sessionTimer);
  token = null;
  if (broadcast) {
    // Revoke the refresh cookie server-side before reloading (at most 2 s),
    // so the reloaded page — and the other tabs told below — can't silently
    // sign back in with it.
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), 2000);
    await fetch(API_BASE + '/auth/logout', { method: 'POST', credentials: 'include', signal: abort.signal }).then(r => r.text()).catch(() => {});
    clearTimeout(timer);
    try { localStorage.setItem(LOGOUT_KEY, Date.now() + '|' + message); } catch (e) {}
  }
  try {
    sessionStorage.removeItem(CART_KEY);
    localStorage.removeItem('ledger_group_view'); // GROUP_VIEW_KEY (declared later in this file)
    localStorage.removeItem('ledger_token'); // cleanup of any token stored by a pre-cookie-migration session
    if (message) sessionStorage.setItem(LOGOUT_MESSAGE_KEY, message);
  } catch (e) {}
  location.replace('/');
}

// ---------- department groups (loop iteration 19) ----------
// Every document/customer/project/product belongs to one group; the server
// only returns the caller's own (app/core/group_scope.py). The Group select
// appears on the create forms only when there is a choice: superusers and
// users in several groups. Hidden on edit: a superuser moves records with "Move…" instead.
let myGroups = [];     // [{id, name}] the user may create records in
let groupNames = {};   // id -> name, for the labels in the lists
let defaultGroupId = '';
const GROUP_FORMS = ['doc-form', 'project-form', 'customer-form', 'product-form'];

async function loadMyGroups(me) {
  // /admin/groups: every group for a superuser, the user's own otherwise
  const groups = (await apiFetch('/admin/groups')) || [];
  groups.forEach(g => { groupNames[g.id] = g.name; });
  myGroups = groups.filter(g => g.is_active).sort((a, b) => a.name.localeCompare(b.name));
  const mine = (me.groups || []).map(g => g.group_id);
  const firstOwn = myGroups.find(g => mine.includes(g.id));
  defaultGroupId = firstOwn ? firstOwn.id : (me.is_superuser ? '' : (myGroups[0] || {}).id || '');
  GROUP_FORMS.forEach(formId => {
    let wrap = document.getElementById(formId + '-group-wrap');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.id = formId + '-group-wrap';
      wrap.innerHTML = `<label for="${formId}-group">Group</label><select id="${formId}-group"></select>`;
      document.getElementById(formId).prepend(wrap);
    }
    wrap.querySelector('select').innerHTML = '<option value="">— choose a group —</option>' +
      myGroups.map(g => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join('');
    showGroupSelect(formId, true);
  });
  // Bulk import: every imported product goes to this group.
  let importWrap = document.getElementById('bulk-import-group-wrap');
  if (!importWrap) {
    importWrap = document.createElement('div');
    importWrap.id = 'bulk-import-group-wrap';
    importWrap.innerHTML = '<label for="bulk-import-group">Group (every imported product goes to it)</label><select id="bulk-import-group"></select>';
    const btn = document.getElementById('bulk-import-submit-btn');
    btn.parentNode.insertBefore(importWrap, btn);
  }
  const importSelect = importWrap.querySelector('select');
  importSelect.innerHTML = document.getElementById('product-form-group').innerHTML;
  importSelect.value = defaultGroupId;
  importWrap.style.display = groupChoiceNeeded() ? '' : 'none';
  refreshMoveControls();
  restoreGroupView(me);
}

function groupChoiceNeeded() {
  return currentUserIsSuperuser || myGroups.length > 1;
}

function showGroupSelect(formId, creating) {
  const wrap = document.getElementById(formId + '-group-wrap');
  if (!wrap) return;
  const show = creating && groupChoiceNeeded();
  const select = wrap.querySelector('select');
  wrap.style.display = show ? '' : 'none';
  select.required = show;
  // a superuser viewing one group creates in it by default (still changeable)
  const viewing = currentUserIsSuperuser && !['', 'none'].includes(groupSwitcher.value) ? groupSwitcher.value : '';
  if (creating) select.value = viewing || defaultGroupId;
  if (select.selectedIndex < 0) select.value = defaultGroupId;
  if (formId === 'doc-form') refreshDocGroupPickers();
}

// The group the form's new record goes to, or undefined when the server decides (single group).
function chosenGroupId(formId) {
  const wrap = document.getElementById(formId + '-group-wrap');
  return wrap && wrap.style.display !== 'none' ? (wrap.querySelector('select').value || null) : undefined;
}

// In the document form, pickers offer only rows of the document's group.
function inDocGroup(row) {
  const groupId = chosenGroupId('doc-form');
  if (groupId) return row.group_id === groupId;
  if (editingDocumentId) {
    const doc = cachedDocuments.find(d => d.id === editingDocumentId);
    if (doc) return row.group_id === doc.group_id;
  }
  return true;
}

function refreshDocGroupPickers() {
  if (typeof refreshLineItemProductOptions === 'function') refreshLineItemProductOptions();
  if (typeof refreshCustomerPicker === 'function') refreshCustomerPicker();
  if (typeof refreshProjectPicker === 'function') refreshProjectPicker();
}

function groupTag(groupId) {
  const name = groupId ? (groupNames[groupId] || 'Other group') : 'Unassigned';
  return ` <span class="group-tag" style="display:inline-block; font-size:11px; padding:1px 6px; border-radius:8px; background:var(--surface-2, #eef); color:var(--text-dim); margin-left:4px;">${escapeHtml(name)}</span>`;
}

document.addEventListener('change', e => {
  if (e.target && e.target.id === 'doc-form-group') refreshDocGroupPickers();
});
['doc', 'project', 'customer', 'product'].forEach(kind => {
  // after the form's own click handler (which resets the form)
  document.getElementById('toggle-' + kind + '-form').addEventListener('click', () => setTimeout(() => showGroupSelect(kind + '-form', true)));
});

async function enterApp() {
  startSessionWatch();
  // Defence in depth (logout reloads the page anyway): start from "nothing
  // permission-gated is shown, Documents view", then let /admin/me show
  // what this user may see.
  ['admin-nav-item', 'backup-nav-item', 'groups-card', 'group-select-wrap', 'all-users-card', 'new-group-form-wrap', 'category-split-btn-wrap', 'unassigned-card', 'group-switcher-wrap']
    .forEach(id => { document.getElementById(id).style.display = 'none'; });
  currentUserIsSuperuser = false;
  currentUserCanDeleteDocuments = false;
  document.querySelector('.nav-item[data-view="documents"]').click();
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app').classList.add('active');
  try {
    const me = await apiFetch('/admin/me');
    document.getElementById('whoami-username').textContent = me.username + (me.is_superuser ? ' (superuser)' : '');
    currentUserIsSuperuser = me.is_superuser;
    restoreCart(me.username);
    const isAnyGroupAdmin = me.groups && me.groups.some(g => g.is_group_admin);
    const canManageUsers = me.is_superuser || isAnyGroupAdmin;
    if (canManageUsers) {
      document.getElementById('admin-nav-item').style.display = 'block';
      document.getElementById('groups-card').style.display = 'block';
      const adminGroupIds = me.is_superuser ? null : me.groups.filter(g => g.is_group_admin).map(g => g.group_id);
      // Hide the picker only when there's nothing to pick between: a
      // superuser (or multi-group admin) needs it to choose which group to
      // manage; a single-group admin gets that one group auto-selected by
      // loadAdminGroups below, so the dropdown would just be clutter.
      const showPicker = me.is_superuser || (adminGroupIds && adminGroupIds.length > 1);
      document.getElementById('group-select-wrap').style.display = showPicker ? 'block' : 'none';
      loadAdminGroups(adminGroupIds).then(loadPendingUsers);
      document.getElementById('all-users-card').style.display = 'block';
      loadAllUsers();
    }
    if (me.is_superuser) {
      document.getElementById('new-group-form-wrap').style.display = 'block';
      // Superuser only — the gateway refuses /api/backup/* to anyone else too.
      document.getElementById('backup-nav-item').style.display = 'block';
      document.getElementById('unassigned-card').style.display = 'block';
      loadUnassignedCard();
    }
    const canManageCategories = me.is_superuser || (me.service_access && me.service_access.includes('categories'));
    document.getElementById('category-split-btn-wrap').style.display = canManageCategories ? 'inline-flex' : 'none';
    // Delete is a separate permission from documents' own edit access —
    // see docs/SECURITY_HARDENING_LOG.md's "Delete permission" entry.
    // Enforced server-side regardless; this only controls whether the
    // Delete button is rendered at all (never shown-then-rejected).
    currentUserCanDeleteDocuments = me.is_superuser || (me.service_access && me.service_access.includes('documents-delete'));
    await loadMyGroups(me);
  } catch (e) {
    document.getElementById('whoami-username').textContent = '—';
  }
  loadDocuments();
  loadProducts();
  initDeleteAll(); // DEV-ONLY: delete-all
  loadCustomers();
  loadCompanies();
  loadProjects();
  loadCategories();
}

// ---------- session: idle timeout, max session, all tabs ----------
// The server ends a session after `idle_timeout_s` without a /refresh and at
// `expires_at` regardless (see auth-service refresh_tokens.py); both values
// come from the login/refresh response. Here "activity" means the user's
// own input. The token is refreshed only when there was activity since the
// last refresh, so a tab left open never keeps a session alive by itself.
// Tabs share the activity time and logouts through localStorage: timestamps
// only, never a token.
const ACTIVITY_KEY = 'ledger_last_activity';
const LOGOUT_KEY = 'ledger_logout';
const REFRESH_AT_KEY = 'ledger_refresh_at';
let sessionInfo = null;          // {idle_timeout_s, warning_s, expires_at}
let tokenExpiresAt = 0;          // ms, from the access token's exp claim
let lastRefreshAt = 0;           // ms
let lastActivity = Date.now();   // loading the page counts as activity
let sessionTimer = null;
let sessionWarningOpen = false;

function applySession(data) {
  token = data.access_token;
  sessionInfo = data.session || null;
  lastRefreshAt = Date.now();
  try {
    const claims = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    tokenExpiresAt = claims.exp * 1000;
  } catch (e) {
    tokenExpiresAt = 0;
  }
}

function idleLimitMs() {
  return (sessionInfo ? sessionInfo.idle_timeout_s : 30 * 60) * 1000;
}

function isIdle() {
  return Date.now() - lastActivity >= idleLimitMs();
}

function idleLogoutMessage() {
  const s = idleLimitMs() / 1000;
  return `Logged out after ${s % 60 === 0 ? (s / 60) + ' minutes' : s + ' seconds'} of inactivity.`;
}
const EXPIRED_MESSAGE = 'Your session expired, please log in again.';

let lastActivityBroadcast = 0;
function recordActivity(force = false) {
  // While the warning is up only "Stay logged in" counts, so a stray mouse
  // movement doesn't dismiss it.
  if (sessionWarningOpen && !force) return;
  lastActivity = Date.now();
  if (force || lastActivity - lastActivityBroadcast > 10000) {  // throttled to ~1 per 10 s
    lastActivityBroadcast = lastActivity;
    try { localStorage.setItem(ACTIVITY_KEY, String(lastActivity)); } catch (e) {}
  }
}
['mousedown', 'mousemove', 'keydown', 'scroll', 'wheel', 'touchstart'].forEach(type =>
  window.addEventListener(type, () => recordActivity(), { passive: true, capture: true }));

window.addEventListener('storage', (e) => {
  if (e.key === ACTIVITY_KEY && e.newValue) {
    lastActivity = Math.max(lastActivity, Number(e.newValue));
    if (sessionWarningOpen && !isIdle()) hideSessionWarning();
  } else if (e.key === LOGOUT_KEY && e.newValue && token) {
    doLogout(e.newValue.split('|')[1] || '', false);
  }
});

function formIsDirty() {
  return ['doc', 'product'].some(name =>
    document.getElementById(name + '-modal').classList.contains('active')
    && document.getElementById(name + '-form').dataset.dirty === '1');
}
['doc', 'product'].forEach(name => {
  const form = document.getElementById(name + '-form');
  form.addEventListener('input', () => { form.dataset.dirty = '1'; });
  form.addEventListener('reset', () => { form.dataset.dirty = ''; });
  document.getElementById(name + '-modal-close').addEventListener('click', () => { form.dataset.dirty = ''; });
});

function showSessionWarning(secondsLeft) {
  document.getElementById('session-warning-countdown').textContent = secondsLeft;
  document.getElementById('session-warning-dirty').style.display = formIsDirty() ? 'block' : 'none';
  if (!sessionWarningOpen) {
    sessionWarningOpen = true;
    document.getElementById('session-warning-modal').classList.add('active');
    document.getElementById('session-warning-stay').focus();
  }
}

function hideSessionWarning() {
  sessionWarningOpen = false;
  document.getElementById('session-warning-modal').classList.remove('active');
}

document.getElementById('session-warning-stay').addEventListener('click', () => {
  hideSessionWarning();
  recordActivity(true);
  refreshAccessToken().catch(() => doLogout(EXPIRED_MESSAGE));
});
document.getElementById('session-warning-logout').addEventListener('click', () => doLogout());

function sessionTick() {
  if (!token) return;
  const now = Date.now();
  const sinceActivity = now - lastActivity;
  if (sessionInfo && now >= Date.parse(sessionInfo.expires_at)) {
    doLogout(EXPIRED_MESSAGE);
    return;
  }
  if (sinceActivity >= idleLimitMs()) {
    doLogout(idleLogoutMessage());
    return;
  }
  const warnMs = (sessionInfo ? sessionInfo.warning_s : 60) * 1000;
  if (sinceActivity >= idleLimitMs() - warnMs) {
    showSessionWarning(Math.ceil((idleLimitMs() - sinceActivity) / 1000));
  } else if (sessionWarningOpen) {
    hideSessionWarning();
  }
  // Keep the server-side session alive while the user is active: refresh
  // halfway through the idle window, or shortly before the access token
  // expires, but only if there was activity since the last refresh.
  // (A token capped at the session's hard end can't be extended, so its
  // expiry doesn't make a refresh due.)
  let due = lastRefreshAt + idleLimitMs() / 2;
  const cappedAtSessionEnd = sessionInfo && tokenExpiresAt >= Date.parse(sessionInfo.expires_at) - 1000;
  if (!cappedAtSessionEnd) due = Math.min(due, tokenExpiresAt - 60000);
  if (now >= due && lastActivity > lastRefreshAt && !refreshPromise) {
    refreshAccessToken().catch(() => doLogout(EXPIRED_MESSAGE));
  }
}

function startSessionWatch() {
  clearInterval(sessionTimer);
  sessionTimer = setInterval(sessionTick, 1000);
}

// ---------- my sessions / an admin's view of a user's sessions ----------
function describeUserAgent(ua) {
  if (!ua) return 'Unknown browser';
  const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox'
    : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
  const os = /Windows/.test(ua) ? 'Windows' : /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS'
    : /Mac OS X/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : '';
  return os ? `${browser} on ${os}` : browser;
}

let sessionsView = null;  // {listPath, revokePath(sid) | null, logoutAllPath, self}

async function openSessionsModal(view) {
  sessionsView = view;
  document.getElementById('sessions-modal-title').textContent = view.title;
  document.getElementById('sessions-logout-all-btn').textContent = view.self ? 'Log out all devices' : 'Log out everywhere';
  showBanner('sessions-modal-banner', '');
  document.getElementById('sessions-modal').classList.add('active');
  await loadSessionsList();
}

async function loadSessionsList() {
  const tbody = document.getElementById('sessions-tbody');
  tbody.innerHTML = skeletonRows(5);
  try {
    const sessions = await apiFetch(sessionsView.listPath);
    if (!sessions.length) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="5">No active sessions.</td></tr>';
      return;
    }
    tbody.innerHTML = sessions.map(s => `
      <tr>
        <td>${escapeHtml(describeUserAgent(s.user_agent))}${s.current ? ' <span class="stamp stamp-admin">this device</span>' : ''}</td>
        <td class="mono">${escapeHtml(s.ip || '—')}</td>
        <td>${escapeHtml(new Date(s.started_at).toLocaleString())}</td>
        <td>${escapeHtml(new Date(s.last_activity_at).toLocaleString())}</td>
        <td>${sessionsView.revokePath ? `<button class="link-btn-inline" data-revoke-session-id="${s.id}" style="color:var(--danger);">Revoke</button>` : ''}</td>
      </tr>
    `).join('');
    tbody.querySelectorAll('[data-revoke-session-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          await apiFetch(sessionsView.revokePath(btn.dataset.revokeSessionId), { method: 'POST' });
          loadSessionsList();
        } catch (err) {
          showBanner('sessions-modal-banner', err.message);
        }
      });
    });
  } catch (err) {
    tbody.innerHTML = '';
    showBanner('sessions-modal-banner', err.message);
  }
}

document.getElementById('sessions-modal-close').addEventListener('click', () => {
  document.getElementById('sessions-modal').classList.remove('active');
});

document.getElementById('sessions-logout-all-btn').addEventListener('click', async () => {
  const prompt = sessionsView.self
    ? 'Log out of every device, including this one?'
    : 'End all of this user\'s sessions? They will have to log in again everywhere.';
  if (!confirm(prompt)) return;
  try {
    await apiFetch(sessionsView.logoutAllPath, { method: 'POST' });
    if (sessionsView.self) {
      document.getElementById('sessions-modal').classList.remove('active');
      doLogout('Logged out of all devices.');
    } else {
      loadSessionsList();
    }
  } catch (err) {
    showBanner('sessions-modal-banner', err.message);
  }
});

document.getElementById('my-sessions-btn').addEventListener('click', () => openSessionsModal({
  title: 'My sessions', listPath: '/auth/sessions', revokePath: null, logoutAllPath: '/auth/logout-all', self: true,
}));

// ---------- navigation ----------
document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    item.classList.add('active');
    document.getElementById('view-' + item.dataset.view).classList.add('active');
  });
});

// ---------- documents ----------
document.getElementById('toggle-doc-form').addEventListener('click', () => {
  editingDocumentId = null;
  document.getElementById('doc-form').reset();
  setDocCustomerMode('saved');
  document.getElementById('doc-customer-hint').style.display = 'none';
  document.getElementById('doc-form-title').textContent = 'New document';
  document.getElementById('doc-form-submit-btn').textContent = 'Create document';
  document.getElementById('doc-type').disabled = false;
  document.getElementById('doc-number').disabled = false;
  document.getElementById('doc-number').value = '';
  document.getElementById('doc-number').placeholder = 'Auto-generating…';
  document.getElementById('line-items').innerHTML = '';
  addLineItem();
  document.getElementById('doc-director').innerHTML = '<option value="">— none —</option>';
  prefillNextDocNumber();
  document.getElementById('doc-modal').classList.add('active');
});

document.getElementById('doc-modal-close').addEventListener('click', () => {
  document.getElementById('doc-modal').classList.remove('active');
});

async function prefillNextDocNumber(forceOverwrite = false) {
  try {
    const companyId = resolveDocCompanyIdFromInput();
    const qs = companyId ? ('?company_id=' + encodeURIComponent(companyId)) : '';
    const result = await apiFetch('/documents/next-number' + qs);
    // Only fill it in if the user hasn't already started typing their own
    // while this was loading, and hasn't switched to editing a document —
    // unless forceOverwrite is set (used when the Company selection changes,
    // since the number's prefix should then match the newly chosen company).
    const field = document.getElementById('doc-number');
    if ((forceOverwrite || !field.value) && !editingDocumentId) {
      field.value = result.doc_number;
      field.dataset.preview = result.doc_number;  // untouched preview -> let the server take the number (its counter is locked)
    }
  } catch (err) {
    // non-fatal — the field just stays blank and the server will
    // generate a number at submit time either way.
  }
}

document.getElementById('doc-company').addEventListener('change', () => {
  if (!editingDocumentId) prefillNextDocNumber(true);
  refreshDocDirectorPicker();
});
document.getElementById('doc-company').addEventListener('input', () => {
  if (!editingDocumentId) prefillNextDocNumber(true);
  refreshDocDirectorPicker();
});

async function refreshDocDirectorPicker(selectDirectorId) {
  const select = document.getElementById('doc-director');
  const companyId = resolveDocCompanyIdFromInput();
  if (!companyId) {
    select.innerHTML = '<option value="">— none —</option>';
    return;
  }
  try {
    const directors = await apiFetch('/companies/' + companyId + '/directors');
    select.innerHTML = '<option value="">— none —</option>' +
      directors.map(d => `<option value="${d.id}">${escapeHtml(d.name)}</option>`).join('');
    if (selectDirectorId) select.value = selectDirectorId;
  } catch (err) {
    select.innerHTML = '<option value="">— none —</option>';
  }
}

let editingDocumentId = null;
let cachedDocuments = [];
let lineItemCount = 0;
function addLineItem(prefill) {
  lineItemCount++;
  const id = lineItemCount;
  const div = document.createElement('div');
  div.className = 'line-item-row';
  div.dataset.lineId = id;

  let prefillProductDisplay = '';
  if (prefill && prefill.product_id) {
    const p = cachedProducts.find(p => p.id === prefill.product_id);
    if (p) {
      prefillProductDisplay = `${p.sku} — ${p.name}`;
      div.dataset.resolvedProductId = p.id;
    }
  }

  const safeVal = (v, fallback = '') => (v === undefined || v === null) ? fallback : v;

  div.innerHTML = `
    <div style="flex: 1.4;">
      <label>Product (optional)</label>
      <input type="text" class="li-product-search" list="product-datalist" placeholder="Type to search products…" autocomplete="off" value="${escapeHtml(prefillProductDisplay)}">
    </div>
    <input type="hidden" class="li-desc" value="${prefill ? escapeHtml(prefill.description || '') : ''}">
    <div style="max-width:70px;"><label>Qty</label><input type="text" class="li-qty" placeholder="1" value="${prefill ? safeVal(prefill.quantity) : ''}"></div>
    <div style="max-width:80px;"><label>Unit</label><input type="text" class="li-unit" placeholder="No" value="${prefill ? escapeHtml(prefill.unit || 'No') : 'No'}"></div>
    <div><label>Unit price</label><input type="text" class="li-price" placeholder="20.00" value="${prefill ? safeVal(prefill.unit_price) : ''}"></div>
    <div class="labour-only" style="max-width:110px;"><label>Labour unit price</label><input type="text" class="li-labour" placeholder="optional" value="${prefill ? safeVal(prefill.labour_unit_price) : ''}"></div>
    <div class="labour-only li-line-totals" style="max-width:150px;"><label>Material / Labour total</label><span class="li-mat-total">0.00</span> / <span class="li-lab-total">-</span></div>
    <div><label>Remark</label><input type="text" class="li-remark" placeholder="Optional note" value="${prefill ? escapeHtml(prefill.remark || '') : ''}"></div>
    <button type="button" class="remove-line" data-line-id="${id}">Remove</button>
  `;
  document.getElementById('line-items').appendChild(div);
  div.querySelector('.remove-line').addEventListener('click', () => { div.remove(); updateDocumentTotals(); });
  div.querySelector('.li-product-search').addEventListener('input', (e) => {
    const typed = e.target.value.trim();
    const descInput = div.querySelector('.li-desc');
    const qtyInput = div.querySelector('.li-qty');
    const remarkInput = div.querySelector('.li-remark');
    const unitInput = div.querySelector('.li-unit');
    const match = typed && cachedProducts.filter(inDocGroup).find(p => `${p.sku} — ${p.name}`.toLowerCase() === typed.toLowerCase());
    if (match) {
      div.dataset.resolvedProductId = match.id;
      descInput.value = match.name;
      if (!qtyInput.value) qtyInput.value = '1';
      if (!remarkInput.value && match.remark) remarkInput.value = match.remark;
      if (/^(nos?)?$/i.test(unitInput.value.trim())) unitInput.value = match.unit || 'No'; // a typed unit wins
      updateDocumentTotals();
    } else {
      delete div.dataset.resolvedProductId;
      if (!typed) descInput.value = '';
    }
  });
  div.querySelector('.li-price').addEventListener('input', (e) => {
    e.target.classList.remove('field-error');
  });
  updateDocumentTotals();
}
document.getElementById('add-line-btn').addEventListener('click', addLineItem);

document.getElementById('line-items').addEventListener('input', (e) => {
  if (e.target.matches('.li-qty, .li-price, .li-labour')) updateDocumentTotals();
});
document.getElementById('doc-tax-rate').addEventListener('input', updateDocumentTotals);
document.getElementById('doc-include-labour').addEventListener('change', updateDocumentTotals);

// Live preview only — the server recalculates every total on save. Tax is on
// materials only; labour is added untaxed when "Include labour" is ticked.
function updateDocumentTotals() {
  const withLabour = document.getElementById('doc-include-labour').checked;
  document.getElementById('doc-form').classList.toggle('with-labour', withLabour);
  let subtotal = 0;
  let labour = 0;
  document.querySelectorAll('#line-items .line-item-row').forEach(row => {
    const qty = Number(row.querySelector('.li-qty').value) || 0;
    const price = Number(row.querySelector('.li-price').value) || 0;
    const lab = qty * (Number(row.querySelector('.li-labour').value) || 0);
    subtotal += qty * price;
    labour += lab;
    row.querySelector('.li-mat-total').textContent = (qty * price).toFixed(2);
    row.querySelector('.li-lab-total').textContent = lab ? lab.toFixed(2) : '-';
    const unitInput = row.querySelector('.li-unit'); // No <-> Nos follows the quantity live
    if (/^(nos?)?$/i.test(unitInput.value.trim())) unitInput.value = displayUnit(unitInput.value, qty);
  });
  if (!withLabour) labour = 0;
  const taxRate = Number(document.getElementById('doc-tax-rate').value) || 0;
  const taxTotal = subtotal * (taxRate / 100);
  const currency = document.getElementById('doc-currency').value;
  document.getElementById('doc-totals-subtotal-label').style.display = withLabour ? 'none' : '';
  document.getElementById('doc-totals-grand-label').textContent = withLabour ? 'Grand Total' : 'Total with tax';
  document.getElementById('doc-totals-subtotal').textContent = subtotal.toFixed(2) + ' ' + currency;
  document.getElementById('doc-totals-labour').textContent = labour.toFixed(2) + ' ' + currency;
  document.getElementById('doc-totals-grand').textContent = (subtotal + taxTotal + labour).toFixed(2) + ' ' + currency;
}
document.getElementById('doc-currency').addEventListener('change', updateDocumentTotals);

// Customer: a saved customer (customer_id) or a one-time customer typed for
// this document only (end_user_* fields, never added to Customers).
const DOC_END_USER_FIELDS = ['name', 'organization', 'address', 'phone', 'email'];
function setDocCustomerMode(mode) {
  document.getElementById('doc-customer-mode-' + mode).checked = true;
  document.getElementById('doc-customer-saved-wrap').style.display = mode === 'saved' ? '' : 'none';
  document.getElementById('doc-customer-manual-wrap').style.display = mode === 'manual' ? '' : 'none';
}
function docCustomerMode() {
  return document.getElementById('doc-customer-mode-manual').checked ? 'manual' : 'saved';
}
document.querySelectorAll('input[name="doc-customer-mode"]').forEach(r =>
  r.addEventListener('change', () => setDocCustomerMode(r.value)));

document.getElementById('doc-customer').addEventListener('input', (e) => {
  const hint = document.getElementById('doc-customer-hint');
  const typed = e.target.value.trim();
  const matches = typed && cachedCustomers.some(c => c.name.toLowerCase() === typed.toLowerCase());
  hint.style.display = (typed && !matches) ? 'block' : 'none';
});

function refreshLineItemProductOptions() {
  const datalist = document.getElementById('product-datalist');
  if (!datalist) return;
  datalist.innerHTML = cachedProducts.filter(inDocGroup).map(p => `<option value="${escapeHtml(p.sku)} — ${escapeHtml(p.name)}">`).join('');
}

document.getElementById('doc-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner('doc-modal-banner', '');
  document.querySelectorAll('#line-items .li-price.field-error, #line-items .li-labour.field-error').forEach(el => el.classList.remove('field-error'));
  const overallTaxRate = Number(document.getElementById('doc-tax-rate').value) || 0;
  const includeLabour = document.getElementById('doc-include-labour').checked;
  const items = [];
  const incompleteRows = [];
  document.querySelectorAll('#line-items .line-item-row').forEach((row, rowIndex) => {
    const product_id = row.dataset.resolvedProductId || null;
    let description = row.querySelector('.li-desc').value;
    const remark = row.querySelector('.li-remark').value;
    const quantity = row.querySelector('.li-qty').value;
    const unit = row.querySelector('.li-unit').value.trim() || 'No';
    const unit_price = row.querySelector('.li-price').value;

    const hasSomeData = product_id || description || remark || unit_price;
    if (!hasSomeData) return; // a genuinely blank/unused row — fine to ignore silently

    if (!quantity) {
      incompleteRows.push(`Row ${rowIndex + 1}: needs a quantity`);
      return;
    }
    if (isNaN(Number(quantity))) {
      incompleteRows.push(`Row ${rowIndex + 1}: quantity must be a number`);
      return;
    }
    if (!product_id && !remark) {
      incompleteRows.push(`Row ${rowIndex + 1}: needs either a product, or a remark describing what this line item is`);
      return;
    }
    if (!unit_price) {
      row.querySelector('.li-price').classList.add('field-error');
      incompleteRows.push(`Row ${rowIndex + 1}: needs a unit price — products don't carry a catalog price anymore, enter it per document`);
      return;
    }
    if (isNaN(Number(unit_price))) {
      incompleteRows.push(`Row ${rowIndex + 1}: unit price must be a number`);
      return;
    }
    const labour = row.querySelector('.li-labour').value.trim();
    if (includeLabour && labour && !(Number(labour) >= 0)) {
      row.querySelector('.li-labour').classList.add('field-error');
      incompleteRows.push(`Row ${rowIndex + 1}: labour unit price must be a number, 0 or more`);
      return;
    }
    if (!product_id && !description) description = remark; // free-text row — use the remark as its name/description too, so exports aren't blank

    const item = { quantity: Number(quantity), tax_rate: overallTaxRate, unit };
    if (product_id) item.product_id = product_id;
    if (description) item.description = description;
    if (remark) item.remark = remark;
    if (unit_price) item.unit_price = Number(unit_price);
    if (includeLabour && labour) item.labour_unit_price = Number(labour); // unticked: labour is dropped on save
    items.push(item);
  });
  if (incompleteRows.length > 0) {
    showBanner('doc-modal-banner', 'Fix these before saving — nothing was changed: ' + incompleteRows.join('; '));
    const firstError = document.querySelector('#line-items .field-error');
    if (firstError) firstError.scrollIntoView({ behavior: 'smooth', block: 'center' });
    return;
  }
  if (items.length === 0) {
    showBanner('doc-modal-banner', 'Add at least one line item: either pick a product, or fill in description + quantity + price.');
    return;
  }
  const manualCustomer = docCustomerMode() === 'manual';
  if (manualCustomer && !document.getElementById('doc-end-user-name').value.trim()) {
    showBanner('doc-modal-banner', 'Enter the customer name, or switch to "Saved customer".');
    return;
  }
  const endUser = {};
  DOC_END_USER_FIELDS.forEach(f => {
    endUser['end_user_' + f] = manualCustomer ? (document.getElementById('doc-end-user-' + f).value.trim() || null) : null;
  });
  try {
    setBusy(document.getElementById('doc-form-submit-btn'), true);
    const commonPayload = {
      customer_id: manualCustomer ? null : resolveCustomerIdFromInput(),
      ...endUser,
      project_id: resolveDocProjectIdFromInput(),
      company_id: resolveDocCompanyIdFromInput(),
      director_id: document.getElementById('doc-director').value || null,
      currency: document.getElementById('doc-currency').value,
      terms_and_conditions: document.getElementById('doc-terms').value || null,
      include_labour: includeLabour,
      items
    };
    if (editingDocumentId) {
      await apiFetch('/documents/' + editingDocumentId, { method: 'PATCH', body: JSON.stringify({ ...commonPayload, version: editingVersions.document }) });
    } else {
      await apiFetch('/documents', {
        method: 'POST',
        headers: idempotencyHeader('document'),
        body: JSON.stringify({
          group_id: chosenGroupId('doc-form'),
          doc_type: document.getElementById('doc-type').value,
          doc_number: (v => (v && v !== document.getElementById('doc-number').dataset.preview) ? v : null)(document.getElementById('doc-number').value),
          ...commonPayload
        })
      });
    }
    document.getElementById('doc-form').reset();
    setDocCustomerMode('saved');
    document.getElementById('doc-customer-hint').style.display = 'none';
    document.getElementById('line-items').innerHTML = '';
    addLineItem();
    document.getElementById('doc-modal').classList.remove('active');
    document.getElementById('doc-form-title').textContent = 'New document';
    document.getElementById('doc-form-submit-btn').textContent = 'Create document';
    document.getElementById('doc-type').disabled = false;
    document.getElementById('doc-number').disabled = false;
    editingDocumentId = null;
    idempotencyDone('document');
    loadDocuments();
    showSuccessTick('Document saved');
  } catch (err) {
    const id = editingDocumentId;
    showSaveError(err, 'doc-modal-banner', id && (async () => { startEditDocument(await apiFetch('/documents/' + id)); loadDocuments(); }));
  } finally {
    setBusy(document.getElementById('doc-form-submit-btn'), false);
  }
});

function startEditDocument(doc) {
  editingDocumentId = doc.id;
  editingVersions.document = doc.version;
  showGroupSelect('doc-form', false);
  document.getElementById('doc-modal').classList.add('active');
  document.getElementById('doc-form-title').textContent = 'Edit document — ' + doc.doc_number;
  document.getElementById('doc-form-submit-btn').textContent = 'Save changes';

  document.getElementById('doc-type').value = doc.doc_type;
  document.getElementById('doc-type').disabled = true;
  document.getElementById('doc-number').value = doc.doc_number;
  document.getElementById('doc-number').disabled = true;

  const customer = cachedCustomers.find(c => c.id === doc.customer_id);
  document.getElementById('doc-customer').value = customer ? customer.name : '';
  document.getElementById('doc-customer-hint').style.display = 'none';
  DOC_END_USER_FIELDS.forEach(f => {
    document.getElementById('doc-end-user-' + f).value = doc['end_user_' + f] || '';
  });
  setDocCustomerMode(doc.end_user_name ? 'manual' : 'saved');

  document.getElementById('doc-currency').value = doc.currency || 'USD';
  const docProject = cachedProjects.find(p => p.id === doc.project_id);
  document.getElementById('doc-project').value = docProject
    ? `${docProject.name}${docProject.budget_year ? ' (' + docProject.budget_year + ')' : ''}`
    : '';
  const docCompany = cachedCompanies.find(c => c.id === doc.company_id);
  document.getElementById('doc-company').value = docCompany ? docCompany.name : '';
  refreshDocDirectorPicker(doc.director_id);
  document.getElementById('doc-terms').value = doc.terms_and_conditions || '';

  document.getElementById('doc-include-labour').checked = !!doc.include_labour;
  document.getElementById('line-items').innerHTML = '';
  lineItemCount = 0;
  doc.items.forEach(item => addLineItem(item));
  document.getElementById('doc-tax-rate').value = doc.items.length ? (doc.items[0].tax_rate || 0) : 0;
  updateDocumentTotals();
  if (doc.items.length === 0) addLineItem();

  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function downloadDocumentCatalogue(documentId, docNumber, btn) {
  showBanner('documents-banner', '');
  const originalLabel = btn ? btn.textContent : null;
  if (btn) {
    btn.textContent = 'Preparing…';
    btn.disabled = true;
  }
  try {
    const res = await fetch(API_BASE + '/documents/' + documentId + '/export/catalogue', {
      headers: { 'Authorization': 'Bearer ' + token }
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'Could not generate the catalogue bundle.');
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = docNumber + '-catalogue.zip';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showBanner('documents-banner', err.message);
  } finally {
    if (btn) {
      btn.textContent = originalLabel;
      btn.disabled = false;
    }
  }
}

async function downloadQuotation(documentId, docNumber, format, btn) {
  showBanner('documents-banner', '');
  const endpoint = format === 'pdf' ? '/export/quotation-pdf' : '/export/quotation';
  const extension = format === 'pdf' ? 'pdf' : 'xlsx';
  const originalLabel = btn ? btn.textContent : null;
  if (btn) {
    btn.textContent = 'Preparing…';
    btn.disabled = true;
  }
  try {
    const res = await fetch(API_BASE + '/documents/' + documentId + endpoint, {
      headers: { 'Authorization': 'Bearer ' + token }
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || 'Could not generate quotation.');
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    // The server names the file after the document type (Invoice-…, Proposal-…).
    const named = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') || '');
    a.download = named ? named[1] : 'Quotation-' + docNumber + '.' + extension;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showBanner('documents-banner', err.message);
  } finally {
    if (btn) {
      btn.textContent = originalLabel;
      btn.disabled = false;
    }
  }
}

async function loadDocuments() {
  const tbody = document.getElementById('documents-tbody');
  const mySeq = startLoad('documents');
  try {
    const docs = await apiFetch('/documents');
    if (isStaleLoad('documents', mySeq)) return; // a newer load has since started — discard this one
    cachedDocuments = docs;
    applyFilterAndSort('documents');
  } catch (err) {
    if (isStaleLoad('documents', mySeq)) return;
    tbody.innerHTML = `<tr class="empty-row"><td colspan="13">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById('view-document-close').addEventListener('click', () => {
  document.getElementById('view-document-modal').classList.remove('active');
});

async function openViewDocumentModal(doc) {
  document.getElementById('view-document-title').textContent = 'Document — ' + doc.doc_number;
  document.getElementById('view-document-content').innerHTML = renderDocumentDetailContent(doc, {});
  document.getElementById('view-document-modal').classList.add('active');

  // Fetch sub-items for every product-based line item (in parallel), then
  // re-render with them included — same "position" numbering the
  // Catalogue zip export uses, so what you see here matches what you'd
  // get in that download.
  const subItemsByProductId = {};
  const productIds = [...new Set((doc.items || []).filter(i => i.product_id).map(i => i.product_id))];
  await Promise.all(productIds.map(async (pid) => {
    try {
      const subItems = await apiFetch('/products/' + pid + '/sub-items');
      if (subItems.length) subItemsByProductId[pid] = subItems;
    } catch (err) {
      // non-fatal — that item's row just won't show a sub-items list
    }
  }));

  document.getElementById('view-document-content').innerHTML = renderDocumentDetailContent(doc, subItemsByProductId);
}

function renderDocumentDetailContent(doc, subItemsByProductId) {
  const project = cachedProjects.find(p => p.id === doc.project_id);

  let catalogueNumber = 0; // matches the Catalogue zip export's numbering — only product-based items count
  const itemRows = (doc.items || []).map(item => {
    if (item.product_id) catalogueNumber++;
    const subItems = item.product_id ? subItemsByProductId[item.product_id] : null;

    const mainRow = `
      <tr>
        <td>${escapeHtml(item.description || '—')}${item.product_id ? ` <span class="mono" style="color:var(--text-dim); font-size:12px;">(#${catalogueNumber})</span>` : ''}</td>
        <td class="mono">${item.quantity != null ? item.quantity : '—'}</td>
        <td>${escapeHtml(displayUnit(item.unit, item.quantity))}</td>
        <td class="mono">${fmtMoney(item.unit_price)}</td>
        <td class="mono">${item.tax_rate != null ? item.tax_rate : 0}%</td>
        <td class="mono">${fmtMoney(item.line_total)}</td>
      </tr>
    `;

    if (!subItems) return mainRow;

    const subRows = subItems.map(sub => `
      <tr style="background:var(--paper-dim);">
        <td style="padding-left:28px; font-size:13px;">
          <span class="mono" style="color:var(--text-dim);">${catalogueNumber}.${sub.sequence_number}</span>
          ${escapeHtml(sub.sku)} — ${escapeHtml(sub.name)}${sub.has_file ? '' : ' <span style="color:var(--text-dim); font-size:12px;">(no catalog file)</span>'}
        </td>
        <td class="mono" style="font-size:13px;">1.00</td>
        <td style="font-size:13px;">${escapeHtml(displayUnit(sub.unit, 1))}</td>
        <td class="mono" style="font-size:13px;">${sub.unit_price != null ? fmtMoney(sub.unit_price) : '—'}</td>
        <td style="font-size:13px;">—</td>
        <td class="mono" style="font-size:13px;">${sub.unit_price != null ? fmtMoney(sub.unit_price) : '—'}</td>
      </tr>
    `).join('');

    return mainRow + subRows;
  }).join('');

  return `
    <div class="form-row">
      <div><strong>Type:</strong> <span class="${stampClass(doc.doc_type)}">${escapeHtml(doc.doc_type)}</span></div>
      <div><strong>Status:</strong> <span class="${stampClass(doc.status)}">${escapeHtml(doc.status)}</span></div>
    </div>
    <div class="form-row">
      <div><strong>Customer:</strong> ${escapeHtml(docCustomerName(doc))}</div>
      <div><strong>Project:</strong> ${project ? escapeHtml(project.name) : '—'}</div>
    </div>
    <div class="form-row">
      <div><strong>Issue date:</strong> ${doc.issue_date ? fmtDate(doc.issue_date) : '—'}</div>
      <div><strong>Due date:</strong> ${doc.due_date ? fmtDate(doc.due_date) : '—'}</div>
    </div>
    <table style="margin-top:14px;">
      <thead><tr><th>Description</th><th>Qty</th><th>Unit</th><th>Unit price</th><th>Tax</th><th>Line total</th></tr></thead>
      <tbody>${itemRows || '<tr class="empty-row"><td colspan="6">No line items.</td></tr>'}</tbody>
    </table>
    <div style="text-align:right; margin-top:10px; font-size:14px;">
      <div>Subtotal: <span class="mono">${fmtMoney(doc.subtotal)}</span></div>
      <div>Tax: <span class="mono">${fmtMoney(doc.tax_total)}</span></div>
      ${doc.include_labour ? `<div>Labour (not taxed): <span class="mono">${fmtMoney((doc.items || []).reduce((s, i) => s + Number(i.quantity || 0) * Number(i.labour_unit_price || 0), 0))}</span></div>` : ''}
      <div style="font-weight:600; margin-top:4px;">Total: <span class="mono">${fmtMoney(doc.total)} ${escapeHtml(doc.currency || '')}</span></div>
    </div>
    ${doc.terms_and_conditions ? `
      <div style="margin-top:14px;">
        <strong>Terms &amp; Conditions</strong>
        <div style="white-space:pre-wrap; font-size:13px; margin-top:4px; color:var(--text-dim);">${escapeHtml(doc.terms_and_conditions)}</div>
      </div>
    ` : ''}
  `;
}

function renderDocumentsTable(docs) {
  const tbody = document.getElementById('documents-tbody');
  if (!docs.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="13">No documents match.</td></tr>';
    return;
  }
  const statusOptions = ['draft', 'sent', 'paid', 'void', 'expired'];
  tbody.innerHTML = docs.map(d => `
    <tr>
      ${selectCell('documents', d)}
      <td class="mono"><button class="link-btn-inline" data-view-doc-id="${d.id}">${escapeHtml(d.doc_number)}</button></td>
      <td><span class="${stampClass(d.doc_type)}">${escapeHtml(d.doc_type)}</span></td>
      <td>${escapeHtml(docCustomerName(d))}</td>
      <td>${projectLabel(d.project_id)}</td>
      <td><span class="${stampClass(d.status)}">${escapeHtml(d.status)}</span></td>
      <td class="mono">${fmtMoney(d.total)} ${escapeHtml(d.currency || '')}</td>
      <td>${fmtDate(d.created_at)}</td>
      <td>
        <select class="status-select" data-doc-id="${d.id}" style="margin-bottom:0; padding:6px 8px; font-size:13px;">
          ${statusOptions.map(s => `<option value="${s}" ${s === d.status ? 'selected' : ''}>${s}</option>`).join('')}
        </select>
      </td>
      <td>
        <button class="link-btn-inline" data-export-id="${d.id}" data-export-number="${escapeHtml(d.doc_number)}" data-export-format="xlsx">Excel</button>
        &nbsp;|&nbsp;
        <button class="link-btn-inline" data-export-id="${d.id}" data-export-number="${escapeHtml(d.doc_number)}" data-export-format="pdf">PDF</button>
        &nbsp;|&nbsp;
        <button class="link-btn-inline" data-catalogue-export-id="${d.id}" data-catalogue-export-number="${escapeHtml(d.doc_number)}">Catalogue</button>
      </td>
      <td>
        <button class="link-btn-inline" data-edit-doc-id="${d.id}">Edit</button>
        &nbsp;|&nbsp;
        <button class="link-btn-inline" data-log-doc-id="${d.id}" data-log-doc-number="${escapeHtml(d.doc_number)}">Log</button>
      </td>
      <td>${currentUserCanDeleteDocuments
        ? `<button class="link-btn-inline" data-trash-doc-id="${d.id}" data-trash-doc-number="${escapeHtml(d.doc_number)}" style="color:var(--danger);">Delete</button>`
        : '<span style="color:var(--text-dim);">—</span>'}</td>
      ${groupCell('documents', d)}
    </tr>
  `).join('');
  tbody.querySelectorAll('[data-goto-project-id]').forEach(btn => {
    btn.addEventListener('click', () => gotoProject(btn.dataset.gotoProjectId));
  });
  tbody.querySelectorAll('[data-edit-doc-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const doc = cachedDocuments.find(d => d.id === btn.dataset.editDocId);
      if (doc) startEditDocument(doc);
    });
  });
  tbody.querySelectorAll('[data-log-doc-id]').forEach(btn => {
    btn.addEventListener('click', () => viewAuditLog(btn.dataset.logDocId, btn.dataset.logDocNumber));
  });
  tbody.querySelectorAll('.status-select').forEach(sel => {
    sel.addEventListener('change', async () => {
      try {
        await apiFetch('/documents/' + sel.dataset.docId + '/status?new_status=' + sel.value, { method: 'PATCH' });
        loadDocuments();
      } catch (err) {
        showBanner('documents-banner', err.message);
        loadDocuments();
      }
    });
  });
  tbody.querySelectorAll('[data-export-id]').forEach(btn => {
    btn.addEventListener('click', () => downloadQuotation(btn.dataset.exportId, btn.dataset.exportNumber, btn.dataset.exportFormat, btn));
  });
  tbody.querySelectorAll('[data-catalogue-export-id]').forEach(btn => {
    btn.addEventListener('click', () => downloadDocumentCatalogue(btn.dataset.catalogueExportId, btn.dataset.catalogueExportNumber, btn));
  });
  tbody.querySelectorAll('[data-view-doc-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const doc = cachedDocuments.find(d => d.id === btn.dataset.viewDocId);
      if (doc) openViewDocumentModal(doc);
    });
  });
  tbody.querySelectorAll('[data-trash-doc-id]').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm(`Move "${btn.dataset.trashDocNumber}" to the recycle bin? You can restore it from there later.`)) return;
      showBanner('documents-banner', '');
      try {
        await apiFetch('/documents/' + btn.dataset.trashDocId + '/trash', { method: 'PATCH' });
        loadDocuments();
      } catch (err) {
        showBanner('documents-banner', err.message);
      }
    });
  });
}

document.getElementById('documents-filter').addEventListener('input', () => applyFilterAndSort('documents'));
document.getElementById('documents-type-filter').addEventListener('change', () => applyFilterAndSort('documents'));
document.getElementById('documents-status-filter').addEventListener('change', () => applyFilterAndSort('documents'));
document.getElementById('documents-project-filter').addEventListener('change', () => applyFilterAndSort('documents'));
document.getElementById('documents-date-from').addEventListener('change', () => applyFilterAndSort('documents'));
document.getElementById('documents-date-to').addEventListener('change', () => applyFilterAndSort('documents'));
document.getElementById('documents-reset-filters-btn').addEventListener('click', () => {
  document.getElementById('documents-filter').value = '';
  document.getElementById('documents-type-filter').value = '';
  document.getElementById('documents-status-filter').value = '';
  document.getElementById('documents-project-filter').value = '';
  document.getElementById('documents-date-from').value = '';
  document.getElementById('documents-date-to').value = '';
  applyFilterAndSort('documents');
});

function refreshDocumentsProjectFilter() {
  const select = document.getElementById('documents-project-filter');
  if (!select) return;
  const currentValue = select.value;
  select.innerHTML = '<option value="">All projects</option>' +
    cachedProjects.map(p => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join('');
  if (currentValue) select.value = currentValue;
}

document.getElementById('open-document-trash-btn').addEventListener('click', async () => {
  showBanner('document-trash-banner', '');
  document.getElementById('document-trash-modal').classList.add('active');
  await loadDocumentTrash();
});

document.getElementById('document-trash-close').addEventListener('click', () => {
  document.getElementById('document-trash-modal').classList.remove('active');
});

async function loadDocumentTrash() {
  const tbody = document.getElementById('document-trash-tbody');
  tbody.innerHTML = skeletonRows(6);
  try {
    const docs = await apiFetch('/documents/trash');
    if (!docs.length) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="6">Recycle bin is empty.</td></tr>';
      return;
    }
    tbody.innerHTML = docs.map(d => `
      <tr>
        <td class="mono">${escapeHtml(d.doc_number)}</td>
        <td><span class="${stampClass(d.doc_type)}">${escapeHtml(d.doc_type)}</span></td>
        <td class="mono">${fmtMoney(d.total)} ${escapeHtml(d.currency || '')}</td>
        <td>${d.deleted_by ? escapeHtml(d.deleted_by) : '<span style="color:var(--text-dim);">Unknown</span>'}</td>
        <td>${d.deleted_at ? fmtDate(d.deleted_at) : '<span style="color:var(--text-dim);">—</span>'}</td>
        <td><button class="link-btn-inline" data-restore-doc-id="${d.id}">Restore</button></td>
      </tr>
    `).join('');
    tbody.querySelectorAll('[data-restore-doc-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        showBanner('document-trash-banner', '');
        try {
          await apiFetch('/documents/' + btn.dataset.restoreDocId + '/restore', { method: 'PATCH' });
          await loadDocumentTrash();
          loadDocuments();
        } catch (err) {
          showBanner('document-trash-banner', err.message);
        }
      });
    });
  } catch (err) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="6">${escapeHtml(err.message)}</td></tr>`;
  }
}

// ---------- projects ----------
let editingProjectId = null;
let cachedProjects = [];

let reopenDocModalAfterProject = false;

document.getElementById('doc-new-project-btn').addEventListener('click', () => {
  reopenDocModalAfterProject = true;
  document.getElementById('doc-modal').classList.remove('active');

  editingProjectId = null;
  document.getElementById('project-form').reset();
  document.getElementById('project-form-title').textContent = 'New project';
  document.getElementById('project-form-submit-btn').textContent = 'Create project';
  showBanner('project-modal-banner', '');
  document.getElementById('project-modal').classList.add('active');
});

document.getElementById('toggle-project-form').addEventListener('click', () => {
  editingProjectId = null;
  document.getElementById('project-form').reset();
  document.getElementById('project-form-title').textContent = 'New project';
  document.getElementById('project-form-submit-btn').textContent = 'Create project';
  showBanner('project-modal-banner', '');
  document.getElementById('project-modal').classList.add('active');
});

document.getElementById('project-modal-close').addEventListener('click', () => {
  document.getElementById('project-modal').classList.remove('active');
  if (reopenDocModalAfterProject) {
    reopenDocModalAfterProject = false;
    document.getElementById('doc-modal').classList.add('active');
  }
});

document.getElementById('project-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner('project-modal-banner', '');
  const payload = {
    name: document.getElementById('project-name').value,
    budget_year: document.getElementById('project-year').value || null,
    description: document.getElementById('project-description').value || null
  };
  try {
    setBusy(document.getElementById('project-form-submit-btn'), true);
    let created = null;
    if (editingProjectId) {
      await apiFetch('/projects/' + editingProjectId, { method: 'PATCH', body: JSON.stringify({ ...payload, version: editingVersions.project }) });
    } else {
      created = await apiFetch('/projects', { method: 'POST', headers: idempotencyHeader('project'), body: JSON.stringify({ ...payload, group_id: chosenGroupId('project-form') }) });
      idempotencyDone('project');
    }
    document.getElementById('project-form').reset();
    document.getElementById('project-modal').classList.remove('active');
    document.getElementById('project-form-title').textContent = 'New project';
    document.getElementById('project-form-submit-btn').textContent = 'Create project';
    editingProjectId = null;
    showSuccessTick('Project saved');
    await loadProjects();

    if (reopenDocModalAfterProject) {
      reopenDocModalAfterProject = false;
      if (created) {
        document.getElementById('doc-project').value = created.name + (created.budget_year ? ' (' + created.budget_year + ')' : '');
      }
      document.getElementById('doc-modal').classList.add('active');
    }
  } catch (err) {
    const id = editingProjectId;
    showSaveError(err, 'project-modal-banner', id && (async () => { startEditProject(await apiFetch('/projects/' + id)); loadProjects(); }));
  } finally {
    setBusy(document.getElementById('project-form-submit-btn'), false);
  }
});

function startEditProject(project) {
  editingProjectId = project.id;
  editingVersions.project = project.version;
  showGroupSelect('project-form', false);
  document.getElementById('project-modal').classList.add('active');
  document.getElementById('project-form-title').textContent = 'Edit project — ' + project.name;
  document.getElementById('project-form-submit-btn').textContent = 'Save changes';
  document.getElementById('project-name').value = project.name;
  document.getElementById('project-year').value = project.budget_year || '';
  document.getElementById('project-description').value = project.description || '';
}

async function viewProjectDocuments(project) {
  const tbody = document.getElementById('project-documents-tbody');
  document.getElementById('project-documents-title').textContent =
    'Documents — ' + project.name + (project.budget_year ? ' (' + project.budget_year + ')' : '');
  document.getElementById('project-documents-modal').classList.add('active');
  tbody.innerHTML = skeletonRows(5);
  try {
    const docs = await apiFetch('/projects/' + project.id + '/documents');
    if (!docs.length) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="5">No documents linked to this project yet.</td></tr>';
      return;
    }
    tbody.innerHTML = docs.map(d => `
      <tr>
        <td class="mono">${escapeHtml(d.doc_number)}</td>
        <td><span class="${stampClass(d.doc_type)}">${escapeHtml(d.doc_type)}</span></td>
        <td><span class="${stampClass(d.status)}">${escapeHtml(d.status)}</span></td>
        <td class="mono">${fmtMoney(d.total)} ${escapeHtml(d.currency || '')}</td>
        <td>${fmtDate(d.created_at)}</td>
      </tr>
    `).join('');
  } catch (err) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="5">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById('project-documents-close').addEventListener('click', () => {
  document.getElementById('project-documents-modal').classList.remove('active');
});

function refreshDocCompanyPicker() {
  const datalist = document.getElementById('company-datalist');
  if (datalist) {
    datalist.innerHTML = cachedCompanies.map(c => `<option value="${escapeHtml(c.name)}">`).join('');
  }
}

function resolveDocCompanyIdFromInput() {
  const input = document.getElementById('doc-company');
  const typed = input.value.trim();
  if (!typed) return null;
  const match = cachedCompanies.find(c => c.name.toLowerCase() === typed.toLowerCase());
  return match ? match.id : null;
}

function refreshProjectPicker() {
  const datalist = document.getElementById('project-datalist');
  if (datalist) {
    datalist.innerHTML = cachedProjects.filter(inDocGroup).map(p =>
      `<option value="${escapeHtml(p.name)}${p.budget_year ? ' (' + escapeHtml(p.budget_year) + ')' : ''}">`
    ).join('');
  }

  const searchSelect = document.getElementById('search-project-filter');
  if (searchSelect) {
    const currentValue = searchSelect.value;
    const options = cachedProjects.map(p =>
      `<option value="${p.id}">${escapeHtml(p.name)}${p.budget_year ? ' (' + escapeHtml(p.budget_year) + ')' : ''}</option>`
    ).join('');
    searchSelect.innerHTML = `<option value="">All projects</option>${options}`;
    if (currentValue) searchSelect.value = currentValue;
  }
}

function resolveDocProjectIdFromInput() {
  const input = document.getElementById('doc-project');
  const typed = input.value.trim();
  if (!typed) return null;
  const match = cachedProjects.filter(inDocGroup).find(p => {
    const label = `${p.name}${p.budget_year ? ' (' + p.budget_year + ')' : ''}`;
    return label.toLowerCase() === typed.toLowerCase() || p.name.toLowerCase() === typed.toLowerCase();
  });
  return match ? match.id : null;
}

async function loadProjects() {
  const tbody = document.getElementById('projects-tbody');
  const mySeq = startLoad('projects');
  try {
    const projects = await apiFetch('/projects');
    if (isStaleLoad('projects', mySeq)) return;
    cachedProjects = projects;
    refreshProjectPicker();
    refreshDocumentsProjectFilter();
    refreshProjectBudgetYearFilter();
    applyFilterAndSort('projects');
  } catch (err) {
    if (isStaleLoad('projects', mySeq)) return;
    tbody.innerHTML = `<tr class="empty-row"><td colspan="8">${escapeHtml(err.message)}</td></tr>`;
  }
}

function renderProjectsTable(projects) {
  const tbody = document.getElementById('projects-tbody');
  if (!projects.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="8">No projects match.</td></tr>';
    return;
  }
  tbody.innerHTML = projects.map(p => `
    <tr>
      ${selectCell('projects', p)}
      <td>${escapeHtml(p.name)}</td>
      <td class="mono">${escapeHtml(p.budget_year || '—')}</td>
      <td>${fmtDate(p.created_at)}</td>
      <td><button class="link-btn-inline" data-view-project-id="${p.id}">View documents</button></td>
      <td><button class="link-btn-inline" data-edit-project-id="${p.id}">Edit</button></td>
      <td><button class="link-btn-inline" data-trash-project-id="${p.id}" data-trash-project-name="${escapeHtml(p.name)}" style="color:var(--danger);">Delete</button></td>
      ${groupCell('projects', p)}
    </tr>
  `).join('');
  tbody.querySelectorAll('[data-view-project-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const project = cachedProjects.find(p => p.id === btn.dataset.viewProjectId);
      if (project) viewProjectDocuments(project);
    });
  });
  tbody.querySelectorAll('[data-edit-project-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const project = cachedProjects.find(p => p.id === btn.dataset.editProjectId);
      if (project) startEditProject(project);
    });
  });
  tbody.querySelectorAll('[data-trash-project-id]').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm(`Move "${btn.dataset.trashProjectName}" to the recycle bin? You can restore it from there later.`)) return;
      showBanner('projects-banner', '');
      try {
        await apiFetch('/projects/' + btn.dataset.trashProjectId + '/trash', { method: 'PATCH' });
        loadProjects();
      } catch (err) {
        showBanner('projects-banner', err.message);
      }
    });
  });
}

document.getElementById('projects-filter').addEventListener('input', () => applyFilterAndSort('projects'));
document.getElementById('projects-budget-year-filter').addEventListener('change', () => applyFilterAndSort('projects'));
document.getElementById('projects-date-from').addEventListener('change', () => applyFilterAndSort('projects'));
document.getElementById('projects-date-to').addEventListener('change', () => applyFilterAndSort('projects'));
document.getElementById('projects-reset-filters-btn').addEventListener('click', () => {
  document.getElementById('projects-filter').value = '';
  document.getElementById('projects-budget-year-filter').value = '';
  document.getElementById('projects-date-from').value = '';
  document.getElementById('projects-date-to').value = '';
  applyFilterAndSort('projects');
});

function refreshProjectBudgetYearFilter() {
  const select = document.getElementById('projects-budget-year-filter');
  if (!select) return;
  const currentValue = select.value;
  const years = [...new Set(cachedProjects.map(p => p.budget_year).filter(Boolean))].sort();
  select.innerHTML = '<option value="">All budget years</option>' +
    years.map(y => `<option value="${escapeHtml(y)}">${escapeHtml(y)}</option>`).join('');
  if (currentValue && years.includes(currentValue)) select.value = currentValue;
}

// ---------- company ----------
let editingCompanyId = null;
let editingCompany = null;  // the saved row, for the logo/banner preview and hint

function buildDirectorRow(director) {
  const div = document.createElement('div');
  div.className = 'director-row';
  div.dataset.directorId = director ? director.id : '';
  div.innerHTML = `
    <div class="director-row-header">
      <span class="director-row-title">MD</span>
      <button type="button" class="link-btn-inline director-remove-btn" style="color:var(--danger);">Remove</button>
    </div>
    <div class="form-row" style="flex-wrap:wrap;">
      <div style="min-width:180px;">
        <label>Name</label>
        <input type="text" class="director-name" placeholder="Managing Director's name" value="${director ? escapeHtml(director.name) : ''}">
      </div>
      <div style="min-width:180px;">
        <label>Position</label>
        <input type="text" class="director-position" maxlength="100" required placeholder="Managing Director" value="${director ? escapeHtml(director.position || '') : 'Managing Director'}">
      </div>
      <div style="min-width:180px;">
        <label>Address</label>
        <input type="text" class="director-address" placeholder="Mayangon, Yangon" value="${director ? escapeHtml(director.address || '') : ''}">
      </div>
    </div>
    <div class="form-row" style="flex-wrap:wrap; margin-top:10px;">
      <div style="min-width:180px;">
        <label>Contact No</label>
        <input type="text" class="director-contact" placeholder="+95 9 xxx xxx xxx" value="${director ? escapeHtml(director.contact_no || '') : ''}">
      </div>
      <div style="min-width:180px;">
        <label>Email</label>
        <input type="text" class="director-email" placeholder="name@example.com" value="${director ? escapeHtml(director.email || '') : ''}">
      </div>
    </div>
    <div class="form-row" style="flex-wrap:wrap; align-items:flex-end; margin-top:10px;">
      <div style="min-width:180px;">
        <label>Seal</label>
        <input type="file" class="director-seal-file" accept="image/png,image/jpeg">
        ${director && director.seal_object_key ? `<div style="font-size:12px; color:var(--text-dim); margin-top:2px;">Seal already attached — choosing a new file replaces it. <button type="button" class="link-btn-inline director-view-seal-btn">View</button></div>` : ''}
      </div>
    </div>
  `;
  div.querySelector('.director-remove-btn').addEventListener('click', async () => {
    const directorId = div.dataset.directorId;
    if (directorId && editingCompanyId) {
      if (!confirm('Remove this MD? This deletes them immediately.')) return;
      try {
        await apiFetch('/companies/' + editingCompanyId + '/directors/' + directorId, { method: 'DELETE' });
      } catch (err) {
        showBanner('company-modal-banner', err.message);
        return;
      }
    }
    div.remove();
    renumberDirectorRows();
  });
  if (director && director.seal_object_key) {
    div.querySelector('.director-view-seal-btn').addEventListener('click', async () => {
      try {
        const data = await apiFetch('/companies/' + director.company_id + '/directors/' + director.id + '/seal-url');
        window.open(data.url, '_blank');
      } catch (err) {
        alert(err.message);
      }
    });
  }
  return div;
}

// "MD 1", "MD 2"… by position, so the numbers close up after a Remove
function renumberDirectorRows() {
  document.querySelectorAll('#director-rows .director-row-title').forEach((el, i) => {
    el.textContent = 'MD ' + (i + 1);
  });
}

function resetDirectorRows(directors) {
  const container = document.getElementById('director-rows');
  container.innerHTML = '';
  if (directors && directors.length) {
    directors.forEach(d => container.appendChild(buildDirectorRow(d)));
  } else {
    container.appendChild(buildDirectorRow(null)); // default: one blank row
  }
  renumberDirectorRows();
}

document.getElementById('add-director-row-btn').addEventListener('click', () => {
  document.getElementById('director-rows').appendChild(buildDirectorRow(null));
  renumberDirectorRows();
});

// One image serves both header styles: the select only switches which saved
// variant is previewed (squared logo or proportional banner).
function applyCompanyHeaderStyle() {
  const banner = document.getElementById('company-header-style').value === 'letterhead';
  const c = editingCompany;
  document.getElementById('company-banner-hint').style.display =
    banner && c && c.logo_object_key && !c.letterhead_object_key ? 'block' : 'none';  // uploaded before both variants were kept
  const preview = document.getElementById('company-logo-preview');
  preview.style.display = 'none';
  preview.dataset.wanted = '';
  const key = c && (banner ? c.letterhead_object_key : c.logo_object_key);
  if (!key || key.toLowerCase().endsWith('.svg')) return;
  const wanted = '/companies/' + c.id + (banner ? '/letterhead-url' : '/logo-url');
  preview.dataset.wanted = wanted;
  apiFetch(wanted)
    .then(d => { if (preview.dataset.wanted === wanted) { preview.src = d.url; preview.style.display = 'block'; } })
    .catch(() => {});  // the "View it" link still works
}
document.getElementById('company-header-style').addEventListener('change', applyCompanyHeaderStyle);

document.getElementById('toggle-company-form').addEventListener('click', () => {
  editingCompanyId = null;
  editingCompany = null;
  document.getElementById('company-form').reset();
  document.getElementById('company-form-title').textContent = 'New company';
  document.getElementById('company-form-submit-btn').textContent = 'Create company';
  document.getElementById('company-current-logo-info').style.display = 'none';
  document.getElementById('company-current-seal-info').style.display = 'none';
  applyCompanyHeaderStyle();  // form.reset() put the style select back to "logo"
  resetDirectorRows(null);
  showBanner('company-modal-banner', '');
  document.getElementById('company-modal').classList.add('active');
});

document.getElementById('company-modal-close').addEventListener('click', () => {
  document.getElementById('company-modal').classList.remove('active');
});

let ownSaveDone = false;  // company form: this submit's own PATCH already succeeded
document.getElementById('company-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner('company-modal-banner', '');
  const logoInput = document.getElementById('company-logo');
  const sealInput = document.getElementById('company-seal');
  const payload = {
    name: document.getElementById('company-name').value,
    short_name: document.getElementById('company-short-name').value.trim().toUpperCase() || null,
    address: document.getElementById('company-address').value || null,
    contact_no: document.getElementById('company-contact').value || null,
    support_email: document.getElementById('company-email').value || null,
    header_style: document.getElementById('company-header-style').value
  };
  const logoFile = logoInput.files[0];
  // Same rule as the server (routers/companies.py): checked before anything is saved.
  if (logoFile && !['image/png', 'image/jpeg', 'image/webp', 'image/svg+xml'].includes(logoFile.type)) {
    showBanner('company-modal-banner', 'Logo must be a PNG, JPG, WebP or SVG image.');
    showErrorCross('Logo must be a PNG, JPG, WebP or SVG image');
    return;
  }
  if (logoFile && logoFile.size > 10 * 1024 * 1024) {
    showBanner('company-modal-banner', 'Image is too large (max 10 MB).');
    showErrorCross('Image is too large');
    return;
  }
  try {
    setBusy(document.getElementById('company-form-submit-btn'), true);
    ownSaveDone = false;
    let company;
    if (editingCompanyId) {
      company = await apiFetch('/companies/' + editingCompanyId, { method: 'PATCH', body: JSON.stringify({ ...payload, version: editingVersions.company }) });
      ownSaveDone = true;
    } else {
      company = await apiFetch('/companies', { method: 'POST', headers: idempotencyHeader('company'), body: JSON.stringify(payload) });
    }

    const savedImages = [];
    let logoNote = '';
    if (logoFile) {
      const formData = new FormData();
      formData.append('file', logoFile);
      const uploadResponse = await fetch(API_BASE + '/companies/' + company.id + '/logo', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token },
        body: formData
      });
      if (!uploadResponse.ok) {
        let detail = 'Logo upload failed.';
        try {
          const errBody = await uploadResponse.json();
          if (errBody.detail) detail = errBody.detail;
        } catch (parseErr) {
          // response wasn't JSON — stick with the generic message
        }
        showBanner('company-modal-banner', `Company saved, but the logo didn't upload: ${detail}`);
        showErrorCross(`The logo didn't upload`);
        loadCompanies();
        return;
      }
      // Tick only once the variant this header style uses loads back.
      const saved = await uploadResponse.json();
      const asBanner = payload.header_style === 'letterhead' && saved.letterhead_object_key;
      savedImages.push('/companies/' + company.id + (asBanner ? '/letterhead-url' : '/logo-url'));
      if (payload.header_style === 'letterhead' && !saved.letterhead_object_key) logoNote = saved.note || '';
    }

    if (sealInput.files.length > 0) {
      const formData = new FormData();
      formData.append('file', sealInput.files[0]);
      savedImages.push('/companies/' + company.id + '/seal-url');
      const uploadResponse = await fetch(API_BASE + '/companies/' + company.id + '/seal', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token },
        body: formData
      });
      if (!uploadResponse.ok) {
        let detail = 'Seal upload failed.';
        try {
          const errBody = await uploadResponse.json();
          if (errBody.detail) detail = errBody.detail;
        } catch (parseErr) {
          // response wasn't JSON — stick with the generic message
        }
        showBanner('company-modal-banner', `Company saved, but the seal didn't upload: ${detail}`);
        showErrorCross(`The seal didn't upload`);
        loadCompanies();
        return;
      }
      uploadResponse.text().catch(() => {});  // drain (no-store body)
    }

    // Flush the director rows: create any new ones, rename existing ones,
    // and upload a seal file for any row where one was selected.
    const directorRows = document.querySelectorAll('#director-rows .director-row');
    for (const row of directorRows) {
      const name = row.querySelector('.director-name').value.trim();
      const address = row.querySelector('.director-address').value.trim() || null;
      const contact_no = row.querySelector('.director-contact').value.trim() || null;
      const email = row.querySelector('.director-email').value.trim() || null;
      const position = row.querySelector('.director-position').value.trim() || null;
      const sealFile = row.querySelector('.director-seal-file').files[0];
      let directorId = row.dataset.directorId;

      if (!directorId) {
        if (!name) continue; // a genuinely blank unused row — skip silently
        const created = await apiFetch('/companies/' + company.id + '/directors', {
          method: 'POST',
          body: JSON.stringify({ name, address, contact_no, email, position })
        });
        if (!created) {
          showBanner('company-modal-banner', 'Company saved, but one MD row failed to save.');
          showErrorCross('An MD row failed to save');
          loadCompanies();
          return;
        }
        directorId = created.id;
      } else if (name) {
        await apiFetch('/companies/' + company.id + '/directors/' + directorId, {
          method: 'PATCH',
          body: JSON.stringify({ name, address, contact_no, email, position })
        });
      }

      if (sealFile) {
        const formData = new FormData();
        formData.append('file', sealFile);
        const uploadResponse = await fetch(API_BASE + '/companies/' + company.id + '/directors/' + directorId + '/seal', {
          method: 'POST',
          headers: { 'Authorization': 'Bearer ' + token },
          body: formData
        });
        savedImages.push('/companies/' + company.id + '/directors/' + directorId + '/seal-url');
        if (!uploadResponse.ok) {
          showBanner('company-modal-banner', `Company saved, but a director's seal didn't upload.`);
          showErrorCross("An MD seal didn't upload");
          loadCompanies();
          return;
        }
        uploadResponse.text().catch(() => {});  // drain (no-store body)
      }
    }

    await verifySavedImages(savedImages);
    document.getElementById('company-form').reset();
    resetDirectorRows(null);
    document.getElementById('company-modal').classList.remove('active');
    document.getElementById('company-form-title').textContent = 'New company';
    document.getElementById('company-form-submit-btn').textContent = 'Create company';
    editingCompanyId = null;
    editingCompany = null;
    idempotencyDone('company');  // only now: an image that failed above leaves the key, so Save again reuses the same company
    loadCompanies();
    showSuccessTick(logoNote ? 'Company saved — ' + logoNote : 'Company saved');
  } catch (err) {
    const id = editingCompanyId;
    showSaveError(err, 'company-modal-banner', id && (async () => { startEditCompany(await apiFetch('/companies/' + id)); loadCompanies(); }));
  } finally {
    setBusy(document.getElementById('company-form-submit-btn'), false);
    // Our PATCH went through but a logo/seal/MD step stopped early: those
    // uploads bumped the version too, so take it over for the next Save.
    // shortcut: someone else's edit in that same second would be taken over too; fine at this size.
    if (ownSaveDone && editingCompanyId) {
      apiFetch('/companies/' + editingCompanyId).then(c => { editingVersions.company = c.version; }).catch(() => {});
    }
  }
});

function startEditCompany(company) {
  editingCompanyId = company.id;
  editingVersions.company = company.version;
  editingCompany = company;
  document.getElementById('company-modal').classList.add('active');
  document.getElementById('company-form-title').textContent = 'Edit company — ' + company.name;
  document.getElementById('company-form-submit-btn').textContent = 'Save changes';
  document.getElementById('company-name').value = company.name;
  document.getElementById('company-short-name').value = company.short_name || '';
  document.getElementById('company-address').value = company.address || '';
  document.getElementById('company-contact').value = company.contact_no || '';
  document.getElementById('company-email').value = company.support_email || '';

  document.getElementById('company-logo').value = '';
  document.getElementById('company-seal').value = '';
  document.getElementById('company-header-style').value = company.header_style === 'letterhead' ? 'letterhead' : 'logo';
  applyCompanyHeaderStyle();

  const logoInfo = document.getElementById('company-current-logo-info');
  if (company.logo_object_key) {
    logoInfo.innerHTML = 'An image is already attached. <button type="button" class="link-btn-inline" data-view-current-logo="' + company.id + '">View it</button> — choosing a new file will replace it.';
    logoInfo.style.display = 'block';
    logoInfo.querySelector('[data-view-current-logo]').addEventListener('click', () => viewCompanyImage(company.id, 'logo'));
  } else {
    logoInfo.style.display = 'none';
  }

  const sealInfo = document.getElementById('company-current-seal-info');
  if (company.seal_object_key) {
    sealInfo.innerHTML = 'A seal is already attached. <button type="button" class="link-btn-inline" data-view-current-seal="' + company.id + '">View it</button> — choosing a new file will replace it.';
    sealInfo.style.display = 'block';
    sealInfo.querySelector('[data-view-current-seal]').addEventListener('click', () => viewCompanyImage(company.id, 'seal'));
  } else {
    sealInfo.style.display = 'none';
  }

  resetDirectorRows(null); // show a blank row immediately, replaced once the real list loads
  apiFetch('/companies/' + company.id + '/directors')
    .then(directors => resetDirectorRows(directors))
    .catch(() => resetDirectorRows(null));
}

async function viewCompanyImage(companyId, kind) {
  try {
    const data = await apiFetch('/companies/' + companyId + '/' + kind + '-url');
    window.open(data.url, '_blank');
  } catch (err) {
    alert(err.message);
  }
}

let cachedCompanies = [];

async function loadCompanies() {
  const tbody = document.getElementById('companies-tbody');
  const mySeq = startLoad('companies');
  try {
    const companies = await apiFetch('/companies');
    if (isStaleLoad('companies', mySeq)) return;
    cachedCompanies = companies;
    applyFilterAndSort('company');
    refreshDocCompanyPicker();
  } catch (err) {
    if (isStaleLoad('companies', mySeq)) return;
    tbody.innerHTML = `<tr class="empty-row"><td colspan="7">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById('company-short-name').addEventListener('input', (e) => {
  e.target.value = e.target.value.toUpperCase();
});

// same rule the API enforces on save (companies.SHORT_NAME_RE)
const SHORT_NAME_RE = /^[A-Za-z0-9]{2,6}$/;

function renderCompaniesTable(companies) {
  const tbody = document.getElementById('companies-tbody');
  if (!companies.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="7">No company matches.</td></tr>';
    return;
  }
  tbody.innerHTML = companies.map(c => `
    <tr>
      <td>${escapeHtml(c.name)}</td>
      <td>${escapeHtml(c.short_name || '—')}${c.short_name && !SHORT_NAME_RE.test(c.short_name) ? ' <span class="short-name-warning" style="color:var(--danger); font-size:12px;">Short name needs fixing</span>' : ''}</td>
      <td>${escapeHtml(c.contact_no || '—')}</td>
      <td>${c.logo_object_key ? `<button class="link-btn-inline" data-logo-id="${c.id}">View logo</button>` : '—'}</td>
      <td>${c.seal_object_key ? `<button class="link-btn-inline" data-seal-id="${c.id}">View seal</button>` : '—'}</td>
      <td><button class="link-btn-inline" data-edit-company-id="${c.id}">Edit</button></td>
      <td><button class="link-btn-inline" data-trash-company-id="${c.id}" data-trash-company-name="${escapeHtml(c.name)}" style="color:var(--danger);">Delete</button></td>
    </tr>
  `).join('');
  tbody.querySelectorAll('[data-logo-id]').forEach(btn => {
    btn.addEventListener('click', async () => {
      try {
        const data = await apiFetch('/companies/' + btn.dataset.logoId + '/logo-url');
        window.open(data.url, '_blank');
      } catch (err) {
        alert(err.message);
      }
    });
  });
  tbody.querySelectorAll('[data-seal-id]').forEach(btn => {
    btn.addEventListener('click', async () => {
      try {
        const data = await apiFetch('/companies/' + btn.dataset.sealId + '/seal-url');
        window.open(data.url, '_blank');
      } catch (err) {
        alert(err.message);
      }
    });
  });
  tbody.querySelectorAll('[data-edit-company-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const company = cachedCompanies.find(c => c.id === btn.dataset.editCompanyId);
      if (company) startEditCompany(company);
    });
  });
  tbody.querySelectorAll('[data-trash-company-id]').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm(`Move "${btn.dataset.trashCompanyName}" to the recycle bin? You can restore it from there later.`)) return;
      showBanner('company-banner', '');
      try {
        await apiFetch('/companies/' + btn.dataset.trashCompanyId + '/trash', { method: 'PATCH' });
        loadCompanies();
      } catch (err) {
        showBanner('company-banner', err.message);
      }
    });
  });
}

document.getElementById('company-filter').addEventListener('input', () => applyFilterAndSort('company'));
document.getElementById('company-date-from').addEventListener('change', () => applyFilterAndSort('company'));
document.getElementById('company-date-to').addEventListener('change', () => applyFilterAndSort('company'));
document.getElementById('company-reset-filters-btn').addEventListener('click', () => {
  document.getElementById('company-filter').value = '';
  document.getElementById('company-date-from').value = '';
  document.getElementById('company-date-to').value = '';
  applyFilterAndSort('company');
});

// ---------- customers ----------
let cachedCustomers = [];
let editingCustomerId = null;

let reopenDocModalAfterCustomer = false;

document.getElementById('doc-new-customer-btn').addEventListener('click', () => {
  reopenDocModalAfterCustomer = true;
  document.getElementById('doc-modal').classList.remove('active');

  editingCustomerId = null;
  document.getElementById('customer-form').reset();
  document.getElementById('customer-form-title').textContent = 'New customer';
  document.getElementById('customer-form-submit-btn').textContent = 'Create customer';
  showBanner('customer-modal-banner', '');
  document.getElementById('customer-modal').classList.add('active');
});

document.getElementById('toggle-customer-form').addEventListener('click', () => {
  editingCustomerId = null;
  document.getElementById('customer-form').reset();
  document.getElementById('customer-form-title').textContent = 'New customer';
  document.getElementById('customer-form-submit-btn').textContent = 'Create customer';
  showBanner('customer-modal-banner', '');
  document.getElementById('customer-modal').classList.add('active');
});

document.getElementById('customer-modal-close').addEventListener('click', () => {
  document.getElementById('customer-modal').classList.remove('active');
  if (reopenDocModalAfterCustomer) {
    reopenDocModalAfterCustomer = false;
    document.getElementById('doc-modal').classList.add('active');
  }
});

document.getElementById('customer-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner('customer-modal-banner', '');
  const payload = {
    name: document.getElementById('customer-name').value,
    organization: document.getElementById('customer-organization').value.trim() || null,  // blank -> the name is printed
    email: document.getElementById('customer-email').value || null,
    phone: document.getElementById('customer-phone').value || null,
    billing_address: document.getElementById('customer-address').value
      ? { address: document.getElementById('customer-address').value }
      : null
  };
  try {
    setBusy(document.getElementById('customer-form-submit-btn'), true);
    let created = null;
    if (editingCustomerId) {
      await apiFetch('/customers/' + editingCustomerId, { method: 'PATCH', body: JSON.stringify({ ...payload, version: editingVersions.customer }) });
    } else {
      created = await apiFetch('/customers', { method: 'POST', headers: idempotencyHeader('customer'), body: JSON.stringify({ ...payload, group_id: chosenGroupId('customer-form') }) });
      idempotencyDone('customer');
    }
    document.getElementById('customer-form').reset();
    document.getElementById('customer-modal').classList.remove('active');
    document.getElementById('customer-form-title').textContent = 'New customer';
    document.getElementById('customer-form-submit-btn').textContent = 'Create customer';
    editingCustomerId = null;
    showSuccessTick('Customer saved');
    await loadCustomers();

    if (reopenDocModalAfterCustomer) {
      reopenDocModalAfterCustomer = false;
      if (created) {
        document.getElementById('doc-customer').value = created.name;
        document.getElementById('doc-customer-hint').style.display = 'none';
      }
      document.getElementById('doc-modal').classList.add('active');
    }
  } catch (err) {
    const id = editingCustomerId;
    showSaveError(err, 'customer-modal-banner', id && (async () => { startEditCustomer(await apiFetch('/customers/' + id)); loadCustomers(); }));
  } finally {
    setBusy(document.getElementById('customer-form-submit-btn'), false);
  }
});

function startEditCustomer(customer) {
  editingCustomerId = customer.id;
  editingVersions.customer = customer.version;
  showGroupSelect('customer-form', false);
  document.getElementById('customer-modal').classList.add('active');
  document.getElementById('customer-form-title').textContent = 'Edit customer — ' + customer.name;
  document.getElementById('customer-form-submit-btn').textContent = 'Save changes';
  document.getElementById('customer-name').value = customer.name;
  document.getElementById('customer-organization').value = customer.organization || '';
  document.getElementById('customer-email').value = customer.email || '';
  document.getElementById('customer-phone').value = customer.phone || '';
  document.getElementById('customer-address').value = (customer.billing_address && customer.billing_address.address) || '';
}

// Customer field on the document form is a type-to-search input (backed by
// a <datalist>) rather than a plain dropdown, since customer lists can get
// long — typing "ji" suggests "Jimmy", etc. The actual customer_id is
// resolved by matching the typed text against the cached customer list.
function refreshCustomerPicker() {
  const datalist = document.getElementById('customer-datalist');
  if (!datalist) return;
  datalist.innerHTML = cachedCustomers.filter(inDocGroup).map(c => `<option value="${escapeHtml(c.name)}">`).join('');
}

function resolveCustomerIdFromInput() {
  const input = document.getElementById('doc-customer');
  const typed = input.value.trim();
  if (!typed) return null;
  const match = cachedCustomers.filter(inDocGroup).find(c => c.name.toLowerCase() === typed.toLowerCase());
  return match ? match.id : null;
}

async function loadCustomers() {
  const tbody = document.getElementById('customers-tbody');
  const mySeq = startLoad('customers');
  try {
    const customers = await apiFetch('/customers');
    if (isStaleLoad('customers', mySeq)) return;
    cachedCustomers = customers;
    refreshCustomerPicker();
    applyFilterAndSort('customers');
    if (cachedDocuments.length) applyFilterAndSort('documents');  // the list shows customer names; it may have rendered first
  } catch (err) {
    if (isStaleLoad('customers', mySeq)) return;
    tbody.innerHTML = `<tr class="empty-row"><td colspan="9">${escapeHtml(err.message)}</td></tr>`;
  }
}

function renderCustomersTable(customers) {
  const tbody = document.getElementById('customers-tbody');
  if (!customers.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="9">No customers match.</td></tr>';
    return;
  }
  tbody.innerHTML = customers.map(c => `
    <tr>
      ${selectCell('customers', c)}
      <td><button class="link-btn-inline" data-view-customer-id="${c.id}">${escapeHtml(c.name)}</button></td>
      <td>${c.organization ? escapeHtml(c.organization) : `<span style="color:var(--text-dim);">${escapeHtml(c.name)}</span>`}</td>
      <td>${escapeHtml(c.email || '—')}</td>
      <td>${escapeHtml(c.phone || '—')}</td>
      <td>${fmtDate(c.created_at)}</td>
      <td><button class="link-btn-inline" data-edit-customer-id="${c.id}">Edit</button></td>
      <td><button class="link-btn-inline" data-trash-customer-id="${c.id}" data-trash-customer-name="${escapeHtml(c.name)}" style="color:var(--danger);">Delete</button></td>
      ${groupCell('customers', c)}
    </tr>
  `).join('');
  tbody.querySelectorAll('[data-view-customer-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const customer = cachedCustomers.find(c => c.id === btn.dataset.viewCustomerId);
      if (customer) openViewCustomerModal(customer);
    });
  });
  tbody.querySelectorAll('[data-edit-customer-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const customer = cachedCustomers.find(c => c.id === btn.dataset.editCustomerId);
      if (customer) startEditCustomer(customer);
    });
  });
  tbody.querySelectorAll('[data-trash-customer-id]').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm(`Move "${btn.dataset.trashCustomerName}" to the recycle bin? You can restore it from there later.`)) return;
      showBanner('customers-banner', '');
      try {
        await apiFetch('/customers/' + btn.dataset.trashCustomerId + '/trash', { method: 'PATCH' });
        loadCustomers();
      } catch (err) {
        showBanner('customers-banner', err.message);
      }
    });
  });
}

document.getElementById('customers-filter').addEventListener('input', () => applyFilterAndSort('customers'));
document.getElementById('customers-date-from').addEventListener('change', () => applyFilterAndSort('customers'));
document.getElementById('customers-date-to').addEventListener('change', () => applyFilterAndSort('customers'));
document.getElementById('customers-reset-filters-btn').addEventListener('click', () => {
  document.getElementById('customers-filter').value = '';
  document.getElementById('customers-date-from').value = '';
  document.getElementById('customers-date-to').value = '';
  applyFilterAndSort('customers');
});

// ---------- products ----------
document.getElementById('toggle-product-form').addEventListener('click', () => {
  editingProductId = null;
  document.getElementById('product-form').reset();
  document.getElementById('product-form-title').textContent = 'New product';
  document.getElementById('product-sku').disabled = false;
  document.getElementById('product-form-submit-btn').textContent = 'Create product';
  document.getElementById('product-current-file-info').style.display = 'none';
  setUnitControl(document.getElementById('product-unit'), 'No');
  pendingSubItems = [];
  refreshSubItemDatalist();
  renderSubItemsList();
  document.getElementById('product-modal').classList.add('active');
});

document.getElementById('product-modal-close').addEventListener('click', () => {
  document.getElementById('product-modal').classList.remove('active');
});

let editingProductId = null;
let cachedProducts = [];

// ---------- cart ----------
// Per browser tab, kept in sessionStorage so a reload keeps it — a
// lightweight way to pick several products while browsing, then drop them
// all into a new document's line items at once. Stored with its owner's
// username: another user logging in on this tab never sees it.
const CART_KEY = 'ledger_cart';
let cartItems = [];
let cartOwner = '';
function saveCart() {
  try { sessionStorage.setItem(CART_KEY, JSON.stringify({ user: cartOwner, items: cartItems })); } catch (e) {}
}
function restoreCart(username) {
  cartOwner = username;
  try {
    const saved = JSON.parse(sessionStorage.getItem(CART_KEY) || 'null');
    cartItems = saved && saved.user === username && Array.isArray(saved.items) ? saved.items : [];
  } catch (e) { cartItems = []; }
  updateCartBadge();
}
function pruneCart() { // after products load: drop lines whose product is gone
  const ids = new Set(cachedProducts.map(p => p.id));
  const kept = cartItems.filter(p => ids.has(p.id));
  if (kept.length === cartItems.length) return;
  cartItems = kept;
  updateCartBadge();
}

// ---------- product sub-items (bundle components) ----------
// Create mode: additions are held locally (pendingSubItems) until the
// product actually exists, then flushed to the server right after
// creation succeeds (see the submit handler). Edit mode: the product
// already has an id, so additions/removals hit the API immediately.
let pendingSubItems = [];        // create mode: [{id, sku, name}]
let editingProductSubItems = []; // edit mode: [{id (sub-item row id), sequence_number, product_id, sku, name}]

function refreshSubItemDatalist() {
  const datalist = document.getElementById('sub-item-datalist');
  if (!datalist) return;
  const excludeId = editingProductId; // a product can't be its own sub-item
  datalist.innerHTML = cachedProducts
    .filter(p => p.id !== excludeId)
    .map(p => `<option value="${escapeHtml(p.sku)} — ${escapeHtml(p.name)}">`)
    .join('');
}

function renderSubItemsList() {
  const container = document.getElementById('sub-items-list');
  const items = editingProductId ? editingProductSubItems : pendingSubItems;

  if (!items.length) {
    container.innerHTML = '<span style="color:var(--text-dim); font-size:13px;">No sub-items added yet.</span>';
    return;
  }

  if (editingProductId) {
    container.innerHTML = items.map(item => `
      <div class="line-item-row" style="align-items:center;">
        <div style="flex:0 0 40px;"><span class="mono">${item.sequence_number}</span></div>
        <div>${escapeHtml(item.sku)} — ${escapeHtml(item.name)}${item.has_file ? '' : ' <span style="color:var(--text-dim); font-size:12px;">(no catalog file)</span>'}</div>
        <button type="button" class="remove-line" data-remove-sub-item-id="${item.id}">Remove</button>
      </div>
    `).join('');
    container.querySelectorAll('[data-remove-sub-item-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          await apiFetch('/products/' + editingProductId + '/sub-items/' + btn.dataset.removeSubItemId, { method: 'DELETE' });
          await loadEditingProductSubItems();
        } catch (err) {
          showBanner('product-modal-banner', err.message);
        }
      });
    });
  } else {
    container.innerHTML = items.map((item, i) => `
      <div class="line-item-row" style="align-items:center;">
        <div style="flex:0 0 40px;"><span class="mono">${i + 1}</span></div>
        <div>${escapeHtml(item.sku)} — ${escapeHtml(item.name)}</div>
        <button type="button" class="remove-line" data-remove-pending-index="${i}">Remove</button>
      </div>
    `).join('');
    container.querySelectorAll('[data-remove-pending-index]').forEach(btn => {
      btn.addEventListener('click', () => {
        pendingSubItems.splice(Number(btn.dataset.removePendingIndex), 1);
        renderSubItemsList();
      });
    });
  }
}

async function loadEditingProductSubItems() {
  try {
    editingProductSubItems = await apiFetch('/products/' + editingProductId + '/sub-items');
  } catch (err) {
    editingProductSubItems = [];
  }
  renderSubItemsList();
}

document.getElementById('sub-item-search').addEventListener('input', async (e) => {
  const typed = e.target.value.trim();
  if (!typed) return;
  const match = cachedProducts.find(p => `${p.sku} — ${p.name}`.toLowerCase() === typed.toLowerCase());
  if (!match) return;
  if (match.id === editingProductId) {
    showBanner('product-modal-banner', "A product can't be its own sub-item.");
    e.target.value = '';
    return;
  }

  e.target.value = '';

  if (editingProductId) {
    showBanner('product-modal-banner', '');
    try {
      await apiFetch('/products/' + editingProductId + '/sub-items', {
        method: 'POST',
        body: JSON.stringify({ product_id: match.id })
      });
      await loadEditingProductSubItems();
    } catch (err) {
      showBanner('product-modal-banner', err.message);
    }
  } else {
    pendingSubItems.push({ id: match.id, sku: match.sku, name: match.name });
    renderSubItemsList();
  }
});

function addToCart(productId) {
  const existing = cartItems.find(p => p.id === productId);
  if (existing) {
    existing.quantity += 1;
    updateCartBadge();
    renderCartModal();
    return;
  }
  const product = cachedProducts.find(p => p.id === productId);
  if (!product) return;
  cartItems.push(Object.assign({}, product, { quantity: 1 }));
  updateCartBadge();
}

function removeFromCart(productId) {
  cartItems = cartItems.filter(p => p.id !== productId);
  updateCartBadge();
  renderCartModal();
}

function emptyCart() {
  cartItems = [];
  updateCartBadge();
  renderCartModal();
}

function updateCartBadge() {
  saveCart(); // every add/remove/qty/empty path ends here
  const badge = document.getElementById('cart-count-badge');
  const totalQty = cartItems.reduce((sum, p) => sum + (p.quantity || 1), 0);
  badge.textContent = totalQty ? `(${totalQty})` : '';
}

function renderCartModal() {
  const tbody = document.getElementById('cart-tbody');
  if (!cartItems.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="5">Your cart is empty.</td></tr>';
    return;
  }
  tbody.innerHTML = cartItems.map(p => `
    <tr>
      <td class="mono">${escapeHtml(p.sku)}</td>
      <td>${escapeHtml(p.name)}</td>
      <td><input type="number" min="1" step="1" class="cart-qty-input" data-qty-id="${p.id}" value="${p.quantity || 1}" style="width:64px;"></td>
      <td class="mono">${fmtMoney(p.unit_price)}</td>
      <td><button class="link-btn-inline" data-remove-cart-id="${p.id}" style="color:var(--danger);">Remove</button></td>
    </tr>
  `).join('');
  tbody.querySelectorAll('[data-remove-cart-id]').forEach(btn => {
    btn.addEventListener('click', () => removeFromCart(btn.dataset.removeCartId));
  });
  tbody.querySelectorAll('[data-qty-id]').forEach(input => {
    input.addEventListener('change', () => {
      const item = cartItems.find(p => p.id === input.dataset.qtyId);
      if (!item) return;
      const newQty = Math.max(1, parseInt(input.value, 10) || 1);
      item.quantity = newQty;
      input.value = newQty;
      updateCartBadge();
    });
  });
}

document.getElementById('open-cart-btn').addEventListener('click', () => {
  showBanner('cart-modal-banner', '');
  renderCartModal();
  document.getElementById('cart-modal').classList.add('active');
});

document.getElementById('cart-modal-close').addEventListener('click', () => {
  document.getElementById('cart-modal').classList.remove('active');
});

document.getElementById('empty-cart-btn').addEventListener('click', () => {
  if (!cartItems.length) return;
  if (!confirm('Remove all items from the cart?')) return;
  emptyCart();
});

document.getElementById('import-from-cart-btn').addEventListener('click', () => {
  if (!cartItems.length) {
    showBanner('doc-modal-banner', 'Your cart is empty — add some products to it from the Products tab first.');
    return;
  }
  // Clear out any rows that are still completely blank (e.g. the default
  // starting row) so importing from the cart doesn't leave an empty,
  // unused line sitting above the real items.
  document.querySelectorAll('#line-items .line-item-row').forEach(row => {
    const hasProduct = !!row.dataset.resolvedProductId;
    const hasRemark = row.querySelector('.li-remark').value.trim() !== '';
    const hasPrice = row.querySelector('.li-price').value.trim() !== '';
    if (!hasProduct && !hasRemark && !hasPrice) row.remove();
  });
  cartItems.forEach(p => {
    addLineItem({
      product_id: p.id,
      description: p.name,
      quantity: p.quantity || 1,
      unit: (cachedProducts.find(c => c.id === p.id) || p).unit || 'No', // the live list first: the cart may predate a unit change
      unit_price: null,
      tax_rate: 0
    });
  });
  showBanner('doc-modal-banner', '');
});
let cachedCategories = [];

async function loadCategories() {
  try {
    cachedCategories = await apiFetch('/categories');
    const select = document.getElementById('product-category');
    const currentValue = select.value;
    select.innerHTML = '<option value="">— choose a category —</option>' +
      cachedCategories.map(c => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join('');
    if (currentValue) select.value = currentValue;
  } catch (err) {
    // non-fatal — product form still works, just without a populated dropdown yet
  }
}

document.getElementById('open-category-modal-btn').addEventListener('click', () => {
  document.getElementById('new-category-name').value = '';
  document.getElementById('new-category-description').value = '';
  showBanner('category-modal-banner', '');
  document.getElementById('category-modal').classList.add('active');
});

document.getElementById('category-modal-close').addEventListener('click', () => {
  document.getElementById('category-modal').classList.remove('active');
});

document.getElementById('create-category-btn').addEventListener('click', async () => {
  const name = document.getElementById('new-category-name').value.trim();
  const shortTerm = document.getElementById('new-category-short-term').value.trim();
  const description = document.getElementById('new-category-description').value.trim();
  if (!name) return;
  if (!shortTerm) {
    showBanner('category-modal-banner', 'Short term is required — this becomes the item number prefix for products in this category.');
    return;
  }
  showBanner('category-modal-banner', '');
  try {
    await apiFetch('/categories', { method: 'POST', body: JSON.stringify({ name, short_term: shortTerm, description: description || null }) });
    await loadCategories();
    document.getElementById('product-category').value = name;
    document.getElementById('new-category-short-term').value = '';
    document.getElementById('category-modal').classList.remove('active');
    showSuccessTick('Category saved');
  } catch (err) {
    showBanner('category-modal-banner', err.message);
    showErrorCross(err.message);
  }
});

document.getElementById('category-dropdown-arrow').addEventListener('click', (e) => {
  e.stopPropagation();
  document.getElementById('category-dropdown-menu').classList.toggle('active');
});

document.getElementById('bulk-import-dropdown-arrow').addEventListener('click', (e) => {
  e.stopPropagation();
  document.getElementById('bulk-import-dropdown-menu').classList.toggle('active');
});

document.addEventListener('click', () => {
  document.getElementById('category-dropdown-menu').classList.remove('active');
  document.getElementById('bulk-import-dropdown-menu').classList.remove('active');
});

document.getElementById('download-product-template-btn').addEventListener('click', async () => {
  document.getElementById('bulk-import-dropdown-menu').classList.remove('active');
  try {
    const response = await fetch(API_BASE + '/products/bulk-import-template', {
      headers: { 'Authorization': 'Bearer ' + token }
    });
    if (!response.ok) {
      showBanner('products-banner', 'Could not download the template.');
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'product-bulk-import-template.xlsx';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showBanner('products-banner', err.message);
  }
});

document.getElementById('open-category-list-btn').addEventListener('click', async () => {
  document.getElementById('category-dropdown-menu').classList.remove('active');
  showBanner('category-list-banner', '');
  document.getElementById('category-list-modal').classList.add('active');
  await loadCategories();
  renderCategoryListTable();
});

function renderCategoryListTable() {
  const tbody = document.getElementById('category-list-tbody');
  if (!cachedCategories.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="4">No categories yet.</td></tr>';
    return;
  }
  tbody.innerHTML = cachedCategories.map(c => `
    <tr>
      <td>${escapeHtml(c.name)}</td>
      <td class="mono">${c.short_term ? escapeHtml(c.short_term) : '—'}</td>
      <td style="color:var(--text-dim); font-size:13px;">${c.description ? escapeHtml(c.description) : '—'}</td>
      <td><button class="link-btn-inline" data-edit-category-id="${c.id}">Edit</button></td>
    </tr>
  `).join('');
  tbody.querySelectorAll('[data-edit-category-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const category = cachedCategories.find(c => c.id === btn.dataset.editCategoryId);
      if (category) openEditCategoryModal(category);
    });
  });
}

document.getElementById('category-list-close').addEventListener('click', () => {
  document.getElementById('category-list-modal').classList.remove('active');
});

let editingCategoryId = null;

function openEditCategoryModal(category) {
  editingCategoryId = category.id;
  document.getElementById('edit-category-name').value = category.name;
  document.getElementById('edit-category-short-term').value = category.short_term || '';
  document.getElementById('edit-category-description').value = category.description || '';
  showBanner('edit-category-banner', '');
  document.getElementById('category-list-modal').classList.remove('active');
  document.getElementById('edit-category-modal').classList.add('active');
}

document.getElementById('edit-category-close').addEventListener('click', () => {
  document.getElementById('edit-category-modal').classList.remove('active');
});

document.getElementById('edit-category-save-btn').addEventListener('click', async () => {
  const name = document.getElementById('edit-category-name').value.trim();
  const shortTerm = document.getElementById('edit-category-short-term').value.trim();
  const description = document.getElementById('edit-category-description').value.trim();
  if (!name) return;
  if (!shortTerm) {
    showBanner('edit-category-banner', 'Short term is required — this becomes the item number prefix for products in this category.');
    return;
  }
  showBanner('edit-category-banner', '');
  try {
    await apiFetch('/categories/' + editingCategoryId, {
      method: 'PATCH',
      body: JSON.stringify({ name, short_term: shortTerm, description: description || null })
    });
    await loadCategories();
    document.getElementById('edit-category-modal').classList.remove('active');
    showSuccessTick('Category saved');
  } catch (err) {
    showBanner('edit-category-banner', err.message);
    showErrorCross(err.message);
  }
});

document.getElementById('product-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner('product-modal-banner', '');
  const fileInput = document.getElementById('product-file');
  const payload = {
    sku: document.getElementById('product-sku').value || null,
    name: document.getElementById('product-name').value,
    category: document.getElementById('product-category').value || null,
    description: document.getElementById('product-description').value || null,
    remark: document.getElementById('product-remark').value || null,
    unit: readUnitControl(document.getElementById('product-unit')) || 'No',
    unit_price: null // price isn't set at the catalog level anymore — entered per-document instead
  };
  // Duplicate check, only when the name/description is new or changed (products
  // that are already duplicates can still be edited). The server re-checks
  // exact duplicates, so a failed check here just falls through to it.
  const editingProduct = editingProductId && cachedProducts.find(p => p.id === editingProductId);
  const textChanged = !editingProduct || editingProduct.name !== payload.name
    || (editingProduct.description || null) !== payload.description;
  if (textChanged && !productDupWarningAccepted) {
    try {
      const qs = new URLSearchParams({ name: payload.name, description: payload.description || '' });
      if (editingProductId) qs.set('exclude_id', editingProductId);
      const found = await apiFetch('/products/duplicate-check?' + qs);
      if (found.exact.length) {
        showProductExistsBanner(found.exact[0]);
        return;
      }
      if (found.same_name.length || found.similar.length) {
        openProductDupWarning([...found.same_name, ...found.similar]);
        return;
      }
    } catch (err) {
      // fall through to the save; the server still blocks exact duplicates
    }
  }
  productDupWarningAccepted = false;
  try {
    setBusy(document.getElementById('product-form-submit-btn'), true);
    let product;
    if (editingProductId) {
      // sku can't be changed via PATCH (it's the identity); unit_price is
      // also left out here — it's not managed via this form anymore, and
      // omitting it (rather than sending null) means an edit never wipes
      // out a price a product might already have from before this change.
      const { sku, unit_price, ...updatePayload } = payload;
      product = await apiFetch('/products/' + editingProductId, {
        method: 'PATCH',
        body: JSON.stringify({ ...updatePayload, version: editingVersions.product })
      });
    } else {
      product = await apiFetch('/products', { method: 'POST', headers: idempotencyHeader('product'), body: JSON.stringify({ ...payload, group_id: chosenGroupId('product-form') }) });
      idempotencyDone('product');
    }

    if (fileInput.files.length > 0) {
      const formData = new FormData();
      formData.append('file', fileInput.files[0]);
      const uploadResponse = await fetch(API_BASE + '/products/' + product.id + '/file', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token },
        body: formData
      }).catch(() => ({ ok: false, json: async () => ({ detail: 'the connection failed' }) }));
      if (!uploadResponse.ok) {
        let detail = 'Catalog file upload failed.';
        try {
          const errBody = await uploadResponse.json();
          if (errBody.detail) detail = errBody.detail;
        } catch (parseErr) {
          // response wasn't JSON — stick with the generic message
        }
        if (!editingProductId) {
          // All or nothing for a new product: take it back out, so there's
          // no half-saved product without its file. (An edit already saved.)
          await apiFetch('/products/' + product.id, { method: 'DELETE' }).catch(() => {});
          showBanner('product-modal-banner', `Nothing was saved — the catalog file didn't upload: ${detail}`);
        } else {
          showBanner('product-modal-banner', `Product saved, but the catalog file didn't upload: ${detail}`);
        }
        showErrorCross("The catalog file didn't upload");
        loadProducts();
        return;
      }
      uploadResponse.text().catch(() => {});  // drain (no-store body)
    }

    if (!editingProductId && pendingSubItems.length > 0) {
      for (const item of pendingSubItems) {
        try {
          await apiFetch('/products/' + product.id + '/sub-items', {
            method: 'POST',
            body: JSON.stringify({ product_id: item.id })
          });
        } catch (err) {
          showBanner('product-modal-banner', `Product saved, but a sub-item ("${item.name}") failed to attach: ${err.message}`);
          showErrorCross('A sub-item failed to attach');
          loadProducts();
          return;
        }
      }
    }

    document.getElementById('product-form').reset();
    document.getElementById('product-modal').classList.remove('active');
    document.getElementById('product-form-title').textContent = 'New product';
    document.getElementById('product-sku').disabled = false;
    document.getElementById('product-form-submit-btn').textContent = 'Create product';
    editingProductId = null;
    pendingSubItems = [];
    loadProducts();
    showSuccessTick('Product saved');
  } catch (err) {
    const id = editingProductId;
    if (err.detail && err.detail.existing) { showProductExistsBanner(err.detail.existing); showErrorCross(err.message); }  // 409 race
    else showSaveError(err, 'product-modal-banner', id && (async () => { startEditProduct(await apiFetch('/products/' + id)); loadProducts(); }));
  } finally {
    setBusy(document.getElementById('product-form-submit-btn'), false);
  }
});

// ---------- duplicate products ----------
let productDupWarningAccepted = false;  // set by "Create anyway" for the one resubmit

async function openExistingProduct(productId) {
  try {
    const product = cachedProducts.find(p => p.id === productId) || await apiFetch('/products/' + productId);
    openViewProductModal(product);
  } catch (err) {
    showBanner('product-modal-banner', err.message);
  }
}

function showProductExistsBanner(existing) {
  document.getElementById('product-modal-banner').innerHTML = `<div class="banner banner-error">This item already exists:
    <button type="button" class="link-btn-inline" id="product-exists-link">${escapeHtml(existing.sku)} ${escapeHtml(existing.name)}</button></div>`;
  document.getElementById('product-exists-link').addEventListener('click', () => openExistingProduct(existing.id));
}

function openProductDupWarning(matches) {
  const tbody = document.getElementById('product-dup-warning-tbody');
  tbody.innerHTML = matches.map(p => {
    const desc = p.description || '';
    return `
    <tr>
      <td class="mono">${escapeHtml(p.sku)}</td>
      <td>${escapeHtml(p.name)}</td>
      <td>${escapeHtml(desc.length > 80 ? desc.slice(0, 80) + '…' : desc) || '—'}</td>
      <td><button type="button" class="link-btn-inline" data-use-existing-id="${p.id}">Use existing</button></td>
    </tr>`;
  }).join('');
  tbody.querySelectorAll('[data-use-existing-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.getElementById('product-dup-warning-modal').classList.remove('active');
      document.getElementById('product-modal-close').click();
      openExistingProduct(btn.dataset.useExistingId);
    });
  });
  document.getElementById('product-dup-warning-modal').classList.add('active');
}

document.getElementById('product-dup-warning-cancel').addEventListener('click', () => {
  document.getElementById('product-dup-warning-modal').classList.remove('active');
});
document.getElementById('product-dup-warning-create').addEventListener('click', () => {
  document.getElementById('product-dup-warning-modal').classList.remove('active');
  productDupWarningAccepted = true;
  document.getElementById('product-form').requestSubmit();
});

document.getElementById('open-product-duplicates-btn').addEventListener('click', () => {
  showBanner('product-duplicates-banner', '');
  document.getElementById('product-duplicates-modal').classList.add('active');
  loadProductDuplicates();
});
document.getElementById('product-duplicates-close').addEventListener('click', () => {
  document.getElementById('product-duplicates-modal').classList.remove('active');
});

function renderDuplicateGroups(title, color, groups, dismissable = false) {
  const tables = groups.map(group => `
    ${dismissable ? `<div style="text-align:right; margin-bottom:4px;"><button type="button" class="link-btn-inline" data-dup-dismiss-ids="${group.map(p => p.id).join(',')}" title="These are different products — stop warning me">Not a duplicate</button></div>` : ''}
    <table style="margin-bottom:12px;">
      <thead><tr><th>Item Number</th><th>Name</th><th>Description</th><th>Category</th><th>Delete</th></tr></thead>
      <tbody>${group.map(p => `
        <tr>
          <td class="mono">${escapeHtml(p.sku)}</td>
          <td>${escapeHtml(p.name)}</td>
          <td>${escapeHtml(p.description || '—')}</td>
          <td>${escapeHtml(p.category || '—')}</td>
          <td><button class="link-btn-inline" data-dup-trash-id="${p.id}" data-dup-trash-sku="${escapeHtml(p.sku)}" style="color:var(--danger);">Move to trash</button></td>
        </tr>`).join('')}
      </tbody>
    </table>`).join('');
  return `<h4 style="color:${color}; margin:16px 0 8px;">${title} (${groups.length})</h4>`
    + (tables || '<p style="color:var(--text-dim); font-size:13px;">None found.</p>');
}

// "Not a duplicate" pairs, collapsed at the bottom; Restore lets a pair warn again.
function renderHiddenPairs(pairs) {
  if (!pairs.length) return '';
  const rows = pairs.map(d => `
    <tr>
      <td>${d.products.map(p => `<span class="mono">${escapeHtml(p.sku)}</span> ${escapeHtml(p.description || '—')}`).join('<br>')}</td>
      <td>${escapeHtml(d.products[0].name)}</td>
      <td><button type="button" class="link-btn-inline" data-dup-restore-id="${d.id}">Restore</button></td>
    </tr>`).join('');
  return `<details style="margin-top:16px;"><summary>Hidden pairs (${pairs.length})</summary>
    <table style="margin-top:8px;"><thead><tr><th>Products</th><th>Name</th><th></th></tr></thead><tbody>${rows}</tbody></table></details>`;
}

async function loadProductDuplicates() {
  const content = document.getElementById('product-duplicates-content');
  content.innerHTML = '<p style="color:var(--text-dim); font-size:13px;">Loading…</p>';
  try {
    const [result, hidden] = await Promise.all([apiFetch('/products/duplicates'), apiFetch('/products/duplicates/dismissed')]);
    content.innerHTML = renderDuplicateGroups('Exact duplicates', 'var(--danger)', result.exact_groups)
      + renderDuplicateGroups('Same name', 'inherit', result.same_name_groups, true)
      + renderHiddenPairs(hidden);
    content.querySelectorAll('[data-dup-trash-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm(`Move "${btn.dataset.dupTrashSku}" to the recycle bin? You can restore it from there later.`)) return;
        showBanner('product-duplicates-banner', '');
        try {
          await apiFetch('/products/' + btn.dataset.dupTrashId + '/trash', { method: 'PATCH' });
          loadProductDuplicates();
          loadProducts();
        } catch (err) {
          showBanner('product-duplicates-banner', err.message);
        }
      });
    });
    content.querySelectorAll('[data-dup-dismiss-ids]').forEach(btn => {
      btn.addEventListener('click', async () => {
        showBanner('product-duplicates-banner', '');
        try {
          await apiFetch('/products/duplicates/dismiss', { method: 'POST', body: JSON.stringify({ product_ids: btn.dataset.dupDismissIds.split(',') }) });
          showSuccessTick('Marked as not a duplicate');
          loadProductDuplicates();
        } catch (err) {
          showBanner('product-duplicates-banner', err.message);
        }
      });
    });
    content.querySelectorAll('[data-dup-restore-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        showBanner('product-duplicates-banner', '');
        try {
          await apiFetch('/products/duplicates/dismissed/' + btn.dataset.dupRestoreId, { method: 'DELETE' });
          showSuccessTick('Restored');
          loadProductDuplicates();
        } catch (err) {
          showBanner('product-duplicates-banner', err.message);
        }
      });
    });
  } catch (err) {
    content.innerHTML = '';
    showBanner('product-duplicates-banner', err.message);
  }
}


function startEditProduct(product) {
  editingProductId = product.id;
  editingVersions.product = product.version;
  showGroupSelect('product-form', false);
  document.getElementById('product-form-title').textContent = 'Edit product — ' + product.sku;
  document.getElementById('product-sku').value = product.sku;
  document.getElementById('product-sku').disabled = true; // identity field, not editable
  document.getElementById('product-name').value = product.name;
  document.getElementById('product-category').value = product.category || '';
  document.getElementById('product-description').value = product.description || '';
  document.getElementById('product-remark').value = product.remark || '';
  setUnitControl(document.getElementById('product-unit'), product.unit);
  document.getElementById('product-form-submit-btn').textContent = 'Save changes';
  const fileInfo = document.getElementById('product-current-file-info');
  if (product.image_object_key) {
    fileInfo.textContent = 'A catalog file is already attached. Choosing a new one will replace it.';
    fileInfo.style.display = 'block';
  } else {
    fileInfo.style.display = 'none';
  }
  document.getElementById('product-modal').classList.add('active');
  refreshSubItemDatalist();
  loadEditingProductSubItems();
}

document.getElementById('open-bulk-import-btn').addEventListener('click', () => {
  showBanner('bulk-import-banner', '');
  document.getElementById('bulk-import-excel').value = '';
  document.getElementById('bulk-import-zip').value = '';
  document.getElementById('bulk-import-results').innerHTML = '';
  document.getElementById('bulk-import-modal').classList.add('active');
});

document.getElementById('bulk-import-close').addEventListener('click', () => {
  document.getElementById('bulk-import-modal').classList.remove('active');
});

document.getElementById('bulk-import-submit-btn').addEventListener('click', async () => {
  const excelInput = document.getElementById('bulk-import-excel');
  const zipInput = document.getElementById('bulk-import-zip');
  const resultsEl = document.getElementById('bulk-import-results');

  if (!excelInput.files.length) {
    showBanner('bulk-import-banner', 'Choose a spreadsheet file first.');
    return;
  }

  showBanner('bulk-import-banner', '');
  resultsEl.innerHTML = '<p style="color:var(--text-dim); font-size:13px;">Importing…</p>';
  const submitBtn = document.getElementById('bulk-import-submit-btn');
  submitBtn.disabled = true;

  try {
    const formData = new FormData();
    formData.append('excel_file', excelInput.files[0]);
    const importGroup = document.getElementById('bulk-import-group');
    if (importGroup && importGroup.value) formData.append('group_id', importGroup.value);
    if (zipInput.files.length) {
      formData.append('catalogue_zip', zipInput.files[0]);
    }

    const response = await fetch(API_BASE + '/products/bulk-import', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + token },
      body: formData
    });

    if (!response.ok) {
      let detail = 'Bulk import failed.';
      try {
        const errBody = await response.json();
        if (errBody.detail) detail = errBody.detail;
      } catch (parseErr) {
        // response wasn't JSON — stick with the generic message
      }
      showBanner('bulk-import-banner', detail);
      showErrorCross(detail);
      resultsEl.innerHTML = '';
      return;
    }

    const result = await response.json();
    const rows = result.created.map(item => `
      <tr>
        <td class="mono">${item.row}</td>
        <td class="mono">${escapeHtml(item.sku)}</td>
        <td>${escapeHtml(item.name)}</td>
        <td>${item.has_file ? '✓' : '—'}</td>
      </tr>
    `).join('');

    const skipped = result.skipped || [];
    const warningText = result.warnings.map(w => typeof w === 'string' ? escapeHtml(w)
      : `Row ${w.row} (${escapeHtml(w.name)}): ${escapeHtml(w.reason)}`);
    const skippedRows = skipped.map(item => `
      <tr>
        <td class="mono">${item.row}</td>
        <td>${escapeHtml(item.name)}</td>
        <td>${escapeHtml(item.reason)}</td>
        <td class="mono">${escapeHtml(item.existing_sku)}</td>
      </tr>
    `).join('');

    resultsEl.innerHTML = `
      <p style="font-weight:600;">Created ${result.created.length} product${result.created.length === 1 ? '' : 's'} · Skipped ${skipped.length} duplicate${skipped.length === 1 ? '' : 's'} · ${warningText.length} warning${warningText.length === 1 ? '' : 's'}</p>
      <table>
        <thead><tr><th>Row</th><th>Item Number</th><th>Name</th><th>File attached?</th></tr></thead>
        <tbody>${rows || '<tr class="empty-row"><td colspan="4">Nothing was created.</td></tr>'}</tbody>
      </table>
      ${skipped.length ? `
        <p style="font-weight:600; margin-top:12px;">Skipped (already in the catalogue)</p>
        <table>
          <thead><tr><th>Row</th><th>Name</th><th>Reason</th><th>Existing item</th></tr></thead>
          <tbody>${skippedRows}</tbody>
        </table>` : ''}
      ${warningText.length ? `<p style="color:var(--danger); font-size:13px; margin-top:10px;">${warningText.join('<br>')}</p>` : ''}
    `;
    loadProducts();
    showSuccessTick('Import finished');
  } catch (err) {
    showBanner('bulk-import-banner', err.message);
    showErrorCross(err.message);
    resultsEl.innerHTML = '';
  } finally {
    submitBtn.disabled = false;
  }
});

document.getElementById('sync-categories-from-products-btn').addEventListener('click', async () => {
  document.getElementById('category-dropdown-menu').classList.remove('active');
  if (!confirm('Scan all products and create any missing categories? This is safe to run any time — it only adds categories that don\'t exist yet.')) return;
  try {
    const result = await apiFetch('/categories/sync-from-products', { method: 'POST' });
    if (result.created.length === 0) {
      alert('Nothing to sync — every category already in use is already registered.');
    } else {
      const names = result.created.map(c => `${c.name} (${c.short_term})`).join('\n');
      alert(`Created ${result.created.length} categor${result.created.length === 1 ? 'y' : 'ies'}:\n${names}`);
    }
    loadCategories();
  } catch (err) {
    showBanner('products-banner', err.message);
  }
});

document.getElementById('open-category-bulk-import-btn').addEventListener('click', () => {
  document.getElementById('category-dropdown-menu').classList.remove('active');
  showBanner('category-bulk-import-banner', '');
  document.getElementById('category-bulk-import-excel').value = '';
  document.getElementById('category-bulk-import-results').innerHTML = '';
  document.getElementById('category-bulk-import-modal').classList.add('active');
});

document.getElementById('category-bulk-import-close').addEventListener('click', () => {
  document.getElementById('category-bulk-import-modal').classList.remove('active');
});

document.getElementById('download-category-template-btn').addEventListener('click', async () => {
  try {
    const response = await fetch(API_BASE + '/categories/bulk-import-template', {
      headers: { 'Authorization': 'Bearer ' + token }
    });
    if (!response.ok) {
      showBanner('category-bulk-import-banner', 'Could not download the template.');
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'category-bulk-import-template.xlsx';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showBanner('category-bulk-import-banner', err.message);
  }
});

document.getElementById('category-bulk-import-submit-btn').addEventListener('click', async () => {
  const excelInput = document.getElementById('category-bulk-import-excel');
  const resultsEl = document.getElementById('category-bulk-import-results');

  if (!excelInput.files.length) {
    showBanner('category-bulk-import-banner', 'Choose a spreadsheet file first.');
    return;
  }

  showBanner('category-bulk-import-banner', '');
  resultsEl.innerHTML = '<p style="color:var(--text-dim); font-size:13px;">Importing…</p>';
  const submitBtn = document.getElementById('category-bulk-import-submit-btn');
  submitBtn.disabled = true;

  try {
    const formData = new FormData();
    formData.append('file', excelInput.files[0]);

    const response = await fetch(API_BASE + '/categories/bulk-import', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + token },
      body: formData
    });

    if (!response.ok) {
      let detail = 'Bulk import failed.';
      try {
        const errBody = await response.json();
        if (errBody.detail) detail = errBody.detail;
      } catch (parseErr) {
        // response wasn't JSON — stick with the generic message
      }
      showBanner('category-bulk-import-banner', detail);
      showErrorCross(detail);
      resultsEl.innerHTML = '';
      return;
    }

    const result = await response.json();
    const rows = result.created.map(item => `
      <tr>
        <td class="mono">${item.row}</td>
        <td>${escapeHtml(item.name)}</td>
        <td class="mono">${escapeHtml(item.short_term)}</td>
      </tr>
    `).join('');

    resultsEl.innerHTML = `
      <p style="font-weight:600;">Created ${result.created.length} categor${result.created.length === 1 ? 'y' : 'ies'}.</p>
      <table>
        <thead><tr><th>Row</th><th>Name</th><th>Short Term</th></tr></thead>
        <tbody>${rows || '<tr class="empty-row"><td colspan="3">Nothing was created.</td></tr>'}</tbody>
      </table>
      ${result.warnings.length ? `<p style="color:var(--danger); font-size:13px; margin-top:10px;">${result.warnings.map(w => escapeHtml(w)).join('<br>')}</p>` : ''}
    `;
    loadCategories();
    showSuccessTick('Import finished');
  } catch (err) {
    showBanner('category-bulk-import-banner', err.message);
    showErrorCross(err.message);
    resultsEl.innerHTML = '';
  } finally {
    submitBtn.disabled = false;
  }
});

async function loadProducts() {
  const tbody = document.getElementById('products-tbody');
  const mySeq = startLoad('products');
  try {
    const products = await apiFetch('/products');
    if (isStaleLoad('products', mySeq)) return;
    cachedProducts = products;
    pruneCart();
    refreshLineItemProductOptions();
    refreshProductCategoryFilter();
    applyFilterAndSort('products');
  } catch (err) {
    if (isStaleLoad('products', mySeq)) return;
    tbody.innerHTML = `<tr class="empty-row"><td colspan="12">${escapeHtml(err.message)}</td></tr>`;
  }
}

function refreshProductCategoryFilter() {
  const select = document.getElementById('products-category-filter');
  if (!select) return;
  const currentValue = select.value;
  const categories = [...new Set(cachedProducts.map(p => p.category).filter(Boolean))].sort();
  select.innerHTML = '<option value="">All categories</option>' +
    categories.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
  if (currentValue && categories.includes(currentValue)) select.value = currentValue;
}

async function viewSingleProductFile(productId, btn) {
  const originalLabel = btn ? btn.textContent : null;
  if (btn) {
    btn.textContent = 'Preparing…';
    btn.disabled = true;
  }
  try {
    const data = await apiFetch('/products/' + productId + '/file-url');
    window.open(data.url, '_blank');
  } catch (err) {
    alert(err.message);
  } finally {
    if (btn) {
      btn.textContent = originalLabel;
      btn.disabled = false;
    }
  }
}

async function downloadProductBundle(productId, sku, btn) {
  const originalLabel = btn ? btn.textContent : null;
  if (btn) {
    btn.textContent = 'Preparing…';
    btn.disabled = true;
  }
  try {
    const response = await fetch(API_BASE + '/products/' + productId + '/download-bundle', {
      headers: { 'Authorization': 'Bearer ' + token }
    });
    if (!response.ok) {
      let detail = 'Could not download the bundle.';
      try {
        const errBody = await response.json();
        if (errBody.detail) detail = errBody.detail;
      } catch (parseErr) {
        // response wasn't JSON — stick with the generic message
      }
      alert(detail);
      return;
    }
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${sku}-catalogue.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  } catch (err) {
    alert(err.message);
  } finally {
    if (btn) {
      btn.textContent = originalLabel;
      btn.disabled = false;
    }
  }
}

document.getElementById('view-product-close').addEventListener('click', () => {
  document.getElementById('view-product-modal').classList.remove('active');
});

document.getElementById('view-customer-close').addEventListener('click', () => {
  document.getElementById('view-customer-modal').classList.remove('active');
});

async function openViewCustomerModal(customer) {
  document.getElementById('view-customer-title').textContent = 'Customer — ' + customer.name;
  const content = document.getElementById('view-customer-content');
  content.innerHTML = renderCustomerDetailContent(customer) + '<p style="color:var(--text-dim); font-size:13px; margin-top:10px;">Loading…</p>';
  document.getElementById('view-customer-modal').classList.add('active');

  let usedIn = [];
  try {
    usedIn = await apiFetch('/documents/by-customer/' + customer.id);
  } catch (err) {
    usedIn = [];
  }
  content.innerHTML = renderCustomerDetailContent(customer) + renderUsedInDocuments(usedIn, 'No documents for this customer yet.');
}

function renderCustomerDetailContent(customer) {
  return `
    <div class="form-row">
      <div><strong>Email:</strong> ${escapeHtml(customer.email || '—')}</div>
      <div><strong>Phone:</strong> ${escapeHtml(customer.phone || '—')}</div>
    </div>
    <div class="form-row">
      <div><strong>Created:</strong> ${fmtDate(customer.created_at)}</div>
      <div></div>
    </div>
  `;
}

async function openViewProductModal(product) {
  document.getElementById('view-product-title').textContent = 'Product — ' + product.sku;
  const content = document.getElementById('view-product-content');
  content.innerHTML = renderProductDetailContent(product) + '<p style="color:var(--text-dim); font-size:13px; margin-top:10px;">Loading…</p>';
  document.getElementById('view-product-modal').classList.add('active');
  wireViewProductFileButton(product);

  let subItems = [];
  let usedIn = [];
  try {
    subItems = await apiFetch('/products/' + product.id + '/sub-items');
  } catch (err) {
    subItems = [];
  }
  try {
    usedIn = await apiFetch('/documents/by-product/' + product.id);
  } catch (err) {
    usedIn = [];
  }
  content.innerHTML = renderProductDetailContent(product) + renderUsedInDocuments(usedIn) + renderProductSubItemsReadonly(subItems);
  wireViewProductFileButton(product);
}

function renderUsedInDocuments(usedIn, emptyLabel) {
  if (!usedIn.length) {
    return `<div style="margin-top:14px;"><strong>Used in</strong><div style="font-size:13px; color:var(--text-dim); margin-top:4px;">${emptyLabel || 'Not used in any document yet.'}</div></div>`;
  }
  const rows = usedIn.map(u => `
    <div style="font-size:13px; padding:3px 0;">
      <span class="${stampClass(u.doc_type)}">${escapeHtml(u.doc_type)}</span>
      ${escapeHtml(u.doc_number)}${u.project_name ? ' — ' + escapeHtml(u.project_name) + ' project' : ''}
    </div>
  `).join('');
  return `<div style="margin-top:14px;"><strong>Used in</strong><div style="margin-top:4px;">${rows}</div></div>`;
}

function wireViewProductFileButton(product) {
  const btn = document.getElementById('view-product-file-btn');
  if (!btn) return;
  btn.addEventListener('click', () => {
    if (product.sub_item_count > 0) {
      downloadProductBundle(product.id, product.sku, btn);
    } else {
      viewSingleProductFile(product.id, btn);
    }
  });
}

function renderProductDetailContent(product) {
  const hasFileAction = product.image_object_key || product.sub_item_count > 0;
  return `
    <div class="form-row">
      <div><strong>Item Number:</strong> ${escapeHtml(product.sku)}</div>
      <div><strong>Category:</strong> ${escapeHtml(product.category || '—')}</div>
    </div>
    <div class="form-row">
      <div><strong>Price:</strong> ${fmtMoney(product.unit_price)} ${escapeHtml(product.currency || '')}</div>
      <div>${hasFileAction ? '<button type="button" class="link-btn-inline" id="view-product-file-btn">View file</button>' : ''}</div>
    </div>
    ${product.description ? `
      <div style="margin-top:10px;">
        <strong>Description</strong>
        <div style="font-size:13px; margin-top:4px; color:var(--text-dim);">${escapeHtml(product.description)}</div>
      </div>
    ` : ''}
    ${product.remark ? `
      <div style="margin-top:10px;">
        <strong>Remark</strong>
        <div style="font-size:13px; margin-top:4px; color:var(--text-dim);">${escapeHtml(product.remark)}</div>
      </div>
    ` : ''}
  `;
}

function renderProductSubItemsReadonly(subItems) {
  if (!subItems.length) return '';
  const rows = subItems.map(item => `
    <tr>
      <td class="mono">${item.sequence_number}</td>
      <td>${escapeHtml(item.sku)} — ${escapeHtml(item.name)}${item.has_file ? '' : ' <span style="color:var(--text-dim); font-size:12px;">(no catalog file)</span>'}</td>
    </tr>
  `).join('');
  return `
    <div style="margin-top:14px;">
      <strong>Sub items</strong>
      <table style="margin-top:6px;">
        <thead><tr><th>#</th><th>Product</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderProductsTable(products) {
  const tbody = document.getElementById('products-tbody');
  if (!products.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="12">No products match.</td></tr>';
    return;
  }
  tbody.innerHTML = products.map(p => `
    <tr>
      ${selectCell('products', p)}
      <td class="mono">${escapeHtml(p.sku)}</td>
      <td>
        <button class="link-btn-inline" data-view-product-id="${p.id}">${escapeHtml(p.name)}</button>
        ${p.description ? `<div style="font-size:12px; color:var(--text-dim); margin-top:2px; white-space:pre-line;">${escapeHtml(p.description)}</div>` : ''}
      </td>
      <td>${escapeHtml(p.category || '—')}</td>
      <td>${escapeHtml(p.unit || 'No')}</td>
      <td>${fmtDate(p.created_at)}</td>
      <td>${fmtDate(p.updated_at)}</td>
      <td>${p.sub_item_count > 0
        ? `<button class="link-btn-inline" data-download-bundle-id="${p.id}" data-download-bundle-sku="${escapeHtml(p.sku)}">View file</button>`
        : (p.image_object_key ? `<button class="link-btn-inline" data-product-id="${p.id}">View file</button>` : '—')}</td>
      <td><button class="link-btn-inline" data-add-to-cart-id="${p.id}">+ Add to cart</button></td>
      <td><button class="link-btn-inline" data-edit-id="${p.id}">Edit</button></td>
      <td><button class="link-btn-inline" data-trash-product-id="${p.id}" data-trash-product-sku="${escapeHtml(p.sku)}" style="color:var(--danger);">Delete</button></td>
      ${groupCell('products', p)}
    </tr>
  `).join('');
  tbody.querySelectorAll('[data-add-to-cart-id]').forEach(btn => {
    btn.addEventListener('click', () => addToCart(btn.dataset.addToCartId));
  });
  tbody.querySelectorAll('[data-view-product-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const product = cachedProducts.find(p => p.id === btn.dataset.viewProductId);
      if (product) openViewProductModal(product);
    });
  });
  tbody.querySelectorAll('[data-product-id]').forEach(btn => {
    btn.addEventListener('click', () => viewSingleProductFile(btn.dataset.productId, btn));
  });
  tbody.querySelectorAll('[data-download-bundle-id]').forEach(btn => {
    btn.addEventListener('click', () => downloadProductBundle(btn.dataset.downloadBundleId, btn.dataset.downloadBundleSku, btn));
  });
  tbody.querySelectorAll('[data-edit-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const product = cachedProducts.find(p => p.id === btn.dataset.editId);
      if (product) startEditProduct(product);
    });
  });
  tbody.querySelectorAll('[data-trash-product-id]').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm(`Move "${btn.dataset.trashProductSku}" to the recycle bin? You can restore it from there later.`)) return;
      showBanner('products-banner', '');
      try {
        await apiFetch('/products/' + btn.dataset.trashProductId + '/trash', { method: 'PATCH' });
        loadProducts();
      } catch (err) {
        showBanner('products-banner', err.message);
      }
    });
  });
}

document.getElementById('products-filter').addEventListener('input', () => applyFilterAndSort('products'));
document.getElementById('products-category-filter').addEventListener('change', () => applyFilterAndSort('products'));
document.getElementById('products-date-from').addEventListener('change', () => applyFilterAndSort('products'));
document.getElementById('products-date-to').addEventListener('change', () => applyFilterAndSort('products'));
document.getElementById('products-reset-filters-btn').addEventListener('click', () => {
  document.getElementById('products-filter').value = '';
  document.getElementById('products-category-filter').value = '';
  document.getElementById('products-date-from').value = '';
  document.getElementById('products-date-to').value = '';
  applyFilterAndSort('products');
});

document.getElementById('open-product-trash-btn').addEventListener('click', async () => {
  showBanner('product-trash-banner', '');
  document.getElementById('product-trash-modal').classList.add('active');
  await loadProductTrash();
});

document.getElementById('product-trash-close').addEventListener('click', () => {
  document.getElementById('product-trash-modal').classList.remove('active');
});

async function loadProductTrash() {
  const tbody = document.getElementById('product-trash-tbody');
  tbody.innerHTML = skeletonRows(4);
  try {
    const products = await apiFetch('/products/trash');
    if (!products.length) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="4">Recycle bin is empty.</td></tr>';
      return;
    }
    tbody.innerHTML = products.map(p => `
      <tr>
        <td class="mono">${escapeHtml(p.sku)}</td>
        <td>${escapeHtml(p.name)}</td>
        <td class="mono">${fmtMoney(p.unit_price)}</td>
        <td><button class="link-btn-inline" data-restore-product-id="${p.id}">Restore</button></td>
      </tr>
    `).join('');
    tbody.querySelectorAll('[data-restore-product-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        showBanner('product-trash-banner', '');
        try {
          await apiFetch('/products/' + btn.dataset.restoreProductId + '/restore', { method: 'PATCH' });
          await loadProductTrash();
          loadProducts();
        } catch (err) {
          showBanner('product-trash-banner', err.message);
        }
      });
    });
  } catch (err) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="4">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById('toggle-customer-trash-btn').addEventListener('click', async () => {
  showBanner('customer-trash-banner', '');
  document.getElementById('customer-trash-modal').classList.add('active');
  await loadCustomerTrash();
});

document.getElementById('customer-trash-close').addEventListener('click', () => {
  document.getElementById('customer-trash-modal').classList.remove('active');
});

async function loadCustomerTrash() {
  const tbody = document.getElementById('customer-trash-tbody');
  tbody.innerHTML = skeletonRows(4);
  try {
    const customers = await apiFetch('/customers/trash');
    if (!customers.length) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="4">Recycle bin is empty.</td></tr>';
      return;
    }
    tbody.innerHTML = customers.map(c => `
      <tr>
        <td>${escapeHtml(c.name)}</td>
        <td>${escapeHtml(c.email || '—')}</td>
        <td>${escapeHtml(c.phone || '—')}</td>
        <td><button class="link-btn-inline" data-restore-customer-id="${c.id}">Restore</button></td>
      </tr>
    `).join('');
    tbody.querySelectorAll('[data-restore-customer-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        showBanner('customer-trash-banner', '');
        try {
          await apiFetch('/customers/' + btn.dataset.restoreCustomerId + '/restore', { method: 'PATCH' });
          await loadCustomerTrash();
          loadCustomers();
        } catch (err) {
          showBanner('customer-trash-banner', err.message);
        }
      });
    });
  } catch (err) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="4">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById('toggle-project-trash-btn').addEventListener('click', async () => {
  showBanner('project-trash-banner', '');
  document.getElementById('project-trash-modal').classList.add('active');
  await loadProjectTrash();
});

document.getElementById('project-trash-close').addEventListener('click', () => {
  document.getElementById('project-trash-modal').classList.remove('active');
});

async function loadProjectTrash() {
  const tbody = document.getElementById('project-trash-tbody');
  tbody.innerHTML = skeletonRows(3);
  try {
    const projects = await apiFetch('/projects/trash');
    if (!projects.length) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="3">Recycle bin is empty.</td></tr>';
      return;
    }
    tbody.innerHTML = projects.map(p => `
      <tr>
        <td>${escapeHtml(p.name)}</td>
        <td class="mono">${escapeHtml(p.budget_year || '—')}</td>
        <td><button class="link-btn-inline" data-restore-project-id="${p.id}">Restore</button></td>
      </tr>
    `).join('');
    tbody.querySelectorAll('[data-restore-project-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        showBanner('project-trash-banner', '');
        try {
          await apiFetch('/projects/' + btn.dataset.restoreProjectId + '/restore', { method: 'PATCH' });
          await loadProjectTrash();
          loadProjects();
        } catch (err) {
          showBanner('project-trash-banner', err.message);
        }
      });
    });
  } catch (err) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="3">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById('toggle-company-trash-btn').addEventListener('click', async () => {
  showBanner('company-trash-banner', '');
  document.getElementById('company-trash-modal').classList.add('active');
  await loadCompanyTrash();
});

document.getElementById('company-trash-close').addEventListener('click', () => {
  document.getElementById('company-trash-modal').classList.remove('active');
});

async function loadCompanyTrash() {
  const tbody = document.getElementById('company-trash-tbody');
  tbody.innerHTML = skeletonRows(3);
  try {
    const companies = await apiFetch('/companies/trash');
    if (!companies.length) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="3">Recycle bin is empty.</td></tr>';
      return;
    }
    tbody.innerHTML = companies.map(c => `
      <tr>
        <td>${escapeHtml(c.name)}</td>
        <td>${escapeHtml(c.short_name || '—')}</td>
        <td><button class="link-btn-inline" data-restore-company-id="${c.id}">Restore</button></td>
      </tr>
    `).join('');
    tbody.querySelectorAll('[data-restore-company-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        showBanner('company-trash-banner', '');
        try {
          await apiFetch('/companies/' + btn.dataset.restoreCompanyId + '/restore', { method: 'PATCH' });
          await loadCompanyTrash();
          loadCompanies();
        } catch (err) {
          showBanner('company-trash-banner', err.message);
        }
      });
    });
  } catch (err) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="3">${escapeHtml(err.message)}</td></tr>`;
  }
}

// ---------- generic 3-state column sorting (asc -> desc -> back to normal) ----------
// Used by Products, Customers, Projects, and Company tables — each already
// caches its full list and has a render*Table(list) function from the filter
// work above, so sorting just re-filters + re-sorts + re-renders from cache.

const sortState = {
  products: { column: null, dir: null },
  documents: { column: null, dir: null },
  customers: { column: null, dir: null },
  projects: { column: null, dir: null },
  company: { column: null, dir: null },
};

const sortAccessors = {
  products: {
    name: p => (p.name || '').toLowerCase(),
    category: p => (p.category || '').toLowerCase(),
    created: p => new Date(p.created_at).getTime(),
    modified: p => new Date(p.updated_at).getTime(),
  },
  customers: {
    name: c => (c.name || '').toLowerCase(),
    created_at: c => new Date(c.created_at).getTime(),
  },
  projects: {
    name: p => (p.name || '').toLowerCase(),
    budget_year: p => p.budget_year || '',
  },
  company: {
    name: c => (c.name || '').toLowerCase(),
  },
  documents: {
    doc_number: d => (d.doc_number || '').toLowerCase(),
    total: d => Number(d.total) || 0,
    created_at: d => new Date(d.created_at).getTime(),
  },
};

const tableConfig = {
  products: {
    cache: () => cachedProducts,
    filterInputId: 'products-filter',
    matchFn: (p, q) => p.name.toLowerCase().includes(q) || p.sku.toLowerCase().includes(q) || (p.category || '').toLowerCase().includes(q),
    render: renderProductsTable,
    extraFilters: [
      { selectId: 'products-category-filter', matchFn: (p, val) => (p.category || '') === val },
    ],
    dateFilter: { fromId: 'products-date-from', toId: 'products-date-to', field: 'created_at' },
  },
  documents: {
    cache: () => cachedDocuments,
    filterInputId: 'documents-filter',
    matchFn: (d, q) => d.doc_number.toLowerCase().includes(q),
    render: renderDocumentsTable,
    extraFilters: [
      { selectId: 'documents-type-filter', matchFn: (d, val) => d.doc_type === val },
      { selectId: 'documents-status-filter', matchFn: (d, val) => d.status === val },
      { selectId: 'documents-project-filter', matchFn: (d, val) => d.project_id === val },
    ],
    dateFilter: { fromId: 'documents-date-from', toId: 'documents-date-to', field: 'created_at' },
  },
  customers: {
    cache: () => cachedCustomers,
    filterInputId: 'customers-filter',
    matchFn: (c, q) => c.name.toLowerCase().includes(q) || (c.organization || '').toLowerCase().includes(q),
    render: renderCustomersTable,
    dateFilter: { fromId: 'customers-date-from', toId: 'customers-date-to', field: 'created_at' },
  },
  projects: {
    cache: () => cachedProjects,
    filterInputId: 'projects-filter',
    matchFn: (p, q) => p.name.toLowerCase().includes(q),
    render: renderProjectsTable,
    extraFilters: [
      { selectId: 'projects-budget-year-filter', matchFn: (p, val) => (p.budget_year || '') === val },
    ],
    dateFilter: { fromId: 'projects-date-from', toId: 'projects-date-to', field: 'created_at' },
  },
  company: {
    cache: () => cachedCompanies,
    filterInputId: 'company-filter',
    matchFn: (c, q) => c.name.toLowerCase().includes(q),
    render: renderCompaniesTable,
    dateFilter: { fromId: 'company-date-from', toId: 'company-date-to', field: 'created_at' },
  },
};

function applyFilterAndSort(tableName) {
  const config = tableConfig[tableName];
  const q = document.getElementById(config.filterInputId).value.trim().toLowerCase();
  let list = q ? config.cache().filter(item => config.matchFn(item, q)) : config.cache().slice();
  const filterKey = [q]; // every filter value; a change clears the row selection

  if (config.extraFilters) {
    for (const ef of config.extraFilters) {
      const val = document.getElementById(ef.selectId).value;
      filterKey.push(val);
      if (val) list = list.filter(item => ef.matchFn(item, val));
    }
  }

  if (config.dateFilter) {
    const fromVal = document.getElementById(config.dateFilter.fromId).value;
    const toVal = document.getElementById(config.dateFilter.toId).value;
    filterKey.push(fromVal, toVal);
    if (fromVal) {
      const fromTime = new Date(fromVal).getTime();
      list = list.filter(item => new Date(item[config.dateFilter.field]).getTime() >= fromTime);
    }
    if (toVal) {
      const toTime = new Date(toVal).getTime() + 24 * 60 * 60 * 1000 - 1; // include the whole "to" day
      list = list.filter(item => new Date(item[config.dateFilter.field]).getTime() <= toTime);
    }
  }

  const state = sortState[tableName];
  if (state.column && state.dir) {
    const getValue = sortAccessors[tableName][state.column];
    list.sort((a, b) => {
      const va = getValue(a), vb = getValue(b);
      if (va < vb) return state.dir === 'asc' ? -1 : 1;
      if (va > vb) return state.dir === 'asc' ? 1 : -1;
      return 0;
    });
  }
  const sel = selection[tableName];
  if (sel) {
    // New filters or a reloaded list (new cache array): drop the selection so a
    // bulk action never touches rows that are no longer shown. Sorting keeps it.
    const key = JSON.stringify(filterKey);
    if (key !== sel.key || config.cache() !== sel.cache) sel.ids.clear();
    sel.key = key;
    sel.cache = config.cache();
    sel.shown = list.map(item => item.id);
  }
  config.render(list);
  if (sel) syncSelection(tableName);
  if (sel && currentUserIsSuperuser) refreshGroupSwitcher(); // option counts follow the open list
}

// ---------- moving records between groups (superuser, loop iteration 20) ----------
// Each of the four lists gets a Group column (checkbox + label + "Move…"),
// a group filter and a "Move selected…" button. The server decides what has
// to move together (a document with its customer/project, a customer with
// its documents, a bundle with its sub-items); the modal shows that via a
// dry run before anything changes.
const MOVE_LINK_LABELS = {
  documents: 'Also move its customer and project',
  customers: 'Also move its documents',
  projects: 'Also move its documents',
  products: 'Also move linked products (bundle / sub-items)',
};
const MOVE_RELOAD = { documents: () => loadDocuments(), customers: () => loadCustomers(), projects: () => loadProjects(), products: () => loadProducts() };
let moveState = null; // {table, ids}

function groupCell(table, row) {
  if (!currentUserIsSuperuser) return `<td>${groupTag(row.group_id)}</td>`;
  return `<td style="white-space:nowrap;">${groupTag(row.group_id)}
    <button class="link-btn-inline" data-move-table="${table}" data-move-id="${row.id}">Move…</button></td>`;
}

Object.keys(MOVE_LINK_LABELS).forEach(table => {
  const showing = document.createElement('span');
  showing.id = table + '-group-showing';
  showing.className = 'group-tag';
  showing.style.cssText = 'display:none; font-size:12px; padding:2px 8px; border-radius:8px; background:var(--rust); color:#fff; margin-top:6px; white-space:nowrap;';
  document.querySelector(`#view-${table} .view-header > div`).appendChild(showing);
  const bulk = document.createElement('button');
  bulk.type = 'button';
  bulk.className = 'btn-secondary';
  bulk.id = table + '-move-selected-btn';
  bulk.textContent = 'Move selected to group…';
  bulk.style.display = 'none';
  bulk.addEventListener('click', () => {
    const ids = [...selection[table].ids];
    if (!ids.length) { showErrorCross('Select rows first'); return; }
    openMoveModal(table, ids);
  });
  const reset = document.getElementById(table + '-reset-filters-btn');
  reset.parentNode.insertBefore(bulk, reset.nextSibling);
  // The global sidebar switcher (iteration 29); Reset filters leaves it alone.
  tableConfig[table].extraFilters = (tableConfig[table].extraFilters || []).concat({
    selectId: 'group-switcher',
    matchFn: (item, val) => (val === 'none' ? !item.group_id : item.group_id === val),
  });
  document.getElementById(table + '-tbody').addEventListener('click', e => {
    const btn = e.target.closest('[data-move-id]');
    if (btn) openMoveModal(table, [btn.dataset.moveId]);
  });
});

// ---------- select all (loop iteration 24) ----------
// Every user gets a checkbox column on the four lists. The selection is a Set
// of ids per list (not read from the DOM) so it survives re-rendering and
// sorting; applyFilterAndSort clears it when filters change or the list
// reloads. "Select all" picks exactly the rows currently shown (the lists are
// not paginated). Bulk buttons (Move selected…, iteration 25's Add to cart)
// live in the selection bar and read selection[table].ids.
const selection = {}; // table -> {ids: Set, shown: [id], key, cache, bar, count}

function selectCell(table, row) {
  const checked = selection[table].ids.has(row.id) ? ' checked' : '';
  return `<td><input type="checkbox" class="row-select" value="${row.id}" aria-label="Select row"${checked} style="width:auto; margin:0;"></td>`;
}

function syncSelection(table) {
  const s = selection[table];
  const n = s.ids.size; // always a subset of s.shown (cleared on every filter change)
  const all = document.getElementById(table + '-select-all');
  all.disabled = !s.shown.length;
  all.checked = n > 0 && n === s.shown.length;
  all.indeterminate = n > 0 && n < s.shown.length;
  s.bar.style.display = n ? 'flex' : 'none';
  s.count.textContent = `${n} selected`;
  if (s.cartBtn) s.cartBtn.textContent = `Add to cart (${n})`;
}

Object.keys(MOVE_LINK_LABELS).forEach(table => {
  const tbody = document.getElementById(table + '-tbody');
  const all = document.getElementById(table + '-select-all');
  const bar = document.createElement('div');
  bar.id = table + '-selection-bar';
  bar.setAttribute('aria-live', 'polite');
  bar.style.cssText = 'display:none; position:sticky; top:0; z-index:2; gap:10px; align-items:center; padding:8px 10px; margin-bottom:8px; background:var(--paper-dim); border:1px solid var(--hairline); border-radius:6px;';
  bar.innerHTML = `<strong id="${table}-selection-count">0 selected</strong><button type="button" class="btn-secondary">Clear</button>`;
  bar.appendChild(document.getElementById(table + '-move-selected-btn'));
  tbody.closest('table').before(bar);
  const s = selection[table] = { ids: new Set(), shown: [], key: null, cache: null, bar, count: bar.querySelector('strong') };
  const clear = () => { s.ids.clear(); applyFilterAndSort(table); };
  bar.querySelector('button').addEventListener('click', clear);
  document.getElementById(table + '-reset-filters-btn').addEventListener('click', clear);
  // one delegated handler per table, no per-row listeners
  tbody.addEventListener('change', e => {
    if (!e.target.classList.contains('row-select')) return;
    if (e.target.checked) s.ids.add(e.target.value); else s.ids.delete(e.target.value);
    syncSelection(table);
  });
  all.addEventListener('change', () => {
    if (all.checked) s.shown.forEach(id => s.ids.add(id)); else s.ids.clear();
    tbody.querySelectorAll('.row-select').forEach(cb => { cb.checked = all.checked; });
    syncSelection(table);
  });
});

// ---------- add selected products to the cart (loop iteration 25) ----------
// Quantity 1 each; products already in the cart are skipped (unlike the
// single "+ Add to cart", which increments). The selection is always a subset
// of the shown rows, which come from cachedProducts (the list is not paged).
const BULK_CART_MAX = 200;
function addSelectedToCart() {
  const s = selection.products;
  const ids = [...s.ids];
  if (ids.length > BULK_CART_MAX) {
    showErrorCross(`Select up to ${BULK_CART_MAX} at a time; ${ids.length} selected`);
    return;
  }
  const inCart = new Set(cartItems.map(p => p.id));
  const fresh = ids.filter(id => !inCart.has(id)).map(id => cachedProducts.find(p => p.id === id));
  if (fresh.some(p => !p)) { showErrorCross('The product list changed — select again'); return; } // never add fewer silently
  fresh.forEach(p => cartItems.push(Object.assign({}, p, { quantity: 1 })));
  updateCartBadge();
  const skipped = ids.length - fresh.length;
  showSuccessTick(skipped ? `${fresh.length} added, ${skipped} already in cart` : `${fresh.length} added to cart`);
  s.ids.clear();
  applyFilterAndSort('products');
}
(() => {
  const btn = selection.products.cartBtn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn-secondary';
  btn.id = 'products-cart-selected-btn';
  btn.addEventListener('click', addSelectedToCart);
  selection.products.bar.appendChild(btn);
})();

// ---------- set the unit of the selected products (loop iteration 30) ----------
// e.g. filter category "Cable", Select all, Set unit… M. The server applies it
// to all or none (an id outside the user's groups is a 404).
(() => {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'btn-secondary';
  btn.id = 'products-set-unit-btn';
  btn.textContent = 'Set unit…';
  btn.addEventListener('click', () => {
    const n = selection.products.ids.size;
    document.getElementById('set-unit-title').textContent = `Set unit for ${n} product${n === 1 ? '' : 's'}`;
    setUnitControl(document.getElementById('set-unit-select'), 'No');
    showBanner('set-unit-banner', '');
    document.getElementById('set-unit-modal').classList.add('active');
  });
  selection.products.bar.appendChild(btn);
})();
document.getElementById('set-unit-close').addEventListener('click', () => {
  document.getElementById('set-unit-modal').classList.remove('active');
});
document.getElementById('set-unit-submit').addEventListener('click', async () => {
  const unit = readUnitControl(document.getElementById('set-unit-select'));
  if (!unit) { showBanner('set-unit-banner', 'Type the unit.'); return; }
  try {
    const res = await apiFetch('/products/set-unit', { method: 'POST', body: JSON.stringify({ ids: [...selection.products.ids], unit }) });
    document.getElementById('set-unit-modal').classList.remove('active');
    showSuccessTick(`Unit set on ${res.updated} product${res.updated === 1 ? '' : 's'}`);
    selection.products.ids.clear();
    loadProducts();
  } catch (err) {
    showBanner('set-unit-banner', err.message);
  }
});

function refreshMoveControls() {
  const options = myGroups.map(g => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join('');
  refreshGroupSwitcher();
  Object.keys(MOVE_LINK_LABELS).forEach(table => {
    document.getElementById(table + '-move-selected-btn').style.display = currentUserIsSuperuser ? '' : 'none';
  });
  document.getElementById('move-group-target').innerHTML = '<option value="">— choose a group —</option>' + options;
  document.getElementById('unassigned-target').innerHTML = '<option value="">— choose a group —</option>' + options;
}

// ---------- global group switcher (superuser, loop iteration 29) ----------
// One "Viewing" select in the sidebar filters Products, Documents, Customers
// and Projects through their extraFilters (value '' = all, 'none' =
// unassigned). A view filter only — the server scope is unchanged. Kept in
// localStorage as {user, id}, so another user never inherits it; cleared on logout.
const GROUP_VIEW_KEY = 'ledger_group_view';
const groupSwitcher = document.getElementById('group-switcher');
let groupViewOwner = '';

function groupViewName() {
  const v = groupSwitcher.value;
  return !v ? '' : v === 'none' ? 'Unassigned' : (groupNames[v] || 'Other group');
}

// Options show counts for the list that is open (none on other pages).
function refreshGroupSwitcher() {
  const view = (document.querySelector('.nav-item.active') || {}).dataset;
  const rows = view && MOVE_LINK_LABELS[view.view] ? tableConfig[view.view].cache() : null;
  const count = id => rows ? ` (${rows.filter(r => (id === 'none' ? !r.group_id : r.group_id === id)).length})` : '';
  const current = groupSwitcher.value;
  groupSwitcher.innerHTML = `<option value="">All groups${rows ? ` (${rows.length})` : ''}</option>` +
    myGroups.map(g => `<option value="${g.id}">${escapeHtml(g.name)}${count(g.id)}</option>`).join('') +
    `<option value="none">Unassigned${count('none')}</option>`;
  groupSwitcher.value = [...groupSwitcher.options].some(o => o.value === current) ? current : '';
}

function applyGroupView() {
  const name = groupViewName();
  Object.keys(MOVE_LINK_LABELS).forEach(table => {
    applyFilterAndSort(table); // the filter key changed, so this also clears the selection
    const label = document.getElementById(table + '-group-showing');
    label.textContent = `Showing: ${name}`;
    label.style.display = name ? 'inline-block' : 'none';
  });
  refreshDeleteAllLabels();
}

function restoreGroupView(me) {
  groupViewOwner = me.username;
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(GROUP_VIEW_KEY) || 'null'); } catch (e) {}
  groupSwitcher.value = '';
  if (me.is_superuser && saved && saved.user === me.username) groupSwitcher.value = saved.id;
  if (groupSwitcher.selectedIndex < 0) groupSwitcher.value = ''; // that group is gone
  document.getElementById('group-switcher-wrap').style.display = me.is_superuser ? '' : 'none';
  applyGroupView();
}

groupSwitcher.addEventListener('change', () => {
  try { localStorage.setItem(GROUP_VIEW_KEY, JSON.stringify({ user: groupViewOwner, id: groupSwitcher.value })); } catch (e) {}
  applyGroupView();
});
document.querySelectorAll('.nav-item').forEach(item => item.addEventListener('click', refreshGroupSwitcher));

function describeMoved(moved) {
  const parts = Object.entries(moved).filter(([, n]) => n).map(([e, n]) => `${n} ${n === 1 ? e.slice(0, -1) : e}`);
  return parts.join(', ') || 'nothing (already in that group)';
}

function openMoveModal(table, ids) {
  moveState = { table, ids };
  document.getElementById('move-group-title').textContent = `Move ${ids.length} ${ids.length === 1 ? table.slice(0, -1) : table} to group`;
  document.getElementById('move-group-links-label').textContent = MOVE_LINK_LABELS[table];
  document.getElementById('move-group-links').checked = true;
  document.getElementById('move-group-target').value = '';
  document.getElementById('move-group-preview').textContent = 'Choose a group.';
  showBanner('move-group-banner', '');
  document.getElementById('move-group-modal').classList.add('active');
}

async function postMove(dryRun) {
  const { table, ids } = moveState;
  const body = {
    group_id: document.getElementById('move-group-target').value,
    with_links: document.getElementById('move-group-links').checked,
    dry_run: dryRun,
  };
  if (ids.length === 1) return apiFetch(`/${table}/${ids[0]}/move`, { method: 'POST', body: JSON.stringify(body) });
  return apiFetch(`/${table}/move`, { method: 'POST', body: JSON.stringify({ ...body, ids }) });
}

async function previewMove() {
  const preview = document.getElementById('move-group-preview');
  showBanner('move-group-banner', '');
  if (!document.getElementById('move-group-target').value) { preview.textContent = 'Choose a group.'; return; }
  preview.textContent = 'Checking…';
  try {
    preview.textContent = 'Will move: ' + describeMoved((await postMove(true)).moved);
  } catch (err) {
    preview.textContent = '';
    showBanner('move-group-banner', err.message);
  }
}
document.getElementById('move-group-target').addEventListener('change', previewMove);
document.getElementById('move-group-links').addEventListener('change', previewMove);
document.getElementById('move-group-close').addEventListener('click', () => {
  document.getElementById('move-group-modal').classList.remove('active');
});
document.getElementById('move-group-submit').addEventListener('click', async () => {
  if (!document.getElementById('move-group-target').value) { showBanner('move-group-banner', 'Choose a group.'); return; }
  try {
    const result = await postMove(false);
    document.getElementById('move-group-modal').classList.remove('active');
    showSuccessTick('Moved ' + describeMoved(result.moved));
    // a move can touch the other lists too (a document's customer/project)
    Object.values(MOVE_RELOAD).forEach(reload => reload());
  } catch (err) {
    showBanner('move-group-banner', err.message);
    showErrorCross(err.message);
  }
});

async function loadUnassignedCard() {
  const el = document.getElementById('unassigned-counts');
  try {
    const [docs, prods] = await Promise.all([apiFetch('/documents/unassigned-counts'), apiFetch('/products/unassigned-counts')]);
    el.textContent = `Documents ${docs.documents} · Products ${prods.products} · Customers ${docs.customers} · Projects ${docs.projects}`;
  } catch (err) {
    el.textContent = err.message;
  }
}
document.getElementById('unassigned-assign-btn').addEventListener('click', async () => {
  const select = document.getElementById('unassigned-target');
  if (!select.value) { showErrorCross('Choose a group'); return; }
  const name = select.options[select.selectedIndex].textContent;
  if (!confirm(`Give every unassigned document, product, customer and project to "${name}"?`)) return;
  try {
    const body = JSON.stringify({ group_id: select.value });
    await apiFetch('/documents/assign-unassigned', { method: 'POST', body });
    await apiFetch('/products/assign-unassigned', { method: 'POST', body });
    showSuccessTick('Assigned to ' + name);
    loadUnassignedCard();
    Object.values(MOVE_RELOAD).forEach(reload => reload());
  } catch (err) {
    showErrorCross(err.message);
  }
});

document.querySelectorAll('.sort-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const tableName = btn.dataset.sortTable;
    const column = btn.dataset.sortCol;
    const state = sortState[tableName];

    if (state.column !== column) {
      state.column = column;
      state.dir = 'asc';
    } else if (state.dir === 'asc') {
      state.dir = 'desc';
    } else {
      state.column = null;
      state.dir = null;
    }

    // Reset all sort button icons/active state for this table, then mark the current one
    document.querySelectorAll(`.sort-btn[data-sort-table="${tableName}"]`).forEach(b => {
      b.textContent = '⇅';
      b.classList.remove('active');
    });
    if (state.column) {
      btn.textContent = state.dir === 'asc' ? '▲' : '▼';
      btn.classList.add('active');
    }

    applyFilterAndSort(tableName);
  });
});

// ---------- search ----------
async function runSearch() {
  const q = document.getElementById('search-input').value.trim();
  const projectId = document.getElementById('search-project-filter').value;
  const resultsEl = document.getElementById('search-results');
  showBanner('search-banner', '');
  if (!q) { resultsEl.innerHTML = ''; return; }
  try {
    let url = '/search?q=' + encodeURIComponent(q);
    if (projectId) url += '&project_id=' + encodeURIComponent(projectId);
    const data = await apiFetch(url);
    let html = '';
    if (data.documents && data.documents.length) {
      html += '<div class="search-section-title">Documents</div><table><tbody>';
      html += data.documents.map(d => `
        <tr>
          <td class="mono">${escapeHtml(d.doc_number)}</td>
          <td><span class="${stampClass(d.doc_type)}">${escapeHtml(d.doc_type)}</span></td>
          <td>${d.project_name
            ? `<button class="link-btn-inline" data-goto-project-id="${d.project_id}">${escapeHtml(d.project_name)}</button>` +
              (d.project_budget_year ? ` <span class="mono" style="color:var(--text-dim); font-size:12px;">(${escapeHtml(d.project_budget_year)})</span>` : '')
            : '<span style="color:var(--text-dim);">No project</span>'}</td>
          <td class="mono">${fmtMoney(d.total)}</td>
        </tr>`).join('');
      html += '</tbody></table>';
    }
    if (data.products && data.products.length) {
      html += '<div class="search-section-title">Products</div><table><tbody>';
      html += data.products.map(p => `
        <tr>
          <td class="mono">${escapeHtml(p.sku)}</td>
          <td>${escapeHtml(p.name)}</td>
          <td class="mono">${fmtMoney(p.unit_price)}</td>
        </tr>`).join('');
      html += '</tbody></table>';
    }
    if (!html) html = '<p style="color:var(--text-dim);">No results. Try a different term.</p>';
    resultsEl.innerHTML = html;
    resultsEl.querySelectorAll('[data-goto-project-id]').forEach(btn => {
      btn.addEventListener('click', () => gotoProject(btn.dataset.gotoProjectId));
    });
  } catch (err) {
    showBanner('search-banner', err.message);
  }
}
document.getElementById('search-btn').addEventListener('click', runSearch);
document.getElementById('search-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') runSearch();
});

// ---------- admin panel ----------
let cachedAllUsers = [];
let editingUserId = null;

async function loadAllUsers() {
  const tbody = document.getElementById('all-users-tbody');
  try {
    const users = await apiFetch('/admin/users');
    cachedAllUsers = users;
    renderAllUsersTable(users);
  } catch (err) {
    // 403 here just means this account can't manage users — card is hidden anyway in that case
    tbody.innerHTML = `<tr class="empty-row"><td colspan="5">${escapeHtml(err.message)}</td></tr>`;
  }
}

function renderAllUsersTable(users) {
  const tbody = document.getElementById('all-users-tbody');
  if (!users.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="5">No users match.</td></tr>';
    return;
  }
  // Users are never removable (only deactivated) — see Task 5 of the User
  // Control feature: there is deliberately no delete/remove action here.
  tbody.innerHTML = users.map(u => `
    <tr>
      <td>${escapeHtml(u.username)}</td>
      <td>${u.groups && u.groups.length ? escapeHtml(u.groups.join(', ')) : '—'}</td>
      <td>${u.is_superuser ? '<span class="stamp stamp-admin">Admin</span>' : (u.roles && u.roles.length ? escapeHtml(u.roles.join(', ')) : '<span style="color:var(--text-dim);">— no role —</span>')}</td>
      <td>
        ${u.is_superuser && !currentUserIsSuperuser
          ? '<span class="stamp stamp-paid" style="font-size:10px;">active</span>'
          : (!u.is_approved
              ? '<span style="color:var(--danger);">Pending</span>'
              : `<button class="status-toggle-btn ${u.is_active ? 'is-active' : 'is-disabled'}" data-toggle-user-id="${u.id}" data-toggle-active="${u.is_active}">${u.is_active ? 'Active' : 'Disabled'}</button>`)}
      </td>
      <td>${u.is_superuser && !currentUserIsSuperuser ? '<span style="color:var(--text-dim);">—</span>' : `<button class="link-btn-inline" data-edit-user-id="${u.id}">Manage</button>
        &nbsp;|&nbsp; <button class="link-btn-inline" data-user-sessions-id="${u.id}" data-user-sessions-name="${escapeHtml(u.username)}">Sessions</button>`}</td>
    </tr>
  `).join('');
  tbody.querySelectorAll('[data-edit-user-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const user = cachedAllUsers.find(u => u.id === btn.dataset.editUserId);
      if (user) openEditUserModal(user);
    });
  });
  tbody.querySelectorAll('[data-user-sessions-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.userSessionsId;
      openSessionsModal({
        title: 'Sessions — ' + btn.dataset.userSessionsName,
        listPath: `/admin/users/${id}/sessions`,
        revokePath: sid => `/admin/users/${id}/sessions/${sid}/revoke`,
        logoutAllPath: `/admin/users/${id}/logout-all`,
        self: false,
      });
    });
  });
  tbody.querySelectorAll('[data-toggle-user-id]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const currentlyActive = btn.dataset.toggleActive === 'true';
      showBanner('admin-banner', '');
      try {
        await apiFetch('/admin/users/' + btn.dataset.toggleUserId + '/active', {
          method: 'PATCH',
          body: JSON.stringify({ is_active: !currentlyActive })
        });
        loadAllUsers();
      } catch (err) {
        showBanner('admin-banner', err.message);
      }
    });
  });
}

document.getElementById('all-users-filter').addEventListener('input', (e) => {
  const q = e.target.value.trim().toLowerCase();
  const filtered = q ? cachedAllUsers.filter(u => u.username.toLowerCase().includes(q)) : cachedAllUsers;
  renderAllUsersTable(filtered);
});

function updateEditUserToggleBtn(isActive) {
  const btn = document.getElementById('edit-user-toggle-active-btn');
  btn.textContent = isActive ? 'Deactivate account' : 'Activate account';
  btn.dataset.active = isActive;
}

document.getElementById('edit-user-toggle-active-btn').addEventListener('click', async () => {
  const currentlyActive = document.getElementById('edit-user-toggle-active-btn').dataset.active === 'true';
  showBanner('edit-user-banner', '');
  try {
    await apiFetch('/admin/users/' + editingUserId + '/active', {
      method: 'PATCH',
      body: JSON.stringify({ is_active: !currentlyActive })
    });
    updateEditUserToggleBtn(!currentlyActive);
    loadAllUsers();
  } catch (err) {
    showBanner('edit-user-banner', err.message);
  }
});

async function openEditUserModal(user) {
  editingUserId = user.id;
  document.getElementById('edit-user-title').textContent = 'Manage user — ' + user.username;
  document.getElementById('edit-user-username').value = user.username;
  document.getElementById('edit-user-password').value = '';
  showBanner('edit-user-banner', '');
  document.getElementById('edit-user-modal').classList.add('active');

  // An admin isn't in a group and doesn't carry roles/project grants — see
  // CreateUserInGroupRequest's docstring (never accepts is_superuser) and
  // get_user_allowed_project_ids (superuser always returns "ALL" regardless
  // of any grant). Nothing in those sections applies, so skip loading and
  // showing them entirely rather than a confusing empty state.
  const isAdmin = !!user.is_superuser;
  document.getElementById('edit-user-group-sections').style.display = isAdmin ? 'none' : 'block';
  document.getElementById('edit-user-admin-only-row').style.display = isAdmin ? 'block' : 'none';
  if (isAdmin) {
    updateEditUserToggleBtn(user.is_active);
    return;
  }
  await loadEditUserGroups();
  await loadGroupsForAddPicker();
  await loadEditUserRoles();
  await loadManageableRolesForPicker();
  await loadEditUserProjectAccess();
}

async function loadEditUserGroups() {
  const container = document.getElementById('edit-user-groups-list');
  container.innerHTML = '<p style="color:var(--text-dim); font-size:13px;">Loading…</p>';
  try {
    const groups = await apiFetch('/admin/users/' + editingUserId + '/groups');
    if (!groups.length) {
      container.innerHTML = '<p style="color:var(--text-dim); font-size:13px;">Not a member of any group yet.</p>';
      return;
    }
    container.innerHTML = groups.map(g => `
      <span class="user-chip">${escapeHtml(g.group_name)}${g.is_group_admin ? ' <span style="color:var(--text-dim);">(admin)</span>' : ''}
        <button data-remove-group-id="${g.group_id}">✕</button>
      </span>
    `).join('');
    container.querySelectorAll('[data-remove-group-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const user = cachedAllUsers.find(u => u.id === editingUserId);
        if (!user) return;
        try {
          await apiFetch('/admin/groups/' + btn.dataset.removeGroupId + '/members/' + encodeURIComponent(user.username), { method: 'DELETE' });
          loadEditUserGroups();
          loadEditUserRoles(); // removing a group also drops any roles held via it — refresh both
          loadEditUserProjectAccess(); // the grantable projects follow the user's groups
        } catch (err) {
          showBanner('edit-user-banner', err.message);
        }
      });
    });
  } catch (err) {
    container.innerHTML = `<p style="color:var(--danger); font-size:13px;">${escapeHtml(err.message)}</p>`;
  }
}

async function loadGroupsForAddPicker() {
  const select = document.getElementById('edit-user-add-group');
  select.innerHTML = '<option value="">— choose a group —</option>';
  try {
    const groups = await apiFetch('/admin/groups');
    groups.forEach(g => {
      const opt = document.createElement('option');
      opt.value = g.id;
      opt.textContent = g.name;
      select.appendChild(opt);
    });
  } catch (err) {
    // non-fatal — user can still manage roles/password without a group picker
  }
}

document.getElementById('edit-user-add-group-btn').addEventListener('click', async () => {
  const groupId = document.getElementById('edit-user-add-group').value;
  const user = cachedAllUsers.find(u => u.id === editingUserId);
  if (!groupId || !user) return;
  showBanner('edit-user-banner', '');
  try {
    await apiFetch('/admin/groups/' + groupId + '/members', { method: 'POST', body: JSON.stringify({ username: user.username }) });
    loadEditUserGroups();
    loadEditUserProjectAccess();
  } catch (err) {
    showBanner('edit-user-banner', err.message);
  }
});

async function loadEditUserProjectAccess() {
  const container = document.getElementById('edit-user-project-checkboxes');
  container.innerHTML = '<span style="color:var(--text-dim); font-size:13px;">Loading…</span>';
  try {
    // Server decides the pool: projects of the user's own groups (iteration 26).
    const available = await apiFetch('/admin/users/' + editingUserId + '/available-projects');
    const availableProjects = cachedProjects.filter(p => available.project_ids.includes(p.id));
    const currentGrants = await apiFetch('/admin/users/' + editingUserId + '/project-access');
    // Old grants outside the pool still narrow what the user sees — list them so they can be removed.
    const strayGrants = currentGrants.filter(id => !available.project_ids.includes(id));

    if (!availableProjects.length && !strayGrants.length) {
      container.innerHTML = currentUserIsSuperuser
        ? '<span style="color:var(--text-dim); font-size:13px;">No projects in this user\'s groups yet.</span>'
        : '<span style="color:var(--text-dim); font-size:13px;">No shared group has project access yet — ask a superuser to grant some to your group first.</span>';
      return;
    }

    container.innerHTML = availableProjects.map(p => `
      <label>
        <input type="checkbox" class="edit-user-project-cb" data-project-id="${p.id}" ${currentGrants.includes(p.id) ? 'checked' : ''}>
        ${escapeHtml(p.name)}${p.budget_year ? ' (' + escapeHtml(p.budget_year) + ')' : ''}${groupTag(p.group_id)}
      </label>
    `).join('') + strayGrants.map(id => {
      const p = cachedProjects.find(x => x.id === id);
      return `
      <label style="color:var(--danger);">
        <input type="checkbox" class="edit-user-project-cb" data-project-id="${id}" checked>
        ${p ? escapeHtml(p.name) + groupTag(p.group_id) : 'Deleted or hidden project'} — not in this user's groups; untick to remove
      </label>`;
    }).join('');

    container.querySelectorAll('.edit-user-project-cb').forEach(cb => {
      cb.addEventListener('change', async () => {
        showBanner('edit-user-banner', '');
        try {
          if (cb.checked) {
            await apiFetch('/admin/users/' + editingUserId + '/project-access', {
              method: 'POST', body: JSON.stringify({ project_id: cb.dataset.projectId })
            });
          } else {
            await apiFetch('/admin/users/' + editingUserId + '/project-access/' + cb.dataset.projectId, { method: 'DELETE' });
          }
          flashSaved(cb.closest('label'));
        } catch (err) {
          showBanner('edit-user-banner', err.message);
          loadEditUserProjectAccess();
        }
      });
    });
  } catch (err) {
    container.innerHTML = `<span style="color:var(--danger); font-size:13px;">${escapeHtml(err.message)}</span>`;
  }
}

document.getElementById('edit-user-close').addEventListener('click', () => {
  document.getElementById('edit-user-modal').classList.remove('active');
});

// ---------- create user (Task 3 — Admin -> All Users -> Create User) ----------

const CREATE_USER_ADMIN_SENTINEL = '__admin__';

async function loadCreateUserRolePicker() {
  const groupId = document.getElementById('create-user-group').value;
  const roleSelect = document.getElementById('create-user-role');
  roleSelect.innerHTML = '<option value="">— no role yet —</option>';
  if (!groupId) return;
  try {
    const roles = await apiFetch('/admin/groups/' + groupId + '/roles');
    roles.forEach(r => {
      const opt = document.createElement('option');
      opt.value = r.id;
      opt.textContent = r.name;
      roleSelect.appendChild(opt);
    });
  } catch (err) {
    // non-fatal — admin can still create the user without picking a role yet
  }
}

function updateCreateUserGroupVisibility() {
  const isAdmin = document.getElementById('create-user-group').value === CREATE_USER_ADMIN_SENTINEL;
  document.getElementById('create-user-role-wrap').style.display = isAdmin ? 'none' : 'block';
  document.getElementById('create-user-admin-hint').style.display = isAdmin ? 'block' : 'none';
  if (!isAdmin) loadCreateUserRolePicker();
}
document.getElementById('create-user-group').addEventListener('change', updateCreateUserGroupVisibility);

document.getElementById('open-create-user-btn').addEventListener('click', async () => {
  showBanner('create-user-banner', '');
  document.getElementById('create-user-username').value = '';
  document.getElementById('create-user-password').value = '';
  const groupSelect = document.getElementById('create-user-group');
  groupSelect.innerHTML = '';
  try {
    const groups = await apiFetch('/admin/groups');
    groups.forEach(g => {
      const opt = document.createElement('option');
      opt.value = g.id;
      opt.textContent = g.name;
      groupSelect.appendChild(opt);
    });
    // Only a superuser can create another admin — a group admin managing
    // their own group never sees this option, matching POST /auth/users
    // being superuser-only server-side (see admin.py's CreateUserInGroupRequest,
    // which has no is_superuser field at all).
    if (currentUserIsSuperuser) {
      const adminOpt = document.createElement('option');
      adminOpt.value = CREATE_USER_ADMIN_SENTINEL;
      adminOpt.textContent = 'Admin';
      groupSelect.appendChild(adminOpt);
    }
    // Default to the Operation group — that's where normal operational users belong.
    const operation = groups.find(g => g.name === 'Operation');
    if (operation) groupSelect.value = operation.id;
  } catch (err) {
    showBanner('create-user-banner', err.message);
  }
  updateCreateUserGroupVisibility();
  document.getElementById('create-user-modal').classList.add('active');
});

document.getElementById('create-user-close').addEventListener('click', () => {
  document.getElementById('create-user-modal').classList.remove('active');
});

document.getElementById('create-user-submit-btn').addEventListener('click', async () => {
  const username = document.getElementById('create-user-username').value.trim();
  const password = document.getElementById('create-user-password').value;
  const groupValue = document.getElementById('create-user-group').value;
  const isAdmin = groupValue === CREATE_USER_ADMIN_SENTINEL;
  showBanner('create-user-banner', '');
  if (!username || !password) {
    showBanner('create-user-banner', 'Username and password are required.');
    return;
  }
  try {
    document.getElementById('create-user-submit-btn').disabled = true;
    if (isAdmin) {
      // Superuser-only endpoint — a bare account with no group/role, since
      // an admin bypasses group scoping entirely (see services/auth-service/
      // app/routers/auth.py's create_user).
      await apiFetch('/auth/users', { method: 'POST', body: JSON.stringify({ username, password, is_superuser: true }) });
    } else {
      const roleId = document.getElementById('create-user-role').value;
      if (!groupValue) {
        showBanner('create-user-banner', 'Group is required for a group member.');
        return;
      }
      const payload = { username, password };
      if (roleId) payload.role_id = roleId;
      await apiFetch('/admin/groups/' + groupValue + '/users', { method: 'POST', body: JSON.stringify(payload) });
    }
    document.getElementById('create-user-modal').classList.remove('active');
    loadAllUsers();
    showSuccessTick('User created');
  } catch (err) {
    showBanner('create-user-banner', err.message);
    showErrorCross(err.message);
  } finally {
    document.getElementById('create-user-submit-btn').disabled = false;
  }
});

document.getElementById('edit-user-save-btn').addEventListener('click', async () => {
  const username = document.getElementById('edit-user-username').value.trim();
  const password = document.getElementById('edit-user-password').value;
  showBanner('edit-user-banner', '');
  try {
    const payload = { username };
    if (password) payload.password = password;
    await apiFetch('/admin/users/' + editingUserId, { method: 'PATCH', body: JSON.stringify(payload) });
    document.getElementById('edit-user-password').value = '';
    loadAllUsers();
    showBanner('edit-user-banner', 'Saved.', 'info');
    showSuccessTick('User saved');
  } catch (err) {
    showBanner('edit-user-banner', err.message);
    showErrorCross(err.message);
  }
});

async function loadEditUserRoles() {
  const container = document.getElementById('edit-user-roles-list');
  container.innerHTML = '<p style="color:var(--text-dim); font-size:13px;">Loading…</p>';
  try {
    const roles = await apiFetch('/admin/users/' + editingUserId + '/roles');
    if (!roles.length) {
      container.innerHTML = '<p style="color:var(--text-dim); font-size:13px;">No roles assigned yet.</p>';
      return;
    }
    container.innerHTML = roles.map(r => `
      <span class="user-chip">${escapeHtml(r.role_name)} <span style="color:var(--text-dim);">(${escapeHtml(r.group_name)})</span>
        <button data-remove-role-id="${r.role_id}">✕</button>
      </span>
    `).join('');
    container.querySelectorAll('[data-remove-role-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const user = cachedAllUsers.find(u => u.id === editingUserId);
        if (!user) return;
        try {
          await apiFetch('/admin/roles/' + btn.dataset.removeRoleId + '/assign/' + encodeURIComponent(user.username), { method: 'DELETE' });
          loadEditUserRoles();
        } catch (err) {
          showBanner('edit-user-banner', err.message);
        }
      });
    });
  } catch (err) {
    container.innerHTML = `<p style="color:var(--danger); font-size:13px;">${escapeHtml(err.message)}</p>`;
  }
}

async function loadManageableRolesForPicker() {
  const select = document.getElementById('edit-user-add-role');
  select.innerHTML = '<option value="">— choose a role —</option>';
  try {
    const groups = await apiFetch('/admin/groups');
    for (const group of groups) {
      const roles = await apiFetch('/admin/groups/' + group.id + '/roles');
      roles.forEach(r => {
        const opt = document.createElement('option');
        opt.value = r.id;
        opt.textContent = r.name + ' (' + group.name + ')';
        select.appendChild(opt);
      });
    }
  } catch (err) {
    // non-fatal — user can still edit username/password without an add-role picker
  }
}

document.getElementById('edit-user-add-role-btn').addEventListener('click', async () => {
  const roleId = document.getElementById('edit-user-add-role').value;
  const user = cachedAllUsers.find(u => u.id === editingUserId);
  if (!roleId || !user) return;
  showBanner('edit-user-banner', '');
  try {
    await apiFetch('/admin/roles/' + roleId + '/assign', { method: 'POST', body: JSON.stringify({ username: user.username }) });
    loadEditUserRoles();
  } catch (err) {
    showBanner('edit-user-banner', err.message);
  }
});

let cachedGroups = [];
let selectedGroupId = null;

async function loadPendingUsers() {
  const tbody = document.getElementById('pending-users-tbody');
  try {
    const pending = await apiFetch('/admin/users/pending');
    if (!pending.length) {
      tbody.innerHTML = '<tr class="empty-row"><td colspan="4">No pending registrations.</td></tr>';
      return;
    }
    tbody.innerHTML = pending.map(u => {
      const group = cachedGroups.find(g => g.id === u.requested_group_id);
      return `
      <tr>
        <td>${escapeHtml(u.username)}</td>
        <td>${group ? escapeHtml(group.name) : '<span style="color:var(--text-dim);">— none specified —</span>'}</td>
        <td>${fmtDate(u.created_at)}</td>
        <td>
          <button class="btn-secondary" data-approve-id="${u.id}" style="padding:6px 12px; font-size:13px;">Approve</button>
          <button class="btn-secondary" data-deny-id="${u.id}" data-deny-username="${escapeHtml(u.username)}" style="padding:6px 12px; font-size:13px; color:var(--danger); border-color:var(--danger);">Deny</button>
        </td>
      </tr>
    `;
    }).join('');
    tbody.querySelectorAll('[data-approve-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          await apiFetch('/admin/users/' + btn.dataset.approveId + '/approve', { method: 'POST' });
          loadPendingUsers();
        } catch (err) {
          showBanner('admin-banner', err.message);
        }
      });
    });
    tbody.querySelectorAll('[data-deny-id]').forEach(btn => {
      btn.addEventListener('click', async () => {
        if (!confirm(`Deny and delete the registration request from "${btn.dataset.denyUsername}"? This can't be undone.`)) return;
        try {
          await apiFetch('/admin/users/' + btn.dataset.denyId + '/deny', { method: 'POST' });
          loadPendingUsers();
        } catch (err) {
          showBanner('admin-banner', err.message);
        }
      });
    });
  } catch (err) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="4">${escapeHtml(err.message)}</td></tr>`;
  }
}

document.getElementById('create-group-btn').addEventListener('click', async () => {
  const name = document.getElementById('new-group-name').value.trim();
  if (!name) return;
  showBanner('admin-banner', '');
  try {
    await apiFetch('/admin/groups', { method: 'POST', body: JSON.stringify({ name }) });
    document.getElementById('new-group-name').value = '';
    loadAdminGroups();
    showSuccessTick('Group created');
  } catch (err) {
    showBanner('admin-banner', err.message);
    showErrorCross(err.message);
  }
});

async function loadAdminGroups(restrictToGroupIds) {
  try {
    let groups = await apiFetch('/admin/groups');
    if (restrictToGroupIds) {
      groups = groups.filter(g => restrictToGroupIds.includes(g.id));
    }
    cachedGroups = groups;
    const select = document.getElementById('group-select');
    const currentValue = select.value;
    select.innerHTML = '<option value="">— choose a group —</option>' +
      cachedGroups.map(g => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join('');
    if (currentValue) {
      select.value = currentValue;
    } else if (cachedGroups.length === 1) {
      // Only one group to administer — no need to make them pick it manually.
      select.value = cachedGroups[0].id;
      select.dispatchEvent(new Event('change'));
    }
  } catch (err) {
    showBanner('admin-banner', err.message);
  }
}

document.getElementById('group-select').addEventListener('change', (e) => {
  selectedGroupId = e.target.value || null;
  const panel = document.getElementById('group-detail-panel');
  const controls = document.getElementById('group-manage-controls');
  if (!selectedGroupId) {
    panel.style.display = 'none';
    controls.style.display = 'none';
    return;
  }
  const group = cachedGroups.find(g => g.id === selectedGroupId);
  document.getElementById('group-detail-name-2').textContent = group ? group.name : '';
  panel.style.display = 'block';
  refreshGroupManageControls(group);
  loadGroupRoles();
});

function refreshGroupManageControls(group) {
  if (!group) return;
  const controls = document.getElementById('group-manage-controls');
  controls.style.display = 'block';
  document.getElementById('group-status-stamp').innerHTML = group.is_active
    ? '<span class="stamp stamp-paid" style="font-size:10px;">active</span>'
    : '<span class="stamp stamp-void" style="font-size:10px;">disabled</span>';
  document.getElementById('group-toggle-btn').textContent = group.is_active ? 'Disable group' : 'Enable group';
}

document.getElementById('group-toggle-btn').addEventListener('click', async () => {
  const group = cachedGroups.find(g => g.id === selectedGroupId);
  if (!group) return;
  showBanner('admin-banner', '');
  try {
    await apiFetch('/admin/groups/' + selectedGroupId + '/active', {
      method: 'PATCH',
      body: JSON.stringify({ is_active: !group.is_active })
    });
    await loadAdminGroups();
    const updated = cachedGroups.find(g => g.id === selectedGroupId);
    refreshGroupManageControls(updated);
  } catch (err) {
    showBanner('admin-banner', err.message);
  }
});

document.getElementById('group-delete-btn').addEventListener('click', async () => {
  const group = cachedGroups.find(g => g.id === selectedGroupId);
  if (!group) return;
  if (!confirm(`Delete group "${group.name}"? This removes all its members, roles, and access grants. This can't be undone.`)) return;
  showBanner('admin-banner', '');
  try {
    await apiFetch('/admin/groups/' + selectedGroupId, { method: 'DELETE' });
    selectedGroupId = null;
    document.getElementById('group-detail-panel').style.display = 'none';
    document.getElementById('group-manage-controls').style.display = 'none';
    document.getElementById('group-select').value = '';
    loadAdminGroups();
  } catch (err) {
    showBanner('admin-banner', err.message);
  }
});

document.getElementById('create-role-btn').addEventListener('click', async () => {
  const name = document.getElementById('new-role-name').value.trim();
  if (!name || !selectedGroupId) return;
  showBanner('admin-banner', '');
  try {
    await apiFetch('/admin/groups/' + selectedGroupId + '/roles', { method: 'POST', body: JSON.stringify({ name }) });
    document.getElementById('new-role-name').value = '';
    loadGroupRoles();
    showSuccessTick('Role created');
  } catch (err) {
    showBanner('admin-banner', err.message);
    showErrorCross(err.message);
  }
});

const ALL_SERVICES = ['documents', 'products', 'search', 'audit-log', 'categories'];

let cachedGroupRoles = [];
let currentUserIsSuperuser = false;
let currentUserCanDeleteDocuments = false;

async function loadGroupRoles() {
  const tbody = document.getElementById('roles-tbody');
  tbody.innerHTML = skeletonRows(4);
  const mySeq = startLoad('groupRoles');
  const groupId = selectedGroupId;
  try {
    const roles = await apiFetch('/admin/groups/' + groupId + '/roles');
    if (isStaleLoad('groupRoles', mySeq)) return; // a newer group was selected while this was in flight
    cachedGroupRoles = roles;
    renderRolesTable(roles);
  } catch (err) {
    if (isStaleLoad('groupRoles', mySeq)) return;
    tbody.innerHTML = `<tr class="empty-row"><td colspan="4">${escapeHtml(err.message)}</td></tr>`;
  }
}

function renderRolesTable(roles) {
  const tbody = document.getElementById('roles-tbody');
  if (!roles.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="4">No roles match.</td></tr>';
    return;
  }
  tbody.innerHTML = roles.map(r => `
    <tr>
      <td>${escapeHtml(r.name)}</td>
      <td>${r.is_active
        ? '<span class="stamp stamp-paid" style="font-size:10px;">active</span>'
        : '<span class="stamp stamp-void" style="font-size:10px;">disabled</span>'}</td>
      <td class="mono" style="font-size:12px;">${r.services.length ? escapeHtml(r.services.join(', ')) : '—'}</td>
      <td><button class="link-btn-inline" data-edit-role-id="${r.id}">Edit</button></td>
    </tr>
  `).join('');
  tbody.querySelectorAll('[data-edit-role-id]').forEach(btn => {
    btn.addEventListener('click', () => openEditRoleModal(btn.dataset.editRoleId));
  });
}

document.getElementById('roles-filter').addEventListener('input', (e) => {
  const q = e.target.value.trim().toLowerCase();
  const filtered = q ? cachedGroupRoles.filter(r => r.name.toLowerCase().includes(q)) : cachedGroupRoles;
  renderRolesTable(filtered);
});

function flashSaved(el) {
  if (!el) return;
  el.classList.remove('save-flash');
  void el.offsetWidth; // restart the animation if it's already mid-flash
  el.classList.add('save-flash');
  setTimeout(() => el.classList.remove('save-flash'), 800);
}

let editingRoleId = null;

document.getElementById('edit-role-close').addEventListener('click', () => {
  document.getElementById('edit-role-modal').classList.remove('active');
});

async function openEditRoleModal(roleId) {
  editingRoleId = roleId;
  showBanner('edit-role-banner', '');
  document.getElementById('edit-role-modal').classList.add('active');
  await renderEditRoleModal();
}

async function renderEditRoleModal() {
  const roleId = editingRoleId;
  try {
    const detail = await apiFetch('/admin/roles/' + roleId);

    document.getElementById('edit-role-title').textContent = 'Edit role — ' + detail.name;
    document.getElementById('edit-role-name').value = detail.name;
    document.getElementById('edit-role-status-stamp').innerHTML = detail.is_active
      ? '<span class="stamp stamp-paid" style="font-size:10px;">active</span>'
      : '<span class="stamp stamp-void" style="font-size:10px;">disabled</span>';
    document.getElementById('edit-role-toggle-btn').textContent = detail.is_active ? 'Disable role' : 'Enable role';

    const serviceRows = ALL_SERVICES.map(s => `
      <div class="role-permission-row">
        <label>
          <input type="checkbox" class="role-service-cb" data-service="${s}" ${detail.services.includes(s) ? 'checked' : ''}>
          ${s}
        </label>
        ${s === 'documents' ? `
          <div class="role-permission-suboptions">
            <label class="inline-suboption">
              Access level:
              <select class="role-level-select" style="margin:0; padding:2px 4px; font-size:12px; width:auto;" ${detail.services.includes('documents') ? '' : 'disabled'}>
                <option value="edit" ${detail.service_levels && detail.service_levels.documents === 'edit' ? 'selected' : ''}>Edit</option>
                <option value="view" ${detail.service_levels && detail.service_levels.documents === 'view' ? 'selected' : ''}>View only</option>
              </select>
            </label>
            <label class="inline-suboption">
              <input type="checkbox" class="role-delete-cb" ${detail.services.includes('documents-delete') ? 'checked' : ''} ${detail.services.includes('documents') ? '' : 'disabled'}>
              Delete — separate from Edit; a role can edit documents without being able to delete them
            </label>
          </div>
        ` : ''}
      </div>
    `).join('');
    document.getElementById('edit-role-service-checkboxes').innerHTML = serviceRows;

    const userChips = detail.assigned_users.map(u => `
      <span class="user-chip">${escapeHtml(u)} <button data-unassign-user="${escapeHtml(u)}" title="Remove this role from ${escapeHtml(u)}">✕</button></span>
    `).join('') || '<span style="color:var(--text-dim); font-size:13px;">No users have this role yet — assign it when creating/adding a user in the Members section.</span>';
    document.getElementById('edit-role-users-list').innerHTML = userChips;

    document.getElementById('edit-role-service-checkboxes').querySelectorAll('.role-service-cb').forEach(cb => {
      cb.addEventListener('change', async () => {
        showBanner('edit-role-banner', '');
        try {
          if (cb.checked) {
            const levelSelect = document.querySelector('.role-level-select');
            const level = (cb.dataset.service === 'documents' && levelSelect) ? levelSelect.value : 'edit';
            await apiFetch('/admin/roles/' + roleId + '/access', { method: 'POST', body: JSON.stringify({ service_name: cb.dataset.service, access_level: level }) });
            if (cb.dataset.service === 'documents') {
              const levelSelect2 = document.querySelector('.role-level-select');
              const deleteCb = document.querySelector('.role-delete-cb');
              if (levelSelect2) levelSelect2.disabled = false;
              if (deleteCb) deleteCb.disabled = false;
            }
          } else {
            await apiFetch('/admin/roles/' + roleId + '/access/' + cb.dataset.service, { method: 'DELETE' });
            if (cb.dataset.service === 'documents') {
              // Turning off "documents" entirely also revokes the separate
              // Delete permission server-side isn't automatic — Delete is
              // meaningless without any documents access at all, so drop
              // it here too rather than leaving an orphaned grant.
              const deleteCb = document.querySelector('.role-delete-cb');
              if (deleteCb && deleteCb.checked) {
                try { await apiFetch('/admin/roles/' + roleId + '/access/documents-delete', { method: 'DELETE' }); } catch (e) { /* best-effort */ }
              }
              const levelSelect2 = document.querySelector('.role-level-select');
              if (levelSelect2) levelSelect2.disabled = true;
              if (deleteCb) { deleteCb.checked = false; deleteCb.disabled = true; }
            }
          }
          flashSaved(cb.closest('label'));
          loadGroupRoles();
        } catch (err) {
          showBanner('edit-role-banner', err.message);
          renderEditRoleModal();
        }
      });
    });

    document.getElementById('edit-role-service-checkboxes').querySelectorAll('.role-level-select').forEach(sel => {
      sel.addEventListener('change', async () => {
        showBanner('edit-role-banner', '');
        try {
          await apiFetch('/admin/roles/' + roleId + '/access', { method: 'POST', body: JSON.stringify({ service_name: 'documents', access_level: sel.value }) });
          flashSaved(sel);
        } catch (err) {
          showBanner('edit-role-banner', err.message);
          renderEditRoleModal();
        }
      });
    });

    document.getElementById('edit-role-service-checkboxes').querySelectorAll('.role-delete-cb').forEach(cb => {
      cb.addEventListener('change', async () => {
        showBanner('edit-role-banner', '');
        try {
          if (cb.checked) {
            await apiFetch('/admin/roles/' + roleId + '/access', { method: 'POST', body: JSON.stringify({ service_name: 'documents-delete' }) });
          } else {
            await apiFetch('/admin/roles/' + roleId + '/access/documents-delete', { method: 'DELETE' });
          }
          flashSaved(cb.closest('label'));
          loadGroupRoles();
        } catch (err) {
          showBanner('edit-role-banner', err.message);
          renderEditRoleModal();
        }
      });
    });

    document.getElementById('edit-role-users-list').querySelectorAll('[data-unassign-user]').forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          await apiFetch('/admin/roles/' + roleId + '/assign/' + encodeURIComponent(btn.dataset.unassignUser), { method: 'DELETE' });
          renderEditRoleModal();
        } catch (err) {
          showBanner('edit-role-banner', err.message);
        }
      });
    });
  } catch (err) {
    showBanner('edit-role-banner', err.message);
  }
}

document.getElementById('edit-role-rename-btn').addEventListener('click', async () => {
  const newName = document.getElementById('edit-role-name').value.trim();
  if (!newName) return;
  showBanner('edit-role-banner', '');
  try {
    await apiFetch('/admin/roles/' + editingRoleId + '/rename', { method: 'PATCH', body: JSON.stringify({ name: newName }) });
    await renderEditRoleModal();
    loadGroupRoles();
    showSuccessTick('Role renamed');
  } catch (err) {
    showBanner('edit-role-banner', err.message);
    showErrorCross(err.message);
  }
});

document.getElementById('edit-role-toggle-btn').addEventListener('click', async () => {
  const currentlyActive = document.getElementById('edit-role-toggle-btn').textContent === 'Disable role';
  showBanner('edit-role-banner', '');
  try {
    await apiFetch('/admin/roles/' + editingRoleId + '/active', {
      method: 'PATCH',
      body: JSON.stringify({ is_active: !currentlyActive })
    });
    await renderEditRoleModal();
    loadGroupRoles();
  } catch (err) {
    showBanner('edit-role-banner', err.message);
  }
});

document.getElementById('edit-role-delete-btn').addEventListener('click', async () => {
  const name = document.getElementById('edit-role-name').value;
  if (!confirm(`Delete role "${name}"? This removes its access grants and unassigns everyone who has it. This can't be undone.`)) return;
  showBanner('edit-role-banner', '');
  try {
    await apiFetch('/admin/roles/' + editingRoleId, { method: 'DELETE' });
    document.getElementById('edit-role-modal').classList.remove('active');
    loadGroupRoles();
  } catch (err) {
    showBanner('edit-role-banner', err.message);
  }
});

// ---------- audit log ----------
document.getElementById('audit-log-close').addEventListener('click', () => {
  document.getElementById('audit-log-modal').classList.remove('active');
});

async function viewAuditLog(documentId, docNumber) {
  const modal = document.getElementById('audit-log-modal');
  const content = document.getElementById('audit-log-content');
  document.getElementById('audit-log-title').textContent = 'Audit log — ' + docNumber;
  content.innerHTML = '<p style="color:var(--text-dim); font-size:13px;">Loading…</p>';
  modal.classList.add('active');
  try {
    const entries = await apiFetch('/documents/' + documentId + '/audit-log');
    if (!entries.length) {
      content.innerHTML = '<p style="color:var(--text-dim); font-size:13px;">No activity recorded yet.</p>';
      return;
    }
    const actionLabels = {
      created: 'Created', viewed: 'Viewed', edited: 'Edited',
      status_changed: 'Changed status', file_uploaded: 'Uploaded a file'
    };
    content.innerHTML = `
      <table>
        <thead><tr><th>User</th><th>Action</th><th>When</th></tr></thead>
        <tbody>
          ${entries.map(e => `
            <tr>
              <td>${escapeHtml(e.username)}</td>
              <td>${escapeHtml(actionLabels[e.action] || e.action)}</td>
              <td>${new Date(e.created_at).toLocaleString()}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  } catch (err) {
    content.innerHTML = `<p style="color:var(--danger); font-size:13px;">${escapeHtml(err.message)}</p>`;
  }
}

// ---------- backup & restore (superuser only) ----------
function formatBytes(n) {
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(1) : n) + ' ' + units[i];
}

async function loadBackups() {
  const seq = startLoad('backups');
  const tbody = document.getElementById('backup-list-tbody');
  try {
    const rows = await apiFetch('/backup/list');
    if (isStaleLoad('backups', seq)) return;
    tbody.innerHTML = rows.length ? rows.map(b => `
      <tr>
        <td class="mono">${escapeHtml(b.name)}</td>
        <td>${escapeHtml(formatBytes(b.size))}</td>
        <td>${escapeHtml(new Date(b.created).toLocaleString())}</td>
        <td><button class="link-btn-inline" data-backup-name="${escapeHtml(b.name)}">Download</button></td>
      </tr>`).join('') : '<tr class="empty-row"><td colspan="4">No backups yet.</td></tr>';
    tbody.querySelectorAll('[data-backup-name]').forEach(btn => btn.addEventListener('click', () =>
      downloadBackup(btn.dataset.backupName).catch(err => showBanner('backup-banner', err.message))));
  } catch (err) {
    if (!isStaleLoad('backups', seq)) showBanner('backup-banner', err.message);
  }
}

// A single-use, 60-second ticket turns the download into a plain link, so the
// browser streams the file to disk with its own progress bar instead of this
// page holding the whole backup in memory.
async function downloadBackup(name) {
  const password = document.getElementById('backup-password').value;
  const ticket = await apiFetch('/backup/download-ticket', { method: 'POST', body: JSON.stringify({ name, password }) });
  const a = document.createElement('a');
  a.href = ticket.url;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

document.getElementById('backup-nav-item').addEventListener('click', loadBackups);

document.getElementById('backup-download-btn').addEventListener('click', async () => {
  const btn = document.getElementById('backup-download-btn');
  const progress = document.getElementById('backup-progress');
  showBanner('backup-banner', '');
  btn.disabled = true;
  progress.textContent = 'Preparing backup… this can take a few minutes for a lot of files.';
  try {
    const created = await apiFetch('/backup/create', { method: 'POST' });
    await downloadBackup(created.name);
    progress.textContent = `Downloading ${created.name} — your browser shows the progress.`;
    showSuccessTick('Backup ready');
    loadBackups();
  } catch (err) {
    progress.textContent = '';
    showBanner('backup-banner', err.message);
    showErrorCross('Backup failed');
  } finally {
    btn.disabled = false;
  }
});

let restoreUploadId = null;

document.getElementById('restore-check-btn').addEventListener('click', async () => {
  const fileInput = document.getElementById('restore-file');
  const wrap = document.getElementById('restore-confirm-wrap');
  showBanner('backup-banner', '');
  document.getElementById('restore-result').innerHTML = '';
  wrap.style.display = 'none';
  restoreUploadId = null;
  if (!fileInput.files.length) { showBanner('backup-banner', 'Choose a backup file first.'); return; }
  const btn = document.getElementById('restore-check-btn');
  btn.disabled = true;
  btn.textContent = 'Uploading and checking…';
  try {
    const formData = new FormData();
    formData.append('file', fileInput.files[0]);
    formData.append('password', document.getElementById('restore-password').value);
    // Multipart upload: apiFetch would force a JSON Content-Type (same as the logo upload).
    const res = await fetch(API_BASE + '/backup/restore/validate', {
      method: 'POST', headers: { 'Authorization': 'Bearer ' + token }, body: formData,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(formatApiErrorDetail(body.detail) || 'The file could not be checked.');
    const s = body.summary;
    restoreUploadId = body.upload_id;
    document.getElementById('restore-summary').textContent =
      `Backup from ${new Date(s.created_at).toLocaleString()}: ${s.products} products, ${s.documents} documents, ` +
      `${s.companies} companies, ${s.customers} customers, ${s.projects} projects, ${s.users} users, ${s.files} files.`;
    document.getElementById('restore-confirm').value = '';
    document.getElementById('restore-start-btn').disabled = true;
    wrap.style.display = 'block';
  } catch (err) {
    showBanner('backup-banner', err.message);
    showErrorCross('Not a usable backup');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Check file';
  }
});

document.getElementById('restore-confirm').addEventListener('input', (e) => {
  document.getElementById('restore-start-btn').disabled = e.target.value !== 'RESTORE';
});

document.getElementById('restore-start-btn').addEventListener('click', async () => {
  const result = document.getElementById('restore-result');
  showBanner('backup-banner', '');
  try {
    await apiFetch('/backup/restore', { method: 'POST', body: JSON.stringify({
      upload_id: restoreUploadId, confirm: document.getElementById('restore-confirm').value }) });
  } catch (err) {
    showBanner('backup-banner', err.message);
    showErrorCross('Restore did not start');
    return;
  }
  // The restore replaces the sessions table (this session included) and the
  // API answers 503 meanwhile: stop the idle/refresh timer so it can't sign
  // out mid-way and hide the outcome.
  clearInterval(sessionTimer);
  document.getElementById('restore-confirm-wrap').style.display = 'none';
  ['restore-check-btn', 'backup-download-btn'].forEach(id => { document.getElementById(id).disabled = true; });
  let st = { state: 'running', step: 'starting' };
  while (st.state === 'running') {
    result.innerHTML = `<div class="banner banner-info">Restoring… please wait (${escapeHtml(st.step || '')}). Don't close this page.</div>`;
    await new Promise(r => setTimeout(r, 1000));
    try {
      // Public endpoint, no token on purpose: this session ends during the restore.
      st = await (await fetch(API_BASE + '/backup/status')).json();
    } catch (e) { /* gateway hiccup — keep polling */ }
  }
  const ok = st.state === 'done';
  result.innerHTML = `<div class="banner banner-${ok ? 'info' : 'error'}">${escapeHtml(st.message || (ok ? 'Restore complete.' : 'Restore failed.'))}` +
    (ok ? ' Everyone has been signed out.' : '') + '</div>' +
    '<button class="btn-primary" id="restore-signin-btn">Sign in again</button>';
  document.getElementById('restore-signin-btn').addEventListener('click', () => doLogout(''));
  if (ok) showSuccessTick('Restore complete'); else showErrorCross('Restore failed');
});

// ---------- boot ----------
addLineItem(); // start with one blank line item row

async function loadPublicGroups() {
  try {
    const res = await fetch(API_BASE + '/auth/groups-public');
    if (!res.ok) return;
    const groups = await res.json();
    const select = document.getElementById('register-group');
    select.innerHTML = '<option value="">— not sure / none —</option>' +
      groups.map(g => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join('');
  } catch (e) {
    // non-fatal — registration still works without picking a group
  }
}
loadPublicGroups();

// No token lives in localStorage anymore, so there's nothing to check
// synchronously on load — instead, try a silent refresh against the
// HttpOnly cookie (present only if there's a still-valid prior session).
// Success re-enters the app exactly as before; failure (no cookie, or an
// expired/revoked one) just leaves the login screen showing.
(async function bootstrap() {
  let logoutMessage = '';
  try {
    logoutMessage = sessionStorage.getItem(LOGOUT_MESSAGE_KEY) || '';
    sessionStorage.removeItem(LOGOUT_MESSAGE_KEY);
  } catch (e) {}
  try {
    await refreshAccessToken();
    enterApp();
  } catch (e) {
    // No live session: show the login screen once (no reload here, so no loop).
    document.getElementById('login-screen').style.display = 'flex';
    if (logoutMessage) {
      document.getElementById('login-error').innerHTML = `<div class="banner banner-info">${escapeHtml(logoutMessage)}</div>`;
    }
  }
})();

// DEV-ONLY: delete-all BEGIN — temporary one-click wipes (loop iteration 15).
// Superuser only (iteration 29): dev-config is false for anyone else and both
// services answer 403 too; refused in production. The four group-scoped wipes
// follow the sidebar switcher (?group_id=); companies are shared, so they don't.
const DELETE_ALL = [
  // [button id, API path, banner id, reload, noun]
  ['delete-all-products-btn', '/products', 'products-banner', () => loadProducts(), 'products'],
  ['delete-all-documents-btn', '/documents', 'documents-banner', () => loadDocuments(), 'documents'],
  ['delete-all-customers-btn', '/customers', 'customers-banner', () => loadCustomers(), 'customers'],
  ['delete-all-projects-btn', '/projects', 'projects-banner', () => loadProjects(), 'projects'],
  ['delete-all-companies-btn', '/companies', 'company-banner', () => loadCompanies(), 'companies'],
];

async function initDeleteAll() {
  let enabled = false;
  try {
    enabled = (await apiFetch('/products/dev-config')).delete_all_enabled; // one ENVIRONMENT drives both services
  } catch (e) { /* hidden on any error */ }
  DELETE_ALL.forEach(([id]) => { document.getElementById(id).style.display = enabled ? '' : 'none'; });
}

function refreshDeleteAllLabels() {
  const v = groupSwitcher.value;
  DELETE_ALL.forEach(([id, , , , noun]) => {
    document.getElementById(id).textContent = noun === 'companies' ? 'Delete ALL companies (dev)'
      : !v ? 'Delete ALL (dev)' : v === 'none' ? 'Delete ALL unassigned (dev)' : `Delete ALL in ${groupViewName()} (dev)`;
  });
}

DELETE_ALL.forEach(([id, path, banner, reload, noun]) => {
  document.getElementById(id).addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    const v = noun === 'companies' ? '' : groupSwitcher.value;
    const what = !v ? `ALL ${noun}` : v === 'none' ? `ALL unassigned ${noun}` : `ALL ${noun} in ${groupViewName()}`;
    if (!confirm(`Delete ${what}? This cannot be undone.`)) return;
    btn.disabled = true;
    try {
      const res = await apiFetch(v ? `${path}?group_id=${encodeURIComponent(v)}` : path, { method: 'DELETE' });
      await reload();
      showBanner(banner, `Deleted ${res.deleted} ${noun}`, 'info');
    } catch (err) {
      showBanner(banner, err.message);
    } finally {
      btn.disabled = false;
    }
  });
});
// DEV-ONLY: delete-all END

const API_BASE = '/api';

// ---------- stale-response guarding ----------
// If you click refresh (or switch tabs, or re-trigger a load some other
// way) before a previous fetch for that same list has finished, an older
// slower response could otherwise land AFTER a newer one and overwrite it
// with stale data. Each list gets a counter: every load bumps it and
// captures its own number, then only applies its result if no newer load
// has started in the meantime — otherwise it just discards itself.
const loadSeq = { documents: 0, products: 0, customers: 0, projects: 0, companies: 0, groupMembers: 0, groupRoles: 0 };
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
function skeletonRows(colspan) {
  const widths = [80, 55, 70];
  return widths.map(w =>
    `<tr class="empty-row skeleton-row"><td colspan="${colspan}"><span class="skeleton-bar" style="width:${w}%"></span></td></tr>`
  ).join('');
}
function stampClass(value) {
  return 'stamp stamp-' + String(value || '').toLowerCase();
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
function refreshAccessToken() {
  if (!refreshPromise) {
    refreshPromise = fetch(API_BASE + '/auth/refresh', { method: 'POST', credentials: 'include' })
      .then(async res => {
        if (!res.ok) throw new Error('refresh failed');
        const data = await res.json();
        token = data.access_token;
        return token;
      })
      .finally(() => { refreshPromise = null; });
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
        doLogout();
        throw new Error('Session expired — please sign in again.');
      }
    }
    doLogout();
    throw new Error('Session expired — please sign in again.');
  }
  if (!res.ok) {
    let detail = 'Something went wrong.';
    try {
      const body = await res.json();
      detail = formatApiErrorDetail(body.detail) || detail;
    } catch (e) {}
    if (res.status === 403) detail = "You don't have access to this. (" + detail + ")";
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function showBanner(elId, message, type = 'error') {
  const el = document.getElementById(elId);
  el.innerHTML = message ? `<div class="banner banner-${type}">${escapeHtml(message)}</div>` : '';
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
    token = data.access_token;
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

document.getElementById('logout-btn').addEventListener('click', doLogout);

function doLogout() {
  // Best-effort server-side revocation of the refresh cookie — this is
  // what makes logout actually invalidate the session (rather than just
  // forgetting the access token client-side while it — and the refresh
  // cookie — would otherwise still be perfectly valid). Fire-and-forget:
  // the UI logs out regardless of whether this succeeds.
  fetch(API_BASE + '/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {});
  token = null;
  localStorage.removeItem('ledger_token'); // cleanup of any token stored by a pre-cookie-migration session
  document.getElementById('app').classList.remove('active');
  document.getElementById('login-screen').style.display = 'flex';
  document.getElementById('login-username').value = '';
  document.getElementById('login-password').value = '';
}

async function enterApp() {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('app').classList.add('active');
  try {
    const me = await apiFetch('/admin/me');
    document.getElementById('whoami-username').textContent = me.username + (me.is_superuser ? ' (superuser)' : '');
    currentUserIsSuperuser = me.is_superuser;
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
    }
    const canManageCategories = me.is_superuser || (me.service_access && me.service_access.includes('categories'));
    document.getElementById('category-split-btn-wrap').style.display = canManageCategories ? 'inline-flex' : 'none';
  } catch (e) {
    document.getElementById('whoami-username').textContent = '—';
  }
  loadDocuments();
  loadProducts();
  loadCustomers();
  loadCompanies();
  loadProjects();
  loadCategories();
}

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
    <div style="max-width:80px;"><label>Unit</label><input type="text" class="li-unit" placeholder="Nos" value="${prefill ? escapeHtml(prefill.unit || 'Nos') : 'Nos'}"></div>
    <div><label>Unit price</label><input type="text" class="li-price" placeholder="20.00" value="${prefill ? safeVal(prefill.unit_price) : ''}"></div>
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
    const match = typed && cachedProducts.find(p => `${p.sku} — ${p.name}`.toLowerCase() === typed.toLowerCase());
    if (match) {
      div.dataset.resolvedProductId = match.id;
      descInput.value = match.name;
      if (!qtyInput.value) qtyInput.value = '1';
      if (!remarkInput.value && match.remark) remarkInput.value = match.remark;
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
  if (e.target.matches('.li-qty, .li-price')) updateDocumentTotals();
});
document.getElementById('doc-tax-rate').addEventListener('input', updateDocumentTotals);

function updateDocumentTotals() {
  let subtotal = 0;
  document.querySelectorAll('#line-items .line-item-row').forEach(row => {
    const qty = Number(row.querySelector('.li-qty').value) || 0;
    const price = Number(row.querySelector('.li-price').value) || 0;
    subtotal += qty * price;
  });
  const taxRate = Number(document.getElementById('doc-tax-rate').value) || 0;
  const taxTotal = subtotal * (taxRate / 100);
  const currency = document.getElementById('doc-currency').value;
  document.getElementById('doc-totals-subtotal').textContent = subtotal.toFixed(2) + ' ' + currency;
  document.getElementById('doc-totals-grand').textContent = (subtotal + taxTotal).toFixed(2) + ' ' + currency;
}
document.getElementById('doc-currency').addEventListener('change', updateDocumentTotals);

document.getElementById('doc-customer').addEventListener('input', (e) => {
  const hint = document.getElementById('doc-customer-hint');
  const typed = e.target.value.trim();
  const matches = typed && cachedCustomers.some(c => c.name.toLowerCase() === typed.toLowerCase());
  hint.style.display = (typed && !matches) ? 'block' : 'none';
});

function refreshLineItemProductOptions() {
  const datalist = document.getElementById('product-datalist');
  if (!datalist) return;
  datalist.innerHTML = cachedProducts.map(p => `<option value="${escapeHtml(p.sku)} — ${escapeHtml(p.name)}">`).join('');
}

document.getElementById('doc-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner('doc-modal-banner', '');
  document.querySelectorAll('#line-items .li-price.field-error').forEach(el => el.classList.remove('field-error'));
  const overallTaxRate = Number(document.getElementById('doc-tax-rate').value) || 0;
  const items = [];
  const incompleteRows = [];
  document.querySelectorAll('#line-items .line-item-row').forEach((row, rowIndex) => {
    const product_id = row.dataset.resolvedProductId || null;
    let description = row.querySelector('.li-desc').value;
    const remark = row.querySelector('.li-remark').value;
    const quantity = row.querySelector('.li-qty').value;
    const unit = row.querySelector('.li-unit').value || 'Nos';
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
    if (!product_id && !description) description = remark; // free-text row — use the remark as its name/description too, so exports aren't blank

    const item = { quantity: Number(quantity), tax_rate: overallTaxRate, unit };
    if (product_id) item.product_id = product_id;
    if (description) item.description = description;
    if (remark) item.remark = remark;
    if (unit_price) item.unit_price = Number(unit_price);
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
  try {
    const submitBtn = document.getElementById('doc-form-submit-btn');
    submitBtn.disabled = true;
    const commonPayload = {
      customer_id: resolveCustomerIdFromInput(),
      project_id: resolveDocProjectIdFromInput(),
      company_id: resolveDocCompanyIdFromInput(),
      director_id: document.getElementById('doc-director').value || null,
      currency: document.getElementById('doc-currency').value,
      terms_and_conditions: document.getElementById('doc-terms').value || null,
      items
    };
    if (editingDocumentId) {
      await apiFetch('/documents/' + editingDocumentId, { method: 'PATCH', body: JSON.stringify(commonPayload) });
    } else {
      await apiFetch('/documents', {
        method: 'POST',
        body: JSON.stringify({
          doc_type: document.getElementById('doc-type').value,
          doc_number: document.getElementById('doc-number').value || null,
          ...commonPayload
        })
      });
    }
    document.getElementById('doc-form').reset();
    document.getElementById('doc-customer-hint').style.display = 'none';
    document.getElementById('line-items').innerHTML = '';
    addLineItem();
    document.getElementById('doc-modal').classList.remove('active');
    document.getElementById('doc-form-title').textContent = 'New document';
    document.getElementById('doc-form-submit-btn').textContent = 'Create document';
    document.getElementById('doc-type').disabled = false;
    document.getElementById('doc-number').disabled = false;
    editingDocumentId = null;
    loadDocuments();
  } catch (err) {
    showBanner('doc-modal-banner', err.message);
  } finally {
    document.getElementById('doc-form-submit-btn').disabled = false;
  }
});

function startEditDocument(doc) {
  editingDocumentId = doc.id;
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

  document.getElementById('doc-currency').value = doc.currency || 'USD';
  const docProject = cachedProjects.find(p => p.id === doc.project_id);
  document.getElementById('doc-project').value = docProject
    ? `${docProject.name}${docProject.budget_year ? ' (' + docProject.budget_year + ')' : ''}`
    : '';
  const docCompany = cachedCompanies.find(c => c.id === doc.company_id);
  document.getElementById('doc-company').value = docCompany ? docCompany.name : '';
  refreshDocDirectorPicker(doc.director_id);
  document.getElementById('doc-terms').value = doc.terms_and_conditions || '';

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
    a.download = 'Quotation-' + docNumber + '.' + extension;
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
    tbody.innerHTML = `<tr class="empty-row"><td colspan="10">${escapeHtml(err.message)}</td></tr>`;
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
  const customer = cachedCustomers.find(c => c.id === doc.customer_id);
  const project = cachedProjects.find(p => p.id === doc.project_id);

  let catalogueNumber = 0; // matches the Catalogue zip export's numbering — only product-based items count
  const itemRows = (doc.items || []).map(item => {
    if (item.product_id) catalogueNumber++;
    const subItems = item.product_id ? subItemsByProductId[item.product_id] : null;

    const mainRow = `
      <tr>
        <td>${escapeHtml(item.description || '—')}${item.product_id ? ` <span class="mono" style="color:var(--text-dim); font-size:12px;">(#${catalogueNumber})</span>` : ''}</td>
        <td class="mono">${item.quantity != null ? item.quantity : '—'}</td>
        <td>${escapeHtml(item.unit || '')}</td>
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
        <td style="font-size:13px;">Nos</td>
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
      <div><strong>Customer:</strong> ${customer ? escapeHtml(customer.name) : '—'}</div>
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
    tbody.innerHTML = '<tr class="empty-row"><td colspan="10">No documents match.</td></tr>';
    return;
  }
  const statusOptions = ['draft', 'sent', 'paid', 'void', 'expired'];
  tbody.innerHTML = docs.map(d => `
    <tr>
      <td class="mono"><button class="link-btn-inline" data-view-doc-id="${d.id}">${escapeHtml(d.doc_number)}</button></td>
      <td><span class="${stampClass(d.doc_type)}">${escapeHtml(d.doc_type)}</span></td>
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
      <td><button class="link-btn-inline" data-trash-doc-id="${d.id}" data-trash-doc-number="${escapeHtml(d.doc_number)}" style="color:var(--danger);">Delete</button></td>
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
    document.getElementById('project-form-submit-btn').disabled = true;
    let created = null;
    if (editingProjectId) {
      await apiFetch('/projects/' + editingProjectId, { method: 'PATCH', body: JSON.stringify(payload) });
    } else {
      created = await apiFetch('/projects', { method: 'POST', body: JSON.stringify(payload) });
    }
    document.getElementById('project-form').reset();
    document.getElementById('project-modal').classList.remove('active');
    document.getElementById('project-form-title').textContent = 'New project';
    document.getElementById('project-form-submit-btn').textContent = 'Create project';
    editingProjectId = null;
    await loadProjects();

    if (reopenDocModalAfterProject) {
      reopenDocModalAfterProject = false;
      if (created) {
        document.getElementById('doc-project').value = created.name + (created.budget_year ? ' (' + created.budget_year + ')' : '');
      }
      document.getElementById('doc-modal').classList.add('active');
    }
  } catch (err) {
    showBanner('project-modal-banner', err.message);
  } finally {
    document.getElementById('project-form-submit-btn').disabled = false;
  }
});

function startEditProject(project) {
  editingProjectId = project.id;
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
    datalist.innerHTML = cachedProjects.map(p =>
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
  const match = cachedProjects.find(p => {
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
    tbody.innerHTML = `<tr class="empty-row"><td colspan="5">${escapeHtml(err.message)}</td></tr>`;
  }
}

function renderProjectsTable(projects) {
  const tbody = document.getElementById('projects-tbody');
  if (!projects.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="6">No projects match.</td></tr>';
    return;
  }
  tbody.innerHTML = projects.map(p => `
    <tr>
      <td>${escapeHtml(p.name)}</td>
      <td class="mono">${escapeHtml(p.budget_year || '—')}</td>
      <td>${fmtDate(p.created_at)}</td>
      <td><button class="link-btn-inline" data-view-project-id="${p.id}">View documents</button></td>
      <td><button class="link-btn-inline" data-edit-project-id="${p.id}">Edit</button></td>
      <td><button class="link-btn-inline" data-trash-project-id="${p.id}" data-trash-project-name="${escapeHtml(p.name)}" style="color:var(--danger);">Delete</button></td>
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

function buildDirectorRow(director) {
  const div = document.createElement('div');
  div.className = 'director-row';
  div.dataset.directorId = director ? director.id : '';
  div.style.cssText = 'margin-bottom:14px; padding:12px; border:1px solid var(--border); border-radius:8px;';
  div.innerHTML = `
    <div class="form-row" style="flex-wrap:wrap;">
      <div style="min-width:180px;">
        <label>Name</label>
        <input type="text" class="director-name" placeholder="Managing Director's name" value="${director ? escapeHtml(director.name) : ''}">
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
      <div style="min-width:180px; text-align:right;">
        <button type="button" class="link-btn-inline director-remove-btn" style="color:var(--danger);">Remove</button>
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

function resetDirectorRows(directors) {
  const container = document.getElementById('director-rows');
  container.innerHTML = '';
  if (directors && directors.length) {
    directors.forEach(d => container.appendChild(buildDirectorRow(d)));
  } else {
    container.appendChild(buildDirectorRow(null)); // default: one blank row
  }
}

document.getElementById('add-director-row-btn').addEventListener('click', () => {
  document.getElementById('director-rows').appendChild(buildDirectorRow(null));
});

document.getElementById('toggle-company-form').addEventListener('click', () => {
  editingCompanyId = null;
  document.getElementById('company-form').reset();
  document.getElementById('company-form-title').textContent = 'New company';
  document.getElementById('company-form-submit-btn').textContent = 'Create company';
  document.getElementById('company-current-logo-info').style.display = 'none';
  document.getElementById('company-current-seal-info').style.display = 'none';
  resetDirectorRows(null);
  showBanner('company-modal-banner', '');
  document.getElementById('company-modal').classList.add('active');
});

document.getElementById('company-modal-close').addEventListener('click', () => {
  document.getElementById('company-modal').classList.remove('active');
});

document.getElementById('company-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  showBanner('company-modal-banner', '');
  const logoInput = document.getElementById('company-logo');
  const sealInput = document.getElementById('company-seal');
  const payload = {
    name: document.getElementById('company-name').value,
    short_name: document.getElementById('company-short-name').value || null,
    position: document.getElementById('company-position').value || null,
    address: document.getElementById('company-address').value || null,
    contact_no: document.getElementById('company-contact').value || null,
    support_email: document.getElementById('company-email').value || null
  };
  try {
    document.getElementById('company-form-submit-btn').disabled = true;
    let company;
    if (editingCompanyId) {
      company = await apiFetch('/companies/' + editingCompanyId, { method: 'PATCH', body: JSON.stringify(payload) });
    } else {
      company = await apiFetch('/companies', { method: 'POST', body: JSON.stringify(payload) });
    }

    if (logoInput.files.length > 0) {
      const formData = new FormData();
      formData.append('file', logoInput.files[0]);
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
        loadCompanies();
        return;
      }
    }

    if (sealInput.files.length > 0) {
      const formData = new FormData();
      formData.append('file', sealInput.files[0]);
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
        loadCompanies();
        return;
      }
    }

    // Flush the director rows: create any new ones, rename existing ones,
    // and upload a seal file for any row where one was selected.
    const directorRows = document.querySelectorAll('#director-rows .director-row');
    for (const row of directorRows) {
      const name = row.querySelector('.director-name').value.trim();
      const address = row.querySelector('.director-address').value.trim() || null;
      const contact_no = row.querySelector('.director-contact').value.trim() || null;
      const email = row.querySelector('.director-email').value.trim() || null;
      const sealFile = row.querySelector('.director-seal-file').files[0];
      let directorId = row.dataset.directorId;

      if (!directorId) {
        if (!name) continue; // a genuinely blank unused row — skip silently
        const created = await apiFetch('/companies/' + company.id + '/directors', {
          method: 'POST',
          body: JSON.stringify({ name, address, contact_no, email })
        });
        if (!created) {
          showBanner('company-modal-banner', 'Company saved, but one MD row failed to save.');
          loadCompanies();
          return;
        }
        directorId = created.id;
      } else if (name) {
        await apiFetch('/companies/' + company.id + '/directors/' + directorId, {
          method: 'PATCH',
          body: JSON.stringify({ name, address, contact_no, email })
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
        if (!uploadResponse.ok) {
          showBanner('company-modal-banner', `Company saved, but a director's seal didn't upload.`);
          loadCompanies();
          return;
        }
      }
    }

    document.getElementById('company-form').reset();
    resetDirectorRows(null);
    document.getElementById('company-modal').classList.remove('active');
    document.getElementById('company-form-title').textContent = 'New company';
    document.getElementById('company-form-submit-btn').textContent = 'Create company';
    editingCompanyId = null;
    loadCompanies();
  } catch (err) {
    showBanner('company-modal-banner', err.message);
  } finally {
    document.getElementById('company-form-submit-btn').disabled = false;
  }
});

function startEditCompany(company) {
  editingCompanyId = company.id;
  document.getElementById('company-modal').classList.add('active');
  document.getElementById('company-form-title').textContent = 'Edit company — ' + company.name;
  document.getElementById('company-form-submit-btn').textContent = 'Save changes';
  document.getElementById('company-name').value = company.name;
  document.getElementById('company-short-name').value = company.short_name || '';
  document.getElementById('company-position').value = company.position || '';
  document.getElementById('company-address').value = company.address || '';
  document.getElementById('company-contact').value = company.contact_no || '';
  document.getElementById('company-email').value = company.support_email || '';

  document.getElementById('company-logo').value = '';
  document.getElementById('company-seal').value = '';

  const logoInfo = document.getElementById('company-current-logo-info');
  if (company.logo_object_key) {
    logoInfo.innerHTML = 'A logo is already attached. <button type="button" class="link-btn-inline" data-view-current-logo="' + company.id + '">View it</button> — choosing a new file will replace it.';
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

function renderCompaniesTable(companies) {
  const tbody = document.getElementById('companies-tbody');
  if (!companies.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="7">No company matches.</td></tr>';
    return;
  }
  tbody.innerHTML = companies.map(c => `
    <tr>
      <td>${escapeHtml(c.name)}</td>
      <td>${escapeHtml(c.position || '—')}</td>
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
    email: document.getElementById('customer-email').value || null,
    phone: document.getElementById('customer-phone').value || null,
    billing_address: document.getElementById('customer-address').value
      ? { address: document.getElementById('customer-address').value }
      : null
  };
  try {
    document.getElementById('customer-form-submit-btn').disabled = true;
    let created = null;
    if (editingCustomerId) {
      await apiFetch('/customers/' + editingCustomerId, { method: 'PATCH', body: JSON.stringify(payload) });
    } else {
      created = await apiFetch('/customers', { method: 'POST', body: JSON.stringify(payload) });
    }
    document.getElementById('customer-form').reset();
    document.getElementById('customer-modal').classList.remove('active');
    document.getElementById('customer-form-title').textContent = 'New customer';
    document.getElementById('customer-form-submit-btn').textContent = 'Create customer';
    editingCustomerId = null;
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
    showBanner('customer-modal-banner', err.message);
  } finally {
    document.getElementById('customer-form-submit-btn').disabled = false;
  }
});

function startEditCustomer(customer) {
  editingCustomerId = customer.id;
  document.getElementById('customer-modal').classList.add('active');
  document.getElementById('customer-form-title').textContent = 'Edit customer — ' + customer.name;
  document.getElementById('customer-form-submit-btn').textContent = 'Save changes';
  document.getElementById('customer-name').value = customer.name;
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
  datalist.innerHTML = cachedCustomers.map(c => `<option value="${escapeHtml(c.name)}">`).join('');
}

function resolveCustomerIdFromInput() {
  const input = document.getElementById('doc-customer');
  const typed = input.value.trim();
  if (!typed) return null;
  const match = cachedCustomers.find(c => c.name.toLowerCase() === typed.toLowerCase());
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
  } catch (err) {
    if (isStaleLoad('customers', mySeq)) return;
    tbody.innerHTML = `<tr class="empty-row"><td colspan="5">${escapeHtml(err.message)}</td></tr>`;
  }
}

function renderCustomersTable(customers) {
  const tbody = document.getElementById('customers-tbody');
  if (!customers.length) {
    tbody.innerHTML = '<tr class="empty-row"><td colspan="6">No customers match.</td></tr>';
    return;
  }
  tbody.innerHTML = customers.map(c => `
    <tr>
      <td><button class="link-btn-inline" data-view-customer-id="${c.id}">${escapeHtml(c.name)}</button></td>
      <td>${escapeHtml(c.email || '—')}</td>
      <td>${escapeHtml(c.phone || '—')}</td>
      <td>${fmtDate(c.created_at)}</td>
      <td><button class="link-btn-inline" data-edit-customer-id="${c.id}">Edit</button></td>
      <td><button class="link-btn-inline" data-trash-customer-id="${c.id}" data-trash-customer-name="${escapeHtml(c.name)}" style="color:var(--danger);">Delete</button></td>
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
// Lives only in memory for this browser tab (resets on page refresh) — a
// lightweight way to pick several products while browsing, then drop them
// all into a new document's line items at once.
let cartItems = [];

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
      unit: 'Nos',
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
  } catch (err) {
    showBanner('category-modal-banner', err.message);
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
  } catch (err) {
    showBanner('edit-category-banner', err.message);
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
    unit_price: null // price isn't set at the catalog level anymore — entered per-document instead
  };
  try {
    document.getElementById('product-form-submit-btn').disabled = true;
    let product;
    if (editingProductId) {
      // sku can't be changed via PATCH (it's the identity); unit_price is
      // also left out here — it's not managed via this form anymore, and
      // omitting it (rather than sending null) means an edit never wipes
      // out a price a product might already have from before this change.
      const { sku, unit_price, ...updatePayload } = payload;
      product = await apiFetch('/products/' + editingProductId, {
        method: 'PATCH',
        body: JSON.stringify(updatePayload)
      });
    } else {
      product = await apiFetch('/products', { method: 'POST', body: JSON.stringify(payload) });
    }

    if (fileInput.files.length > 0) {
      const formData = new FormData();
      formData.append('file', fileInput.files[0]);
      const uploadResponse = await fetch(API_BASE + '/products/' + product.id + '/file', {
        method: 'POST',
        headers: { 'Authorization': 'Bearer ' + token },
        body: formData
      });
      if (!uploadResponse.ok) {
        let detail = 'Catalog file upload failed.';
        try {
          const errBody = await uploadResponse.json();
          if (errBody.detail) detail = errBody.detail;
        } catch (parseErr) {
          // response wasn't JSON — stick with the generic message
        }
        showBanner('product-modal-banner', `Product saved, but the catalog file didn't upload: ${detail}`);
        loadProducts();
        return;
      }
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
  } catch (err) {
    showBanner('product-modal-banner', err.message);
  } finally {
    document.getElementById('product-form-submit-btn').disabled = false;
  }
});

function startEditProduct(product) {
  editingProductId = product.id;
  document.getElementById('product-form-title').textContent = 'Edit product — ' + product.sku;
  document.getElementById('product-sku').value = product.sku;
  document.getElementById('product-sku').disabled = true; // identity field, not editable
  document.getElementById('product-name').value = product.name;
  document.getElementById('product-category').value = product.category || '';
  document.getElementById('product-description').value = product.description || '';
  document.getElementById('product-remark').value = product.remark || '';
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

    resultsEl.innerHTML = `
      <p style="font-weight:600;">Created ${result.created.length} product${result.created.length === 1 ? '' : 's'}.</p>
      <table>
        <thead><tr><th>Row</th><th>Item Number</th><th>Name</th><th>File attached?</th></tr></thead>
        <tbody>${rows || '<tr class="empty-row"><td colspan="4">Nothing was created.</td></tr>'}</tbody>
      </table>
      ${result.warnings.length ? `<p style="color:var(--danger); font-size:13px; margin-top:10px;">${result.warnings.map(w => escapeHtml(w)).join('<br>')}</p>` : ''}
    `;
    loadProducts();
  } catch (err) {
    showBanner('bulk-import-banner', err.message);
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
  } catch (err) {
    showBanner('category-bulk-import-banner', err.message);
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
    refreshLineItemProductOptions();
    refreshProductCategoryFilter();
    applyFilterAndSort('products');
  } catch (err) {
    if (isStaleLoad('products', mySeq)) return;
    tbody.innerHTML = `<tr class="empty-row"><td colspan="9">${escapeHtml(err.message)}</td></tr>`;
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
    tbody.innerHTML = '<tr class="empty-row"><td colspan="9">No products match.</td></tr>';
    return;
  }
  tbody.innerHTML = products.map(p => `
    <tr>
      <td class="mono">${escapeHtml(p.sku)}</td>
      <td>
        <button class="link-btn-inline" data-view-product-id="${p.id}">${escapeHtml(p.name)}</button>
        ${p.description ? `<div style="font-size:12px; color:var(--text-dim); margin-top:2px; white-space:pre-line;">${escapeHtml(p.description)}</div>` : ''}
      </td>
      <td>${escapeHtml(p.category || '—')}</td>
      <td>${fmtDate(p.created_at)}</td>
      <td>${fmtDate(p.updated_at)}</td>
      <td>${p.sub_item_count > 0
        ? `<button class="link-btn-inline" data-download-bundle-id="${p.id}" data-download-bundle-sku="${escapeHtml(p.sku)}">View file</button>`
        : (p.image_object_key ? `<button class="link-btn-inline" data-product-id="${p.id}">View file</button>` : '—')}</td>
      <td><button class="link-btn-inline" data-add-to-cart-id="${p.id}">+ Add to cart</button></td>
      <td><button class="link-btn-inline" data-edit-id="${p.id}">Edit</button></td>
      <td><button class="link-btn-inline" data-trash-product-id="${p.id}" data-trash-product-sku="${escapeHtml(p.sku)}" style="color:var(--danger);">Delete</button></td>
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
        <td>${escapeHtml(c.position || '—')}</td>
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
    matchFn: (c, q) => c.name.toLowerCase().includes(q),
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

  if (config.extraFilters) {
    for (const ef of config.extraFilters) {
      const val = document.getElementById(ef.selectId).value;
      if (val) list = list.filter(item => ef.matchFn(item, val));
    }
  }

  if (config.dateFilter) {
    const fromVal = document.getElementById(config.dateFilter.fromId).value;
    const toVal = document.getElementById(config.dateFilter.toId).value;
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
  config.render(list);
}

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
      <td>${u.is_superuser ? 'Superuser' : (u.roles && u.roles.length ? escapeHtml(u.roles.join(', ')) : '<span style="color:var(--text-dim);">— no role —</span>')}</td>
      <td>
        ${u.is_superuser
          ? '<span class="stamp stamp-paid" style="font-size:10px;">active</span>'
          : (!u.is_approved
              ? '<span style="color:var(--danger);">Pending</span>'
              : `<button class="status-toggle-btn ${u.is_active ? 'is-active' : 'is-disabled'}" data-toggle-user-id="${u.id}" data-toggle-active="${u.is_active}">${u.is_active ? 'Active' : 'Disabled'}</button>`)}
      </td>
      <td>${u.is_superuser ? '<span style="color:var(--text-dim);">—</span>' : `<button class="link-btn-inline" data-edit-user-id="${u.id}">Manage</button>`}</td>
    </tr>
  `).join('');
  tbody.querySelectorAll('[data-edit-user-id]').forEach(btn => {
    btn.addEventListener('click', () => {
      const user = cachedAllUsers.find(u => u.id === btn.dataset.editUserId);
      if (user) openEditUserModal(user);
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

async function openEditUserModal(user) {
  editingUserId = user.id;
  document.getElementById('edit-user-title').textContent = 'Manage user — ' + user.username;
  document.getElementById('edit-user-username').value = user.username;
  document.getElementById('edit-user-password').value = '';
  showBanner('edit-user-banner', '');
  document.getElementById('edit-user-modal').classList.add('active');
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
  } catch (err) {
    showBanner('edit-user-banner', err.message);
  }
});

async function loadEditUserProjectAccess() {
  const container = document.getElementById('edit-user-project-checkboxes');
  container.innerHTML = '<span style="color:var(--text-dim); font-size:13px;">Loading…</span>';
  try {
    let availableProjects;
    if (currentUserIsSuperuser) {
      availableProjects = cachedProjects;
    } else {
      const available = await apiFetch('/admin/users/' + editingUserId + '/available-projects');
      availableProjects = cachedProjects.filter(p => available.project_ids.includes(p.id));
    }

    const currentGrants = await apiFetch('/admin/users/' + editingUserId + '/project-access');

    if (!availableProjects.length) {
      container.innerHTML = currentUserIsSuperuser
        ? '<span style="color:var(--text-dim); font-size:13px;">No projects created yet.</span>'
        : '<span style="color:var(--text-dim); font-size:13px;">No shared group has project access yet — ask a superuser to grant some to your group first.</span>';
      return;
    }

    container.innerHTML = availableProjects.map(p => `
      <label>
        <input type="checkbox" class="edit-user-project-cb" data-project-id="${p.id}" ${currentGrants.includes(p.id) ? 'checked' : ''}>
        ${escapeHtml(p.name)}${p.budget_year ? ' (' + escapeHtml(p.budget_year) + ')' : ''}
      </label>
    `).join('');

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

document.getElementById('create-user-group').addEventListener('change', loadCreateUserRolePicker);

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
    // Default to the Operation group — that's where normal operational users belong.
    const operation = groups.find(g => g.name === 'Operation');
    if (operation) groupSelect.value = operation.id;
  } catch (err) {
    showBanner('create-user-banner', err.message);
  }
  await loadCreateUserRolePicker();
  document.getElementById('create-user-modal').classList.add('active');
});

document.getElementById('create-user-close').addEventListener('click', () => {
  document.getElementById('create-user-modal').classList.remove('active');
});

document.getElementById('create-user-submit-btn').addEventListener('click', async () => {
  const username = document.getElementById('create-user-username').value.trim();
  const password = document.getElementById('create-user-password').value;
  const groupId = document.getElementById('create-user-group').value;
  const roleId = document.getElementById('create-user-role').value;
  showBanner('create-user-banner', '');
  if (!username || !password || !groupId) {
    showBanner('create-user-banner', 'Username, password, and group are required.');
    return;
  }
  try {
    document.getElementById('create-user-submit-btn').disabled = true;
    const payload = { username, password };
    if (roleId) payload.role_id = roleId;
    await apiFetch('/admin/groups/' + groupId + '/users', { method: 'POST', body: JSON.stringify(payload) });
    document.getElementById('create-user-modal').classList.remove('active');
    loadAllUsers();
  } catch (err) {
    showBanner('create-user-banner', err.message);
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
  } catch (err) {
    showBanner('edit-user-banner', err.message);
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
  } catch (err) {
    showBanner('admin-banner', err.message);
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
  } catch (err) {
    showBanner('admin-banner', err.message);
  }
});

const ALL_SERVICES = ['documents', 'products', 'search', 'audit-log', 'categories'];

let cachedGroupRoles = [];
let currentUserIsSuperuser = false;

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

    const serviceCheckboxes = ALL_SERVICES.map(s => `
      <label>
        <input type="checkbox" class="role-service-cb" data-service="${s}" ${detail.services.includes(s) ? 'checked' : ''}>
        ${s}
        ${s === 'documents' ? `
          <select class="role-level-select" style="margin:0 0 0 4px; padding:2px 4px; font-size:12px; width:auto;" ${detail.services.includes('documents') ? '' : 'disabled'}>
            <option value="edit" ${detail.service_levels && detail.service_levels.documents === 'edit' ? 'selected' : ''}>Edit</option>
            <option value="view" ${detail.service_levels && detail.service_levels.documents === 'view' ? 'selected' : ''}>View only</option>
          </select>
        ` : ''}
      </label>
    `).join('');
    document.getElementById('edit-role-service-checkboxes').innerHTML = serviceCheckboxes;

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
            if (cb.dataset.service === 'documents' && levelSelect) levelSelect.disabled = false;
          } else {
            await apiFetch('/admin/roles/' + roleId + '/access/' + cb.dataset.service, { method: 'DELETE' });
            if (cb.dataset.service === 'documents') {
              const levelSelect = document.querySelector('.role-level-select');
              if (levelSelect) levelSelect.disabled = true;
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
  } catch (err) {
    showBanner('edit-role-banner', err.message);
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
  try {
    await refreshAccessToken();
    enterApp();
  } catch (e) {
    document.getElementById('login-screen').style.display = 'flex';
  }
})();

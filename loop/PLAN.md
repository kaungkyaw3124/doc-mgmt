# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **15** (revised: now covers 5 pages) |
| Goal | **Part A** — A one-click **"Delete ALL …"** button on each of the Products, Documents, Customers, Projects and Companies pages, for the user's development/testing stage. Temporary, and must be trivial to remove later. **Part B** — modal headings with the Close button stay visible while the modal body scrolls (see "Part B" at the bottom). **Part C** — animated success tick after saves (see "Part C", do last) |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants
The user is still developing and wants to wipe data between tests without clicking Delete
on every row. Their words: "just 1 click", "I want to test and delete, after that I'm gonna
remove that" (the buttons themselves). Later request: "in delete all product, we also need
to add delete all in documents, customers, projects, company."
Every row already has its own Delete button; do not change those.

## Decisions already made (do not ask the user again)
- **One click, no confirm dialog, no typed word.** The user explicitly asked for this.
- **Permanent delete** (not the recycle bin), including rows already in the recycle bin.
- **Dev-only guard (the one safety rail)**: the button is shown and the endpoint answers only
  when `ENVIRONMENT` is not `production` (both services have `environment: str = "development"`
  in `app/core/config.py`; compose passes `ENVIRONMENT: ${ENVIRONMENT:-development}`).
  In production every endpoint returns 403 and the UI hides the buttons. No other friction.
- Same permission rule as the existing single delete on each entity; view-only user gets 403.
- Button labels: **"Delete ALL products (dev)"**, **"Delete ALL documents (dev)"**,
  **"Delete ALL customers (dev)"**, **"Delete ALL projects (dev)"**, **"Delete ALL companies (dev)"**.
  Red danger style, next to each page's "+ New …" button
  (`web/index.html`: `toggle-product-form` ~690, `toggle-doc-form` ~617, `toggle-customer-form` ~740,
  `toggle-project-form` ~784, `toggle-company-form` ~829).
- Each click calls its endpoint, refreshes that list, shows a banner "Deleted N <things>".
- **Related data is never a blocker** (this is a wipe, not a safe delete):
  - Documents: delete rows; `line_items` and `audit_log_entries` go by FK cascade (verify, add explicit deletes if the DB/SQLite does not cascade); remove each document from the search index (`search_client.remove_document_from_index`) and best-effort delete its stored files (PDF/attachments/MinIO) — never fail because a file is missing.
  - Customers / Projects: first set `documents.customer_id` / `documents.project_id` to NULL, then delete. Documents are kept.
  - Companies: first set `documents.company_id` and `documents.director_id` to NULL, delete the company's directors (CASCADE exists on `company_directors.company_id`; verify), best-effort delete logo/seal/letterhead files from MinIO, then delete companies. Documents are kept.
  - Products: delete all rows including soft-deleted, and sub-item links (check FK on `product_sub_items`), remove each from Meilisearch, best-effort delete files. Categories are kept.
  - Do NOT cross-delete: "Delete ALL customers" does not delete documents, and so on.
- Documents/Customers/Projects/Companies live in **document-service**, Products in **catalogue-service**: two services, two guards, same pattern.

## Tasks
1. **Endpoints**: `DELETE /products` (catalogue-service, `routers/products.py`),
   `DELETE /documents`, `DELETE /customers`, `DELETE /projects`, `DELETE /companies`
   (document-service routers). Collection routes with no id: define them above the
   `/{id}` routes so they don't clash with `/trash`, `/duplicate-check`, etc. Each returns
   403 in production or for view-only access, else `{"deleted": <count>}`.
2. **Gateway/auth**: check `infra/nginx/nginx.conf.template` and the auth-service RBAC
   mapping so `DELETE` on these five collection paths is allowed for the same users who can
   delete a single item today, and is not blocked by a method/path rule.
3. **Buttons + handlers** in `web/index.html` and `web/js/app.js`, hidden in production.
   Expose the environment to the UI with the smallest existing mechanism (a field on an
   endpoint the UI already calls, or a tiny `GET /config`-style response); do not hard-code a hostname check.
4. **Easy to remove later**: wrap everything in marked blocks —
   `# DEV-ONLY: delete-all BEGIN/END` in Python, `<!-- DEV-ONLY: delete-all -->` and
   `// DEV-ONLY: delete-all BEGIN/END` in web files. Keep each endpoint in the smallest block.
   The report must list every touched file and line range and the exact removal steps.
5. Do not change existing delete/trash/restore behaviour.

## Tests
- Catalogue (`services/catalogue-service/tests/`) and document-service (`services/document-service/tests/`), following existing patterns. For each entity:
  - several rows incl. related data → `DELETE` → 200 with correct count; list endpoints are empty afterwards; related rows handled as above (line items/audit gone; documents kept with NULL references for customers/projects/companies; directors gone; categories kept);
  - `ENVIRONMENT=production` → 403, nothing deleted; view-only → 403, nothing deleted;
  - a MinIO/search error does not fail the request; empty table → `{"deleted": 0}`.
- Playwright `tests/test_ui_delete_all.py`: for each of the 5 pages create rows through the API, open the page, click the button once (fail if a `confirm` dialog fires), the list shows the empty state and the banner says "Deleted N …". One test checks buttons are absent when production (if the stack can't be switched, test the UI logic with a stubbed response and say so).
- All existing tests still pass.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Catalogue + document-service unit tests pass | new tests + both whole suites |
| A2 | UI test passes | `pytest tests/test_ui_delete_all.py -v` |
| A3 | No regressions | `./scripts/verify.sh` passes |
| A4 | Proof | `loop/evidence/iter-15-<page>-before.png` and `-after.png` for products, documents, customers, projects, companies |
| A5 | Removal steps | the report lists every DEV-ONLY block and the exact removal steps |

## Out of scope — record under "Found but not fixed" if you notice them
- Delete-all for categories, users, or the recycle bins of other types.
- Changing the per-row delete buttons.

---

## Part B — Sticky modal heading + Close button (do after Part A, same iteration)

### What the user wants
In pop-up windows (first seen in **Bulk import products**), when the user scrolls down a long
result list, the heading and the **Close** button scroll out of view. The user chose: **keep the
heading and Close button fixed at the top while the body scrolls** (not a "go to top" button).
Pure CSS, no scroll listeners, no JS.

### Decisions already made
- Sticky header = the modal's `<h3>` title + the Close button, always visible.
- In **Bulk import products** (and the category bulk-import modal if it has the same layout), the
  result summary line ("Created N products · Skipped N duplicates · N warnings") and the
  Import button area should also stay visible; only the long results table scrolls.
  Keep it simple: if that needs more than a small CSS change, do only the header + Close and record the rest under "Found but not fixed".
- Apply to **every** `.modal-box` modal in `web/index.html`, not just bulk import, using one shared
  CSS rule (see `.modal-overlay`/`.modal-box`/`.modal-close` ~lines 431-446; `.modal-box-scrollbody`
  ~448-475 already keeps its heading fixed, do not break it).
- Suggested approach (use another if it is simpler/more robust): `.modal-box { padding-top:0 }`, wrap
  heading + Close in a header bar with `position: sticky; top: 0; background: #fff; z-index: 1;
  padding: 20px 0 12px; border-bottom: 1px solid <existing border colour>;` — the current Close is
  `float:right` before the `<h3>`, so a small markup change per modal (or a CSS-only change
  that makes `.modal-close` `position: sticky; top: 0; float: right` plus a sticky `h3`) is needed.
  Check every modal still looks the same when it is short (no scrolling).

### Tests
- Playwright `tests/test_ui_modal_sticky_header.py`: open Bulk import products, import a spreadsheet
  with 40+ rows (generate with openpyxl) so the result list overflows, scroll the modal body to the
  bottom, assert the **Close** button's bounding box is inside the viewport and visible, click it and
  assert the modal closes. Parametrize over at least 3 other modals (audit log, create user, edit role)
  with `evaluate` to make the body taller than the box if they are short, asserting Close stays visible after scrolling.
- Existing UI tests still pass.

### Acceptance checks
| # | Check | How to verify |
|---|---|---|
| B1 | Close/heading stay visible after scrolling | the Playwright test above |
| B2 | Short modals unchanged | screenshots `loop/evidence/iter-15-modal-short-before/after.png` look identical |
| B3 | Proof | `loop/evidence/iter-15-bulk-import-scrolled.png` (scrolled to the bottom with heading + Close visible) |
| B4 | No regressions | `./scripts/verify.sh` passes |

---

## Part C — Animated success tick after saves (do last, same iteration)

### What the user wants
When data is saved (product, company, logo, seal, letterhead, bulk import, …), confirm it with a
small **animated check mark** like the iPhone's, instead of only text. **No sound at all** (the user
said so — do not add audio, a sound toggle, or vibration). Must not hurt page performance.

### Decisions already made
- **CSS-only**: one small inline SVG check (circle + tick drawn with `stroke-dasharray` /
  `stroke-dashoffset` keyframes). No library (no Lottie, no GIF, no JS animation loop). Total added code < ~1.5 KB.
- One shared helper in `web/js/app.js`, e.g. `showSuccessTick(anchorEl, message?)`, and one shared CSS block.
  Every existing "saved" confirmation calls it instead of / alongside the text banner (keep the text banner
  for errors and for details such as bulk-import counts; the tick is the success signal).
- Shown **only after the server confirms** (HTTP 2xx). For logo / seal / letterhead uploads show the tick only after the
  saved image is loaded back (`img.onload` of the returned URL) so it proves the file is really there.
- Failure: a small red cross with a short shake and the error text. Same helper, `showErrorCross(...)`.
- Placement: small tick near the Save/Import button (or top-right toast), auto-fades after ~1.5 s,
  never blocks clicks (`pointer-events: none`), removes its DOM node when done.
- `@media (prefers-reduced-motion: reduce)`: show a static tick (no animation).
- Bulk import: one tick plus the existing "Created N products" summary.
- Do not change what gets saved or any API; this is UI feedback only.

### Tasks
1. Inspect `web/js/app.js` / `web/index.html` for every create/update/upload success path (products, categories, customers,
   projects, companies incl. logo/seal/letterhead/MD seal, documents, users/roles, bulk imports) and list them in the report.
2. Add the shared CSS + helper, wire all success paths and the matching failure paths.
3. Make sure the page does not get heavier: report the size of added CSS/JS in bytes.

### Tests
- Playwright `tests/test_ui_success_tick.py`: create a product → the tick element appears, has the animation, disappears
  after ~2 s and leaves no DOM node behind; a failing save (e.g. forced 400 via route interception) shows the cross, not the tick;
  logo upload → tick only after the image loads; with `reduced_motion="reduce"` no animation is applied; no `<audio>`/`AudioContext` in the code (grep).
- Existing UI tests still pass.

### Acceptance checks
| # | Check | How to verify |
|---|---|---|
| C1 | Tick on every success path, cross on failures | the Playwright test + the path list in the report |
| C2 | No sound, no new dependency | grep shows no audio code; `package`/CDN list unchanged |
| C3 | Weight | report states added bytes (< ~1.5 KB CSS+JS) |
| C4 | Proof | `loop/evidence/iter-15-tick-*.png` (mid-animation + finished) |
| C5 | No regressions | `./scripts/verify.sh` passes |

# Change Plan v1 — Pricing Model, Print Layout, Multi-Director Companies, Backup Strategy

Status: **DESIGN ONLY — not approved, not implemented.** No application code
has been changed to produce this document. Do not begin implementation
until this plan is explicitly signed off, including the open questions in
§13.

Scope: Feature 1 (remove product pricing), Feature 2 (quotation print
layout), Feature 3 (multi-director companies), Feature 4 (director
selection + historical immutability), Feature 5 (backup strategy).

---

## 1. Executive Summary

Features 1, 2, 3, and 4 are a connected chain, not four independent tickets:

- **Feature 1** moves pricing authority out of `catalogue-service` entirely
  and makes it purely a per-line-item concept in `document-service` — this
  is a **genuine architectural improvement**, not just a data model tweak:
  it removes a coupling where a catalogue-service value (`Product.unit_price`)
  silently set a commercial term on a document. I recommend it as designed,
  with one addition the request didn't specify (a `discount` field) flagged
  as a blocking open question (§13).
- **Feature 2** turns out to be smaller than the request implies: the PDF
  and XLSX exports **already print a product description today**, as its
  own column. The real work is a layout change (stack name+description in
  one cell instead of two columns), not a new data pipeline. While
  investigating this I found an existing, unrelated correctness issue
  worth deciding on now: exports currently **re-fetch a product's name and
  description live from catalogue-service at export time**, not from a
  stored snapshot — meaning editing a product today silently rewrites the
  text on every past quotation that referenced it, including ones already
  sent to a customer. This directly contradicts the immutability principle
  Feature 4 asks for, and I recommend fixing it as part of this same body
  of work (see §13, Q10).
- **Feature 3** is a straightforward one-to-many extension
  (`Company` → `Director`), fully containable inside `document-service`'s
  own database — a real, DB-enforced foreign key is correct here (unlike
  most cross-entity references in this codebase, which are deliberately
  loose because they cross service/database boundaries — this one doesn't).
- **Feature 4** is the highest-risk item. "Historical quotations must never
  change" is a hard requirement that the naive implementation (store a
  `director_id` and look it up live at export time) **will not satisfy** —
  I found direct proof in the existing company logo/seal upload code that
  a later edit reuses the same storage key, silently replacing the file a
  historical document would otherwise still be pointing at. The correct
  design is a **write-time snapshot** (freeze name/position, and physically
  copy the seal image to a new, document-owned storage key at the moment a
  director is selected) — the same principle this codebase already applies
  to line items (a `LineItem` never re-reads a product's live price).
- A **cross-cutting blocker surfaces here that wasn't urgent before**: this
  codebase creates its schema via SQLAlchemy `Base.metadata.create_all()`,
  which can add a brand-new table but **cannot add a column to an existing
  table, and cannot drop a column**. Feature 1 (drop `products.unit_price`)
  and Feature 4 (add four columns to `documents`) are both impossible to
  actually apply to a running deployment without a real migration. This
  plan treats standing up applied migrations as a prerequisite, not a
  parallel nice-to-have — see §7 and §9.
- **Feature 5** is an infrastructure/process deliverable, not a code
  change. The single biggest finding: this system currently has **zero
  backup of any kind** and every data store lives on **one host with no
  offsite copy** — a single disk failure today loses everything. The plan
  below is designed to be implemented and *proven via a real restore drill*
  before Feature 1's destructive column drop is executed, because that
  drop is the first truly irreversible step in this whole change set.

**Recommended sequencing** (detailed in §10): backups and a proven restore
first → additive/low-risk schema (directors) → director selection with the
snapshot mechanism → print layout (bundled with the export-file touches
Feature 4 already requires) → pricing removal last, once the discount
question is answered and migrations are proven to work.

---

## 2. Architecture Impact

### Feature 1 — Remove product pricing
Removes a cross-service coupling: today, `document-service` calls
`catalogue-service` for a product and, if the caller didn't supply a price,
uses `product["unit_price"]` as the line item's price
(`services/document-service/app/routers/documents.py`, `_process_items`).
After this change, `catalogue-service` has no pricing concept at all and
`document-service` never reads a price from it — pricing becomes entirely
local to `document-service`, which is the right owner of commercial terms.
No new services, no new inter-service calls; this is a net *simplification*
of the request flow in `_process_items`.

### Feature 2 — Print layout
Presentation-only. No architectural change. Confirms (see §6) that the
data needed is already flowing through the export pipeline.

### Feature 3 — Multi-director companies
Adds one new entity (`Director`) owned by `document-service`, in the same
`docmgmt` database as `Company`. This is an **intra-service** relationship
— unlike the cross-service references elsewhere in this codebase
(`project_id` in auth-service, `product_id` in `line_items`), which are
deliberately unenforced because they cross database boundaries, a
`Director.company_id` foreign key is fully appropriate and should be a real,
DB-enforced FK. No new service, no new inter-service call.

### Feature 4 — Director selection with historical immutability
Introduces a **write-time snapshot pattern** on `Document`, extending a
principle this codebase already uses for line items (a `LineItem` stores
its own `description`/`unit_price` rather than re-reading the product live).
The new piece is that one of the snapshotted fields is a *file*, not just
scalar data — the director's seal image — which needs a physical object
copy, not just a foreign key, to be truly immutable (see §9, Risk R2).
`document-service/app/core/storage.py` gains a new capability
(server-side object copy) it doesn't have today.

### Feature 5 — Backup strategy
Pure operations/infrastructure addition — no application architecture
change. Closes a real, currently-unaddressed single-point-of-failure (see
§9).

### Cross-cutting: the migration gap becomes load-bearing
`document-service` has Alembic scaffolding but its `main.py` deliberately
calls `Base.metadata.create_all()` instead of running it; `catalogue-service`
has no Alembic at all. `create_all()` only creates tables that don't exist —
it never alters an existing table. Every one of Features 1, 3, and 4 needs
an actual `ALTER TABLE` (drop columns / add columns / add a table with a
backfill), which `create_all()` cannot do. **This plan requires standing up
real, applied migrations in both `document-service` (activate what already
exists) and `catalogue-service` (build from scratch) as part of this work,
not as a separate future task.**

---

## 3. Database Changes

All changes below are additive-first where possible, to keep rollback cheap
(see §8). Table/column names are proposals for approval, not final.

### Feature 1 — `catalogue` database

| Table | Change |
|---|---|
| `products` | Drop `unit_price` (Numeric). **Open question (§13, Q2)**: also drop `currency`, which exists only to pair with `unit_price` and has no other reader. |

No index changes required (`unit_price` isn't indexed today).

### Feature 1 (expanded scope) — `docmgmt` database

| Table | Change |
|---|---|
| `line_items` | Add `discount` (type/semantics **blocked on §13, Q1** — percentage vs. flat amount, pre- or post-tax) |

### Feature 3 — `docmgmt` database

New table:

```
directors
  id                 UUID PK
  company_id         UUID FK -> companies.id, ON DELETE CASCADE
  name               VARCHAR(255) NOT NULL
  position            VARCHAR(100)              -- e.g. "Director", "Managing Director"
  seal_object_key     VARCHAR(500)               -- MinIO key, own image per director
  is_deleted          BOOLEAN NOT NULL DEFAULT false   -- same recycle-bin convention as every other resource
  created_at          TIMESTAMPTZ
  updated_at          TIMESTAMPTZ
```

`ON DELETE CASCADE` on `company_id` is safe *only because* Feature 4 stores
its own snapshot on `Document` rather than a live-resolved reference — a
company (and its directors) can be fully deleted without corrupting any
historical document. Index on `company_id` (mirrors every other FK in this
schema being indexed) and on `is_deleted`.

**Data migration required, not just schema**: every existing `Company` row
today carries a single `position` and `seal_object_key` directly. The
migration must backfill one `Director` row per existing company from these
fields so no company silently loses its signer information. Exact backfill
values need a product decision (§13, Q5) — e.g. what `name` to use when the
company has no natural "director name" today (there isn't one — `Company`
never had a name field for the *signer*, only `position`).

### Feature 4 — `docmgmt` database

| Table | Change |
|---|---|
| `documents` | Add `director_id` (UUID, FK → `directors.id`, **nullable, `ON DELETE SET NULL`**), `director_name` (VARCHAR(255)), `director_position` (VARCHAR(100)), `director_seal_object_key` (VARCHAR(500)) |

`director_id` is kept as a real, nullable FK (not a loose UUID) because it's
intra-service — but it is explicitly **not** what export code reads from.
It exists only for traceability/UI convenience ("this document originally
used this director row, if it still exists"). The three snapshot columns
are the sole source of truth for PDF/XLSX generation, and must never be
re-derived by joining to `directors` at export time.

### Migration mechanics (applies to all of the above)

`document-service` already has Alembic wired up but unused — this plan
requires actually running it (`alembic upgrade head` on deploy, not
`create_all()`). `catalogue-service` has no Alembic at all — this plan
requires standing it up (mirroring `document-service`'s existing setup)
specifically to execute the `products.unit_price`/`currency` column drop
safely. See §7 for the concrete migration plan and §9 for what happens if
this isn't done.

---

## 4. API Changes

### Feature 1

| Endpoint | Change |
|---|---|
| `POST /products`, `PATCH /products/{id}` (catalogue-service) | Remove `unit_price` (and `currency`, pending §13 Q2) from `ProductCreate`/`ProductUpdate`/`ProductOut` |
| `GET /products/{id}/sub-items`, `POST /products/{id}/sub-items` | Remove `unit_price`/`currency` from `SubItemOut` |
| `POST /products/bulk-import` | Stop reading a `Price`/`unit_price`/`unit price` spreadsheet column; update the endpoint's own docstring/behavior description |
| `POST /documents`, `PATCH /documents/{id}` (document-service) | `_process_items` no longer auto-fills `unit_price` from the linked product — every line item (product-linked or not) must supply its own `unit_price` explicitly, or the existing `422` ("each item needs either a product_id, or both description and unit_price") is no longer accurate and must be reworded, since `unit_price` is now *always* required regardless of `product_id`. Add `discount` to `DocumentItemIn`/`DocumentItemOut` (pending §13 Q1) |

### Feature 2
No API/contract changes — the data (`name`, `description`) is already
present in every export code path. Purely a template/rendering change.

### Feature 3 (document-service)

New endpoints, proposed nested under the existing `/companies` prefix
(rationale in §6 — this avoids any Nginx gateway change):

| Endpoint | Method | Notes |
|---|---|---|
| `GET /companies/{company_id}/directors` | GET | active directors for that company (dropdown source for Feature 4) |
| `POST /companies/{company_id}/directors` | POST | `{name, position?}` — requires edit access, same as company writes today |
| `GET /companies/{company_id}/directors/trash` | GET | recycle bin, same convention as every other resource |
| `PATCH /companies/{company_id}/directors/{id}` | PATCH | partial update |
| `PATCH /companies/{company_id}/directors/{id}/trash` \| `/restore` | PATCH | soft delete/restore |
| `DELETE /companies/{company_id}/directors/{id}` | DELETE | hard delete — safe due to `ON DELETE SET NULL` design (§3) |
| `POST /companies/{company_id}/directors/{id}/seal` | POST | multipart upload, same standardization treatment (Pillow resize/pad) as company seal today |
| `GET /companies/{company_id}/directors/{id}/seal-url` | GET | presigned URL |

**Deprecation, not removal**: `Company.position` and
`Company.seal_object_key` stay in the model and API for now (pending §13
Q4), so nothing that reads them today breaks immediately.

### Feature 4 (document-service)

| Endpoint | Change |
|---|---|
| `POST /documents`, `PATCH /documents/{id}` | `DocumentCreate`/`DocumentUpdate` gain optional `director_id`. Server validates the chosen director belongs to the chosen `company_id` (`400` if not); on success, copies the director's current name/position/seal into the document's snapshot columns (see §6 for the copy mechanism) |
| `GET /documents/{id}`, list endpoints | `DocumentOut` gains `director_id`, `director_name`, `director_position` (read-only, reflects the frozen snapshot, not a live director lookup) |
| (business rule, not a new endpoint) | If `PATCH /documents/{id}` changes `company_id` and the document's existing `director_id` doesn't belong to the new company, the server clears `director_id` and the three snapshot fields rather than leaving a mismatched director attached |

### Feature 5
No application API changes. If a "reindex everything" admin endpoint is
approved as part of closing the Meilisearch-backup question (§13 Q10), that
would be a new, separate, superuser-only endpoint per service
(`POST /admin/reindex` or similar) — flagged as an optional addition, not
required for the backup plan itself to work.

---

## 5. Frontend Changes

All changes are within `web/index.html` (the entire frontend is one file).

### Feature 1
- Product form (`#product-form`): remove the Unit Price input.
- Products table: remove the `Price` column and its sort button; adjust
  `colspan` on the table's skeleton-loader rows (currently `10` → `9`).
- View-product modal (`renderProductDetailContent`): remove price display.
- Bulk-import UI copy: stop mentioning an optional Price column.
- Cart (`renderCartModal`, cart table header `Item Number | Name | Qty |
  Price | Remove`): remove the Price column and any per-item price math —
  the cart becomes name/quantity only; price is entered once the cart is
  imported into a document's line items, same as a manually-added line
  item today.
- Document line-item row (`addLineItem`, `refreshLineItemProductOptions`):
  stop auto-filling Unit Price when a product is selected (name/description
  autofill only, per the requirement); add a new **Discount** input per
  line item (pending §13 Q1 for its exact semantics/label), and update the
  client-side subtotal preview math to match whatever discount rule is
  approved.

### Feature 2
- `renderDocumentDetailContent` (view-document modal): currently renders
  only `item.description` as a single line (see §6 — this field is
  overloaded to mean "item name" today, not the product's actual
  description). Extend the existing per-product `Promise.all` fetch loop
  in `openViewDocumentModal` (already fetching sub-items per unique
  `product_id`) to also fetch each product's `description`, and render it
  as a second, smaller line under the item name — matching the PDF/XLSX
  layout change instead of introducing a third, inconsistent presentation.

### Feature 3
- Company view/edit: add a "Directors" section (list existing directors,
  add new, edit, soft-delete/restore, upload a director's seal) — modeled
  directly on the existing group/role management panel pattern already in
  the Admin view for structure/consistency.
- Company table/detail: decide whether to keep showing the single
  deprecated company-level Seal column, or replace it with a "Directors
  (N)" link into the new management panel (recommend the latter once §13
  Q4 is resolved).

### Feature 4
- Document create/edit form: after a Company is chosen (existing
  `doc-company` field), fetch `GET /companies/{id}/directors` and populate
  a new Director field — same typeahead/datalist pattern already used for
  Customer/Project/Company selection in this form
  (`doc-customer`/`doc-project`/`doc-company` + their `<datalist>`s).
  Re-fetch and clear the current selection whenever the Company selection
  changes (mirrors the existing doc-number re-preview behavior on company
  change).
- View-document modal: display the selected director's name/position (and
  optionally a seal thumbnail) using the document's own snapshot fields —
  never fetch the live `directors` resource for this display.

### Feature 5
No frontend changes.

---

## 6. Export Changes

### Feature 1
- `export_quotation.py` (XLSX) and `export_pdf.py`/`quotation.html` (PDF):
  remove the sub-item price/amount cells entirely. This is **low-risk**:
  the existing code comment already states sub-item rows are "informational,
  already covered by the parent's price" and are **excluded from the Total
  formula today** (`main_item_rows` only counts parent rows) — confirmed by
  reading `export_quotation.py:147,163,197`. Removing sub-item pricing
  changes nothing about how totals are computed.
- Both export paths already compute `unit_price`/`amount` **from the
  `LineItem` row itself** (`item.unit_price`, `item.quantity`), never from
  `Product.unit_price` directly — so removing the column from `Product`
  requires no export-code change to the main item rows, only to the
  sub-item rows described above.

### Feature 2 — the actual current behavior, precisely
Contrary to the request's framing ("Current: prints only product name"),
**both the PDF and XLSX exports already print a description today**, as
its own table column (`quotation.html`: `<th>Description</th>`;
`export_quotation.py` headers: `["No", "Item", "Description", "Qty", ...]`).
The real change is **where** it prints, not **whether** it prints:

- **PDF / `quotation.html`**: merge the "Item" and "Description" columns
  into one cell — product name on its own line (existing weight/size),
  description on the line directly beneath it in smaller, muted text (new
  CSS class, e.g. `.item-desc`). Frees a column's width, which should be
  redistributed to the remaining columns.
- **XLSX / `export_quotation.py`**: **recommend keeping Description as its
  own column** rather than merging, since a spreadsheet's value is in being
  filterable/sortable/copyable per field — merging into one wrapped cell
  with an embedded newline is possible (the codebase already uses
  `Alignment(wrap_text=True)` elsewhere) but actively reduces the file's
  usefulness as data. **This needs explicit confirmation** (§13, Q3) since
  it means PDF and XLSX intentionally end up laid out differently.
- **UI preview**: see §5, Feature 2 — piggyback on the existing per-product
  fetch loop already in `openViewDocumentModal` rather than adding new
  N+1 calls.

**Separately discovered issue, relevant here**: both `export_pdf.py` and
`export_quotation.py` build the printed item name/description by calling
`catalogue_client.get_product(item.product_id)` **live, at export time**
(`services/document-service/app/routers/documents.py`,
`_gather_export_data`) — not from anything stored on `LineItem`. A product
rename or description edit today silently changes the text on every past
export of every document that ever referenced it, including one already
emailed to a customer. This is the same class of problem Feature 4 is
explicitly being built to prevent for directors. Recommend deciding now
(§13, Q10) whether to also snapshot the product's name/description onto
`LineItem` at document create/update time — the infrastructure (an
`item.description` column, already partially used this way) already
exists; it would need to stop being overwritten by a later product edit.

### Feature 3
No export changes (Company logo remains company-level and untouched by
this feature; director seal export handling is entirely Feature 4).

### Feature 4
- `export_pdf.py`/`quotation.html`: replace the current "company seal"
  concept in the supplier block with the document's frozen director
  fields — `director_name`, `director_position`,
  `director_seal_object_key` — fetched via `get_file_bytes` exactly like
  the company logo is today, and embedded as a base64 data URI. Company
  **logo** stays as-is (brand mark, not a signer — unaffected).
- `export_quotation.py`: same substitution for the XLSX signer block.
- **Fallback behavior** (pending confirmation, §13 Q7): if a document has
  no `director_id` (created before this feature, or the user skipped
  selection), recommend falling back to the deprecated
  `Company.position`/`Company.seal_object_key` fields for the transition
  period, rather than leaving the signer block blank — avoids a jarring
  regression on every historical document rendered after this ships.
- `_gather_export_data` (`documents.py`): must read the director block
  **only** from the `Document` row's own snapshot columns — must not join
  to `directors` at export time, or the entire point of Feature 4 is
  defeated.

### Feature 5
N/A — not an application export.

---

## 7. Migration Plan

Ordered, with the hard blocker called out explicitly.

1. **Stand up real, applied migrations before touching schema for these
   features** (see §2, §9):
   - `document-service`: switch `app/main.py` from
     `Base.metadata.create_all()` to running `alembic upgrade head` at
     container startup (or as an explicit pre-start step in the deploy
     process — either is acceptable, but it must happen automatically on
     every deploy, not be a manual step someone can forget).
   - `catalogue-service`: stand up Alembic from scratch (copy
     `document-service`'s `alembic/`/`alembic.ini` shape), with an initial
     migration that captures the table shapes `create_all()` has been
     silently maintaining so far, so the migration history has an honest
     starting point.
2. **Feature 3 migration** (additive, lowest risk — do first): create
   `directors` table + backfill migration (one `Director` row per existing
   `Company`, per the resolved values in §13 Q5). Purely additive — no
   existing column touched, cheap to roll back (`DROP TABLE directors`).
3. **Feature 4 migration** (additive): add `director_id`, `director_name`,
   `director_position`, `director_seal_object_key` to `documents`, all
   nullable. Purely additive, cheap to roll back (`ALTER TABLE documents
   DROP COLUMN ...`), and safe to deploy ahead of the application code that
   uses them (existing rows simply have nulls).
4. **Feature 1 migration, line item discount** (additive): add
   `line_items.discount`, nullable or defaulted to `0` — blocked on §13
   Q1.
5. **Feature 1 migration, drop pricing** (destructive — do last, and only
   after a proven backup/restore per Feature 5 and §13 Q2 is answered):
   - **Before** running this migration: export a CSV/JSON snapshot of every
     `products.id, sku, unit_price, currency` row as an extra, independent
     safety net (cheap, and narrower/faster to restore from than a full
     database backup if this specific data is ever needed again).
   - Take (and verify) a fresh `catalogue` database backup per the Feature
     5 procedure, in addition to routine scheduled backups.
   - `ALTER TABLE products DROP COLUMN unit_price` (and `currency`,
     pending Q2).
6. **Data backfill validation**: after step 2's backfill runs, manually
   spot-check a sample of companies (especially any with a blank
   `position` or no `seal_object_key` today) to confirm the generated
   `Director` rows are sane before relying on them in production exports.

No migration in this plan requires application downtime — all are either
purely additive or (step 5) removing a column nothing will read anymore
once the corresponding application code has already deployed and been
running successfully.

---

## 8. Rollback Plan

| Feature | Rollback path | Cost |
|---|---|---|
| 1 (discount, additive) | Drop the `discount` column; revert app code. | Low |
| 1 (drop pricing, destructive) | Restore `unit_price`/`currency` columns via `ALTER TABLE ... ADD COLUMN`; repopulate from the pre-migration CSV snapshot (§7 step 5) or the full DB backup if the CSV wasn't taken. Revert app code. | Medium — data restoration required, not just a code revert |
| 3 (directors) | `DROP TABLE directors`; revert app code. Since `Company.position`/`seal_object_key` are being *kept* (deprecated, not removed — §13 Q4), no company-level data is lost by rolling this back. | Low |
| 4 (director selection) | Drop the four new `documents` columns; revert app code. Existing documents are unaffected either way since the columns are nullable and additive. | Low |
| 2 (print layout) | Template/formatting revert only — no data involved. | Very low |
| 5 (backups) | N/A — infrastructure addition, nothing to roll back in the application. | — |

**General principle applied throughout this plan**: keep destructive
changes (column drops) isolated to the smallest possible step (Feature 1's
pricing drop) and sequence them last, specifically so that if anything
earlier in the chain needs to be rolled back, it can be done with a simple
additive-reversal rather than a data restore.

---

## 9. Risks

| # | Risk | Feature | Severity | Mitigation |
|---|---|---|---|---|
| R1 | `create_all()` cannot apply any of the required schema changes (new columns, dropped columns) to an already-running database — deploying the model changes alone will silently do nothing, and the app will crash on first read/write of a column that doesn't exist. | 1, 4 | **High** | Migrations must be proven working (§7 step 1) *before* any model change in this plan ships. Treat as a hard gate, not a parallel task. |
| R2 | A director's seal image, if only *referenced* (not copied) by a document, can be silently replaced later: the existing company logo/seal upload code reuses the exact same object key on every re-upload (`company-seals/{company_id}/{filename}`, confirmed in `companies.py`) — the same pattern applied naively to directors would let a future seal re-upload retroactively change historical documents' printed seal, directly violating the stated requirement. | 4 | **High** | Physically copy the seal image bytes to a new, document-owned object key at the moment of selection (§6), not just store a reference. |
| R3 | No offsite/second copy of any data today — Postgres, MinIO, and Meilisearch all live as bind mounts on one host. A single disk/host failure loses everything, with no recovery path. | 5 (pre-existing, exposed by this plan) | **Critical** | This is the primary deliverable of Feature 5 — see §1 and the design below. |
| R4 | A backup plan that has never been restored is unproven. | 5 | High | A full restore-to-a-new-host drill is a required step before this plan treats Feature 5 as "done," and specifically before Feature 1's destructive migration is executed. |
| R5 | PDF layout fragility: `quotation.html`'s own CSS comments record it has already overflowed twice at smaller header-margin values as content grew taller than estimated — stacking a description line under every item name will make item rows taller and may interact with the existing `break-inside: avoid` page-break rules in unexpected ways, especially for long descriptions. | 2 | Medium | Test with realistic (long) product descriptions across a multi-page quotation before sign-off, not just short sample data. |
| R6 | Historical pricing data is permanently lost once `products.unit_price` is dropped — it cannot be reconstructed from `line_items` (which only has the price *at the time each document was created*, not a general "what did this product cost historically" record). | 1 | Medium | CSV export before the drop (§7 step 5) as an explicit, independent safety net beyond the routine backup. |
| R7 | Director/company data-migration backfill may produce nonsensical `Director` rows for companies with incomplete data (no `position`, no `seal_object_key`, or even no clear "name" to use — `Company` has never had a distinct signer-name field). | 3 | Medium | Product decision required before the backfill runs (§13 Q5); manual spot-check step included in §7. |
| R8 | Editing a document's `company_id` after a director was already selected can leave a mismatched director attached if not explicitly handled. | 4 | Low/Medium | Explicit server-side rule: clear `director_id`/snapshot fields on a company change that invalidates them (§4). |
| R9 | The pre-existing "exports re-fetch product name/description live" behavior (discovered while scoping Feature 2) means Feature 2, as a pure layout change, ships without fixing a real immutability gap that sits right next to the one Feature 4 is designed to close — leaving it unresolved may read as an inconsistent bar for "historical accuracy" across the same feature set. | 2 | Medium | Explicit decision needed (§13 Q10) rather than an implicit gap. |
| R10 | Meilisearch has no supported backup/restore path exercised anywhere in this codebase today, and no "rebuild the index from Postgres" tooling exists either. | 5 | Medium | Covered in the Feature 5 design (native dump API) plus a recommended (optional) reindex-all endpoint. |

---

## 10. Recommended Implementation Order

1. **Feature 5 (backup strategy) — first, before any schema change in this
   plan.** You want a proven restore path in place before Feature 1's
   irreversible column drop happens later in the sequence.
2. **Migration infrastructure activation** (§7 step 1) — required before
   steps 3 onward can actually apply their schema changes.
3. **Feature 3 (directors schema + API + admin UI)** — additive, no
   dependents yet, lowest risk, and its backfill needs time to be
   spot-checked (§7 step 6) before Feature 4 builds on top of it.
4. **Feature 4 (director selection + snapshot mechanism)** — depends on
   Feature 3.
5. **Feature 2 (print layout), bundled with the export-file work Feature 4
   already requires** — both touch `quotation.html`, `export_pdf.py`, and
   `export_quotation.py`; doing them together avoids two separate passes
   over the same files and the associated PDF-fragility retesting (R5)
   only needs to happen once.
6. **Feature 1 (remove pricing)** — last. Requires the discount question
   (§13 Q1) resolved, benefits from the migration process having already
   been exercised successfully on the additive changes above before
   attempting the first destructive one.

---

## 11. Estimated Complexity

Rough sizing, assuming one engineer familiar with this codebase and
excluding time spent waiting on the open questions in §13 to be answered.

| Feature | Size | Rough effort | Why |
|---|---|---|---|
| 5 — Backup strategy | Large | 3–5 days, plus ongoing | New tooling, offsite target setup (depends on what's available — §13 Q8), and a real restore drill is part of "done," not optional |
| 3 — Multi-director companies | Medium | 2–3 days | New table + router + backfill migration + admin-style UI panel — but a well-worn shape in this codebase (mirrors existing CRUD + soft-delete + file-upload patterns closely) |
| 4 — Director selection + snapshot | Medium/Large | 3–4 days | New storage capability (object copy), new validation rule (company/director consistency), UI flow, export rewiring |
| 2 — Print layout | Small | 1–2 days | Template/layout only, but PDF fragility (R5) needs real testing time, not just a visual check |
| 1 — Remove product pricing | Medium/Large | 3–5 days | Spans both services + frontend + a genuinely new concept (discount) + the first real destructive migration this project has run |

**Total, sequential**: roughly 12–19 engineering days, not counting time
waiting on the §13 answers or on standing up an offsite backup target
(which may itself depend on infrastructure this project doesn't control).

---

## 12. Files That Will Change

### catalogue-service
- `app/models.py` — drop `Product.unit_price` (and `currency`, pending Q2)
- `app/schemas.py` — remove price fields from `ProductCreate`/`Update`/`Out`, `SubItemOut`
- `app/routers/products.py` — `create_product`, `update_product`, `bulk_import_products`, `list_sub_items`, `add_sub_item`
- `app/core/search_client.py` — drop price fields from the indexed Meilisearch record
- `alembic/`, `alembic.ini` (**new** — doesn't exist today) + one or more revision files

### document-service
- `app/models.py` — new `Director` model; `Document` gains 4 new columns; `Company` relationship to `directors`
- `app/schemas.py` — `DirectorCreate`/`Update`/`Out`; `DocumentCreate`/`Update`/`Out` gain `director_id` + read-only director fields; `DocumentItemIn`/`Out` gain `discount` (pending Q1)
- `app/routers/documents.py` — `_process_items` (remove price auto-fill, add discount handling), `create_document`/`update_document` (director validation + snapshot-copy call, company-change consistency rule), `_gather_export_data` (read snapshot fields, not live director/company lookup)
- `app/routers/companies.py` — deprecate (don't remove yet) `position`/`seal_object_key` write paths, pending Q4
- `app/routers/directors.py` (**new**) — director CRUD, soft delete, seal upload, nested under `/companies/{id}/directors`
- `app/core/storage.py` — new object-copy helper (e.g. `copy_file(src_key, dst_key)` via boto3 `copy_object`)
- `app/core/export_pdf.py`, `app/core/export_quotation.py` — remove sub-item pricing; swap company-seal block for director snapshot fields; layout change for name/description
- `app/templates/quotation.html` — merge Item/Description columns into one cell with new CSS; remove sub-item price cells; director block
- `app/main.py` — register the new `directors` router; switch startup from `create_all()` to `alembic upgrade head`
- `alembic/versions/000X_*.py` (**new**, several) — directors table + backfill, documents director columns, line_items discount column

### frontend
- `web/index.html` — product form/table/modal (remove price), cart (remove price), document line-item rows (remove price autofill, add discount input), document form (director selection), view-document modal (description line, director display), company view (directors management panel)

### infra / ops
- No `infra/nginx/nginx.conf` change required if directors are nested under the existing `/companies` prefix (recommended — see §6/§4 rationale).
- **New**: a `backup/` (or `ops/backup/`) directory at repo root, mirroring the existing `seed-data-setup/scripts/` convention — scripts for Postgres dump, MinIO mirror, Meilisearch dump, and their restores; scheduling config (cron or equivalent); documentation of the offsite target once §13 Q8 is answered.

### docs (post-implementation follow-up, not part of this plan's implementation)
- `docs/data-model.md`, `docs/api.md`, `docs/workflows.md`, `docs/known-issues.md` (retire the "Alembic not applied" and "SUPPLIER_* unused" findings once fixed) will need a refresh pass after implementation — out of scope for this design document itself.

---

## 13. Questions or Assumptions That Must Be Resolved Before Implementation

These are genuine blockers, not formalities — implementation should not
start on the affected feature until each is answered.

1. **(Blocks Feature 1) Discount semantics**: is `discount` a percentage or
   a flat amount? Applied before or after `tax_rate`? Per-line-item only,
   or is a document-level discount also wanted? The request lists it
   alongside Unit Price/Quantity/Tax with no further detail — I need this
   specified before touching `line_items` or the total-calculation logic
   in `_process_items`.
2. **(Feature 1) Should `Product.currency` also be removed?** It exists
   today only to pair with `unit_price` and has no other reader once price
   is gone. My recommendation: yes, remove it too — but confirm, since the
   request only said "pricing."
3. **(Feature 2) XLSX layout**: keep Description as its own column (my
   recommendation, for spreadsheet usability) or merge it under Item Name
   with a line break, matching the PDF exactly? These are different
   trade-offs for a printed document vs. working data — please confirm
   which is wanted, or if both should match exactly regardless of the
   usability cost.
4. **(Feature 3) Should `Company.position`/`Company.seal_object_key` be
   kept as deprecated fallback fields, or removed once every company has
   at least one Director?** My recommendation: keep them for now (cheaper
   rollback, smoother transition — see §8) and revisit removal in a later,
   separate change once the fallback is confirmed unused in practice.
5. **(Feature 3) Backfill values**: for the one-time migration that
   creates a `Director` row per existing `Company`, what should populate
   `Director.name` for companies with no natural signer-name field today
   (only `position` exists, e.g. "Director")? Options: use the company's
   own name, a placeholder like "Authorized Signatory," or require manual
   entry post-migration with the director left in a clearly-incomplete
   state. Needs a product decision.
6. Confirming a simple point rather than raising a new one: one seal image
   per director (as specified) — please confirm this is final and no
   director needs multiple seal variants.
7. **(Feature 4) Fallback when no director is selected**: for documents
   created before this feature (or where the user skips director
   selection), should exports fall back to the deprecated company-level
   `position`/`seal_object_key`, or simply omit the signer block? My
   recommendation: fall back, for a smoother transition — confirm.
8. **(Feature 5) Offsite backup target**: what's actually available —
   a cloud object storage bucket, a second on-prem host, something else?
   This plan is written generically and cannot finalize the offsite
   mechanism (e.g. `mc mirror` destination) without knowing what
   infrastructure/budget is approved.
9. **(Feature 5) RPO/RTO targets**: the schedule/retention proposal in the
   backup design assumes reasonable defaults (nightly Postgres, hourly
   MinIO mirror, 14/8/6 daily/weekly/monthly retention) — these need
   explicit business sign-off, not just an engineering guess.
10. **(Feature 2 / cross-cutting)** Should this change set also fix the
    newly-discovered issue where exports re-fetch a product's live
    name/description at export time instead of a stored snapshot (§6, R9)
    — meaning a product edit today silently rewrites historical,
    already-sent quotations? This wasn't part of the original ask, but it's
    the same class of problem Feature 4 is solving for directors, discovered
    while implementing the immediately-adjacent Feature 2 change. Recommend
    fixing it in the same pass; needs explicit confirmation since it's
    scope beyond what was requested.
11. **(Feature 5, optional addition)** Should a superuser-only "rebuild
    search index from the database" endpoint be added to `document-service`
    and `catalogue-service`? Meilisearch has no restore tooling exercised
    in this codebase today; since it's a derived index (Postgres is the
    source of truth), a reindex endpoint would make Meilisearch effectively
    disposable/low-priority in the backup plan rather than something that
    must be perfectly restored. Not required for Feature 5 to be
    considered complete, but recommended.

---

**Next step**: resolve §13, then re-review this plan before any
implementation branch is opened.

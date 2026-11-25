# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **30** |
| Goal | **Unit on products.** A product has a Unit (No, M, Set, Roll …) chosen when it is created/edited and in bulk import; documents take it from the product; "No" prints as **No** for quantity 1 and **Nos** for 2 or more. A bulk "Set unit…" action fixes the existing products (e.g. all cables → M) |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user wants
"add unit in product. some products is cable and i want to put M (Meter). When we create a document, the unit is Nos automatically. If 1 item it is **No**, 2 or more it is **Nos**, automatic according to qty. I like to put unit at the beginning of the product create, product bulk import."
Today: the unit lives only on the document line item (`line_items.unit`, default "Nos", typed by hand, `web/js/app.js` ~809/915/2529; hard-coded "Nos" in `export_pdf.py` ~102, `export_quotation.py` ~329/360, `templates/quotation.html` ~292, sub-item rows in `app.js` ~1209).

## Decisions already made (planner defaults — user did not object)
- **Product field `unit`** (catalogue-service, `String(20)`, NOT NULL, default **"No"**; migration guarded/idempotent, single head, SQLite-compatible; existing products get "No"). Not part of the duplicate key (name + description only); not part of group scope; included in product search results, `ProductOut`, create/update schemas, move.
- **Allowed values**: a dropdown of common units **No, M, Set, Pcs, Roll, Box, Lot, Kg, Ft** plus **Other…** (free text, max 20 chars, trimmed, not empty). Server normalizes common spellings on input (case-insensitive): `m/meter/metre/meters/metres → M`, `no/nos/number → No`, `pc/pcs/piece/pieces → Pcs`, `ft/feet/foot → Ft`, `kg/kgs → Kg`; anything else is kept as typed. Blank → "No".
- **Product form** (create and edit): a **Unit** select next to the price, default No; placed early in the form (right after Name/Category) as the user asked.
- **Products list**: a narrow **Unit** column.
- **Bulk import**: optional **Unit** column (header case-insensitive, like Category/Price/Description); blank → No; normalized as above; the downloadable template (`GET /products/bulk-import-template`) gets the column with an example row; the import dialog's help text mentions it; import result/skip reasons unchanged. A bad value (> 20 chars) skips that row with a clear reason.
- **Bulk "Set unit…"** in the Products selection bar (iteration 24 bar; editors, not only admins): choose a unit → applies to every selected product (filter by category "Cable", Select all, Set unit… M). New endpoint `POST /products/set-unit` `{ids, unit}`, group-scoped (out-of-scope ids → 404, nothing changed), edit access required, returns `{updated: N}`; success tick; list reloads; selection clears.
- **Documents use the product's unit**: when a product is added to a document (product picker, "Import from cart", bundles/sub-items), the line's unit is prefilled with the product's unit (not "Nos"). Manual lines keep default "No". Still editable per line; stored on the line item as today (a snapshot — later product changes don't alter old documents).
- **Plural rule — one shared helper, used EVERYWHERE a unit is shown**: if the unit is "No" or "Nos" (any case) → show **"No" when quantity == 1, "Nos" otherwise** (0, 2, 2.5 → "Nos"). Every other unit is shown exactly as stored (M stays M). Apply in: the document form's line rows (updates live as the quantity changes), the document detail view, PDF (`export_pdf.py`), XLSX (`export_quotation.py`, both places), `templates/quotation.html` (incl. the tail/sub-item rows), and the sub-item display in `app.js` (~1209: quantity 1.00 → "No"). The stored value for default lines becomes "No" (old rows with stored "Nos" print by the same rule, so an old quotation with qty 1 will now show "No" — expected and desired; mention in the report).
- Server-side default for `LineItemCreate.unit` changes from "Nos" to "No".
- Group isolation, concurrency (version/409), Idempotency-Key behaviour unchanged. Product file/catalogue-file features untouched.

## Tasks
1. Inspect: product model/schemas/routers (create, update, bulk import parse + template, search index payload), the document line-item code paths and every place printing a unit (list above), `catalogue_client.py` fields passed to document-service, the product form and list in `web/js/app.js` / `index.html`, the cart import (`~2518`). List them in the report.
2. Catalogue: migration + model + schemas + normalizer + bulk import column/template + `POST /products/set-unit`.
3. Document-service: pass the product's unit to new lines, default "No", one plural helper (Python) used by PDF/XLSX/template.
4. Web: unit select in the product form, Unit column, selection-bar "Set unit…", bulk-import help text, document form prefill + live No/Nos, one JS plural helper used by form/detail/sub-items.
5. Tests + evidence.

## Tests
- Unit (catalogue): normalizer table; create/update with unit; default "No"; > 20 chars → 422; bulk import with/without the Unit column (blank → No, "meter" → M, bad value → row skipped with reason); template has the Unit header; `set-unit` happy path, out-of-scope id → 404 and nothing changed, view-only → 403; migration single head + idempotent; duplicate check unchanged (same name+description with different units is still an exact duplicate).
- Unit (document): plural helper table (No/1 → "No", No/2 → "Nos", Nos/1 → "No", "no"/0 → "Nos", 2.5 → "Nos", M/1 and M/5 → "M", empty → "No"); PDF and XLSX for a quotation with lines: qty 1 No, qty 2 No, qty 3 M, a sub-item row → expected strings (read the PDF text / XLSX cells); a new line from a product with unit M carries M.
- Playwright `tests/test_ui_product_unit.py` on the throwaway `loop-test` stack ONLY: create a product with unit M; bulk import a file with a Unit column; select the "Cable" products → Set unit… M → list shows M; create a document by adding the M product and a "No" product → unit prefilled; change quantity 1 → 2 → the "No" line flips to "Nos" live, the M line stays M; download PDF/XLSX → same strings; open an older document → still renders.
- Existing suites (layout, labour, XLSX print, select-all, cart, duplicates) pass; `./scripts/verify.sh` passes.
- Evidence: `loop/evidence/iter-30-product-form-unit.png`, `iter-30-set-unit-bar.png`, `iter-30-document-units.png` (form with No/Nos/M), `iter-30-pdf-units.png`.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Product has a Unit (form, list, API); default No; normalized; validation | unit + Playwright |
| A2 | Bulk import Unit column + updated template | unit tests + Playwright |
| A3 | "Set unit…" bulk action on selected products, group-scoped | unit + Playwright |
| A4 | New document lines take the product's unit; manual lines default No | unit + Playwright |
| A5 | No/Nos by quantity everywhere (form live, detail, PDF, XLSX, sub-items); M and others unchanged | helper tables + PDF/XLSX text + screenshots |
| A6 | Migration single head; no regressions; data safety (tests only on `loop-test`) | `alembic heads`, `./scripts/verify.sh`, report states which stack |

## Out of scope — record under "Found but not fixed"
A managed list of units editable by the admin; unit conversion (M ↔ Ft); plural forms for other units (Set → Sets); changing the unit of existing documents' lines in bulk.

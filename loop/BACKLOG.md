# BACKLOG.md — the planner's memory of what the user asked for

The user gives fixes one at a time. Every request is written here the moment it arrives so nothing is lost.
Status: **QUEUED** (plan written in `loop/queue/`), **LIVE** (in `loop/PLAN.md`), **DONE** (accepted), **IDEA** (discussed, no plan yet).

| # | Request (user's words, short) | Status | Where |
|---|---|---|---|
| 1 | Department data separation: documents/products/customers/projects belong to the creator's group; users see only their group | DONE (iteration 19) | `loop/history/report-iter-19.md` |
| 2 | Superuser can move records between groups; "unassigned data" card | LIVE (iteration 20) | `loop/PLAN.md` |
| 3 | Concurrency safety: unique numbers, version check, idempotency, atomic saves | QUEUED | `loop/queue/iter-21-concurrency.md` |
| 4 | XLSX prints with Remark/Amount on a second page, only 5 columns on page 1 — must fit on one page wide | QUEUED | `loop/queue/iter-22-xlsx-print-fit.md` |
| 11 | Invoice/proposal documents export with the heading QUOTATION and "Quotation ID" — heading and ID label must follow the document type (PDF, XLSX, file names) | QUEUED (Part B of iteration 22) | `loop/queue/iter-22-xlsx-print-fit.md` |
| 10 | Labour cost per line item (tax on products only, labour untaxed; labour optional): Material + Labour column groups (unit price, total), SUB Total and Grand Total rows in the form, PDF and XLSX | QUEUED | `loop/queue/iter-23-labour-columns.md` |
| 12 | Select all on Products/Documents/Customers/Projects (selects the filtered rows, e.g. 10 of 100 by date) | LIVE (iteration 24) | `loop/PLAN.md` |
| 13 | Add selected (filtered) products to the cart in one click | QUEUED | `loop/queue/iter-25-bulk-add-to-cart.md` |
| 14 | Bug: group user ets (granted 2 City Mart projects) sees "No projects match"; Manage-user project list shows other groups' projects and is laid out inline | LIVE (iteration 26) | `loop/PLAN.md` |
| 15 | "Not a duplicate" for same-name products (remembered, no renaming, restore from Hidden pairs) | QUEUED | `loop/queue/iter-27-not-a-duplicate.md` |
| 16 | Small fixes: nginx header size with ~100 groups, cart kept on reload, 500 on no-price line item, `loop-test-down.sh` | QUEUED | `loop/queue/iter-28-small-fixes.md` |
| 17 | Global group switcher for the admin (replaces per-list group dropdowns) + dev Delete ALL buttons only for the admin, group-aware | LIVE (iteration 29) | `loop/PLAN.md` |
| 18 | Unit on products (M for cables etc.); documents take it; No/Nos by quantity; Unit column in bulk import; bulk Set unit… | LIVE (iteration 30) | `loop/PLAN.md` |
| 19 | Excel export: logo/banner/seals/heading too small — match the PDF proportions; fix #### total, clipped amount header, visible apostrophe on phone numbers | LIVE (iteration 31) | `loop/PLAN.md` |
| 20 | Excel MD seal must sit inside the last column (right of where iteration 31 put it) | LIVE (iteration 32) | `loop/PLAN.md` |
| 21 | Excel header banner bigger (625 × 140 px from column A, per the user's red box) | LIVE (iteration 33) | `loop/PLAN.md` |
| 22 | Excel (5 screenshots): banner 677×197 + company seal centred in last column + END USER block moved to the last column + Qty/Unit centred | LIVE (iteration 34) | `loop/PLAN.md` |
| 23 | Excel print: Customer Service footer on every page; Terms + Regards + MD seal together at the end, seal never cut by a page break | QUEUED | `loop/queue/iter-35-xlsx-print-footer-tail.md` |
| 24 | PDF/Excel: too much empty space around banner/seals — auto-trim blank margins, tighten header→title and Regards→seal gaps | LIVE (iteration 36) | `loop/PLAN.md` |
| 25 | Excel: 5 empty rows between header images and the title (reserve rows from fitted image height, not the box) | LIVE (iteration 37) | `loop/PLAN.md` |
| 26 | Excel opens with a repair message: "String properties from /xl/worksheets/sheet1.xml" (suspect: print footer from iteration 35) — waiting for the user's Excel error log | QUEUED — root cause found: whitespace-only rich-text runs in the signature cell (iteration 38) | `loop/queue/iter-38-xlsx-excel-repair-and-tail.md` |
| 27 | Excel: "Regards," at the top and big gap above the signature (same cause as #26) | QUEUED (iteration 38) | `loop/queue/iter-38-xlsx-excel-repair-and-tail.md` |
| 28 | Excel: centre the "No" (item number) column | LIVE (iteration 39) | `loop/PLAN.md` |
| 5 | Pin the Bulk import summary line + Import button in the modal | IDEA (iteration 15 pinned only heading + Close) | — |
| 6 | Remove the DEV-ONLY "Delete ALL" buttons before real production use | IDEA (steps in `loop/history/report-iter-15.md` §4) | — |
| 7 | Off-site copy of backups | IDEA (user has not chosen a destination) | — |
| 8 | VM-only group rule in `authz.py` / scripts `seed_city_mart.py`, `migrate_projects_to_group.py`, `test_group_isolation.py` (never committed) | superseded by #1 | — |
| 9 | Product duplicate check while typing (autocomplete) | IDEA (`/products/duplicate-check` exists) | — |

Recently DONE: letterhead banner with single logo/banner upload (17, 18), backup & restore (16), delete-all + sticky modal heading + success tick (15).

Small leftovers from iteration 19's report (not planned): 500 on a line item for a product with no price (`_process_items`), document-service logs silent after startup (alembic `fileConfig`), dev-only Delete ALL companies still wipes shared companies, VM must discard its local `authz.py` edit before pulling.

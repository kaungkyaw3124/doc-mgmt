# BACKLOG.md — the planner's memory of what the user asked for

The user gives fixes one at a time. Every request is written here the moment it arrives so nothing is lost.
Status: **QUEUED** (plan written in `loop/queue/`), **LIVE** (in `loop/PLAN.md`), **DONE** (accepted), **IDEA** (discussed, no plan yet).

| # | Request (user's words, short) | Status | Where |
|---|---|---|---|
| 1 | Department data separation: documents/products/customers/projects belong to the creator's group; users see only their group | LIVE (iteration 19, in progress on the user's PC) | `loop/PLAN.md` |
| 2 | Superuser can move records between groups; "unassigned data" card | QUEUED | `loop/queue/iter-20-move-between-groups.md` |
| 3 | Concurrency safety: unique numbers, version check, idempotency, atomic saves | QUEUED | `loop/queue/iter-21-concurrency.md` |
| 4 | XLSX prints with Remark/Amount on a second page, only 5 columns on page 1 — must fit on one page wide | QUEUED | `loop/queue/iter-22-xlsx-print-fit.md` |
| 11 | Invoice/proposal documents export with the heading QUOTATION and "Quotation ID" — heading and ID label must follow the document type (PDF, XLSX, file names) | QUEUED (Part B of iteration 22) | `loop/queue/iter-22-xlsx-print-fit.md` |
| 10 | Labour cost per line item (tax on products only, labour untaxed; labour optional): Material + Labour column groups (unit price, total), SUB Total and Grand Total rows in the form, PDF and XLSX | QUEUED | `loop/queue/iter-23-labour-columns.md` |
| 5 | Pin the Bulk import summary line + Import button in the modal | IDEA (iteration 15 pinned only heading + Close) | — |
| 6 | Remove the DEV-ONLY "Delete ALL" buttons before real production use | IDEA (steps in `loop/history/report-iter-15.md` §4) | — |
| 7 | Off-site copy of backups | IDEA (user has not chosen a destination) | — |
| 8 | VM-only group rule in `authz.py` / scripts `seed_city_mart.py`, `migrate_projects_to_group.py`, `test_group_isolation.py` (never committed) | superseded by #1 | — |
| 9 | Product duplicate check while typing (autocomplete) | IDEA (`/products/duplicate-check` exists) | — |

Recently DONE: letterhead banner with single logo/banner upload (17, 18), backup & restore (16), delete-all + sticky modal heading + success tick (15).

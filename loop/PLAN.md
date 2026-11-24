# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **29** |
| Goal | **(A) One global group switcher for the admin; (B) dev tools (Delete ALL) only for the admin.** The switcher filters Products, Documents, Customers and Projects at once; the Delete ALL buttons are superuser-only in the UI and on the server, and respect the chosen group |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user wants
"in filter, i want to do based on group. that will be great for admin. and dev tools will only show in admin." The planner recommended option A (one global switcher instead of four per-list dropdowns) and the user said "a is good do that". Screenshot: Products list has an **All groups** dropdown (iteration 20) next to Reset filters, and earlier `ets` (not admin) could see **Delete ALL projects (dev)**.

## Part A — global group switcher (superuser only)
- A selector at the top of the sidebar: **"Viewing: All groups ▾"** (superuser only; non-superusers see nothing new — they already see only their own groups). Options: **All groups**, one per group (name), **Unassigned**. Option labels show the record count of the **list currently open** (e.g. `City Mart (30)`), refreshed when the user changes page or the list reloads.
- It filters **Products, Documents, Customers, Projects at once** with the same rule the per-list dropdown uses today (`group_id === chosen`, `none` = no group). Implement it through the existing `tableConfig[table].extraFilters` / `applyFilterAndSort` mechanism so search, category, date filters, sorting, Select all, Add to cart and Move keep working on exactly what is shown. **Remove the four per-list "All groups" dropdowns** (`<table>-group-filter`) — the switcher replaces them; Reset filters no longer touches the group (the switcher is global; it has its own reset by choosing "All groups").
- Changing the switcher clears any selection (iteration 24 rule: a changed filter clears it).
- **Remembered** in `localStorage` per username, every access in try/catch, falls back to All groups; if the remembered group no longer exists, fall back to All groups. Cleared on logout.
- **Create defaults**: while a group is chosen, the superuser's "New product / document / customer / project" forms pre-select that group in their existing group picker (still changeable). With All groups / Unassigned nothing is pre-selected.
- A small clear label on each list header while a group is chosen, e.g. "Showing: City Mart" so the admin never forgets a filter is on.
- Group isolation unchanged: this is a view filter only; server scope rules stay as they are.

## Part B — dev tools only for the admin
- **UI**: the five **Delete ALL … (dev)** buttons (products, documents, customers, projects, companies) render **only for a superuser** AND when not production. `GET /products/dev-config` (and the document-service equivalent if any) returns `delete_all_enabled` true only for a superuser (use `X-Is-Superuser`) and non-production.
- **Server**: all five delete-all endpoints (`DELETE /products|/documents|/customers|/projects|/companies`) answer **403** to anyone who is not a superuser, in addition to the existing production block and view-only rule. Today they delete "everything in the caller's groups" for a normal editor — that goes away. Keep `X-Has-Document-Delete` rule for documents as is.
- **Group-aware**: the four group-scoped endpoints accept optional `?group_id=<uuid>|none`: delete only that group's rows (`none` = unassigned). Without the parameter they delete everything (as now). Companies are shared (no group): the companies button ignores the switcher.
- **UI wording and safety**: when a group is chosen the button reads **"Delete ALL in <Group> (dev)"** and sends `group_id`; with Unassigned **"Delete ALL unassigned (dev)"**; with All groups **"Delete ALL (dev)"**. Add a **confirm dialog** (it had none by earlier request; with groups now involved it must say exactly what will go: "Delete ALL products in City Mart? This cannot be undone."). Success banner "Deleted N …" as today.
- Do NOT remove the buttons (item #6 in BACKLOG, later); do not change production behaviour (still blocked).

## Tasks
1. Inspect: the four `<table>-group-filter` creators and `refreshMoveControls` (`web/js/app.js` ~3820–3950), `tableConfig` and `applyFilterAndSort`, the sidebar markup in `web/index.html`, `initDeleteAll` / `DELETE_ALL` (~5180), the five DELETE endpoints and `dev-config`; list in the report.
2. Part A: switcher component, wiring into the four lists, remove per-list dropdowns, localStorage, create defaults, header label, counts.
3. Part B: server 403 + `group_id` param; dev-config; UI visibility, labels, confirm.
4. Update the tests that use the old per-list `.group-filter` selectors (`test_ui_group_move.py`, `test_ui_select_all.py`, others — find with grep) and the existing delete-all tests (`test_delete_all.py`, `test_delete_all_products.py`, `tests/test_ui_delete_all.py`) to the new behaviour.
5. Tests + evidence.

## Tests
- Playwright `tests/test_ui_group_switcher.py` on the throwaway `loop-test` stack ONLY: two groups with different products/documents/customers/projects → admin sees the switcher; choose City Mart → all four lists show only City Mart rows and the header label; Unassigned shows only unassigned; All groups shows all; reload → choice remembered; log out and in as another user → no remembered value; a group user has **no** switcher and no per-list group dropdown; switching clears the selection; "New product" form pre-selects the chosen group; counts in the labels match the rows.
- API: non-superuser `DELETE` on all five endpoints → 403 (even when editor with delete rights); superuser without param deletes all; with `group_id=<A>` deletes only A (B untouched); `group_id=none` only unassigned; bad uuid → 422; production → 403 still.
- Playwright `tests/test_ui_delete_all.py` updated: admin sees buttons with the group wording; confirm dialog text; cancel does nothing; accept deletes only that group's rows; group user sees none of the buttons.
- All existing suites pass; `./scripts/verify.sh` passes.
- Evidence: `loop/evidence/iter-29-switcher-sidebar.png`, `iter-29-switcher-products-citymart.png`, `iter-29-delete-all-confirm.png`.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Switcher filters all four lists at once; All / group / Unassigned; remembered per user; admin only | Playwright |
| A2 | Per-list group dropdowns removed; search/date/select/cart/move still work with the switcher | Playwright + updated suites |
| A3 | Create forms pre-select the chosen group; header label shown | Playwright |
| A4 | Delete ALL buttons visible only to a superuser; non-superuser gets 403 from the server | Playwright + API tests |
| A5 | Delete ALL respects the chosen group (`group_id`), confirm dialog names the group; production still blocked | API + Playwright |
| A6 | No regressions; data safety (tests only on `loop-test`) | `./scripts/verify.sh`, report states which stack |

## Out of scope — record under "Found but not fixed"
Removing the Delete ALL buttons entirely (BACKLOG #6); a switcher for non-superusers who belong to several groups (note it as an idea: they may want one too); per-group counts across all four lists at once.

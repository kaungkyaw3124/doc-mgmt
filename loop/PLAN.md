# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **20** |
| Goal | Superuser can **move** documents, products, customers and projects from one group to another (one or many at a time) and sees/assigns unassigned data in the UI |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user wants
"Admin can change the document, product, customer, project group from one to another."

## Decisions already made
- **Who**: superusers only (group admins cannot move data across groups).
- **API** (document-service and catalogue-service): `POST /{entity}/{id}/move {group_id}` and `POST /{entity}/move {ids:[…], group_id}`; the target must be an existing active group; 404 for unknown ids; atomic per request (all or nothing); writes an audit entry (who, from, to).
- **Link rules** (no cross-group links must hold after a move):
  - moving a **document** → its customer and project are moved with it by default (checkbox "also move its customer and project", on); if unchecked and they are in another group the move is refused with a clear message; line items keep their snapshots, products are never moved implicitly;
  - moving a **customer** or **project** that is used by documents of the old group → refuse unless "also move its documents" is ticked (then they move together);
  - moving a **product** → allowed; existing documents keep their snapshot lines.
- **Search**: moved rows are re-indexed with the new group immediately.
- **UI**: in each of the four lists a "Group" column and a group filter (superuser), a "Move to group…" action on a row and for selected rows (checkbox selection), a small modal (target group select + the link checkbox, shows what will be moved, e.g. "1 document, 1 customer, 1 project"), success tick/error cross from iteration 15.
- **Unassigned data card** (Admin → Groups): counts of unassigned documents/products/customers/projects, with an "Assign all to group…" button using the iteration-19 endpoint.
- Non-superusers never see move controls and get 403 on the endpoints.

## Notes from the planner
- Iteration 19 is accepted (PASS); its report is in `loop/history/report-iter-19.md`. Group ownership (`group_id`), the scope helper, gateway headers `X-Group-Ids` / `X-Is-Superuser`, search filtering and `scripts/assign-unassigned.sh` already exist — build on them, do not redo them.
- Fix the small items iteration 19 listed under "Found but not fixed" **only if** they touch code you change anyway; otherwise leave them (they are in `loop/BACKLOG.md`).
- Tests run on the throwaway `loop-test` stack only (LOOP.md "Protect the user's data").

## Tests (throwaway stack only)
Moves for all four entities (single + bulk), link-rule refusals and "move together", audit entries, search re-index (the old group stops seeing it, the new group finds it), non-superuser 403, unassigned card counts + assign, Playwright flow (superuser moves a document with its customer/project from Operations to CityMart; the Operations user no longer sees it, the CityMart user does). Evidence screenshots. All existing tests pass.

## Acceptance checks
A1 move API + rules · A2 audit + search consistency · A3 UI flow · A4 unassigned card · A5 permissions · A6 no regressions/data safety.

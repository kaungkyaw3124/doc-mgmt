# report.md — Iteration 24

## 1. Summary
- **Status**: DONE
- **Goal**: Select all on every list. Products, Documents, Customers and Projects each get a checkbox column for every user, plus a "Select all" header checkbox that selects exactly the rows currently shown (after the search, category, date and group filters), and a selection bar showing the count.
- **Ponytail skill loaded**: yes
- **Branch / commit**: `dev` @ (this commit)
- **One-paragraph summary**: All four lists now have a narrow first column with a row checkbox for every user and a header "Select all shown" checkbox. One shared helper in `web/js/app.js` keeps a `Set` of ids per list. `applyFilterAndSort` (which every filter, search, reload and sort already goes through) builds a key from all filter values. If the key changes, or the list was reloaded (new cache array), the Set is cleared. Sorting keeps it. The header checkbox selects or clears exactly the rendered rows. It shows indeterminate for a partial selection and is disabled on an empty list. A selection bar (`N selected`, Clear, and for superusers "Move selected to group…") appears above the table when at least one row is ticked. It is sticky and `aria-live="polite"`. The superuser's checkbox moved out of the Group cell. "Move selected to group…" now reads the shared Set. All of it is covered by Playwright on the throwaway loop-test stack.

## 2. Baseline (before any change)
```
loop-test: COMPOSE_PROJECT_NAME=loop-test COMPOSE_OVERRIDE=infra/docker-compose.loop-test.yml GATEWAY=http://localhost:18080 ALLOW_DESTRUCTIVE_TESTS=1 ./scripts/verify.sh --keep
======================= 87 passed, 18 skipped in 49.28s ========================
verify.sh: PASSED (stack left running: --keep)
```

## 3. Acceptance checks
| # | Check | Result | Evidence |
|---|---|---|---|
| A1 | Select all = exactly the filtered rows (10 of 100) on Products | PASS | `test_select_all_selects_exactly_the_filtered_products`: creates 100 products, backdates 10 to a random day in Postgres (`UPDATE 10`), filters by that date → 10 rows → Select all → "10 selected", ticked ids == those 10. Then a token search → 100 rows → "100 selected" == all 100 ids → header again → 0, bar hidden. `loop/evidence/iter-24-select-all-products.png` |
| A2 | Same on Documents, Customers, Projects | PASS | `test_select_all_on_documents_customers_projects_and_move_selected`: on each list, Select all count == rendered rows. On customers and projects (filtered by token) the ticked ids == the 3 created rows. |
| A3 | Filter/search/reset clears; sort keeps | PASS | Products test: sort by name keeps "10 selected" with the same ids. Clearing the date filter clears the selection and hides the bar. Reset filters clears. The Clear button clears. After the superuser's move, the reload clears (bar hidden). |
| A4 | Header states + a11y labels | PASS | `[checked, indeterminate, disabled]` asserted: `[True, False, False]` all ticked, `[False, True, False]` after unticking one (`iter-24-select-all-indeterminate.png`), disabled on an empty result. `aria-label="Select all shown"` / `"Select row"` asserted. |
| A5 | Every user sees the column; superuser Move works; isolation | PASS | Ops group user (not a superuser) sees the column and Select all on Documents, Customers and Projects. The picked ids are a subset of what `/documents` etc. return for that user. The other group's customer never appears. Move button hidden for them. Superuser: Select all 3 → untick 1 → Move selected → preview "2 customers" → after the move exactly those 2 are in CityMart and the third is still in Operations. `test_ui_group_move.py` passes. |
| A6 | No regressions; data safety | PASS | verify.sh 87 passed, 19 skipped (the 19th skip is the new UI test, since the verify venv has no Playwright). Full `tests/test_ui_*.py` in the Playwright container: 37 passed, 8 skipped. All on the loop-test stack. |

Performance: un-select-all of 100 rendered rows measured < 100 ms in the test (one delegated `change` handler per table, no per-row listeners). 500 rows was not measured.

## 4. Changes made
| File | Change | Why |
|---|---|---|
| `web/js/app.js` | modified | `selection` Sets, `selectCell`, `syncSelection`, and per-table bar + delegated handlers. `applyFilterAndSort` clears the selection when the filter key changes or the cache changes. `groupCell` lost its checkbox. "Move selected" reads the Set and lives in the bar. Empty/error `colspan`s +1 on the four lists. |
| `web/index.html` | modified | Header checkbox column on the four tables. Skeleton `colspan`s now match the column count (13/11/9/8). |
| `tests/test_ui_select_all.py` | added | A1–A5 Playwright tests (destructive marker, loop-test only). |
| `tests/test_ui_group_move.py` | modified | Selector `.move-select` → `.row-select` (the checkbox moved to the shared column). Same assertions. |
| `tests/test_ui_customer_organization.py` | modified | Organization cell is now `td` index 2 (new first column). Same assertions. |

## 5. Test → fix log
| Attempt | Failing check/test | Error (1 line) | Root cause | Fix |
|---|---|---|---|---|
| 1 | `test_select_all_on_documents_customers_projects_and_move_selected` | admin's lists showed "Something went wrong." | nginx: `upstream sent too big header ... subrequest: "/_verify"`. The loop-test data folder had never been wiped and held 104 groups, so the superuser's `X-Group-Ids` header grew past nginx's 4k proxy buffer. Not caused by this change. | Reset the throwaway stack (`down -v` + `rm -rf infra/data-loop-test`, as its header says) and reran everything fresh. See §7. |
| 2 | `test_ui_customer_organization.py` | expected org text, got the name | test picks the Organization cell by index. The new first column shifts it by one. | index 1 → 2 |

## 6. Final verification output
```
loop-test (fresh): ./scripts/verify.sh --keep
======================= 87 passed, 19 skipped in 45.98s ========================
verify.sh: PASSED (stack left running: --keep)
tests/test_ui_*.py (mcr.microsoft.com/playwright/python:v1.65.1-noble, --network host, docker socket mounted for the psql backdate, loop-test):
37 passed, 8 skipped in 130.70s
```

## 7. Found but not fixed (out of scope)
- **Superuser locked out once there are ~100 groups**: auth-service `/verify` sends every group id in `X-Group-Ids` (`services/auth-service/app/routers/auth.py:301`). Past nginx's default `proxy_buffer_size` (4k), every API call from that user returns 500 (`upstream sent too big header`). Found on the loop-test stack with 104 leftover test groups. Fix: raise `proxy_buffer_size`/`proxy_buffers` in the `/_verify` location (e.g. 16k), or don't send the list for superusers (they see everything anyway).
- **loop-test data was not wiped by previous iterations** despite the reports saying `down -v`: `down -v` does not remove the bind-mounted `infra/data-loop-test/` folders. This time I removed them with `docker run --rm -v .../infra:/infra alpine rm -rf /infra/data-loop-test` (the files belong to the container user). A `scripts/loop-test-down.sh` would make that one step.
- "Sort-reset" in the plan: I treated every sort click, including the third click that removes sorting, as sorting, so it keeps the selection. Only filters, search, Reset, Clear and reloads clear it.
- Ideas (out of scope): shift-click range select, bulk delete, a selection that survives filter changes, saved selections.

## 8. Blockers / decisions needed
None.

## 9. Suggestions for the next iteration
- Iteration 25 "Add selected to cart": append a button to `products-selection-bar` and read `[...selection.products.ids]`. Nothing else is needed from the selection side.
- Fix the `X-Group-Ids` header size (§7) before real deployments grow their group count.

Data safety: verify.sh and all Playwright tests ran only on the throwaway `loop-test` stack (ports 18080/18443). It was torn down with `down -v` and its `infra/data-loop-test/` folder was deleted. The user's stack and `infra/data/` were not touched by any test.

App running at http://localhost:8080 (login: admin / CHANGE_ME, local only)

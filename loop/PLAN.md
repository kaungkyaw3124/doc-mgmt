# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **27** |
| Goal | **"Not a duplicate" for products.** On the "Products — possible duplicates" page the user can mark a "Same name" group as different products; it stops showing (until a product in it changes), without renaming anything |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user wants
Screenshot: "Same name (10)" lists products with the same name but different descriptions (Edge AI Device: Jetson Orin Nano vs Orin NX/AGX; External Hard Disk 1 TB vs Seagate 4TB). They are NOT duplicates, but the warning returns every time. The user wants to tell the app "not a duplicate" and have it remembered. (They suggested renaming to name-(NO); the planner recommended a dismissal instead — renaming changes names printed on quotations — and the user agreed with "ok".)

## Decisions already made
- **Button "Not a duplicate"** on every group in the **Same name** section of the duplicates page (all products in the group are declared different from each other). No automatic renaming. Exact duplicates (same name + same description) can NOT be dismissed and stay blocked/listed as today.
- **Storage** (catalogue-service, new Alembic migration, single head, guarded/idempotent like the others, SQLite-compatible for tests): table `product_duplicate_dismissals` — `id`, `group_id` (nullable, same meaning as products), `product_a_id`, `product_b_id` (stored in sorted order, unique together), `fingerprint_a`, `fingerprint_b` (hash of `norm(name)+"|"+norm(description)` of each product **at dismissal time**), `created_by` (username), `created_at`. No FK across services; FK to products with ON DELETE CASCADE.
- **A pair counts as dismissed only while both products' current fingerprints equal the stored ones.** If either product's name or description is edited later, the pair is no longer dismissed and can show again (it is checked again).
- **Groups with 3+ products**: the button dismisses every pair in that group. A product added later with the same name forms new, undismissed pairs, so the group reappears showing only the new product plus the ones it clashes with (or the whole group — pick the simpler; document the choice in the report).
- **`GET /products/duplicates`** hides dismissed pairs from `same_name_groups` (a group is shown only if it still has 2+ products connected by undismissed pairs). Response unchanged otherwise; add a `hidden_count` number.
- **New endpoints** (group-scoped like everything else; edit access required; `404` for out-of-scope products; superuser may act on any group):
  - `POST /products/duplicates/dismiss` body `{product_ids: [...]}` (2+ ids, all in the same group) → `{dismissed_pairs: N}`; idempotent.
  - `GET /products/duplicates/dismissed` → the hidden pairs with product briefs.
  - `DELETE /products/duplicates/dismissed/{id}` → restore (the pair can show again).
- **UI** (`web/js/app.js`, `index.html`): button in each Same-name group header ("Not a duplicate" with a tooltip "These are different products — stop warning me"); success tick; list refreshes. A collapsed section **"Hidden pairs (N)"** at the bottom with **Restore** per pair. The Products page's "Possible duplicates" link/count (if it shows a count) must use the new filtered numbers.
- **Out of scope / explicitly NOT done**: the duplicate **warning while typing a new product** and the **bulk-import "same name" warning** compare a not-yet-saved product with the catalogue, so there is no pair to dismiss — leave both as they are (they are only warnings). "Similar names" stays as is. Do not touch exact-duplicate blocking.
- Group isolation (iteration 19): dismissals are per group; City Mart's decisions never hide Operation's warnings.

## Tasks
1. Inspect `core/duplicates.py` (`groups()`), `routers/products.py` duplicate endpoints (~lines 260–300), the duplicates page in `web/js/app.js`; list findings.
2. Migration + model + schemas; fingerprint helper in `duplicates.py`; filter dismissed pairs in `groups()` / the endpoint; the three new endpoints.
3. UI: button, Hidden pairs section, restore.
4. Tests + evidence.

## Tests
- Unit (`services/catalogue-service/tests/test_duplicate_dismissals.py`): two same-name products → listed; dismiss → not listed, `hidden_count` 1; edit one description → listed again; restore → listed; 3 products same name → dismiss group → none listed, add a 4th → shows with the new one; exact duplicates are not dismissable (422/ignored) and still listed; idempotent dismiss; ids from two different groups → 422.
- Group scope: group user cannot dismiss/restore another group's products (404); dismissal in group A does not hide the same pair names in group B.
- Migration: single head, idempotent on repeat (existing `test_migrations_idempotent` style).
- Playwright `tests/test_ui_not_duplicate.py` on the throwaway `loop-test` stack ONLY: create the screenshot's two "External Hard Disk" products → duplicates page lists them → Not a duplicate → gone, tick shown → reload page → still gone → Hidden pairs (1) → Restore → back.
- Existing suites (incl. `test_duplicates.py`, `test_ui_duplicate_products.py`) pass; `./scripts/verify.sh` passes.
- Evidence: `loop/evidence/iter-27-duplicates-before.png`, `iter-27-duplicates-hidden.png`.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Dismissed same-name group no longer listed; survives reload | unit + Playwright |
| A2 | Editing name/description brings the warning back | unit |
| A3 | Restore from "Hidden pairs" works | unit + Playwright |
| A4 | Exact duplicates unaffected and not dismissable | unit |
| A5 | Per-group isolation and edit-access rules | scope tests |
| A6 | Single migration head, no regressions, data safety (tests only on `loop-test`) | `alembic heads`, `./scripts/verify.sh`, report states which stack |

## Found but not fixed (record in the report)
Dismissing from the typing/bulk-import warnings (needs a different design); "Similar" dismissals.

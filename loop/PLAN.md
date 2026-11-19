# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **25** |
| Goal | **Add selected products to the cart in one click.** On the Products list, filter (for example by date), Select all, then **Add selected to cart (N)** |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user wants
"filter with date and select all and add to cart" — e.g. 100 products, date filter leaves 10, Select all, Add to cart puts those 10 in the cart. Builds on iteration 24 (shared selection + selection bar).

## Decisions already made
- Button **"Add to cart (N)"** in the Products selection bar (all users, not only superusers). Cart is products-only; Documents/Customers/Projects get no cart button.
- Each selected product is added with **quantity 1**. A product **already in the cart is skipped** (its quantity is NOT increased) — different from the single "+ Add to cart" link, which still increments. Reuse `cartItems` / `updateCartBadge()`; do not duplicate cart logic (extract a helper if useful).
- Product data comes from `cachedProducts`; selected ids missing from the cache (e.g. other pages not loaded) must be fetched or the cache must already hold the whole filtered list — verify which is true and handle it so **all N selected** are added, never fewer silently.
- **Limit: 200 products per bulk add.** Above that, add nothing and show a clear message ("Select up to 200 at a time; N selected"). The selection bar shows the count so the user can narrow the filter.
- **Result message** (success tick / banner as used elsewhere): "10 added to cart" or "8 added, 2 already in cart". After adding, the selection is cleared and the cart badge updates (badge counts total quantity as today).
- Group isolation: only products the user can see exist in the list, so only those can be added; creating a document from the cart behaves as today.
- No sound; reuse the existing success tick.

## Tasks
1. Inspect `addToCart`, `cartItems`, `cachedProducts`, how the Products list is loaded/paged and how the cart creates a document (`web/js/app.js` ~2293–2480).
2. Add the bulk action to the iteration-24 selection bar; implement skip-duplicates and the 200 cap; messages.
3. Tests + evidence.

## Tests
- Playwright `tests/test_ui_bulk_cart.py` on the throwaway `loop-test` stack ONLY: 100 products, date filter → 10 → Select all → Add to cart (10) → cart modal lists exactly those 10, quantity 1, badge = 10 → repeat with 5 already in the cart → "5 added, 5 already in cart", their quantities unchanged.
- 201 selected → nothing added, message shown.
- Select all with no filter (100) → all 100 added.
- Empty selection: the button is hidden.
- Bulk-added cart → "create document from cart" still works with the right lines (use the existing cart→document flow).
- Group user only ever carts products from their own group.
- All existing tests pass; `./scripts/verify.sh` passes.
- Evidence: `loop/evidence/iter-25-bulk-cart.png` (selection bar + cart with 10 items).

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | Filter → Select all → Add to cart adds exactly the filtered products, qty 1 | Playwright + screenshot |
| A2 | Already-in-cart products skipped, quantities unchanged, message correct | Playwright |
| A3 | 200 cap message; nothing added above it | Playwright |
| A4 | Cart → document flow works with bulk-added items; group isolation intact | Playwright + suites |
| A5 | No regressions; data safety | `./scripts/verify.sh`, report states which stack |

## Out of scope — record under "Found but not fixed"
Per-item quantity choice before adding, adding documents/customers to a cart, saved carts.

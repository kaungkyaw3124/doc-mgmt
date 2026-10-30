# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **13** |
| Goal | Repeated blocks in forms are clearly separated, so the user can see where one MD / line item / sub-item ends and the next begins |
| Attempt limit | 5 per failing check |
| Branch | whichever the session opened on (`dev` / `security/auth-hardening`) |
| Status | READY FOR CLAUDE CODE |

## What the user wants
In the Company form's **Managing Directors** list, two MDs run together with
nothing between them. The user drew a line between them in a screenshot and
asked for the same kind of separation "in this MD and others".

Today:
- `.director-row` (`web/js/app.js` ~line 1448, container `#director-rows` in
  `web/index.html` ~line 1211) has **no CSS at all**, so MDs just stack.
- `.line-item-row` (document line items, product sub-items) has only a faint
  `1px dashed var(--hairline)` bottom border (`web/index.html` ~line 328).

## Tasks (UI only: `web/index.html` CSS + small markup changes in `web/js/app.js`)
1. **Managing Directors**: give each `.director-row` a clear block:
   - A small header line **"MD 1"**, **"MD 2"**… (numbered by position, renumbered
     after Remove) on the left, with the existing **Remove** link moved to the
     right of that header.
   - A separator between MDs: a **solid 1px line in a visible but calm colour**
     (darker than `--hairline`; use an existing CSS variable if one fits, e.g.
     `--border`), with ~16px space above and below. No line above the first MD
     and none after the last.
   - Optional: a very light background tint on every second MD, only if it looks
     clean with the existing palette. The separator line is the must-have.
2. **Document line items** (New/Edit document form) and **product sub-items**
   (product form): make the separator between rows more visible in the same style
   as the MDs (solid, same colour). Keep the compact one-row layout; don't add a
   numbered header there, since line items already have their own fields.
3. Check for any other repeated-block lists in forms (grep `className = '…-row'`
   and similar) and apply the same separator if they have the same problem. List
   what you changed in the report.
4. Use one shared CSS rule or variable for the separator colour and spacing, so
   all repeated blocks look the same.
5. Don't change field names, behaviour, or the API. Works on narrow screens (the
   MD rows already use `flex-wrap`).

## Tests
- Playwright `tests/test_ui_row_separators.py`:
  1. Company form with 2 MDs: the headers read "MD 1" and "MD 2"; the second
     `.director-row` has a visible top border (computed style: width ≥ 1px, colour
     not transparent) and the first has none; Remove on MD 1 renumbers the rest to "MD 1".
  2. Document form with 2 line items: the separator between them is visible (computed style).
  3. Saving the company with 2 MDs still works (no behaviour change).
- All existing UI tests still pass.

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | New UI tests pass | `pytest tests/test_ui_row_separators.py -v` |
| A2 | No regressions | `./scripts/verify.sh` passes (incl. existing UI tests) |
| A3 | Proof | `loop/evidence/iter-13-md-rows.png` (Company form with 2–3 MDs), `iter-13-line-items.png` (document form with 3 line items), `iter-13-sub-items.png` |

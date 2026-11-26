# PLAN.md — Current Iteration

| Field | Value |
|---|---|
| Iteration | **38** |
| Goal | **Excel: fix the "We found a problem with some content … String properties from /xl/worksheets/sheet1.xml" repair message AND the gap between "Regards," and the signature** — both come from the same cause (whitespace-only rich-text runs in the signature cell) |
| Attempt limit | 5 per failing check |
| Status | READY FOR CLAUDE CODE |

## What the user reported
1. Excel opens the quotation XLSX with a repair dialog: *"Repaired Records: String properties from /xl/worksheets/sheet1.xml part"* (screenshots in the conversation; appeared after iteration 35).
2. Screenshot `loop/evidence/user-iter-38-xlsx-tail-gap.png`: in Excel "Regards," sits at the **top**, level with the "Terms and Conditions" heading, while the MD signature starts ≈ 120 px lower; the user marks the empty strip below the Terms lines and says *"remove that small space too"*. Expected: "Regards," right after the last Terms line and the visible signature directly under it (gap ≈ 6–10 px), no dead rows below.

## Root cause (found by generating the file and reading `xl/worksheets/sheet1.xml`)
Since iteration 35 the Terms / "Regards," / MD seal share one row; the last-column cell is a `CellRichText` made of **spacer runs that contain only whitespace** (`export_quotation.py` ~lines 575–585):
```
<r>…<t>&#160;\n</t></r>                      <- nbsp + newline, no xml:space="preserve"
<r>…<t>\n\n\n\n\n\n\n\n\n\n\n\n\n</t></r>   <- 13 newlines only, no xml:space="preserve"
<r>…<t>Regards,</t></r>
```
openpyxl writes `xml:space="preserve"` for runs like `"\nItem 1 …"` but **not for runs that are whitespace-only** (Python's `str.strip()` also removes the non-breaking space). Excel reads such a run as an invalid/empty string property: it shows the repair dialog and drops the run, so the spacer lines vanish — which is exactly why "Regards," jumps to the top while the seal (placed at a computed y offset) stays low. LibreOffice keeps the spacers, which is why the earlier LibreOffice tests passed.

## What to change (`services/document-service/app/core/export_quotation.py`, the tail block)
- **Never emit a rich-text run whose text is empty or whitespace-only (`text.strip() == ""`).** Fold spacing into a run that has visible text: build the last-column cell as ONE bold 10 pt run `"\n" * k + "Regards,"` (leading newlines preserved because the run also contains visible text — verify the XML shows `xml:space="preserve"`), with `k` chosen so "Regards," lands on the line right after the last Terms line (the Terms cell has a 12 pt heading line + n lines at 10 pt; compute `k` from the line count, one extra line for the 12 pt heading). No nbsp-only runs.
- Same rule everywhere else the exporter builds rich text (`CellRichText`/`TextBlock` for item cells, phone/contact cells, Terms): audit them; any run that is whitespace-only or has leading/trailing whitespace must carry `xml:space="preserve"` (test below enforces it) — if openpyxl does not write the attribute for a given run, restructure the run or post-process the XML (as a last resort patch the saved sheet XML in a small function applied before returning the BytesIO) so Excel never sees an unpreserved significant space.
- Re-check the position math afterwards: the seal's `y` offset (`regards_end + 4`) must equal the real bottom of the "Regards," line, so the visible signature sits directly under it; the tail row height = Terms height vs. seal bottom (whichever is larger) + 6 pt, nothing more; the Customer Service block (on screen only) starts one row after the tail row.
- Keep everything else: tail stays one row (never split across pages), MD seal in the last column (iteration 32), print footer (iteration 35), trimmed images (36), reduced header gap (37).
- If the footer text itself turns out to produce a repair message too (see the new XML check), fix it in the same iteration; but the evidence points to the signature cell.

## Tests
- **New XML hygiene test** (`services/document-service/tests/test_xlsx_excel_safe.py`): generate the XLSX for several documents (with/without director seal, with/without director, letterhead + seal, labour on, 45 items, multi-line terms, `&` in names, long Myanmar/Unicode text) and scan **every** `<t>` element in `xl/worksheets/sheet*.xml` and `xl/sharedStrings.xml`: (a) no `<t>` whose text is empty or whitespace-only (after `str.strip()`), (b) any `<t>` whose text starts or ends with whitespace (incl. `\n`, nbsp) has `xml:space="preserve"`, (c) no control characters other than tab/LF/CR, (d) header/footer strings ≤ 255 chars per section with balanced codes (no `&` followed by a digit and a digit-leading text). Run it for the pre-fix code to prove it fails (record that in the report) and for the fixed code.
- Layout: with the fixed XML, the position of "Regards," (line index inside the cell) equals the number of Terms lines + heading; the seal top ≈ Regards bottom + 4 px (±3), and no empty rows between the signature bottom and the end of the tail row beyond 6 pt (compute from row heights).
- LibreOffice render of the long-case quotation: "Regards," below the last Terms line (not at the heading line) and the seal directly under it; `test_xlsx_print_setup.py`, image-size, labour, units, footer tests pass.
- Evidence: `loop/evidence/iter-38-xlsx-tail.png` (LibreOffice) and the XML snippet of the signature cell before/after in the report.
- Existing suites pass; `./scripts/verify.sh` passes (loop-test stack / in-process only).

## Acceptance checks
| # | Check | How to verify |
|---|---|---|
| A1 | No whitespace-only or unpreserved significant-space `<t>` anywhere in the XLSX | XML hygiene test (fails before, passes after) |
| A2 | "Regards," on the line after the last Terms line; signature directly under it; no extra dead rows | layout test + LibreOffice render |
| A3 | Excel-specific rules documented in `docs/` (why whitespace-only runs break Excel) | docs diff |
| A4 | No regressions; one page wide; tail stays together; data safety | existing suites, `./scripts/verify.sh` |

## Out of scope — record under "Found but not fixed"
Anything in the PDF; a real-Excel automated check is impossible here — say so in the report and list the XML rules checked instead. Ask the user to confirm in real Excel that the repair dialog is gone.

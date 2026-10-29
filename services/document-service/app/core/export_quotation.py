from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XLImage
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont

THIN = Side(style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEADER_FILL = PatternFill("solid", fgColor="EFEFEF")

_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _safe_str(value):
    """
    Neutralizes spreadsheet formula injection: openpyxl (and Excel/Sheets on
    open) treats any string cell value starting with =, +, -, or @ as a
    live formula. Every string here can originate from another user's input
    (product description, customer address, remarks, terms...), so prefix
    those with a straight quote to force plain text instead of letting a
    crafted value like "=cmd|'/c calc'!A1" execute when the export is opened.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_TRIGGER_CHARS):
        return "'" + value
    return value


def _name_and_description_rich_text(name, description, size=10):
    """
    Builds a single cell value with the name in bold and the description
    (if any) on the line below in regular weight — openpyxl can't mix
    formatting within a plain string, so this uses its "rich text" cell
    support instead. Both pieces still go through _safe_str first, since
    rich text doesn't change how Excel decides whether a cell is a formula
    (that's based on the cell's first character either way).
    """
    name = _safe_str(name or "")
    bold_font = InlineFont(rFont="Arial", sz=size, b=True)
    if not description:
        return CellRichText(TextBlock(bold_font, name))
    normal_font = InlineFont(rFont="Arial", sz=size)
    return CellRichText(
        TextBlock(bold_font, name),
        TextBlock(normal_font, "\n" + _safe_str(description)),
    )

DEFAULT_TERMS = (
    "Price Validity: 30 Days\n"
    "Payment Lead Time: 35% on Agreement, 65% on delivery\n"
    "Lead Time: 30 to 45 days after payment confirmation\n"
    "Delivery: DDP\n"
    "Warranty: 2 years from the date of delivery\n"
    "\n"
    "Important Note:\n"
    "1. Price quoted includes shipping charge.\n"
    "2. Commercial tax and local delivery charges are excluded unless stated.\n"
    "3. Maintenance, installation, configuration charges and other technical services are not included.\n"
    "4. Order cancellation fees may apply; cancellation is not permitted once delivery has commenced.\n"
    "5. Customer is responsible for any bank transaction charges."
)


def generate_quotation_xlsx(document, customer, items_with_product, company=None, logo_bytes=None, seal_bytes=None, logo_mime="image/png", seal_mime="image/png", director=None, director_seal_bytes=None, director_seal_mime="image/png") -> BytesIO:
    """
    items_with_product: list of (LineItem, product_dict_or_None, sub_items_list) tuples.
    company: dict with keys name/position/address/contact_no/support_email,
              or None if no company profile has been set up yet. Its name
              goes on the Supplier heading line ("SUPPLIER: <name>"); its
              position/address/contact_no are only fallbacks for the
              director's own, and support_email appears only in the
              Customer Service footer.
    director: dict with keys name/address/contact_no/email/position — the
              selected Managing Director. The Supplier block's rows
              (Name / Position / Address / Contact No) describe this MD,
              each falling back to the company's value when the MD's is
              empty; it also drives the signature block at the bottom.
              None if no director was chosen on this document (Name then
              shows "—").
    logo_bytes / seal_bytes: raw image bytes to embed, or None. SVGs are
    skipped here (not embedded) — openpyxl's image support goes through
    PIL, which can't rasterize SVG; the PDF export handles SVG logos fine
    since browsers/WeasyPrint render SVG natively.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Quotation"

    col_widths = [8, 55, 8, 8, 16, 16, 30]
    for i, w in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    row = 1

    # Header order matches the PDF export (quotation.html): logo/seal row
    # FIRST, then the QUOTATION title below it — not side by side. An
    # anchored image floats independently of cell content, so if the
    # title were written into row 1 (the same row the images anchor to),
    # it would render overlapping/beside the images instead of clearly
    # above them; reserving dedicated rows for the images before writing
    # the title avoids that.
    has_header_image = bool((logo_bytes and logo_mime != "image/svg+xml") or (seal_bytes and seal_mime != "image/svg+xml"))

    if logo_bytes and logo_mime != "image/svg+xml":
        try:
            img = XLImage(BytesIO(logo_bytes))
            img.width = 90
            img.height = 90
            ws.add_image(img, f"A{row}")
        except Exception:
            pass  # bad/unsupported image format shouldn't block the whole export

    if seal_bytes and seal_mime != "image/svg+xml":
        try:
            seal_img = XLImage(BytesIO(seal_bytes))
            seal_img.width = 90
            seal_img.height = 90
            ws.add_image(seal_img, f"F{row}")
        except Exception:
            pass  # bad/unsupported image format shouldn't block the whole export

    if has_header_image:
        row += 6  # reserve rows for the (now taller) logo/seal row before the title

    ws.merge_cells(f"A{row}:E{row}")
    ws[f"A{row}"] = "QUOTATION"
    # Same heading treatment as the PDF (quotation.html .title): same size as the Date line
    # (11pt), bold dark red, down from 18pt black.
    ws[f"A{row}"].font = Font(name="Arial", size=11, bold=True, color="C00000")
    row += 2

    ws[f"A{row}"] = "Date:"
    ws[f"A{row}"].font = Font(name="Arial", bold=True)
    ws[f"B{row}"] = document.issue_date.isoformat() if document.issue_date else date.today().isoformat()
    row += 2

    # Same layout as the PDF (quotation.html): the company is named on the
    # heading line, and the rows below describe the MD selected on this
    # document — each MD field falling back to the company's own value when
    # the MD has none. No Email row: the company's support email is already
    # in the Customer Service footer. The whole heading is one string in A
    # (B left empty) so it reads as a single line and overflows rightwards.
    company_info = company or {}
    director_info = director or {}
    company_name = company_info.get("name")
    ws[f"A{row}"] = _safe_str(f"SUPPLIER: {company_name}") if company_name else "SUPPLIER"
    ws[f"A{row}"].font = Font(name="Arial", bold=True)
    ws[f"E{row}"] = "END USER"
    ws[f"E{row}"].font = Font(name="Arial", bold=True)
    row += 1

    supplier_rows = [("Name", _safe_str(director_info.get("name")) or "—")]
    # The selected MD's own position (CompanyDirector.position); omitted when
    # no MD is selected or the MD has none (e.g. created before the column).
    # Falls back to the company's "Signer position" when the MD has none.
    supplier_position = director_info.get("position") or company_info.get("position")
    if supplier_position:
        supplier_rows.append(("Position", _safe_str(supplier_position)))
    supplier_rows += [
        ("Address", _safe_str(director_info.get("address") or company_info.get("address") or "")),
        ("Contact No", _safe_str(director_info.get("contact_no") or company_info.get("contact_no") or "")),
    ]
    customer_name = _safe_str(customer["name"]) if customer else "—"
    customer_address = ""
    if customer and customer.get("billing_address"):
        customer_address = _safe_str(", ".join(str(v) for v in customer["billing_address"].values()))
    end_user_rows = [
        ("Customer Name", customer_name),
        ("Quotation ID", document.doc_number),
        ("Organization", customer_name),
        ("Address", customer_address),
    ]

    for i in range(4):
        s_label, s_val = supplier_rows[i] if i < len(supplier_rows) else ("", "")
        e_label, e_val = end_user_rows[i]
        if s_label:
            ws[f"A{row}"] = s_label
            ws[f"A{row}"].font = Font(name="Arial", size=10)
            ws[f"B{row}"] = s_val
        ws[f"E{row}"] = e_label
        ws[f"E{row}"].font = Font(name="Arial", size=10)
        ws[f"F{row}"] = e_val
        row += 1
    row += 1

    headers = ["No", "Item", "Qty", "Unit", f"Price ({document.currency})", f"Amount ({document.currency})", "Remark"]
    for col, h in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col, value=h)
        cell.font = Font(name="Arial", bold=True, size=10)
        cell.fill = HEADER_FILL
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    # Same intent as the PDF, where the whole header repeats on every page:
    # when the sheet is printed, rows 1 through this column-titles row
    # (title, date, SUPPLIER / END USER, column titles) repeat at the top of
    # every printed page. Excel's print titles repeat cell content only —
    # the floating logo/seal images still print on page 1 alone.
    ws.print_title_rows = f"1:{row}"
    row += 1

    categories = {p["category"] for _, p, _ in items_with_product if p and p.get("category")}
    group_label = categories.pop() if len(categories) == 1 else "Items"
    ws.merge_cells(f"A{row}:G{row}")
    ws[f"A{row}"] = group_label
    ws[f"A{row}"].font = Font(name="Arial", bold=True, italic=True, size=10)
    row += 1

    main_item_rows = []  # only these contribute to the Total — sub-item rows are informational, already covered by the parent's price
    catalogue_number = 0  # matches the Catalogue zip export's numbering — only product-based items count
    for idx, (item, product, sub_items) in enumerate(items_with_product, start=1):
        item_name = product["name"] if product else (item.description or "")
        description = (product.get("description") if product else None) or item.description or ""
        item_cell_text = _name_and_description_rich_text(item_name, description)
        line_amount = (item.quantity or 0) * (item.unit_price or 0)
        remark = _safe_str(item.remark or (product.get("remark") if product else None) or "")

        values = [idx, item_cell_text, float(item.quantity or 0), item.unit or "Nos", float(item.unit_price or 0), float(line_amount), remark]
        for col, v in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=v)
            if col != 2:  # Item column carries its own rich-text formatting (bold name + normal description) — don't override it
                cell.font = Font(name="Arial", size=10)
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=(col in (2, 7)))
            if col in (5, 6):
                cell.number_format = "#,##0.00"
        main_item_rows.append(row)
        row += 1

        if item.product_id:
            catalogue_number += 1
        if sub_items:
            for sub in sub_items:
                sub_label = f"{catalogue_number}.{sub.get('sequence_number', '?')}"
                sub_price = float(sub["unit_price"]) if sub.get("unit_price") is not None else ""
                sub_name = f"{sub.get('sku', '')} — {sub.get('name', '')}"
                sub_desc = sub.get("description") or ""
                sub_cell_text = _name_and_description_rich_text(sub_name, sub_desc, size=9)
                sub_values = [
                    sub_label,
                    sub_cell_text,
                    1.0,
                    "Nos",
                    sub_price,
                    sub_price,
                    "",
                ]
                for col, v in enumerate(sub_values, start=1):
                    cell = ws.cell(row=row, column=col, value=v)
                    if col != 2:
                        cell.font = Font(name="Arial", size=9)
                    cell.border = BORDER
                    cell.alignment = Alignment(vertical="top", wrap_text=(col == 2))
                    if col in (5, 6) and v != "":
                        cell.number_format = "#,##0.00"
                row += 1
    last_item_row = row - 1

    # Tax is a single rate applied uniformly to every line item (the
    # document-level "Tax rate %" field in web/js/app.js) — read off the
    # first item, same as the app's own document-detail view
    # (`doc.items[0].tax_rate`). Only show the Subtotal/Tax breakdown when
    # there actually is tax, same as the PDF export.
    tax_rate_value = 0.0
    if items_with_product and items_with_product[0][0].tax_rate is not None:
        tax_rate_value = float(items_with_product[0][0].tax_rate)
    subtotal_formula = ("=" + "+".join(f"F{r}" for r in main_item_rows)) if main_item_rows else 0

    if tax_rate_value > 0:
        ws.merge_cells(f"A{row}:E{row}")
        ws[f"A{row}"] = "Subtotal:"
        ws[f"A{row}"].font = Font(name="Arial", bold=True)
        ws[f"A{row}"].alignment = Alignment(horizontal="right")
        subtotal_cell = ws.cell(row=row, column=6)
        subtotal_cell.value = subtotal_formula
        subtotal_cell.font = Font(name="Arial", bold=True)
        subtotal_cell.number_format = "#,##0.00"
        subtotal_cell.border = BORDER
        subtotal_row = row
        row += 1

        ws.merge_cells(f"A{row}:E{row}")
        ws[f"A{row}"] = f"Tax ({tax_rate_value:g}%):"
        ws[f"A{row}"].font = Font(name="Arial", bold=True)
        ws[f"A{row}"].alignment = Alignment(horizontal="right")
        tax_cell = ws.cell(row=row, column=6)
        tax_cell.value = f"=F{subtotal_row}*{tax_rate_value}/100"
        tax_cell.font = Font(name="Arial", bold=True)
        tax_cell.number_format = "#,##0.00"
        tax_cell.border = BORDER
        tax_row = row
        row += 1

        total_formula = f"=F{subtotal_row}+F{tax_row}"
    else:
        total_formula = subtotal_formula

    ws.merge_cells(f"A{row}:E{row}")
    ws[f"A{row}"] = "Total:"
    ws[f"A{row}"].font = Font(name="Arial", bold=True)
    ws[f"A{row}"].alignment = Alignment(horizontal="right")
    total_cell = ws.cell(row=row, column=6)
    total_cell.value = total_formula
    total_cell.font = Font(name="Arial", bold=True)
    total_cell.number_format = "#,##0.00"
    total_cell.border = BORDER
    row += 2

    ws[f"A{row}"] = "Terms and Conditions"
    ws[f"A{row}"].font = Font(name="Arial", bold=True, size=12)
    row += 1
    terms_text = document.terms_and_conditions or DEFAULT_TERMS
    for line in terms_text.split("\n"):
        ws.merge_cells(f"A{row}:G{row}")
        ws[f"A{row}"] = _safe_str(line)
        ws[f"A{row}"].font = Font(name="Arial", size=10)
        row += 1
    row += 1

    # Name/position used to be printed under the seal image too, but the
    # seal itself already carries the director's name and title — showing
    # both was redundant, so the seal now stands alone whenever one was
    # actually placed. Name/position text is kept ONLY as a fallback for
    # a director with no seal uploaded yet, so the signature area is
    # never left completely blank.
    if director:
        seal_row = row
        seal_placed = False
        if director_seal_bytes and director_seal_mime != "image/svg+xml":
            try:
                md_seal_img = XLImage(BytesIO(director_seal_bytes))
                md_seal_img.width = 100
                md_seal_img.height = 100
                ws.add_image(md_seal_img, f"G{row}")
                row += 6  # leave room for the image
                seal_placed = True
            except Exception:
                pass  # bad/unsupported image format shouldn't block the whole export
        if not seal_placed:
            ws.merge_cells(f"F{row}:G{row}")
            ws[f"F{row}"] = _safe_str(director.get("name", ""))
            ws[f"F{row}"].font = Font(name="Arial", bold=True)
            ws[f"F{row}"].alignment = Alignment(horizontal="center")
            row += 1
            ws.merge_cells(f"F{row}:G{row}")
            ws[f"F{row}"] = _safe_str(director.get("position")) or "Managing Director"
            ws[f"F{row}"].font = Font(name="Arial", size=9, italic=True, color="666666")
            ws[f"F{row}"].alignment = Alignment(horizontal="center")
            row += 1
        row = max(row, seal_row + 7) + 1

    ws[f"A{row}"] = "Customer Service"
    ws[f"A{row}"].font = Font(name="Arial", bold=True)
    row += 1
    ws[f"A{row}"] = "Email:"
    ws[f"B{row}"] = _safe_str(company.get("support_email", "")) if company else ""
    row += 1
    ws[f"A{row}"] = "Phone:"
    # support_phone was removed from Company — contact_no is the closest
    # remaining company-level phone number for this generic footer block
    # (the Supplier block above shows the director's own number, falling
    # back to this one only when the director has none).
    ws[f"B{row}"] = _safe_str(company.get("contact_no", "")) if company else ""

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer

from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.drawing.image import Image as XLImage

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


def generate_quotation_xlsx(document, customer, items_with_product, company=None, logo_bytes=None, seal_bytes=None, logo_mime="image/png", seal_mime="image/png") -> BytesIO:
    """
    items_with_product: list of (LineItem, product_dict_or_None, sub_items_list) tuples.
    company: dict with keys name/position/address/contact_no/support_email/support_phone,
             or None if no company profile has been set up yet.
    logo_bytes / seal_bytes: raw image bytes to embed, or None. SVGs are
    skipped here (not embedded) — openpyxl's image support goes through
    PIL, which can't rasterize SVG; the PDF export handles SVG logos fine
    since browsers/WeasyPrint render SVG natively.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Quotation"

    col_widths = [8, 26, 46, 8, 8, 16, 16, 30]
    for i, w in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    row = 1

    if logo_bytes and logo_mime != "image/svg+xml":
        try:
            img = XLImage(BytesIO(logo_bytes))
            img.width = 120
            img.height = 60
            ws.add_image(img, "F1")
        except Exception:
            pass  # bad/unsupported image format shouldn't block the whole export

    if seal_bytes and seal_mime != "image/svg+xml":
        try:
            seal_img = XLImage(BytesIO(seal_bytes))
            seal_img.width = 80
            seal_img.height = 80
            ws.add_image(seal_img, "H1")
        except Exception:
            pass  # bad/unsupported image format shouldn't block the whole export

    ws.merge_cells(f"A{row}:E{row}")
    ws[f"A{row}"] = "QUOTATION"
    ws[f"A{row}"].font = Font(name="Arial", size=18, bold=True)
    row += 4  # leave room for the logo alongside the title

    ws[f"A{row}"] = "Date:"
    ws[f"A{row}"].font = Font(name="Arial", bold=True)
    ws[f"B{row}"] = document.issue_date.isoformat() if document.issue_date else date.today().isoformat()
    row += 2

    company_name = _safe_str(company["name"]) if company else "—"
    ws[f"A{row}"] = "SUPPLIER"
    ws[f"A{row}"].font = Font(name="Arial", bold=True)
    ws[f"B{row}"] = company_name
    ws[f"E{row}"] = "END USER"
    ws[f"E{row}"].font = Font(name="Arial", bold=True)
    row += 1

    supplier_rows = [
        ("Position", _safe_str(company.get("position")) if company else ""),
        ("Address", _safe_str(company.get("address")) if company else ""),
        ("Contact No", _safe_str(company.get("contact_no")) if company else ""),
    ]
    customer_name = _safe_str(customer["name"]) if customer else "—"
    customer_address = ""
    if customer and customer.get("billing_address"):
        customer_address = _safe_str(", ".join(str(v) for v in customer["billing_address"].values()))
    end_user_rows = [
        ("Customer ID", str(customer["id"]) if customer else "—"),
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

    headers = ["No", "Item", "Description", "Qty", "Unit", f"Price ({document.currency})", f"Amount ({document.currency})", "Remark"]
    for col, h in enumerate(headers, start=1):
        cell = ws.cell(row=row, column=col, value=h)
        cell.font = Font(name="Arial", bold=True, size=10)
        cell.fill = HEADER_FILL
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    row += 1

    categories = {p["category"] for _, p, _ in items_with_product if p and p.get("category")}
    group_label = categories.pop() if len(categories) == 1 else "Items"
    ws.merge_cells(f"A{row}:H{row}")
    ws[f"A{row}"] = group_label
    ws[f"A{row}"].font = Font(name="Arial", bold=True, italic=True, size=10)
    row += 1

    main_item_rows = []  # only these contribute to the Total — sub-item rows are informational, already covered by the parent's price
    catalogue_number = 0  # matches the Catalogue zip export's numbering — only product-based items count
    for idx, (item, product, sub_items) in enumerate(items_with_product, start=1):
        item_name = _safe_str(product["name"] if product else (item.description or ""))
        description = _safe_str((product.get("description") if product else None) or item.description or "")
        line_amount = (item.quantity or 0) * (item.unit_price or 0)
        remark = _safe_str(item.remark or (product.get("remark") if product else None) or "")

        values = [idx, item_name, description, float(item.quantity or 0), item.unit or "Nos", float(item.unit_price or 0), float(line_amount), remark]
        for col, v in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=v)
            cell.font = Font(name="Arial", size=10)
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=(col in (3, 8)))
            if col in (6, 7):
                cell.number_format = "#,##0.00"
        main_item_rows.append(row)
        row += 1

        if item.product_id:
            catalogue_number += 1
        if sub_items:
            for sub in sub_items:
                sub_label = f"{catalogue_number}.{sub.get('sequence_number', '?')}"
                sub_price = float(sub["unit_price"]) if sub.get("unit_price") is not None else ""
                sub_values = [
                    sub_label,
                    _safe_str(f"{sub.get('sku', '')} — {sub.get('name', '')}"),
                    _safe_str(sub.get("description") or ""),
                    1.0,
                    "Nos",
                    sub_price,
                    sub_price,
                    "",
                ]
                for col, v in enumerate(sub_values, start=1):
                    cell = ws.cell(row=row, column=col, value=v)
                    cell.font = Font(name="Arial", size=9)
                    cell.border = BORDER
                    cell.alignment = Alignment(vertical="top", wrap_text=(col == 3))
                    if col in (6, 7) and v != "":
                        cell.number_format = "#,##0.00"
                row += 1
    last_item_row = row - 1

    ws.merge_cells(f"A{row}:F{row}")
    ws[f"A{row}"] = "Total:"
    ws[f"A{row}"].font = Font(name="Arial", bold=True)
    ws[f"A{row}"].alignment = Alignment(horizontal="right")
    total_cell = ws.cell(row=row, column=7)
    total_cell.value = ("=" + "+".join(f"G{r}" for r in main_item_rows)) if main_item_rows else 0
    total_cell.font = Font(name="Arial", bold=True)
    total_cell.number_format = "#,##0.00"
    total_cell.border = BORDER
    row += 2

    ws[f"A{row}"] = "Terms and Conditions"
    ws[f"A{row}"].font = Font(name="Arial", bold=True, size=12)
    row += 1
    terms_text = document.terms_and_conditions or DEFAULT_TERMS
    for line in terms_text.split("\n"):
        ws.merge_cells(f"A{row}:H{row}")
        ws[f"A{row}"] = _safe_str(line)
        ws[f"A{row}"].font = Font(name="Arial", size=10)
        row += 1
    row += 1

    ws[f"A{row}"] = "Customer Service"
    ws[f"A{row}"].font = Font(name="Arial", bold=True)
    row += 1
    ws[f"A{row}"] = "Email:"
    ws[f"B{row}"] = _safe_str(company.get("support_email", "")) if company else ""
    row += 1
    ws[f"A{row}"] = "Phone:"
    ws[f"B{row}"] = _safe_str(company.get("support_phone", "")) if company else ""

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer

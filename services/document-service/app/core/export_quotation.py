import math
from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.header_footer import HeaderFooterItem
from openpyxl.utils.escape import unescape
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.utils.units import pixels_to_EMU
from openpyxl.drawing.image import Image as XLImage
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont

from app.core.units import display_unit

THIN = Side(style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEADER_FILL = PatternFill("solid", fgColor="EFEFEF")

_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _safe_str(value):
    """
    Neutralizes spreadsheet formula injection: openpyxl (and Excel/Sheets on
    open) treats any string cell value starting with =, +, -, or @ as a
    live formula. Every string here can originate from another user's input
    (product description, customer address, remarks, terms...), so a crafted
    value like "=cmd|'/c calc'!A1" must never become a formula. Such values
    are written as a rich-text string (stored as text, never a formula) —
    unlike a leading quote, nothing extra shows in the cell ("+9599..." stays
    exactly as typed).
    """
    if isinstance(value, str) and value.startswith(_FORMULA_TRIGGER_CHARS):
        return CellRichText(value)
    return value


def _name_and_description_rich_text(name, description, size=10):
    """
    Builds a single cell value with the name in bold and the description
    (if any) on the line below in regular weight — openpyxl can't mix
    formatting within a plain string, so this uses its "rich text" cell
    support instead. A rich-text cell is always stored as text, so a name
    starting with "=" can't become a formula here (no _safe_str needed).
    """
    name = name or ""
    bold_font = InlineFont(rFont="Arial", sz=size, b=True)
    if not description:
        return CellRichText(TextBlock(bold_font, name))
    normal_font = InlineFont(rFont="Arial", sz=size)
    return CellRichText(
        TextBlock(bold_font, name),
        TextBlock(normal_font, "\n" + description),
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


def fit_inside(width, height, box_width, box_height):
    """Largest (w, h) with the image's aspect ratio that fits inside the box (scales up or down)."""
    scale = min(box_width / width, box_height / height)
    return width * scale, height * scale


# The word a document's type puts on its exports (heading, ID label, sheet and
# file name). Catalogue documents have their own export; their quotation
# export keeps saying Quotation, as it always has.
_DOC_TYPE_LABELS = {"quotation": "Quotation", "invoice": "Invoice", "proposal": "Proposal", "catalogue": "Quotation"}


def doc_type_label(doc_type):
    """'invoice' -> 'Invoice'. doc_type is not validated on input, so an unknown
    type is capitalised only when it is plain ASCII letters/digits (safe in a
    sheet name and a download header); anything else falls back to Quotation."""
    t = (doc_type or "").lower()
    return _DOC_TYPE_LABELS.get(t) or (t.capitalize() if t.isascii() and t.isalnum() else "Quotation")


# Zero shows as "-" (a line with no labour), like the user's own sheets.
LABOUR_FORMAT = '#,##0.00;-#,##0.00;"-"'


def _labour_totals(ws, row, item_rows, tax_rate):
    """SUB Total (materials F | labour H) -> Tax on materials (only if taxed) ->
    Grand Total = materials + labour + tax. Labour is never taxed. Live formulas,
    every cell bordered and bold. Returns the row after the block + 1 blank."""
    def line(label, cells, merge_amount=False):
        ws.merge_cells(f"A{row}:E{row}")
        if merge_amount:
            ws.merge_cells(f"F{row}:H{row}")
        for c in range(1, 10):  # after merging: merging resets the merged-over cells' style
            ws.cell(row=row, column=c).border = BORDER
            ws.cell(row=row, column=c).font = Font(name="Arial", bold=True)
        ws[f"A{row}"] = label
        ws[f"A{row}"].alignment = Alignment(horizontal="center")
        for col, formula, fmt in cells:
            ws[f"{col}{row}"] = formula
            ws[f"{col}{row}"].number_format = fmt
            ws[f"{col}{row}"].alignment = Alignment(shrink_to_fit=True)

    def total(col):
        return ("=" + "+".join(f"{col}{r}" for r in item_rows)) if item_rows else 0

    sub_row = row
    line("SUB Total", [("F", total("F"), "#,##0.00"), ("H", total("H"), LABOUR_FORMAT)])
    row += 1
    grand = f"=F{sub_row}+H{sub_row}"
    if tax_rate > 0:
        line(f"Tax on materials ({tax_rate:g}%)", [("F", f"=F{sub_row}*{tax_rate}/100", "#,##0.00")])
        grand += f"+F{row}"
        row += 1
    line("Grand Total", [("F", grand, "#,##0.00")], merge_amount=True)
    ws[f"F{row}"].alignment = Alignment(horizontal="center", shrink_to_fit=True)
    return row + 2


# Excel shows a column of width w (characters) as trunc((256*w + trunc(128/7)) / 256 * 7) px.
def _col_px(width):
    return int((256 * width + 128 // 7) / 256 * 7)


# Image sizes are fractions of the table width: the sizes the user drew on the
# default 7-column sheet (987 px wide), scaled when the sheet is wider (labour
# columns, wider money columns). The seal is ~17% of the width, as in the PDF.
DEFAULT_TABLE_PX = 987
BANNER_BOX = (677, 197)
BANNER_X = 3  # px from column A's left edge
HEADER_IMAGE_PX = 170  # squared logo
SEAL_PX = 190  # company seal, centred in the last column
MD_SEAL_BOX = (180, 200)
ROW_PX = 20  # a 15pt row


def _money_width(values):
    """Price/amount column width: 40% over the widest '#,##0.00' value, so a
    zoomed-out Excel (digits relatively wider) still shows it instead of ####."""
    longest = max((len(f"{v:,.2f}") for v in values), default=0)
    return min(30, max(16, math.ceil(longest * 1.4 + 2)))


def _open_image(data, mime):
    """XLImage, or None for no/SVG/broken image. SVGs are skipped: openpyxl
    goes through PIL, which can't rasterize SVG."""
    if not data or mime == "image/svg+xml":
        return None
    try:
        return XLImage(BytesIO(data))
    except Exception:
        return None  # bad/unsupported image format shouldn't block the whole export


def _place(ws, img, col_px, x, row0, w, h, y=0):
    """Float img w x h px with its left edge x px from the sheet's left edge,
    y px below the top of 0-based row row0."""
    col = 0
    while col < len(col_px) - 1 and x >= col_px[col]:
        x -= col_px[col]
        col += 1
    img.width, img.height = round(w), round(h)
    img.anchor = OneCellAnchor(
        _from=AnchorMarker(col=col, colOff=pixels_to_EMU(round(x)), row=row0, rowOff=pixels_to_EMU(round(y))),
        ext=XDRPositiveSize2D(pixels_to_EMU(round(w)), pixels_to_EMU(round(h))),
    )
    ws.add_image(img)


LINE_PT = 12.75  # one 10pt Arial line, as Excel lays it out
HEADING_PT = 15.75  # one 12pt bold line
MAX_ROW_PT = 409  # Excel's tallest row


def _line_count(lines, width):
    """Lines a wrapped cell `width` characters wide needs. ~1 char per width unit
    over-estimates (Excel fits ~1.1), so the row is never too short."""
    return sum(max(1, math.ceil(len(line) / width)) for line in lines)


class _Footer(HeaderFooterItem):
    """openpyxl writes footer line breaks as _x000a_, which LibreOffice prints
    literally; a plain line break works in Excel and LibreOffice."""
    def __str__(self):
        return unescape(super().__str__())


def _footer_lines(company):
    """Customer Service footer lines, like the PDF's; empty values left out, none -> []."""
    company = company or {}
    lines = [f"{label}: {company.get(key)}"[:100] for label, key in (("Email", "support_email"), ("Phone", "contact_no")) if company.get(key)]
    return ["Customer Service", *lines] if lines else []


def is_letterhead(company, logo_bytes, logo_mime):
    return bool(company and company.get("header_style") == "letterhead" and logo_bytes and logo_mime != "image/svg+xml")


def generate_quotation_xlsx(document, customer, items_with_product, company=None, logo_bytes=None, seal_bytes=None, logo_mime="image/png", seal_mime="image/png", director=None, director_seal_bytes=None, director_seal_mime="image/png") -> BytesIO:
    """
    items_with_product: list of (LineItem, product_dict_or_None, sub_items_list) tuples.
    company: dict with keys name/address/contact_no/support_email, or None
              if no company profile has been set up yet. Its name goes on
              the Supplier heading line ("SUPPLIER: <name>"); its
              address/contact_no are only fallbacks for the director's own,
              and support_email appears only in the Customer Service footer.
    director: dict with keys name/address/contact_no/email/position — the
              selected Managing Director. The Supplier block's rows
              (Name / Position / Address / Contact No) describe this MD;
              address and contact fall back to the company's when the MD's
              is empty (Position has no fallback); it also drives the
              signature block at the bottom.
              None if no director was chosen on this document (Name then
              shows "—").
    logo_bytes / seal_bytes: raw image bytes to embed, or None. SVGs are
    skipped here (not embedded) — openpyxl's image support goes through
    PIL, which can't rasterize SVG; the PDF export handles SVG logos fine
    since browsers/WeasyPrint render SVG natively.
    """
    wb = Workbook()
    ws = wb.active
    label = doc_type_label(getattr(document, "doc_type", None))
    ws.title = label

    include_labour = bool(getattr(document, "include_labour", False))
    # Every money value the sheet shows, incl. the totals, sizes the money columns.
    money = [0.0]
    materials = labour_sum = 0.0
    for item, _product, sub_items in items_with_product:
        qty, price = float(item.quantity or 0), float(item.unit_price or 0)
        labour_price = float(getattr(item, "labour_unit_price", None) or 0) if include_labour else 0.0
        money += [abs(price), abs(qty * price), abs(labour_price), abs(qty * labour_price)]
        money += [abs(float(sub["unit_price"])) for sub in sub_items or () if sub.get("unit_price") is not None]
        materials += qty * price
        labour_sum += qty * labour_price
    rate = float(items_with_product[0][0].tax_rate or 0) if items_with_product else 0.0
    money += [abs(materials), abs(materials * (1 + rate / 100) + labour_sum)]
    m = _money_width(money)
    # Labour adds Labour unit price / total (G, H) before Remark; Item gives up some width.
    col_widths = [8, 45, 8, 8, m, m, m, m, 30] if include_labour else [8, 55, 8, 8, m, m, 30]
    last_col = get_column_letter(len(col_widths))  # G, or I with labour
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
    col_px = [_col_px(w) for w in col_widths]
    scale = sum(col_px) / DEFAULT_TABLE_PX
    logo_img = _open_image(logo_bytes, logo_mime)
    seal_img = _open_image(seal_bytes, seal_mime)
    header_h = 0
    if logo_img:
        if is_letterhead(company, logo_bytes, logo_mime):
            # Letterhead banner from column A's left edge (the user's red box),
            # fitted inside the box with its own proportions; same box without a seal.
            w, h = fit_inside(logo_img.width, logo_img.height, BANNER_BOX[0] * scale, BANNER_BOX[1] * scale)
            _place(ws, logo_img, col_px, BANNER_X * scale, 0, w, h)
            # Reserve the whole box (not the fitted image) plus one empty row before the title.
            header_h = BANNER_BOX[1] * scale + ROW_PX
        else:
            w, h = fit_inside(logo_img.width, logo_img.height, HEADER_IMAGE_PX * scale, HEADER_IMAGE_PX * scale)
            _place(ws, logo_img, col_px, 0, 0, w, h)
            header_h = h
    if seal_img:
        # Centred in the last column (G, or I with labour), pulled left only if it would pass the table's edge.
        w, h = fit_inside(seal_img.width, seal_img.height, SEAL_PX * scale, SEAL_PX * scale)
        _place(ws, seal_img, col_px, min(sum(col_px[:-1]) + (col_px[-1] - w) / 2, sum(col_px) - w), 0, w, h)
        header_h = max(header_h, h)
    if header_h:
        # Images float over these rows, so the title starts below the tallest
        # one with a small gap; explicit 15pt rows so every viewer agrees.
        reserved = math.ceil((header_h + 10) / ROW_PX)
        for r in range(row, row + reserved):
            ws.row_dimensions[r].height = 15
        row += reserved

    ws.merge_cells(f"A{row}:E{row}")
    ws[f"A{row}"] = label.upper()
    # Bold dark red like the PDF's heading, 16pt to match the larger letterhead images.
    ws[f"A{row}"].font = Font(name="Arial", size=16, bold=True, color="C00000")
    ws.row_dimensions[row].height = 22  # a 16pt line
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
    ws[f"{last_col}{row}"] = "END USER"
    ws[f"{last_col}{row}"].font = Font(name="Arial", bold=True)
    row += 1

    supplier_rows = [("Name", _safe_str(director_info.get("name")) or "—")]
    # The selected MD's own position (required since loop iteration 12; no
    # company fallback). Omitted when no MD is selected.
    supplier_position = director_info.get("position")
    if supplier_position:
        supplier_rows.append(("Position", _safe_str(supplier_position)))
    supplier_rows += [
        ("Address", _safe_str(director_info.get("address") or company_info.get("address") or "")),
        ("Contact No", _safe_str(director_info.get("contact_no") or company_info.get("contact_no") or "")),
    ]
    customer_name = _safe_str(customer["name"]) if customer else "—"
    customer_address = ""
    if customer and customer.get("address"):  # one-time customer: typed as one block
        customer_address = _safe_str(customer["address"])
    elif customer and customer.get("billing_address"):
        customer_address = _safe_str(", ".join(str(v) for v in customer["billing_address"].values()))
    customer_org = _safe_str(customer.get("organization") or customer["name"]) if customer else "—"
    end_user_rows = [
        ("Customer Name", customer_name),
        (f"{label} ID", document.doc_number),
        ("Organization", customer_org),
        ("Address", customer_address),
    ]

    for i in range(4):
        s_label, s_val = supplier_rows[i] if i < len(supplier_rows) else ("", "")
        e_label, e_val = end_user_rows[i]
        if s_label:
            ws[f"A{row}"] = s_label
            ws[f"A{row}"].font = Font(name="Arial", size=10)
            # Column A is narrow and B holds the value, so "Contact No" would be clipped.
            ws[f"A{row}"].alignment = Alignment(shrink_to_fit=True)
            ws[f"B{row}"] = s_val
        # END USER sits in the last column as "Label: value", wrapped; the row grows
        # to fit (~1.3 chars per width unit at 10pt Arial).
        end_cell = ws[f"{last_col}{row}"]
        end_cell.value = f"{e_label}: {e_val}"
        end_cell.font = Font(name="Arial", size=10)
        end_cell.alignment = Alignment(wrap_text=True, vertical="top")
        lines = math.ceil(len(end_cell.value) / (col_widths[-1] * 1.3))
        if lines > 1:
            ws.row_dimensions[row].height = 13 * lines + 2
        row += 1
    row += 1

    if include_labour:
        # Two header rows: Material / Labour each span their Unit Price + Total
        # Price; No, Item, Qty, Unit and Remark span both rows.
        top = ["No", "Item", "Qty", "Unit", f"Material ({document.currency})", None, f"Labour ({document.currency})", None, "Remark"]
        for col, h in enumerate(top, start=1):
            for r, v in ((row, h), (row + 1, ["Unit Price", "Total Price"][(col - 5) % 2] if 5 <= col <= 8 else None)):
                cell = ws.cell(row=r, column=col, value=v)
                cell.font = Font(name="Arial", bold=True, size=10)
                cell.fill = HEADER_FILL
                cell.border = BORDER
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for c in "ABCDI":
            ws.merge_cells(f"{c}{row}:{c}{row + 1}")
        ws.merge_cells(f"E{row}:F{row}")
        ws.merge_cells(f"G{row}:H{row}")
        row += 1
    else:
        headers = ["No", "Item", "Qty", "Unit", f"Price ({document.currency})", f"Amount ({document.currency})", "Remark"]
        for col, h in enumerate(headers, start=1):
            cell = ws.cell(row=row, column=col, value=h)
            cell.font = Font(name="Arial", bold=True, size=10)
            cell.fill = HEADER_FILL
            cell.border = BORDER
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.row_dimensions[row].height = 32  # room for a title that wraps onto two lines
    # Same intent as the PDF, where the whole header repeats on every page:
    # when the sheet is printed, rows 1 through this column-titles row
    # (title, date, SUPPLIER / END USER, column titles) repeat at the top of
    # every printed page. Excel's print titles repeat cell content only —
    # the floating logo/seal images still print on page 1 alone.
    ws.print_title_rows = f"1:{row}"
    # Same page margins as the PDF (inches, openpyxl's unit): top 0.3,
    # left/right/bottom 0.5. Excel's own header/footer areas sit inside
    # those margins, so keep them smaller (0.2) or they would push the
    # printed body further in than the user asked for.
    # The Customer Service footer prints at the bottom of every page, like the
    # PDF: 3 lines at 8pt (~0.45in) above the 0.2in footer margin, so the body
    # stops 0.9in from the bottom edge and never runs into it.
    footer = _footer_lines(company)
    ws.page_margins = PageMargins(top=0.3, left=0.5, right=0.5, bottom=0.9 if footer else 0.5, header=0.2, footer=0.2)
    if footer:
        ws.oddFooter = _Footer()
        lines = [line.replace("&", "&&") for line in footer]  # "&" starts a footer code; "&B" toggles bold
        ws.oddFooter.left.text = "\n".join(["&B" + lines[0] + "&B", *lines[1:]])
        ws.oddFooter.left.size = 8
        ws.oddFooter.left.font = "Arial,Regular"
    row += 1

    main_item_rows = []  # only these contribute to the Total — sub-item rows are informational, already covered by the parent's price
    catalogue_number = 0  # matches the Catalogue zip export's numbering — only product-based items count
    for idx, (item, product, sub_items) in enumerate(items_with_product, start=1):
        item_name = product["name"] if product else (item.description or "")
        description = (product.get("description") if product else None) or item.description or ""
        item_cell_text = _name_and_description_rich_text(item_name, description)
        line_amount = (item.quantity or 0) * (item.unit_price or 0)
        remark = _safe_str(item.remark or (product.get("remark") if product else None) or "")

        values = [idx, item_cell_text, float(item.quantity or 0), _safe_str(display_unit(item.unit, item.quantity)), float(item.unit_price or 0), float(line_amount), remark]
        if include_labour:
            # Labour total is a live formula; a line with no labour shows an empty price and "-".
            labour_price = getattr(item, "labour_unit_price", None)
            values[6:6] = [float(labour_price) if labour_price else None, f"=C{row}*G{row}"]
        for col, v in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=v)
            if col != 2:  # Item column carries its own rich-text formatting (bold name + normal description) — don't override it
                cell.font = Font(name="Arial", size=10)
            cell.border = BORDER
            money_col = 5 <= col <= (8 if include_labour else 6)
            # shrink-to-fit: a second guard against #### besides the column width
            cell.alignment = Alignment(horizontal="center" if col in (3, 4) else None, vertical="top", wrap_text=(col in (2, len(values))), shrink_to_fit=money_col)
            if col in (5, 6) or (include_labour and col == 7):
                cell.number_format = "#,##0.00"
            if include_labour and col == 8:
                cell.number_format = LABOUR_FORMAT
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
                    display_unit(sub.get("unit"), 1),
                    sub_price,
                    sub_price,
                    "",
                ]
                if include_labour:
                    sub_values[6:6] = ["", ""]  # sub-items carry no labour
                for col, v in enumerate(sub_values, start=1):
                    cell = ws.cell(row=row, column=col, value=v)
                    if col != 2:
                        cell.font = Font(name="Arial", size=9)
                    cell.border = BORDER
                    cell.alignment = Alignment(horizontal="center" if col in (3, 4) else None, vertical="top", wrap_text=(col == 2), shrink_to_fit=(col in (5, 6)))
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

    if include_labour:
        row = _labour_totals(ws, row, main_item_rows, tax_rate_value)
    elif tax_rate_value > 0:
        ws.merge_cells(f"A{row}:E{row}")
        ws[f"A{row}"] = "Subtotal:"
        ws[f"A{row}"].font = Font(name="Arial", bold=True)
        ws[f"A{row}"].alignment = Alignment(horizontal="right")
        subtotal_cell = ws.cell(row=row, column=6)
        subtotal_cell.value = subtotal_formula
        subtotal_cell.font = Font(name="Arial", bold=True)
        subtotal_cell.number_format = "#,##0.00"
        subtotal_cell.border = BORDER
        subtotal_cell.alignment = Alignment(shrink_to_fit=True)
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
        tax_cell.alignment = Alignment(shrink_to_fit=True)
        tax_row = row
        row += 1

        total_formula = f"=F{subtotal_row}+F{tax_row}"
    else:
        total_formula = subtotal_formula

    if not include_labour:
        ws.merge_cells(f"A{row}:E{row}")
        ws[f"A{row}"] = "Total:"
        ws[f"A{row}"].font = Font(name="Arial", bold=True)
        ws[f"A{row}"].alignment = Alignment(horizontal="right")
        total_cell = ws.cell(row=row, column=6)
        total_cell.value = total_formula
        total_cell.font = Font(name="Arial", bold=True)
        total_cell.number_format = "#,##0.00"
        total_cell.border = BORDER
        total_cell.alignment = Alignment(shrink_to_fit=True)
        row += 2

    # Terms, "Regards," and the MD seal share ONE row: Excel and LibreOffice never
    # split a row across pages, so this tail always prints together and the seal
    # can't cross a page break. (Manual page breaks can't do it: Excel ignores
    # them under Fit To.) Terms (A to the column before last) and the last column
    # are both top-aligned with the same line heights, so "Regards," lands on the
    # line after the last Terms line and the seal right below it, as before.
    # Name/position used to be printed under the seal image too, but the seal
    # itself already carries the director's name and title, so the seal stands
    # alone; name/position text is only the fallback for a director with no seal.
    terms = (document.terms_and_conditions or DEFAULT_TERMS).split("\n")
    md_seal = _open_image(director_seal_bytes, director_seal_mime) if director else None
    seal_w, seal_h = fit_inside(md_seal.width, md_seal.height, MD_SEAL_BOX[0] * scale, MD_SEAL_BOX[1] * scale) if md_seal else (0, 0)
    sign_pt = 2 * LINE_PT + (seal_h * 0.75 + 4 if md_seal else 2 * LINE_PT if director else 0)  # blank line + Regards + seal or name/position
    text_width = sum(col_widths[:-1])
    # Terms too long for one row: the first lines print in their own rows above it.
    head = []
    while len(terms) > 1 and HEADING_PT + _line_count(terms, text_width) * LINE_PT + sign_pt > MAX_ROW_PT:
        head.append(terms.pop(0))
    if head:
        ws[f"A{row}"] = "Terms and Conditions"
        ws[f"A{row}"].font = Font(name="Arial", bold=True, size=12)
        row += 1
        for line in head:
            ws.merge_cells(f"A{row}:{last_col}{row}")
            ws[f"A{row}"] = _safe_str(line)
            ws[f"A{row}"].font = Font(name="Arial", size=10)
            row += 1

    before_last = get_column_letter(len(col_widths) - 1)
    ws.merge_cells(f"A{row}:{before_last}{row}")
    heading = [] if head else [TextBlock(InlineFont(rFont="Arial", sz=12, b=True), "Terms and Conditions\n")]
    ws[f"A{row}"] = CellRichText(*heading, TextBlock(InlineFont(rFont="Arial", sz=10), "\n".join(terms)))  # rich text: never a formula
    ws[f"A{row}"].alignment = Alignment(wrap_text=True, vertical="top")
    ws[f"A{row}"].font = Font(name="Arial", size=10)  # empty lines take the cell font: same pitch in both cells
    heading_pt = 0 if head else HEADING_PT
    height = heading_pt + _line_count(terms, text_width) * LINE_PT + 4
    if director:
        bold = InlineFont(rFont="Arial", sz=10, b=True)
        # empty lines level with the Terms heading and lines, then one blank line
        spacer = ([TextBlock(InlineFont(rFont="Arial", sz=12, b=True), "\u00a0\n")] if heading else []) + [TextBlock(InlineFont(rFont="Arial", sz=10), "\n" * (len(terms) + 1))]
        sign = [TextBlock(bold, "Regards,")]
        if not md_seal:
            sign += [TextBlock(bold, "\n" + (director.get("name") or "")),
                     TextBlock(InlineFont(rFont="Arial", sz=9, i=True, color="666666"), "\n" + (director.get("position") or "Managing Director"))]
        ws[f"{last_col}{row}"] = CellRichText(*spacer, *sign)
        ws[f"{last_col}{row}"].alignment = Alignment(horizontal="center", vertical="top", wrap_text=True)
        ws[f"{last_col}{row}"].font = Font(name="Arial", size=10)
        regards_end = heading_pt + (len(terms) + 2) * LINE_PT
        if md_seal:
            # 4 px inside the last column, pulled left only if it would pass the table's right edge
            x = min(sum(col_px[:-1]) + 4, sum(col_px) - seal_w)
            _place(ws, md_seal, col_px, x, row - 1, seal_w, seal_h, y=(regards_end + 4) * 4 / 3)
            height = max(height, regards_end + 4 + seal_h * 0.75 + 6)
        else:
            height = max(height, regards_end + 2 * LINE_PT + 4)
    ws.row_dimensions[row].height = min(MAX_ROW_PT, height)
    row += 2

    print_end = row - 2  # the tail row; the Customer Service block below is for the screen, the page footer prints it
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

    # The 7 columns are ~10.6in wide but A4 portrait prints ~7.3in, so without
    # this Excel splits the sheet after column E (Price/Amount/Remark on page 2).
    # Fit to 1 page wide, as many tall as needed; the print area stops anything
    # outside A:G from widening the page (every image is anchored inside A:G).
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.print_options.horizontalCentered = True
    ws.print_area = f"A1:{last_col}{print_end}"

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer

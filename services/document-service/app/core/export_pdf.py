import base64
import os
from datetime import date
from io import BytesIO

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML

from app.core.export_quotation import DEFAULT_TERMS

_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")
# autoescape=True: rendered fields include user-controlled document/company
# data (item descriptions, remarks, terms, addresses). Without escaping, a
# crafted field could inject markup into the PDF — including a resource tag
# whose src WeasyPrint would fetch, an SSRF vector against internal hosts.
_env = Environment(loader=FileSystemLoader(_TEMPLATE_DIR), autoescape=True)


def generate_quotation_pdf(document, customer, items_with_product, company=None, logo_bytes=None, seal_bytes=None, logo_mime="image/png", seal_mime="image/png", director=None, director_seal_bytes=None, director_seal_mime="image/png") -> BytesIO:
    template = _env.get_template("quotation.html")

    logo_data_uri = None
    if logo_bytes:
        try:
            b64 = base64.b64encode(logo_bytes).decode("ascii")
            logo_data_uri = f"data:{logo_mime};base64,{b64}"
        except Exception:
            logo_data_uri = None

    seal_data_uri = None
    if seal_bytes:
        try:
            b64 = base64.b64encode(seal_bytes).decode("ascii")
            seal_data_uri = f"data:{seal_mime};base64,{b64}"
        except Exception:
            seal_data_uri = None

    director_seal_data_uri = None
    if director_seal_bytes:
        try:
            b64 = base64.b64encode(director_seal_bytes).decode("ascii")
            director_seal_data_uri = f"data:{director_seal_mime};base64,{b64}"
        except Exception:
            director_seal_data_uri = None

    customer_address = ""
    customer_dict = None
    if customer:
        customer_dict = {"id": str(customer["id"]), "name": customer["name"]}
        if customer.get("billing_address"):
            customer_address = ", ".join(str(v) for v in customer["billing_address"].values())

    categories = {p["category"] for _, p, _ in items_with_product if p and p.get("category")}
    group_label = categories.pop() if len(categories) == 1 else "Items"

    items = []
    total = 0
    catalogue_number = 0  # matches the Catalogue zip export's numbering — only product-based items count
    for item, product, sub_items in items_with_product:
        item_name = product["name"] if product else (item.description or "")
        # item.description is also what item_name falls back to above (a
        # manual line item has no separate name/description split, and a
        # product-linked item defaults its description to the product's
        # own name at creation time — see _process_items in
        # routers/documents.py). Falling back to it unconditionally here
        # (as this used to) printed that identical text a second time on
        # the row, in regular weight right below the bold name, quietly
        # doubling the row's height. Only treat it as a genuinely separate
        # description when it actually differs from item_name — e.g. a
        # product with its own description, or a line item whose
        # description was deliberately customized away from the product
        # name at creation.
        item_description_override = item.description if item.description != item_name else None
        description = (product.get("description") if product else None) or item_description_override or ""
        amount = float(item.quantity or 0) * float(item.unit_price or 0)
        total += amount

        if item.product_id:
            catalogue_number += 1

        items.append({
            "name": item_name,
            "description": description,
            "qty": float(item.quantity or 0),
            "unit": item.unit or "Nos",
            "price": f"{float(item.unit_price or 0):,.2f}",
            "amount": f"{amount:,.2f}",
            "remark": item.remark or (product.get("remark") if product else None) or "",
            "sub_items": [
                {
                    "label": f"{catalogue_number}.{sub['sequence_number']}",
                    "name": sub["name"],
                    "sku": sub["sku"],
                    "description": sub.get("description") or "",
                    "price": f"{float(sub['unit_price']):,.2f}" if sub.get("unit_price") is not None else "",
                    "amount": f"{float(sub['unit_price']):,.2f}" if sub.get("unit_price") is not None else "",
                }
                for sub in sub_items
            ],
        })

    # `total` above is the sum of each line's quantity * unit_price — the
    # pre-tax subtotal. The document's own stored subtotal/tax_total/total
    # (kept in sync with its items on every create/update — see
    # _process_items in routers/documents.py) are the authoritative source
    # for the tax breakdown itself; falling back to the recomputed subtotal
    # only covers a document saved before tax_total existed.
    subtotal_value = float(document.subtotal) if document.subtotal is not None else total
    tax_total_value = float(document.tax_total) if document.tax_total is not None else 0.0
    grand_total_value = float(document.total) if document.total is not None else subtotal_value + tax_total_value
    # Tax is a single rate applied uniformly to every line item (see the
    # document-level "Tax rate %" field in web/js/app.js) — reading it off
    # the first item mirrors how the app's own document-detail view labels
    # it (`doc.items[0].tax_rate`), rather than recomputing a rate from
    # amounts, which would divide by zero for a zero-subtotal document.
    tax_rate_value = 0.0
    if items_with_product and items_with_product[0][0].tax_rate is not None:
        tax_rate_value = float(items_with_product[0][0].tax_rate)

    html_str = template.render(
        logo_data_uri=logo_data_uri,
        seal_data_uri=seal_data_uri,
        director=director,
        director_seal_data_uri=director_seal_data_uri,
        issue_date=document.issue_date.isoformat() if document.issue_date else date.today().isoformat(),
        company=company,
        customer=customer_dict,
        customer_address=customer_address,
        doc_number=document.doc_number,
        currency=document.currency,
        group_label=group_label,
        items=items,
        subtotal=f"{subtotal_value:,.2f}",
        tax_rate=f"{tax_rate_value:g}",
        tax_total=f"{tax_total_value:,.2f}",
        has_tax=tax_total_value > 0,
        total=f"{grand_total_value:,.2f}",
        terms_text=document.terms_and_conditions or DEFAULT_TERMS,
    )

    buffer = BytesIO()
    HTML(string=html_str).write_pdf(buffer)
    buffer.seek(0)
    return buffer

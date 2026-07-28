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


def generate_quotation_pdf(document, customer, items_with_product, company=None, logo_bytes=None, seal_bytes=None, logo_mime="image/png", seal_mime="image/png") -> BytesIO:
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
        description = (product.get("description") if product else None) or item.description or ""
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

    html_str = template.render(
        logo_data_uri=logo_data_uri,
        seal_data_uri=seal_data_uri,
        issue_date=document.issue_date.isoformat() if document.issue_date else date.today().isoformat(),
        company=company,
        customer=customer_dict,
        customer_address=customer_address,
        doc_number=document.doc_number,
        currency=document.currency,
        group_label=group_label,
        items=items,
        total=f"{total:,.2f}",
        terms_text=document.terms_and_conditions or DEFAULT_TERMS,
    )

    buffer = BytesIO()
    HTML(string=html_str).write_pdf(buffer)
    buffer.seek(0)
    return buffer

"""customer -> quotation -> read back -> PDF/XLSX export -> soft delete -> trash."""
import io

from pypdf import PdfReader

from conftest import poll, unique


def test_quotation_lifecycle(admin, customer, quotation):
    doc_id = quotation["id"]
    assert quotation["doc_type"] == "quotation"
    assert quotation["customer_id"] == customer["id"]
    assert len(quotation["items"]) == 1

    got = admin.get(f"/documents/{doc_id}")
    assert got.status_code == 200, got.text
    assert got.json()["doc_number"] == quotation["doc_number"]
    assert float(got.json()["total"]) == 20.0

    pdf = admin.get(f"/documents/{doc_id}/export/quotation-pdf")
    assert pdf.status_code == 200, pdf.text[:300]
    assert pdf.content.startswith(b"%PDF"), pdf.content[:40]

    xlsx = admin.get(f"/documents/{doc_id}/export/quotation")
    assert xlsx.status_code == 200, xlsx.text[:300]
    # XLSX is a zip container.
    assert xlsx.content.startswith(b"PK"), xlsx.content[:40]

    trashed = admin.patch(f"/documents/{doc_id}/trash")
    assert trashed.status_code == 200, trashed.text

    trash = admin.get("/documents/trash")
    assert trash.status_code == 200, trash.text
    assert doc_id in {d["id"] for d in trash.json()}

    active = admin.get("/documents")
    assert active.status_code == 200, active.text
    assert doc_id not in {d["id"] for d in active.json()}


def test_quotation_pdf_supplier_block_shows_company_heading_and_selected_md(admin, customer):
    """Loop iteration 1: the PDF's SUPPLIER heading carries the company name
    and the rows below it describe the MD picked on the document
    (director_id) — checked end to end through the gateway."""
    company_name = unique("Smoke Supplier Co")
    md_name = unique("U Smoke MD")
    company = admin.post("/companies", json={"name": company_name, "short_name": unique("SM")[:12],
                                             "address": "Company Road", "contact_no": "01-000"})
    assert company.status_code == 201, company.text
    company_id = company.json()["id"]
    doc_id = None
    try:
        director = admin.post(f"/companies/{company_id}/directors", json={
            "name": md_name, "position": "Managing Director",
            "address": "BUILDING NO.4", "contact_no": "09-988733966"})
        assert director.status_code == 201, director.text

        doc = admin.post("/documents", json={
            "doc_type": "quotation", "customer_id": customer["id"], "currency": "USD",
            "company_id": company_id, "director_id": director.json()["id"],
            "items": [{"description": unique("test-item"), "quantity": "1", "unit_price": "5.00"}]})
        assert doc.status_code == 201, doc.text
        doc_id = doc.json()["id"]

        pdf = admin.get(f"/documents/{doc_id}/export/quotation-pdf")
        assert pdf.status_code == 200, pdf.text[:300]
        text = "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf.content)).pages)
        assert f"SUPPLIER: {company_name}" in text
        assert md_name in text.split("SUPPLIER", 1)[1].split("END USER", 1)[0]
    finally:
        # Documents first: a company still referenced by one can't be purged.
        if doc_id:
            admin.delete(f"/documents/{doc_id}")
        admin.delete(f"/companies/{company_id}")


def test_quotation_with_one_time_manual_customer(admin):
    """Loop iteration 7: a customer typed on the document only. It prints in
    the PDF's END USER block, is never added to /customers, and the document
    is searchable by the typed name."""
    name = unique("Manual Buyer")  # unique token so search can't match an older run
    doc = admin.post("/documents", json={
        "doc_type": "quotation", "currency": "USD", "customer_id": None,
        "end_user_name": name, "end_user_organization": "Smoke Manual Org",
        "end_user_address": "No. 7, Smoke Street, Yangon", "end_user_phone": "09-000", "end_user_email": "m@x.example",
        "items": [{"description": unique("test-item"), "quantity": "1", "unit_price": "5.00"}]})
    assert doc.status_code == 201, doc.text
    doc_id = doc.json()["id"]
    try:
        assert doc.json()["customer_id"] is None and doc.json()["end_user_name"] == name

        pdf = admin.get(f"/documents/{doc_id}/export/quotation-pdf")
        assert pdf.status_code == 200, pdf.text[:300]
        end_user = PdfReader(io.BytesIO(pdf.content)).pages[0].extract_text().split("END USER", 1)[1]
        assert name in end_user
        assert "Organization Smoke Manual Org" in end_user
        assert "Address No. 7, Smoke Street, Yangon" in end_user

        customers = admin.get("/customers")
        assert customers.status_code == 200, customers.text
        assert name not in {c["name"] for c in customers.json()}

        def _found():
            r = admin.get("/search", params={"q": name, "index": "documents"})
            assert r.status_code == 200, r.text
            return [h for h in r.json().get("documents", []) if h.get("id") == doc_id]
        assert poll(_found), f"document not searchable by the manual customer name {name!r} within 10s"
    finally:
        admin.delete(f"/documents/{doc_id}")


def test_quotation_for_a_saved_customer_with_an_organization(admin):
    """Loop iteration 9: a saved customer's optional Organization prints
    separately from its Name in the PDF's END USER block."""
    name = unique("Org Smoke Buyer")
    org = unique("Org Smoke Holdings")
    cust = admin.post("/customers", json={"name": name, "organization": org})
    assert cust.status_code == 201, cust.text
    assert cust.json()["organization"] == org
    doc_id = None
    try:
        doc = admin.post("/documents", json={
            "doc_type": "quotation", "currency": "USD", "customer_id": cust.json()["id"],
            "items": [{"description": unique("test-item"), "quantity": "1", "unit_price": "5.00"}]})
        assert doc.status_code == 201, doc.text
        doc_id = doc.json()["id"]
        pdf = admin.get(f"/documents/{doc_id}/export/quotation-pdf")
        assert pdf.status_code == 200, pdf.text[:300]
        end_user = PdfReader(io.BytesIO(pdf.content)).pages[0].extract_text().split("END USER", 1)[1]
        assert name in end_user
        assert f"Organization {org}" in end_user
    finally:
        # documents first: a customer still referenced by one can't be deleted
        if doc_id:
            admin.delete(f"/documents/{doc_id}")
        admin.delete(f"/customers/{cust.json()['id']}")

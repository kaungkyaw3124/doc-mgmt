"""customer -> quotation -> read back -> PDF/XLSX export -> soft delete -> trash."""


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

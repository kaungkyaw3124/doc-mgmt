"""category -> product -> fetch."""


def test_category_and_product(admin, category, product):
    assert product["category"] == category["name"]
    # SKU is generated from the category's short_term when omitted.
    assert product["sku"].startswith(category["short_term"])

    got = admin.get(f"/products/{product['id']}")
    assert got.status_code == 200, got.text
    assert got.json()["name"] == product["name"]

    listed = admin.get("/categories")
    assert listed.status_code == 200, listed.text
    assert category["name"] in {c["name"] for c in listed.json()}

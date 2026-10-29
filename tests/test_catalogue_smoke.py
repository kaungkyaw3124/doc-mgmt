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


def test_duplicate_product_is_blocked_and_reported(admin, product):
    """Loop iteration 8: creating the same product again (same name and
    description) is a 409; the duplicate-check / duplicates routes answer
    through the gateway. The fixture removes the product afterwards."""
    again = admin.post("/products", json={"name": product["name"], "category": product["category"]})
    assert again.status_code == 409, again.text
    assert again.json()["detail"]["existing"]["sku"] == product["sku"]

    check = admin.get("/products/duplicate-check", params={"name": product["name"]})
    assert check.status_code == 200, check.text
    assert product["id"] in {p["id"] for p in check.json()["exact"]}

    groups = admin.get("/products/duplicates")
    assert groups.status_code == 200, groups.text
    assert set(groups.json()) == {"exact_groups", "same_name_groups"}

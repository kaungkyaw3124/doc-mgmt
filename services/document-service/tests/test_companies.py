"""
Schema-level regression tests for Company/CompanyDirector: Company keeps
position/address/contact_no/support_email (used by the quotation Supplier
block and signer/signature logic) — only support_phone was removed, since
nothing in the app ever used it and contact_no already covers the
company's phone number. CompanyDirector separately carries its own
address/contact_no/email/seal for the selected Managing Director shown
alongside (not instead of) the company's own Supplier info.

No live Postgres needed — these test the Pydantic schemas directly, the
same way test_gateway_auth.py avoids needing a DB for what it covers.
Full create/update/document-generation behavior against a real database is
covered by the infra-integration CI job (real HTTP through the stack).
"""

from app import schemas


KEPT_COMPANY_FIELDS = {"name", "short_name", "position", "address", "contact_no", "support_email"}


def test_company_create_still_has_kept_fields():
    assert KEPT_COMPANY_FIELDS <= schemas.CompanyCreate.model_fields.keys()


def test_company_update_still_has_kept_fields():
    assert KEPT_COMPANY_FIELDS <= schemas.CompanyUpdate.model_fields.keys()


def test_company_out_still_has_kept_fields():
    assert KEPT_COMPANY_FIELDS <= schemas.CompanyOut.model_fields.keys()


def test_company_create_no_longer_has_support_phone():
    assert "support_phone" not in schemas.CompanyCreate.model_fields
    assert "support_phone" not in schemas.CompanyUpdate.model_fields
    assert "support_phone" not in schemas.CompanyOut.model_fields


def test_company_out_still_has_logo_and_seal():
    fields = schemas.CompanyOut.model_fields.keys()
    assert "logo_object_key" in fields
    assert "seal_object_key" in fields


def test_company_create_round_trips_supplier_fields():
    company = schemas.CompanyCreate(
        name="Trustwell International Co., Ltd.",
        short_name="TW",
        position="Director",
        address="No. 45, Kabar Aye Pagoda Road, Yangon, Myanmar",
        contact_no="+95 9 123 456 789",
        support_email="support@trustwell.example.com",
    )
    assert company.name == "Trustwell International Co., Ltd."
    assert company.position == "Director"
    assert company.address == "No. 45, Kabar Aye Pagoda Road, Yangon, Myanmar"
    assert company.contact_no == "+95 9 123 456 789"
    assert company.support_email == "support@trustwell.example.com"


def test_extra_support_phone_in_payload_is_ignored_not_stored():
    """Pydantic drops unknown keys by default (no extra='forbid' configured
    anywhere in this schema module) — a client still sending the old
    'support_phone' field can't cause it to be persisted; it's simply
    dropped before it ever reaches the model layer."""
    company = schemas.CompanyCreate(name="Acme", support_phone="+95 9 000 000 000")
    assert not hasattr(company, "support_phone")


def test_company_director_create_has_own_contact_fields():
    fields = schemas.CompanyDirectorCreate.model_fields.keys()
    assert {"name", "address", "contact_no", "email"} <= set(fields)


def test_company_director_create_only_requires_name():
    director = schemas.CompanyDirectorCreate(name="Aung Aung")
    assert director.name == "Aung Aung"
    assert director.address is None
    assert director.contact_no is None
    assert director.email is None


def test_company_director_create_accepts_full_payload():
    director = schemas.CompanyDirectorCreate(
        name="Aung Aung",
        address="Mayangon, Yangon",
        contact_no="+95999999999",
        email="aungaung@gmail.com",
    )
    assert director.address == "Mayangon, Yangon"
    assert director.contact_no == "+95999999999"
    assert director.email == "aungaung@gmail.com"


def test_company_director_update_fields_all_optional():
    update = schemas.CompanyDirectorUpdate()
    assert update.name is None
    assert update.address is None
    assert update.contact_no is None
    assert update.email is None


def test_company_director_update_supports_partial_edit():
    update = schemas.CompanyDirectorUpdate(contact_no="+95988888888")
    dumped = update.model_dump(exclude_unset=True)
    assert dumped == {"contact_no": "+95988888888"}


def test_company_director_out_has_own_contact_fields():
    fields = schemas.CompanyDirectorOut.model_fields.keys()
    assert {"name", "address", "contact_no", "email", "seal_object_key", "sort_order"} <= set(fields)


def test_company_and_director_contact_fields_are_independent():
    """Company and CompanyDirector each carry their own address/contact_no
    (and their own email-equivalent field, support_email vs email) —
    setting one's fields must never require or imply the other's, and the
    two must never collapse into a single shared field set."""
    company_fields = set(schemas.CompanyOut.model_fields.keys())
    director_fields = set(schemas.CompanyDirectorOut.model_fields.keys())
    assert "email" not in company_fields  # Company uses support_email, not email
    assert "support_email" not in director_fields  # Director uses email, not support_email

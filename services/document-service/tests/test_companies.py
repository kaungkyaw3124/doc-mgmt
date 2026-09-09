"""
Schema-level regression tests for the Company/CompanyDirector restructuring
(signer/contact info moved off Company onto individual Managing Directors).
No live Postgres needed — these test the Pydantic schemas directly, the
same way test_gateway_auth.py avoids needing a DB for what it covers.
Full create/update/document-generation behavior against a real database is
covered by the infra-integration CI job (real HTTP through the stack).
"""

from app import schemas


REMOVED_COMPANY_FIELDS = {"position", "address", "contact_no", "support_email", "support_phone"}


def test_company_create_no_longer_has_removed_fields():
    assert REMOVED_COMPANY_FIELDS.isdisjoint(schemas.CompanyCreate.model_fields.keys())


def test_company_update_no_longer_has_removed_fields():
    assert REMOVED_COMPANY_FIELDS.isdisjoint(schemas.CompanyUpdate.model_fields.keys())


def test_company_out_no_longer_has_removed_fields():
    assert REMOVED_COMPANY_FIELDS.isdisjoint(schemas.CompanyOut.model_fields.keys())


def test_company_create_still_has_name_short_name():
    fields = schemas.CompanyCreate.model_fields.keys()
    assert "name" in fields
    assert "short_name" in fields


def test_company_out_still_has_logo_and_seal():
    fields = schemas.CompanyOut.model_fields.keys()
    assert "logo_object_key" in fields
    assert "seal_object_key" in fields


def test_company_create_works_without_removed_fields():
    company = schemas.CompanyCreate(name="Trustwell International Co., Ltd.", short_name="TW")
    assert company.name == "Trustwell International Co., Ltd."
    assert not hasattr(company, "position")
    assert not hasattr(company, "address")


def test_extra_removed_field_in_payload_is_ignored_not_stored():
    """Pydantic drops unknown keys by default (no extra='forbid' configured
    anywhere in this schema module) — a client still sending the old
    'position' field can't cause it to be persisted; it's simply dropped
    before it ever reaches the model layer."""
    company = schemas.CompanyCreate(name="Acme", position="Director")
    assert not hasattr(company, "position")


def test_company_director_create_has_new_contact_fields():
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


def test_company_director_out_has_new_contact_fields():
    fields = schemas.CompanyDirectorOut.model_fields.keys()
    assert {"name", "address", "contact_no", "email", "seal_object_key", "sort_order"} <= set(fields)

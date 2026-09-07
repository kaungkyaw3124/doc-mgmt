from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # "production" enables fail-fast startup validation of secrets (see
    # app/core/secrets_check.py); anything else only warns.
    environment: str = "development"

    database_url: str = "postgresql://docmgmt:docmgmt@postgres:5432/docmgmt"

    catalogue_service_url: str = "http://catalogue-service:8000"

    # Shared secret Nginx (and sibling services calling this one directly —
    # see app/core/document_client.py in catalogue-service/search-service)
    # must present on every request, via the X-Internal-Secret header —
    # see app/core/gateway_auth.py. Without this, a caller with any
    # network path to this service (not just through Nginx) could set
    # X-Allowed-Projects/X-Access-Level/etc. itself and grant itself
    # arbitrary access. Never sent to a browser.
    internal_shared_secret: str = "local_dev_internal_secret_change_me"

    meili_url: str = "http://meilisearch:7700"
    meili_master_key: str = "local_dev_master_key_change_me"

    minio_endpoint: str = "minio:9000"                 # internal, container-to-container
    # What a browser/host machine can reach — routed through Nginx (see
    # infra/nginx/nginx.conf's /documents/ location), NOT MinIO's own
    # port directly, which is no longer published to the host.
    minio_public_endpoint: str = "localhost:8080"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "documents"
    minio_secure: bool = False

    # Company profile used on generated Quotation/Invoice exports — same
    # across every document, so configured once here rather than per-document.
    supplier_name: str = "Trustwell International Co., Ltd."
    supplier_position: str = "Director"
    supplier_address: str = ""
    supplier_contact: str = ""
    support_email: str = ""
    support_phone: str = ""

    class Config:
        env_file = ".env"


settings = Settings()

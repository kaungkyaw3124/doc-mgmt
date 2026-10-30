from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # "production" enables fail-fast startup validation of secrets (see
    # app/core/secrets_check.py); anything else only warns.
    environment: str = "development"

    meili_url: str = "http://meilisearch:7700"
    meili_master_key: str = "local_dev_master_key_change_me"

    document_service_url: str = "http://document-service:8000"

    # Shared secret Nginx (and this service's own calls to document-service
    # — see app/core/document_client.py) must present, via the
    # X-Internal-Secret header — see app/core/gateway_auth.py. Never sent
    # to a browser.
    internal_shared_secret: str = "local_dev_internal_secret_change_me"

    class Config:
        env_file = ".env"


settings = Settings()

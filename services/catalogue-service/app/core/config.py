from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # "production" enables fail-fast startup validation of secrets (see
    # app/core/secrets_check.py); anything else only warns.
    environment: str = "development"

    database_url: str = "postgresql://docmgmt:docmgmt@postgres:5432/catalogue"

    document_service_url: str = "http://document-service:8000"

    meili_url: str = "http://meilisearch:7700"
    meili_master_key: str = "local_dev_master_key_change_me"

    minio_endpoint: str = "minio:9000"
    # What a browser/host machine can reach — routed through Nginx (see
    # infra/nginx/nginx.conf's /products/ location), NOT MinIO's own
    # port directly, which is no longer published to the host.
    minio_public_endpoint: str = "localhost:8080"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "products"
    minio_secure: bool = False

    class Config:
        env_file = ".env"


settings = Settings()

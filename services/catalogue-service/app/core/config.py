from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://docmgmt:docmgmt@postgres:5432/catalogue"

    document_service_url: str = "http://document-service:8000"

    meili_url: str = "http://meilisearch:7700"
    meili_master_key: str = "local_dev_master_key_change_me"

    minio_endpoint: str = "minio:9000"
    minio_public_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "products"
    minio_secure: bool = False

    class Config:
        env_file = ".env"


settings = Settings()

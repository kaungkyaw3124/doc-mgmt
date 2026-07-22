from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    meili_url: str = "http://meilisearch:7700"
    meili_master_key: str = "local_dev_master_key_change_me"

    class Config:
        env_file = ".env"


settings = Settings()

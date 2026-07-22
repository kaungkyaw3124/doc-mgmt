from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://docmgmt:docmgmt@postgres:5432/auth"

    # Used only to seed the first admin user on startup if the users table
    # is empty. After that, this env var has no further effect — the real
    # password lives (hashed) in the database and can be changed via the
    # database or a future "change password" endpoint.
    seed_admin_username: str = "admin"
    seed_admin_password: str = "changeme"

    jwt_secret: str = "local_dev_jwt_secret_change_me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    class Config:
        env_file = ".env"


settings = Settings()

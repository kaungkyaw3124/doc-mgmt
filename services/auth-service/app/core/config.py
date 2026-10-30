from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # "production" enables fail-fast startup validation of secrets (see
    # app/core/secrets_check.py) — anything else (default: "development")
    # only warns, so local/dev boots stay convenient.
    environment: str = "development"

    database_url: str = "postgresql://docmgmt:docmgmt@postgres:5432/auth"

    # Used only to seed the first admin user on startup if the users table
    # is empty. After that, this env var has no further effect — the real
    # password lives (hashed) in the database and can be changed via the
    # database or a future "change password" endpoint.
    seed_admin_username: str = "admin"
    seed_admin_password: str = "changeme"

    jwt_secret: str = "local_dev_jwt_secret_change_me"
    jwt_algorithm: str = "HS256"
    # Short-lived on purpose — the browser never persists this token
    # (kept in a JS variable only, not localStorage); a refresh token in
    # an HttpOnly cookie (see refresh_token_expire_days below) transparently
    # renews it, so a short access-token lifetime costs nothing in UX while
    # capping how long a stolen-from-memory/XSS'd token stays useful.
    jwt_expire_minutes: int = 15

    # Refresh token lifetime — the HttpOnly/SameSite cookie set on login,
    # used only to mint new access tokens via POST /refresh (see
    # app/core/refresh_tokens.py). Each use rotates it (the old one is
    # revoked), so a given refresh token is single-use.
    refresh_token_expire_days: int = 14

    # Login rate limiting (see app/core/rate_limit.py). All windows are
    # sliding, backed by the shared `login_attempts` table in Postgres, so
    # these limits are enforced correctly across multiple auth-service
    # instances/replicas (no in-memory/per-process state).
    #
    # - ip: caps total failed attempts from one source IP, any username.
    #   Stops credential stuffing (many usernames) from a single source.
    # - pair: caps failed attempts against one (ip, username) combination.
    #   The main brute-force guard — deliberately NOT keyed on username
    #   alone, so an attacker cannot lock a victim out just by failing
    #   login for the victim's username (that would be a trivial DoS).
    # - username: a looser global cap on one username across ALL source
    #   IPs, so a distributed (many-IP) brute force against one account is
    #   still bounded. Threshold is generous enough that a legitimate user
    #   mistyping their password a few times never trips it, and — like
    #   the other two — it self-heals when the window elapses, it never
    #   requires an admin to unlock the account.
    rate_limit_ip_max_attempts: int = 20
    rate_limit_ip_window_minutes: int = 15
    rate_limit_pair_max_attempts: int = 5
    rate_limit_pair_window_minutes: int = 15
    rate_limit_username_max_attempts: int = 15
    rate_limit_username_window_minutes: int = 15

    class Config:
        env_file = ".env"


settings = Settings()

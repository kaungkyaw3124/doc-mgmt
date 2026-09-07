from fastapi import FastAPI

from app.core.config import settings
from app.core.secrets_check import enforce_production_secrets, is_insecure
from app.core.gateway_auth import verify_gateway_secret
from app.routers import search

app = FastAPI(title="Search Service", version="0.1.0")

app.middleware("http")(verify_gateway_secret)

app.include_router(search.router)


def _secret_problems() -> list[str]:
    problems = []
    if is_insecure(settings.meili_master_key, {"local_dev_master_key_change_me"}):
        problems.append("MEILI_MASTER_KEY")
    if is_insecure(settings.internal_shared_secret, {"local_dev_internal_secret_change_me"}):
        problems.append("INTERNAL_SHARED_SECRET")
    return problems


@app.on_event("startup")
def on_startup():
    enforce_production_secrets(settings.environment, _secret_problems())


@app.get("/health")
def health():
    return {"status": "ok"}

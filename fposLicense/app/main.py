from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api import admin_router, device_router
from .config import get_settings
from .security import require_admin


ROOT = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.environment.lower() == "production":
        missing = []
        if not settings.admin_password_hash:
            missing.append("FPOS_LICENSE_ADMIN_PASSWORD_HASH")
        if not settings.signing_private_key_b64:
            missing.append("FPOS_LICENSE_SIGNING_PRIVATE_KEY_B64")
        if missing:
            raise RuntimeError("Missing production secrets: " + ", ".join(missing))
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

origins = [origin.strip() for origin in settings.allowed_origins.split(",") if origin.strip()]
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

app.include_router(device_router)
app.include_router(admin_router)


@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/admin", include_in_schema=False, dependencies=[Depends(require_admin)])
def dashboard() -> FileResponse:
    return FileResponse(ROOT / "static" / "admin.html")

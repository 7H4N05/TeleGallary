"""
TeleGallery Backend — Main Entry Point
FastAPI application with lifespan management.
"""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.upload import router as upload_router
from api.accounts import router as accounts_router
from api.sessions import router as sessions_router
from api.logs import router as logs_router
from api.folders import router as folders_router
from api.settings import router as settings_router
from config.settings import get_settings
from db.database import init_db
from utils.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    logger.info("Starting TeleGallery backend", version=settings.app_version)
    try:
        import tgcrypto  # noqa: F401

        logger.info("TgCrypto loaded — fast Telegram transfers enabled")
    except ImportError:
        logger.warning(
            "TgCrypto not installed — uploads will be slower. "
            "Run: backend\\venv\\Scripts\\pip install TgCrypto"
        )
    await init_db()
    logger.info("Database initialized")
    await _warm_telegram_accounts()
    yield
    logger.info("Shutting down TeleGallery backend")


async def _warm_telegram_accounts():
    """Connect all saved accounts so uploads and FloodWait failover work without manual Connect."""
    from db.database import AsyncSessionLocal
    from db.repositories.account_repo import AccountRepository
    from telegram.client_manager import get_client_manager
    from utils.session_crypto import maybe_decrypt_session

    try:
        async with AsyncSessionLocal() as db:
            repo = AccountRepository(db)
            accounts = await repo.list_all()
        mgr = get_client_manager()
        for a in accounts:
            if not a.session_string:
                continue
            try:
                plain = maybe_decrypt_session(a.session_string)
                await mgr.add_client(a.id, a.phone, a.api_id, a.api_hash, plain)
                if await mgr.start_client(a.id):
                    logger.info("Warmed Telegram client", account_id=a.id, phone=a.phone)
            except Exception as e:
                logger.warning("Warm account skipped", account_id=a.id, error=str(e))
    except Exception as e:
        logger.warning("Account warm-up skipped", error=str(e))


app = FastAPI(
    title="TeleGallery API",
    description="Backend for TeleGallery — Telegram Photo Gallery Uploader",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tauri app uses custom protocol
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(upload_router, prefix="/api/upload", tags=["Upload"])
app.include_router(accounts_router, prefix="/api/accounts", tags=["Accounts"])
app.include_router(sessions_router, prefix="/api/sessions", tags=["Sessions"])
app.include_router(logs_router, prefix="/api/logs", tags=["Logs"])
app.include_router(folders_router, prefix="/api/folders", tags=["Folders"])
app.include_router(settings_router, prefix="/api/settings", tags=["Settings"])


@app.get("/api/health")
async def health():
    s = get_settings()
    return {"status": "ok", "app": s.app_name, "version": s.app_version}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.backend_host,
        port=settings.backend_port,
        reload=settings.backend_reload,
        log_level=settings.log_level.lower(),
    )

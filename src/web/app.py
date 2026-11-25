"""FastAPI application entrypoint for AI-Invoice-Auditor web portal."""

import asyncio
import contextlib
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .routes import router
from ..database import init_db, DEFAULT_DB_PATH
from ..services.watch_folder import watch_folder_service

logger = logging.getLogger(__name__)


async def _watch_folder_poll_loop(interval_seconds: int) -> None:
    """Optional background polling loop when WATCH_FOLDER_AUTO_POLL_SECONDS > 0."""
    engine = os.getenv("WATCH_FOLDER_ENGINE", "gemini" if os.getenv("GEMINI_API_KEY") else "mock")
    while True:
        try:
            watch_folder_service.scan_and_ingest(engine=engine, is_privileged=True)
        except Exception as exc:
            logger.warning("Watch folder background scan skipped: %s", exc)
        await asyncio.sleep(interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events: initialize database and optional watch folder poller on startup."""
    init_db(DEFAULT_DB_PATH)
    poll_task = None
    poll_seconds = int(os.getenv("WATCH_FOLDER_AUTO_POLL_SECONDS", "0") or "0")
    if poll_seconds > 0:
        poll_task = asyncio.create_task(_watch_folder_poll_loop(poll_seconds))
    try:
        yield
    finally:
        if poll_task is not None:
            poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await poll_task


app = FastAPI(
    title="AI-Invoice-Auditor Portal",
    description="Enterprise IDP & Expense Audit System for Corporate Receipts",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for convenience
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router)

from .auth_routes import auth_router
app.include_router(auth_router)

from .approval_routes import approval_router
app.include_router(approval_router)

from .trip_routes import trip_router
app.include_router(trip_router)

from .storage_routes import storage_router
app.include_router(storage_router)

from .watch_routes import watch_router
app.include_router(watch_router)

# Mount SQLAdmin Enterprise Admin Portal
from .admin_views import setup_admin
setup_admin(app)

# Mount static folder
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/sw.js")
async def serve_service_worker():
    """Serve root-scoped PWA Service Worker."""
    sw_file = static_dir / "sw.js"
    return FileResponse(
        str(sw_file),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.get("/")
async def serve_index():
    """Serve single page web application."""
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "AI-Invoice-Auditor API is running. UI index.html not found."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.web.app:app", host="0.0.0.0", port=8000, reload=True)

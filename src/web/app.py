"""FastAPI application entrypoint for AI-Invoice-Auditor web portal."""

from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

from .routes import router
from ..database import init_db, DEFAULT_DB_PATH


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events: initialize database on startup."""
    init_db(DEFAULT_DB_PATH)
    yield


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

from .storage_routes import storage_router
app.include_router(storage_router)

# Mount SQLAdmin Enterprise Admin Portal
from .admin_views import setup_admin
setup_admin(app)

# Mount static folder
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")



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

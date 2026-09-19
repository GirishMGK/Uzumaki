from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1 import api_router
from app.core.config import get_settings
from app.jobs import capacity_job, digest_job

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.enable_background_jobs:
        capacity_job.start_scheduler()
        digest_job.start_scheduler()
    yield
    if settings.enable_background_jobs:
        capacity_job.stop_scheduler()
        digest_job.stop_scheduler()


app = FastAPI(
    title="Firm RMS API",
    description="Resource Management System for a multi-office CA firm.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


# Optional single-process mode: when RMS_STATIC_DIR points at a built
# frontend (used by the PyInstaller desktop build, see desktop/launcher.py),
# serve it here so the whole app runs as one process on one port. Off by
# default — normal dev/docker deployments serve the frontend separately
# (Vite dev server / nginx). Registered last so it never shadows /api/* or
# /health; unmatched non-API paths fall back to index.html so client-side
# (SPA) routes work on a hard refresh or direct link.
_static_dir = Path(settings.static_dir) if settings.static_dir else None
if _static_dir is not None and _static_dir.is_dir():
    if (_static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=_static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str) -> FileResponse:
        candidate = _static_dir / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_static_dir / "index.html")

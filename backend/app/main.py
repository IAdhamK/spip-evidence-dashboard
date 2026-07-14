import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.analysis.routes import create_analysis_router
from app.analysis.jobs import AnalysisJobManager
from app.analysis.package_routes import create_package_router
from app.config import get_settings
from app.database import Database
from app.frontend_static import NoStoreStaticFiles, no_store_file_response
from app.lifecycle import analysis_lifespan
from app.routes import create_router


settings = get_settings()
db = Database(settings.database_path)
db.ensure_mapping()
db.ensure_parameters()
db.normalize_lumbung_links()

analysis_job_manager = AnalysisJobManager(db, settings)
app = FastAPI(
    title="SPIP Evidence Dashboard",
    version="0.1.0",
    lifespan=analysis_lifespan(analysis_job_manager, settings.analysis_pipeline_v2_enabled),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(create_router(db, analysis_job_manager))
app.include_router(create_analysis_router(db, analysis_job_manager))
app.include_router(create_package_router(db))

static_dir_value = os.environ.get("STATIC_DIR")
static_dir = Path(static_dir_value).resolve() if static_dir_value else None
if static_dir and static_dir.exists():
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", NoStoreStaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        candidate = static_dir / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return no_store_file_response(candidate)
        return no_store_file_response(static_dir / "index.html")

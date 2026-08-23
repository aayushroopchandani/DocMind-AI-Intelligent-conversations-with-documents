import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apis.users import router as users_router
from apis.chats import router as chats_router
from apis.documents import router as documents_router
from apis.quiz_attempts import router as quiz_attempts_router
from apis.tables import router as tables_router
from apis.analysis_runs import router as analysis_runs_router
from apis.analysis_artifacts import router as analysis_artifacts_router
from apis.analysis_spreadsheets import router as analysis_spreadsheets_router
from apis.analysis_plans import router as analysis_plans_router
from apis.analysis_patches import router as analysis_patches_router
from apis.analysis_executions import router as analysis_executions_router
from apis.analysis_diagnostics import router as analysis_diagnostics_router
from config.settings import settings
from db.mongodb import init_mongodb, close_mongodb
from services.cloudinary_setup import init_cloudinary
from scripts.data_analysis_agent.extraction.pipeline import (
    cancel_docling_table_fallbacks,
)
from scripts.data_analysis_agent.runtime.bootstrap import (
    AnalysisRuntime,
    build_analysis_runtime,
)
from scripts.data_analysis_agent.runtime.http import (
    AnalysisRequestBodyLimitMiddleware,
    RequestBodyLimit,
)
from scripts.data_analysis_agent.runtime.observability import (
    configure_analysis_json_logging,
)
from scripts.data_analysis_agent.runtime.services.sse_connections import (
    SSEConnectionLimiter,
    SSEConnectionLimits,
)


logger = logging.getLogger(__name__)

# The ONE FastAPI app for the whole project.
# Routers (like ask_router) are registered here and their endpoints become part of this app.
app = FastAPI(title="DocMind API")

# Register the request guard first. Starlette inserts later middleware outside
# earlier middleware, so CORS still decorates limit errors for the frontend.
app.add_middleware(
    AnalysisRequestBodyLimitMiddleware,
    limits={
        "/analysis/runs": RequestBodyLimit(
            max_body_bytes=settings.analysis_max_run_request_bytes,
            error_code="analysis_request_too_large",
            message=(
                "Analysis run request exceeds the configured byte limit."
            ),
        ),
        "/analysis/artifacts": RequestBodyLimit(
            max_body_bytes=settings.analysis_max_artifact_request_bytes,
            error_code="analysis_artifact_request_too_large",
            message=(
                "Analysis artifact request exceeds the configured byte limit."
            ),
        ),
        "/analysis/spreadsheets": RequestBodyLimit(
            max_body_bytes=settings.analysis_max_spreadsheet_request_bytes,
            error_code="analysis_spreadsheet_request_too_large",
            message=(
                "Spreadsheet request exceeds the configured byte limit."
            ),
        ),
    },
)
# CORS — lets the Next.js frontend (localhost:3000) call this API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(users_router)
app.include_router(chats_router)
app.include_router(documents_router)
app.include_router(quiz_attempts_router)
app.include_router(tables_router)
app.include_router(analysis_runs_router)
app.include_router(analysis_plans_router)
app.include_router(analysis_patches_router)
app.include_router(analysis_executions_router)
app.include_router(analysis_artifacts_router)
app.include_router(analysis_spreadsheets_router)
app.include_router(analysis_diagnostics_router)
app.state.analysis_runtime = None
app.state.analysis_run_service = None
app.state.analysis_artifact_service = None
app.state.analysis_planning_service = None
app.state.analysis_patch_service = None
app.state.analysis_patch_repository = None
app.state.analysis_execution_reader = None
app.state.analysis_diagnostics_service = None
app.state.analysis_sse_limiter = SSEConnectionLimiter(
    SSEConnectionLimits(
        total=settings.analysis_sse_max_connections,
        per_user=settings.analysis_sse_max_connections_per_user,
        per_run=settings.analysis_sse_max_connections_per_run,
    )
)


@app.on_event("startup")
async def on_startup() -> None:
    # Setup only: initializes connections/config if env vars are present.
    configure_analysis_json_logging()
    await init_mongodb()
    init_cloudinary()
    if settings.mongodb_is_configured:
        try:
            runtime = build_analysis_runtime()
            await runtime.start()
        except RuntimeError:
            logger.exception("Data-analysis runtime could not be started")
        else:
            app.state.analysis_runtime = runtime
            app.state.analysis_run_service = runtime.run_service
            app.state.analysis_artifact_service = runtime.artifact_service
            app.state.analysis_planning_service = runtime.planning_service
            app.state.analysis_patch_service = runtime.patch_service
            app.state.analysis_patch_repository = runtime.patch_repository
            app.state.analysis_execution_reader = runtime.execution_reader
            app.state.analysis_diagnostics_service = runtime.diagnostics_service


@app.on_event("shutdown")
async def on_shutdown() -> None:
    runtime: AnalysisRuntime | None = app.state.analysis_runtime
    if runtime is not None:
        await runtime.stop()
    app.state.analysis_runtime = None
    app.state.analysis_run_service = None
    app.state.analysis_artifact_service = None
    app.state.analysis_planning_service = None
    app.state.analysis_patch_service = None
    app.state.analysis_patch_repository = None
    app.state.analysis_execution_reader = None
    app.state.analysis_diagnostics_service = None
    await cancel_docling_table_fallbacks()
    await close_mongodb()

from __future__ import annotations

from dataclasses import dataclass

from config.settings import Settings, settings
from db.mongodb import get_db

from .repositories import (
    MongoAnalysisPlanRepository,
    MongoArtifactRepository,
    MongoAnalysisRunStore,
)
from .execution import ExecutionLimits, MongoNormalizedInputResolver
from .execution.native.subprocess_backend import SubprocessNativeBackend
from .execution.publication import ResultPublisher
from .execution.service import NativeExecutionService
from .execution.results.reader import BlobExecutionResultReader
from .placement import WriteReservationService
from .repositories.executions import MongoExecutionRepository
from .repositories.patches import MongoPatchProposalRepository
from .repositories.reservations import MongoSpatialReservationRepository
from .planning import (
    AnalysisPlanningService,
    ExecutorCapabilities,
    PlanResourcePolicy,
    PlanningContextBuilder,
)
from .repositories.datasets import MongoDatasetCatalogRepository
from .services.artifacts import (
    ArtifactServiceConfig,
    ArtifactVersionService,
)
from .services.artifact_reconciler import ArtifactUploadReconciler
from .services.run_service import AnalysisRunService
from .services.state_machine import AnalysisRunStateMachine
from .services.worker import (
    AnalysisWorkerConfig,
    DurableAnalysisWorker,
)
from .services.diagnostics import AnalysisDiagnosticsService
from .services.execution_reader import ExecutionReadService
from .services.patch_service import PatchService, PatchServiceConfig
from .services.workbook_context import (
    WorkbookContextLimits,
    WorkbookContextService,
)
from .storage.cloudinary import (
    CloudinaryArtifactBlobStore,
    CloudinaryBlobStoreConfig,
)
from .storage.patch_payloads import BlobPayloadReader, BlobPayloadWriter
from .storage.validation import ArtifactValidationLimits


@dataclass(frozen=True, slots=True)
class AnalysisRuntime:
    run_service: AnalysisRunService
    worker: DurableAnalysisWorker
    artifact_service: ArtifactVersionService | None
    planning_service: AnalysisPlanningService | None = None
    artifact_reconciler: ArtifactUploadReconciler | None = None
    diagnostics_service: AnalysisDiagnosticsService | None = None
    patch_service: PatchService | None = None
    patch_repository: MongoPatchProposalRepository | None = None
    execution_reader: ExecutionReadService | None = None

    async def start(self) -> None:
        if self.artifact_reconciler is not None:
            await self.artifact_reconciler.start()
        try:
            await self.worker.start()
        except BaseException:
            if self.artifact_reconciler is not None:
                await self.artifact_reconciler.stop()
            raise

    async def stop(self) -> None:
        try:
            await self.worker.stop()
        finally:
            if self.artifact_reconciler is not None:
                await self.artifact_reconciler.stop()


def build_analysis_runtime(
    active_settings: Settings = settings,
) -> AnalysisRuntime:
    """Assemble one process runtime after MongoDB has been initialized."""

    database = get_db()
    run_store = MongoAnalysisRunStore(database)
    state_machine = AnalysisRunStateMachine(
        run_store,
        maximum_lease_seconds=max(
            3600,
            active_settings.analysis_worker_lease_seconds,
        ),
    )
    dataset_catalog = MongoDatasetCatalogRepository()
    # One profile for the whole process. Planning decides whether a plan may be
    # queued and the repository decides the same for an approval, so they must
    # read the identical capability document or the two paths would disagree.
    #
    # The two readiness flags are deployment settings, not constants: they say
    # what this process actually has installed. Admission reads them, so a
    # deployment without the engine still completes at plan_ready, and one
    # without the browser-side patch adapter never parks a run on a handshake
    # nothing will answer.
    capabilities = ExecutorCapabilities(
        native_execution_ready=(
            active_settings.analysis_native_execution_ready
        ),
        workbook_patches_ready=(
            active_settings.analysis_workbook_patches_ready
        ),
    )
    plan_repository = MongoAnalysisPlanRepository(
        database,
        capabilities=capabilities,
    )
    planning_service = AnalysisPlanningService(
        repository=plan_repository,
        state_machine=state_machine,
        context_builder=PlanningContextBuilder(
            capabilities=capabilities,
            resource_policy=PlanResourcePolicy(
                max_context_bytes=(
                    active_settings.analysis_max_planning_context_bytes
                ),
                max_plan_bytes=active_settings.analysis_max_plan_bytes,
                max_steps=active_settings.analysis_max_plan_steps,
                max_rows_scanned=(
                    active_settings.analysis_max_plan_rows_scanned
                ),
                max_cells_written=(
                    active_settings.analysis_max_plan_cells_written
                ),
                max_joins=active_settings.analysis_max_plan_joins,
                max_python_memory_mb=(
                    active_settings.analysis_max_python_memory_mb
                ),
                max_python_seconds=(
                    active_settings.analysis_max_python_seconds
                ),
                max_estimated_cost_usd=(
                    active_settings.analysis_max_plan_cost_usd
                ),
                max_generated_rows=(
                    active_settings.analysis_max_generated_rows
                ),
                max_chart_cardinality=(
                    active_settings.analysis_max_chart_cardinality
                ),
                plan_approval_python_seconds=(
                    active_settings.analysis_plan_approval_python_seconds
                ),
                plan_approval_cost_usd=(
                    active_settings.analysis_plan_approval_cost_usd
                ),
                plan_approval_generated_rows=(
                    active_settings.analysis_plan_approval_generated_rows
                ),
            )
        ),
    )

    artifact_service: ArtifactVersionService | None = None
    artifact_reconciler: ArtifactUploadReconciler | None = None
    workbook_context: WorkbookContextService | None = None
    # Shared by the artifact service and the execution result publisher, so both
    # write through one configured provider.
    blob_store: CloudinaryArtifactBlobStore | None = None
    if active_settings.cloudinary_is_configured:
        blob_store = CloudinaryArtifactBlobStore(
            CloudinaryBlobStoreConfig(
                cloud_name=active_settings.cloudinary_cloud_name,
                api_key=active_settings.cloudinary_api_key,
                api_secret=active_settings.cloudinary_api_secret,
                max_download_bytes=max(
                    active_settings.analysis_max_artifact_bytes,
                    active_settings.analysis_max_xlsx_uncompressed_bytes,
                ),
            )
        )
        artifact_service = ArtifactVersionService(
            repository=MongoArtifactRepository(),
            blob_store=blob_store,
            config=ArtifactServiceConfig(
                validation_limits=ArtifactValidationLimits(
                    max_upload_bytes=(
                        active_settings.analysis_max_artifact_bytes
                    ),
                    max_archive_members=(
                        active_settings.analysis_max_xlsx_entries
                    ),
                    max_archive_member_bytes=(
                        active_settings.analysis_max_xlsx_uncompressed_bytes
                    ),
                    max_archive_uncompressed_bytes=(
                        active_settings.analysis_max_xlsx_uncompressed_bytes
                    ),
                    max_compression_ratio=(
                        active_settings.analysis_max_xlsx_compression_ratio
                    ),
                )
            ),
        )
        artifact_reconciler = ArtifactUploadReconciler(
            service=artifact_service,
        )
        workbook_context = WorkbookContextService(
            artifact_service=artifact_service,
            dataset_catalog=dataset_catalog,
            limits=WorkbookContextLimits(
                max_inline_cells=(
                    active_settings.analysis_max_inline_cells
                ),
                max_inline_bytes=(
                    active_settings.analysis_max_inline_bytes
                ),
                max_uploaded_cells=(
                    active_settings.analysis_max_uploaded_snapshot_cells
                ),
                max_uploaded_bytes=(
                    active_settings.analysis_max_uploaded_snapshot_bytes
                ),
                max_uploaded_blob_bytes=(
                    active_settings.analysis_max_artifact_bytes
                ),
                max_columns=(
                    active_settings.analysis_max_dataset_columns
                ),
                max_datasets=(
                    active_settings.analysis_max_datasets_per_workbook
                ),
            ),
        )

    run_service = AnalysisRunService(
        store=run_store,
        state_machine=state_machine,
        workbook_context=workbook_context,
        run_deadline_seconds=(
            active_settings.analysis_run_deadline_seconds
        ),
        input_initialization_timeout_seconds=(
            active_settings.analysis_input_initialization_timeout_seconds
        ),
    )
    # One repository and one result reader for the whole process. The publisher
    # writes through them, the patch compiler streams rows from them, and the
    # read API serves previews from them — three callers, one configured client.
    execution_repository = MongoExecutionRepository(database)
    result_reader = (
        BlobExecutionResultReader(
            blob_store,
            max_bytes=active_settings.analysis_max_artifact_bytes,
        )
        if blob_store is not None
        else None
    )
    # The publisher is only built when the deployment declares a native engine.
    # Admission returns PLAN_ONLY otherwise, so a run can never reach an absent
    # executor. It also needs blob storage: without it the engine still runs,
    # but a result cannot be made durable, so executions stay process-local
    # rather than pretending to be published.
    result_publisher = (
        ResultPublisher(
            repository=execution_repository,
            store=blob_store,
        )
        if capabilities.native_execution_ready and blob_store is not None
        else None
    )
    execution_service = (
        NativeExecutionService(
            resolver=MongoNormalizedInputResolver(database),
            backend=SubprocessNativeBackend(),
            publisher=result_publisher,
            capabilities=capabilities,
            limits=ExecutionLimits(
                max_output_rows=active_settings.analysis_max_plan_rows_scanned,
                max_output_cells=active_settings.analysis_max_plan_cells_written,
                max_output_bytes=active_settings.analysis_max_artifact_bytes,
            ),
        )
        if capabilities.native_execution_ready
        else None
    )
    # Only built when a patch can be compiled *and* its payload stored. A
    # deployment without blob storage completes a workbook run at its result
    # rather than parking it on a handshake it could never finish (9.11.1).
    patch_repository = MongoPatchProposalRepository(database)
    patch_service = (
        PatchService(
            state_machine=state_machine,
            plans=plan_repository,
            executions=execution_repository,
            proposals=patch_repository,
            reservations=WriteReservationService(
                MongoSpatialReservationRepository(database),
            ),
            result_reader=result_reader,
            payload_writers=lambda *, workspace_id, patch_id, patch_revision: (
                BlobPayloadWriter(
                    blob_store,
                    workspace_id=workspace_id,
                    patch_id=patch_id,
                    patch_revision=patch_revision,
                )
            ),
            payload_reader=BlobPayloadReader(blob_store),
            config=PatchServiceConfig(
                max_affected_cells=(
                    active_settings.analysis_max_plan_cells_written
                ),
            ),
        )
        if capabilities.workbook_patches_ready and blob_store is not None
        else None
    )
    # Always built: reading what a run executed does not depend on this
    # deployment being able to execute anything. A run recorded by an earlier,
    # engine-enabled process stays readable by one without the engine.
    execution_reader = ExecutionReadService(
        repository=execution_repository,
        preview_reader=result_reader,
    )
    worker = DurableAnalysisWorker(
        state_machine=state_machine,
        dataset_catalog=dataset_catalog,
        planning_service=planning_service,
        execution_service=execution_service,
        patch_service=patch_service,
        config=AnalysisWorkerConfig(
            concurrency=active_settings.analysis_worker_concurrency,
            poll_seconds=active_settings.analysis_worker_poll_seconds,
            lease_seconds=active_settings.analysis_worker_lease_seconds,
            renew_seconds=active_settings.analysis_worker_renew_seconds,
        ),
    )
    return AnalysisRuntime(
        run_service=run_service,
        worker=worker,
        artifact_service=artifact_service,
        planning_service=planning_service,
        artifact_reconciler=artifact_reconciler,
        diagnostics_service=AnalysisDiagnosticsService(
            database=database,
            worker=worker,
        ),
        patch_service=patch_service,
        patch_repository=patch_repository,
        execution_reader=execution_reader,
    )


__all__ = [
    "AnalysisRuntime",
    "build_analysis_runtime",
]

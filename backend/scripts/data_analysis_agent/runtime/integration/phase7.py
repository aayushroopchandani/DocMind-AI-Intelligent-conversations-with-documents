from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError
from langchain_core.callbacks import get_usage_metadata_callback

from scripts.data_analysis_agent.analysis.models import (
    AnalysisIssue,
    AnalysisRequirements,
    AugmentedEvidence,
    DatasetProfiles,
    EvidenceAssessment,
    EvidencePackage,
    NormalizationResult,
    ReadinessDecision,
    RetrievalResult,
)
from scripts.data_analysis_agent.analysis.state import (
    AnalysisPhase,
    analysis_thread_config,
    create_analysis_state,
)
from scripts.data_analysis_agent.runtime.models import (
    AnalysisEventType,
    AnalysisRun,
    AnalysisRunOutcome,
    AnalysisRunPhase,
    DatasetHandle,
    RunIssueSummary,
    TokenUsage,
)

from .contracts import (
    CancellationCheck,
    NullPhase7ProgressReporter,
    Phase7ExecutionCancelled,
    Phase7ExecutionResult,
    Phase7InputError,
    Phase7PlanningArtifacts,
    Phase7Progress,
    Phase7ProgressReporter,
    StreamingAnalysisGraph,
)


async def _never_cancelled() -> bool:
    return False


def _deduplication_suffix(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _phase(value: object) -> AnalysisPhase:
    if isinstance(value, AnalysisPhase):
        return value
    return AnalysisPhase(str(value))


def _artifact(model: type[Any], value: object) -> Any:
    """Avoid re-validating immutable artifacts already produced by the graph."""

    return value if isinstance(value, model) else model.model_validate(value)


def _issue_summaries(values: object) -> tuple[RunIssueSummary, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    issues: list[AnalysisIssue] = []
    for value in values:
        try:
            issues.append(AnalysisIssue.model_validate(value))
        except ValidationError:
            continue
    counts = Counter(
        (
            issue.code.value,
            " ".join(issue.message.split())[:500],
            issue.retryable,
        )
        for issue in issues
    )
    return tuple(
        RunIssueSummary(
            code=code,
            message=message or "The analysis pipeline reported an issue.",
            count=count,
            retryable=retryable,
        )
        for (code, message, retryable), count in sorted(counts.items())
    )[:100]


def _system_error(
    *,
    code: str,
    message: str,
    retryable: bool,
) -> RunIssueSummary:
    return RunIssueSummary(
        code=code,
        message=message,
        retryable=retryable,
    )


def _token_usage(values: Mapping[str, object]) -> TokenUsage:
    input_tokens = 0
    output_tokens = 0
    for value in values.values():
        if not isinstance(value, Mapping):
            continue
        try:
            input_tokens += max(0, int(value.get("input_tokens") or 0))
            output_tokens += max(0, int(value.get("output_tokens") or 0))
        except (TypeError, ValueError):
            continue
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


class _MilestoneProjector:
    """Projects cumulative graph states into idempotent, bounded events."""

    def __init__(self, reporter: Phase7ProgressReporter) -> None:
        self._reporter = reporter
        self._emitted: set[str] = set()

    async def emit_started(
        self,
        *,
        document_count: int,
        pinned_dataset_count: int,
    ) -> None:
        payload = {
            "selected_document_count": document_count,
            "pinned_dataset_count": pinned_dataset_count,
        }
        await self._emit(
            event_type=AnalysisEventType.RETRIEVAL_STARTED,
            key="phase7:retrieval:started",
            payload=payload,
        )
        await self._emit(
            event_type=AnalysisEventType.REQUIREMENTS_STARTED,
            key="phase7:requirements:started",
            payload=payload,
        )

    async def project(self, state: Mapping[str, Any]) -> None:
        await self._retrieval(state.get("retrieval_result"))
        await self._requirements(state.get("analysis_requirements"))
        await self._hydration(state.get("evidence_package"))
        await self._profiles(state.get("dataset_profiles"))
        await self._assessment(state.get("evidence_assessment"))
        await self._completion(state.get("augmented_evidence"))
        await self._normalization(state.get("normalization_result"))

    async def _retrieval(self, value: object) -> None:
        if value is None:
            return
        artifact = _artifact(RetrievalResult, value)
        await self._emit(
            event_type=AnalysisEventType.RETRIEVAL_COMPLETED,
            key="phase7:retrieval:completed",
            payload={
                "retrieval_scope": artifact.retrieval_scope,
                "table_reference_count": len(artifact.table_references),
                "table_candidate_count": len(artifact.table_candidates),
                "text_evidence_count": len(artifact.text_evidence),
            },
        )

    async def _requirements(self, value: object) -> None:
        if value is None:
            return
        artifact = _artifact(AnalysisRequirements, value)
        await self._emit(
            event_type=AnalysisEventType.REQUIREMENTS_COMPLETED,
            key="phase7:requirements:completed",
            payload={
                "operation": artifact.operation.value,
                "requirement_count": len(artifact.requirements),
                "required_count": sum(
                    requirement.required
                    for requirement in artifact.requirements
                ),
            },
        )

    async def _hydration(self, value: object) -> None:
        if value is None:
            return
        artifact = _artifact(EvidencePackage, value)
        await self._emit(
            event_type=AnalysisEventType.EVIDENCE_HYDRATED,
            key="phase7:evidence:hydrated",
            payload={
                "status": artifact.status,
                "retrieved_table_count": artifact.retrieved_table_count,
                "hydrated_dataset_count": artifact.hydrated_table_count,
                "unresolved_table_count": len(artifact.unresolved_tables),
            },
        )

    async def _profiles(self, value: object) -> None:
        if value is None:
            return
        artifact = _artifact(DatasetProfiles, value)
        await self._emit(
            event_type=AnalysisEventType.DATASETS_PROFILED,
            key="phase7:datasets:profiled",
            payload={
                "status": artifact.status,
                "requested_count": artifact.requested_count,
                "profiled_count": artifact.profiled_count,
                "failure_count": len(artifact.failures),
            },
        )

    async def _assessment(self, value: object) -> None:
        if value is None:
            return
        artifact = _artifact(EvidenceAssessment, value)
        await self._emit(
            event_type=AnalysisEventType.EVIDENCE_ASSESSED,
            key="phase7:evidence:assessed",
            payload={
                "decision": artifact.decision.value,
                "required_count": artifact.required_count,
                "supported_count": artifact.supported_count,
                "partial_count": artifact.partial_count,
                "missing_count": artifact.missing_count,
                "conflicting_count": artifact.conflicting_count,
                "ambiguous_count": artifact.ambiguous_count,
                "ambiguity_llm_used": (
                    artifact.diagnostics.ambiguity_llm_used
                ),
            },
        )

    async def _completion(self, value: object) -> None:
        if value is None:
            return
        artifact = _artifact(AugmentedEvidence, value)
        await self._emit(
            event_type=AnalysisEventType.EVIDENCE_COMPLETED,
            key="phase7:evidence:completed",
            payload={
                "status": artifact.status.value,
                "final_decision": artifact.final_decision,
                "added_dataset_count": len(artifact.added_datasets),
                "fact_count": len(artifact.facts),
                "derived_dataset_count": len(artifact.derived_datasets),
                "remaining_requirement_count": len(
                    artifact.remaining_requirement_ids
                ),
            },
        )

    async def _normalization(self, value: object) -> None:
        if value is None:
            return
        artifact = _artifact(NormalizationResult, value)
        if not artifact.datasets:
            await self._emit(
                event_type=AnalysisEventType.DATASET_PREPARED,
                key="phase7:datasets:prepared:empty",
                phase=AnalysisRunPhase.NORMALIZATION,
                payload={
                    "prepared_dataset_count": 0,
                    "selected_fact_count": len(artifact.selected_fact_ids),
                    "selected_derived_dataset_count": len(
                        artifact.selected_derived_dataset_ids
                    ),
                    "non_tabular_requirement_count": len(
                        artifact.non_tabular_requirement_ids
                    ),
                },
            )
            return
        for dataset in artifact.datasets:
            await self._emit(
                event_type=AnalysisEventType.DATASET_PREPARED,
                key=(
                    "phase7:dataset:"
                    f"{_deduplication_suffix(dataset.normalized_dataset_id)}"
                ),
                phase=AnalysisRunPhase.NORMALIZATION,
                payload={
                    "dataset_id": dataset.normalized_dataset_id,
                    "source_dataset_ids": list(dataset.source_dataset_ids),
                    "materialization": dataset.materialization.value,
                    "output_row_count": dataset.output_row_count,
                    "output_column_count": dataset.output_column_count,
                },
            )

    async def _emit(
        self,
        *,
        event_type: AnalysisEventType,
        key: str,
        payload: dict[str, Any],
        phase: AnalysisRunPhase = AnalysisRunPhase.EVIDENCE_PREPARATION,
    ) -> None:
        if key in self._emitted:
            return
        await self._reporter.emit(
            Phase7Progress(
                event_type=event_type,
                phase=phase,
                payload=payload,
                deduplication_key=key,
            )
        )
        self._emitted.add(key)


class Phase7AnalysisAdapter:
    """Execute today's Phase 1-7 graph behind a durable run boundary.

    The adapter deliberately stops at normalized datasets. It never reports a
    plan-ready state and does not mutate workbooks.
    """

    def __init__(self, graph: StreamingAnalysisGraph | None = None) -> None:
        self._graph = graph

    def _selected_graph(self) -> StreamingAnalysisGraph:
        if self._graph is None:
            from scripts.data_analysis_agent.analysis.graph import (
                data_analysis_graph,
            )

            self._graph = data_analysis_graph
        return self._graph

    async def execute(
        self,
        run: AnalysisRun,
        dataset_handles: Sequence[DatasetHandle] = (),
        reporter: Phase7ProgressReporter | None = None,
        is_cancelled: CancellationCheck | None = None,
        *,
        datasets: Sequence[DatasetHandle] | None = None,
        cancellation_check: CancellationCheck | None = None,
    ) -> Phase7ExecutionResult:
        with get_usage_metadata_callback() as usage_callback:
            result = await self._execute(
                run,
                dataset_handles=dataset_handles,
                reporter=reporter,
                is_cancelled=is_cancelled,
                datasets=datasets,
                cancellation_check=cancellation_check,
            )
        return result.model_copy(
            update={"token_usage": _token_usage(usage_callback.usage_metadata)}
        )

    async def _execute(
        self,
        run: AnalysisRun,
        dataset_handles: Sequence[DatasetHandle] = (),
        reporter: Phase7ProgressReporter | None = None,
        is_cancelled: CancellationCheck | None = None,
        *,
        datasets: Sequence[DatasetHandle] | None = None,
        cancellation_check: CancellationCheck | None = None,
    ) -> Phase7ExecutionResult:
        if datasets is not None:
            if dataset_handles:
                raise Phase7InputError(
                    "provide dataset_handles or datasets, not both"
                )
            dataset_handles = datasets
        if cancellation_check is not None:
            if is_cancelled is not None:
                raise Phase7InputError(
                    "provide is_cancelled or cancellation_check, not both"
                )
            is_cancelled = cancellation_check
        pinned = tuple(dataset_handles)
        self._validate_inputs(run=run, datasets=pinned)
        selected_reporter = reporter or NullPhase7ProgressReporter()
        selected_cancellation = is_cancelled or _never_cancelled
        projector = _MilestoneProjector(selected_reporter)

        await self._raise_if_cancelled(selected_cancellation)
        await projector.emit_started(
            document_count=len(run.selected_document_ids),
            pinned_dataset_count=len(pinned),
        )

        state = create_analysis_state(
            user_id=run.user_id,
            chat_id=run.chat_id,
            workspace_id=run.workspace_id,
            query=run.prompt,
            document_ids=run.selected_document_ids,
            pinned_datasets=pinned,
            run_id=run.run_id,
        )
        final_state: Mapping[str, Any] | None = None
        iterator = aiter(
            self._selected_graph().astream(
                state,
                config=analysis_thread_config(state),
                stream_mode="values",
            )
        )
        while True:
            await self._raise_if_cancelled(selected_cancellation)
            try:
                update = await anext(iterator)
            except StopAsyncIteration:
                break
            except Exception:
                return self._failed_result(
                    final_state=final_state,
                    code="phase7_graph_execution_failed",
                    message="The evidence-preparation pipeline could not complete.",
                    retryable=True,
                )
            await self._raise_if_cancelled(selected_cancellation)
            if not isinstance(update, Mapping):
                return self._failed_result(
                    final_state=final_state,
                    code="phase7_invalid_graph_update",
                    message="The evidence-preparation pipeline returned invalid state.",
                    retryable=False,
                )
            final_state = update
            try:
                await projector.project(update)
            except ValidationError:
                return self._failed_result(
                    final_state=final_state,
                    code="phase7_invalid_graph_state",
                    message="The evidence-preparation pipeline returned invalid state.",
                    retryable=False,
                )

        await self._raise_if_cancelled(selected_cancellation)
        return self._map_result(final_state)

    @staticmethod
    def _validate_inputs(
        *,
        run: AnalysisRun,
        datasets: tuple[DatasetHandle, ...],
    ) -> None:
        identities = tuple(
            (dataset.dataset_id, dataset.source_version)
            for dataset in datasets
        )
        if len(identities) != len(set(identities)):
            raise Phase7InputError("pinned dataset versions must be unique")
        if len({dataset_id for dataset_id, _ in identities}) != len(identities):
            raise Phase7InputError(
                "one immutable version per pinned dataset is required"
            )
        for dataset in datasets:
            if dataset.user_id != run.user_id:
                raise Phase7InputError(
                    "pinned datasets must belong to the run user"
                )
            if dataset.workspace_id != run.workspace_id:
                raise Phase7InputError(
                    "pinned datasets must belong to the run workspace"
                )

        expected = {
            (reference.dataset_id, reference.source_version)
            for reference in run.input_dataset_versions
        }
        if expected != set(identities):
            raise Phase7InputError(
                "pinned datasets do not match the run's immutable versions"
            )
        if not run.selected_document_ids and not datasets:
            raise Phase7InputError("the run has no analysis source")

    @staticmethod
    async def _raise_if_cancelled(
        cancellation_check: CancellationCheck,
    ) -> None:
        if await cancellation_check():
            raise Phase7ExecutionCancelled(
                "analysis cancellation was requested"
            )

    def _map_result(
        self,
        final_state: Mapping[str, Any] | None,
    ) -> Phase7ExecutionResult:
        if final_state is None:
            return self._failed_result(
                final_state=None,
                code="phase7_empty_graph_result",
                message="The evidence-preparation pipeline produced no result.",
                retryable=True,
            )
        try:
            graph_phase = _phase(final_state.get("phase"))
        except (TypeError, ValueError):
            return self._failed_result(
                final_state=final_state,
                code="phase7_invalid_final_phase",
                message="The evidence-preparation pipeline ended in an invalid state.",
                retryable=False,
            )

        warnings = _issue_summaries(final_state.get("warnings"))
        errors = _issue_summaries(final_state.get("errors"))
        if graph_phase == AnalysisPhase.PREPARED:
            try:
                artifact = _artifact(
                    NormalizationResult,
                    final_state.get("normalization_result")
                )
                requirements = _artifact(
                    AnalysisRequirements,
                    final_state.get("analysis_requirements"),
                )
                profiles = _artifact(
                    DatasetProfiles,
                    final_state.get("dataset_profiles"),
                )
            except ValidationError:
                return self._failed_result(
                    final_state=final_state,
                    code="phase7_invalid_normalization_result",
                    message="Prepared dataset references could not be validated.",
                    retryable=False,
                )
            if not artifact.can_analyze:
                return self._failed_result(
                    final_state=final_state,
                    code="phase7_normalization_incomplete",
                    message="Dataset normalization did not complete successfully.",
                    retryable=False,
                )
            return Phase7ExecutionResult(
                outcome=AnalysisRunOutcome.DATASETS_PREPARED,
                graph_phase=graph_phase,
                final_dataset_ids=tuple(
                    dataset.normalized_dataset_id
                    for dataset in artifact.datasets
                ),
                source_dataset_ids=tuple(
                    dict.fromkeys(
                        source_id
                        for dataset in artifact.datasets
                        for source_id in dataset.source_dataset_ids
                    )
                ),
                selected_fact_ids=artifact.selected_fact_ids,
                selected_derived_dataset_ids=(
                    artifact.selected_derived_dataset_ids
                ),
                prepared_dataset_count=artifact.prepared_dataset_count,
                total_input_rows=artifact.total_input_rows,
                total_output_rows=artifact.total_output_rows,
                warnings=warnings,
                errors=errors,
                # The graph already validates these checkpoint artifacts at
                # their producing nodes. Avoid recursively rebuilding large
                # immutable profile models on the worker boundary.
                planning_artifacts=Phase7PlanningArtifacts.model_construct(
                    requirements=requirements,
                    dataset_profiles=profiles,
                    normalization=artifact,
                ),
            )

        if graph_phase == AnalysisPhase.FAILED:
            return self._failed_result(
                final_state=final_state,
                code="phase7_pipeline_failed",
                message="The evidence-preparation pipeline could not complete.",
                retryable=any(item.retryable for item in errors),
            )

        assessment_value = final_state.get("evidence_assessment")
        if assessment_value is not None:
            try:
                decision = _artifact(
                    EvidenceAssessment,
                    assessment_value,
                ).decision
            except ValidationError:
                decision = None
            if decision == ReadinessDecision.NEEDS_CLARIFICATION:
                return Phase7ExecutionResult(
                    outcome=AnalysisRunOutcome.CLARIFICATION_REQUIRED,
                    graph_phase=graph_phase,
                    warnings=warnings,
                    errors=errors,
                )
            if decision == ReadinessDecision.UNANSWERABLE:
                return Phase7ExecutionResult(
                    outcome=AnalysisRunOutcome.UNANSWERABLE,
                    graph_phase=graph_phase,
                    warnings=warnings,
                    errors=errors,
                )

        return self._failed_result(
            final_state=final_state,
            code="phase7_incomplete_graph_result",
            message=(
                "The evidence-preparation pipeline stopped without a supported "
                "final decision."
            ),
            retryable=False,
        )

    @staticmethod
    def _failed_result(
        *,
        final_state: Mapping[str, Any] | None,
        code: str,
        message: str,
        retryable: bool,
    ) -> Phase7ExecutionResult:
        try:
            graph_phase = (
                _phase(final_state.get("phase"))
                if final_state is not None
                else AnalysisPhase.FAILED
            )
        except (TypeError, ValueError):
            graph_phase = AnalysisPhase.FAILED
        warnings = _issue_summaries(
            final_state.get("warnings") if final_state is not None else None
        )
        errors = list(
            _issue_summaries(
                final_state.get("errors") if final_state is not None else None
            )
        )
        if not errors:
            errors.append(
                _system_error(
                    code=code,
                    message=message,
                    retryable=retryable,
                )
            )
        return Phase7ExecutionResult(
            outcome=AnalysisRunOutcome.FAILED,
            graph_phase=graph_phase,
            warnings=warnings,
            errors=tuple(errors),
        )


__all__ = ["Phase7AnalysisAdapter"]

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from scripts.data_analysis_agent.analysis.models.preparation import (
    NormalizationResult,
)
from scripts.data_analysis_agent.analysis.models.profile import DatasetProfiles
from scripts.data_analysis_agent.analysis.models.requirements import (
    AnalysisRequirements,
)

from ..models.datasets import DatasetHandle
from ..models.events import AnalysisEventType
from ..models.plans import (
    AnalysisPlan,
    AnalysisPlanDraft,
    AnalysisPlanStatus,
    FinalPatchApprovalCommand,
    FinalPatchProposal,
    GenerateDatasetStep,
    PlanApprovalCommand,
    PlanApprovalStatus,
    PlanDiagnostics,
    PlanProposal,
    StepProvenance,
    step_input_aliases,
)
from ..models.runs import (
    AnalysisRun,
    AnalysisRunPhase,
    RunIssueSummary,
    StageTokenUsage,
    TokenUsage,
)
from ..models.privacy import PrivacySummary
from ..observability.tokens import merge_stage_maps
from ..repositories.plans import (
    AnalysisPlanConflictError,
    AnalysisPlanNotFoundError,
    AnalysisPlanRepository,
)
from .context import PlanningContext, PlanningContextBuilder, PlanningContextError
from .contracts import (
    PlanValidationIssue,
    PlanValidationLayer,
    PlanValidationReport,
    PlanValidationSeverity,
    NullPlanningProgressReporter,
    PlanningProgress,
    PlanningProgressReporter,
    PlanningExecutionResult,
    PlanningOutcome,
)
from .planner import (
    AnalysisPlanner,
    PlannerInvocation,
    PlannerOutputError,
    TypedAnalysisPlanner,
)
from .validation import AnalysisPlanValidator, derive_approval_policy


class AnalysisRunStateReader(Protocol):
    async def require_run(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> AnalysisRun: ...


class AnalysisPlanningService:
    """Bounded plan generation, one repair, persistence, and HITL decisions."""

    def __init__(
        self,
        *,
        repository: AnalysisPlanRepository,
        state_machine: AnalysisRunStateReader,
        context_builder: PlanningContextBuilder | None = None,
        planner: AnalysisPlanner | None = None,
        validator: AnalysisPlanValidator | None = None,
    ) -> None:
        self._repository = repository
        self._state_machine = state_machine
        self._context_builder = context_builder or PlanningContextBuilder()
        self._planner = planner or TypedAnalysisPlanner()
        self._validator = validator or AnalysisPlanValidator()

    async def create_plan(
        self,
        *,
        run: AnalysisRun,
        dataset_handles: tuple[DatasetHandle, ...],
        requirements: AnalysisRequirements,
        profiles: DatasetProfiles,
        normalization: NormalizationResult,
        reporter: PlanningProgressReporter | None = None,
    ) -> PlanningExecutionResult:
        progress = reporter or NullPlanningProgressReporter()
        try:
            context = self._context_builder.build(
                run=run,
                dataset_handles=dataset_handles,
                requirements=requirements,
                profiles=profiles,
                normalization=normalization,
            )
        except PlanningContextError as exc:
            return _failed(
                code="planning_context_invalid",
                message=str(exc),
                retryable=False,
            )

        existing = await self._repository.get_current_plan(
            user_id=run.user_id,
            run_id=run.run_id,
        )
        if (
            existing is not None
            and existing.input_signature == context.input_signature
            and existing.status
            in {
                AnalysisPlanStatus.READY,
                AnalysisPlanStatus.AWAITING_PLAN_APPROVAL,
                AnalysisPlanStatus.APPROVED,
            }
        ):
            await progress.emit(
                PlanningProgress(
                    event_type=AnalysisEventType.PLAN_GENERATED,
                    phase=AnalysisRunPhase.PLAN_VALIDATION,
                    payload={
                        "plan_id": existing.plan_id,
                        "revision": existing.revision,
                        "reused": True,
                    },
                    deduplication_key="planning:plan-reused",
                )
            )
            return PlanningExecutionResult(
                outcome=(
                    PlanningOutcome.APPROVAL_REQUIRED
                    if existing.status
                    == AnalysisPlanStatus.AWAITING_PLAN_APPROVAL
                    else PlanningOutcome.PLAN_READY
                ),
                plan=existing,
                reports=(PlanValidationReport(),),
                token_usage=existing.token_usage,
                token_usage_by_stage=existing.token_usage_by_stage,
            )

        conflicting_targets = (
            await self._repository.list_reserved_write_targets(
                user_id=run.user_id,
                workspace_id=run.workspace_id,
                exclude_run_id=run.run_id,
            )
        )
        reports: list[PlanValidationReport] = []
        invocations: list[PlannerInvocation] = []
        failed_usages: list[TokenUsage] = []
        failed_stages: list[StageTokenUsage] = []
        original = None
        await progress.emit(
            PlanningProgress(
                event_type=AnalysisEventType.PLANNING_STARTED,
                phase=AnalysisRunPhase.PLANNING,
                payload={
                    "input_dataset_count": len(context.input_datasets),
                    "mode": context.mode.value,
                },
                deduplication_key="planning:started",
            )
        )
        try:
            invocation = await self._planner.propose(context)
            invocations.append(invocation)
            original = invocation.proposal
            draft = _draft(context, original)
            await progress.emit(
                PlanningProgress(
                    event_type=AnalysisEventType.PLAN_GENERATED,
                    phase=AnalysisRunPhase.PLANNING,
                    payload={"attempt": 1, "step_count": len(draft.steps)},
                    deduplication_key="planning:generated:1",
                )
            )
            await progress.emit(
                PlanningProgress(
                    event_type=AnalysisEventType.PLAN_VALIDATION_STARTED,
                    phase=AnalysisRunPhase.PLAN_VALIDATION,
                    payload={"attempt": 1},
                    deduplication_key="planning:validation:1",
                )
            )
            report = self._validator.validate(
                draft=draft,
                context=context,
                conflicting_write_targets=conflicting_targets,
            )
        except PlannerOutputError as exc:
            failed_usages.append(exc.token_usage)
            if exc.stage_usage is not None:
                failed_stages.append(exc.stage_usage)
            if exc.code == "planner_unavailable":
                return _failed(
                    code=exc.code,
                    message=str(exc),
                    retryable=True,
                    token_usage=_sum_usage(invocations, failed_usages),
                    token_usage_by_stage=_stage_usage(
                        invocations,
                        failed_stages,
                    ),
                )
            report = _planner_schema_report(exc)
            draft = None
        reports.append(report)

        if not report.valid:
            await _emit_validation_failed(progress, report, attempt=1)
            if not report.repairable:
                return _clarification(
                    reports,
                    _sum_usage(invocations, failed_usages),
                    _stage_usage(invocations, failed_stages),
                )
            await progress.emit(
                PlanningProgress(
                    event_type=AnalysisEventType.PLAN_REPAIR_STARTED,
                    phase=AnalysisRunPhase.PLAN_VALIDATION,
                    payload={"error_count": len(report.errors)},
                    deduplication_key="planning:repair:started",
                )
            )
            try:
                repaired = await self._planner.repair(
                    context,
                    original=original,
                    issues=report.errors,
                )
                invocations.append(repaired)
                draft = _draft(context, repaired.proposal)
                await progress.emit(
                    PlanningProgress(
                        event_type=AnalysisEventType.PLAN_GENERATED,
                        phase=AnalysisRunPhase.PLAN_VALIDATION,
                        payload={"attempt": 2, "step_count": len(draft.steps)},
                        deduplication_key="planning:generated:2",
                    )
                )
                await progress.emit(
                    PlanningProgress(
                        event_type=AnalysisEventType.PLAN_VALIDATION_STARTED,
                        phase=AnalysisRunPhase.PLAN_VALIDATION,
                        payload={"attempt": 2},
                        deduplication_key="planning:validation:2",
                    )
                )
                report = self._validator.validate(
                    draft=draft,
                    context=context,
                    conflicting_write_targets=conflicting_targets,
                )
            except PlannerOutputError as exc:
                failed_usages.append(exc.token_usage)
                if exc.stage_usage is not None:
                    failed_stages.append(exc.stage_usage)
                if exc.code == "planner_unavailable":
                    return _failed(
                        code=exc.code,
                        message=str(exc),
                        retryable=True,
                        token_usage=_sum_usage(invocations, failed_usages),
                        token_usage_by_stage=_stage_usage(
                            invocations,
                            failed_stages,
                        ),
                    )
                report = _planner_schema_report(exc)
                draft = None
            reports.append(report)
            if not report.valid:
                await _emit_validation_failed(progress, report, attempt=2)

        if not report.valid or draft is None:
            return _clarification(
                reports,
                _sum_usage(invocations, failed_usages),
                _stage_usage(invocations, failed_stages),
            )

        approval_policy = derive_approval_policy(
            draft=draft,
            context=context,
        )
        final_invocation = invocations[-1]
        plan = AnalysisPlan.model_validate(
            _build_plan_data(
                draft=draft,
                run=run,
                revision=(existing.revision + 1 if existing is not None else 1),
                generation_attempt=len(reports),
                approval_policy=approval_policy,
                reports=reports,
                invocation=final_invocation,
                privacy=context.privacy,
                token_usage=_sum_usage(invocations, failed_usages),
                token_usage_by_stage=_stage_usage(
                    invocations,
                    failed_stages,
                ),
            )
        )
        try:
            plan = await self._repository.create_plan(plan)
        except AnalysisPlanConflictError:
            conflict_issue = PlanValidationIssue(
                code="write_target_reservation_conflict",
                layer=PlanValidationLayer.CONCURRENCY,
                severity=PlanValidationSeverity.ERROR,
                message=(
                    "Another current plan reserved the workbook target while "
                    "this plan was being generated."
                ),
                path="write_intents",
                repairable=False,
            )
            report = PlanValidationReport(
                issues=(*report.issues, conflict_issue)
            )
            reports[-1] = report
            await _emit_validation_failed(
                progress,
                report,
                attempt=len(reports),
            )
            return _clarification(
                reports,
                _sum_usage(invocations, failed_usages),
                _stage_usage(invocations, failed_stages),
            )
        return PlanningExecutionResult(
            outcome=(
                PlanningOutcome.APPROVAL_REQUIRED
                if approval_policy.plan_approval_required
                else PlanningOutcome.PLAN_READY
            ),
            plan=plan,
            reports=tuple(reports),
            token_usage=_sum_usage(invocations, failed_usages),
            token_usage_by_stage=_stage_usage(invocations, failed_stages),
        )

    async def decide_plan(
        self,
        *,
        user_id: str,
        run_id: str,
        command: PlanApprovalCommand,
        trace_id: str | None = None,
    ) -> AnalysisPlan:
        plan = await self._repository.get_plan(
            user_id=user_id,
            run_id=run_id,
            plan_id=command.plan_id,
        )
        if plan is None:
            raise AnalysisPlanNotFoundError("analysis plan not found")
        _validate_plan_decision(plan, command)
        if command.decision == "approve":
            _validate_workbook_guards(plan, command.workbook_guards)
            conflicts = await self._repository.list_reserved_write_targets(
                user_id=user_id,
                workspace_id=plan.workspace_id,
                exclude_run_id=run_id,
            )
            if set(plan.write_target_keys).intersection(conflicts):
                raise AnalysisPlanConflictError(
                    "another pending run now targets the same workbook sheet"
                )
        target_approval = (
            PlanApprovalStatus.APPROVED
            if command.decision == "approve"
            else PlanApprovalStatus.REJECTED
        )
        run = await self._state_machine.require_run(
            user_id=user_id,
            run_id=run_id,
        )
        decision = await self._repository.decide_plan(
            user_id=user_id,
            run_id=run_id,
            plan_id=command.plan_id,
            expected_revision=command.expected_revision,
            expected_plan_hash=command.expected_plan_hash,
            expected_input_signature=command.expected_input_signature,
            expected_run_version=run.version,
            status=target_approval,
            actor_user_id=user_id,
            decision_id=command.decision_id,
            comment=command.comment,
            rejection_reason=command.rejection_reason,
            decided_at=datetime.now(timezone.utc),
            trace_id=trace_id,
        )
        return decision.plan

    async def get_current_plan(
        self,
        *,
        user_id: str,
        run_id: str,
    ) -> AnalysisPlan:
        plan = await self._repository.get_current_plan(
            user_id=user_id,
            run_id=run_id,
        )
        if plan is None:
            raise AnalysisPlanNotFoundError("analysis plan not found")
        return plan

    async def register_patch_proposal(
        self,
        proposal: FinalPatchProposal,
    ) -> FinalPatchProposal:
        plan = await self._repository.get_plan(
            user_id=proposal.user_id,
            run_id=proposal.run_id,
            plan_id=proposal.plan_id,
        )
        if plan is None:
            raise AnalysisPlanNotFoundError("analysis plan not found")
        if (
            plan.workspace_id != proposal.workspace_id
            or plan.plan_hash != proposal.plan_hash
            or plan.revision != proposal.plan_revision
            or plan.input_signature != proposal.input_signature
        ):
            raise AnalysisPlanConflictError(
                "patch proposal does not match its approved plan"
            )
        _validate_workbook_guards(plan, proposal.workbook_guards)
        if plan.approval.status not in {
            PlanApprovalStatus.NOT_REQUIRED,
            PlanApprovalStatus.APPROVED,
        }:
            raise AnalysisPlanConflictError(
                "patch proposal requires a ready or approved plan"
            )
        return await self._repository.create_patch_proposal(proposal)

    async def decide_patch(
        self,
        *,
        user_id: str,
        run_id: str,
        command: FinalPatchApprovalCommand,
    ) -> FinalPatchProposal:
        proposal = await self._repository.get_patch_proposal(
            user_id=user_id,
            run_id=run_id,
            patch_id=command.patch_id,
        )
        if proposal is None:
            raise AnalysisPlanNotFoundError("patch proposal not found")
        if (
            proposal.patch_hash != command.expected_patch_hash
            or proposal.plan_hash != command.expected_plan_hash
        ):
            raise AnalysisPlanConflictError("patch approval is stale")
        if tuple(command.workbook_guards) != tuple(proposal.workbook_guards):
            raise AnalysisPlanConflictError(
                "workbook changed after the exact patch was proposed"
            )
        target = (
            PlanApprovalStatus.APPROVED
            if command.decision == "approve"
            else PlanApprovalStatus.REJECTED
        )
        return await self._repository.decide_patch(
            user_id=user_id,
            run_id=run_id,
            patch_id=command.patch_id,
            expected_patch_hash=command.expected_patch_hash,
            expected_plan_hash=command.expected_plan_hash,
            status=target,
            actor_user_id=user_id,
            decision_id=command.decision_id,
            comment=command.comment,
            requested_at=proposal.approval.requested_at,
            decided_at=datetime.now(timezone.utc),
        )


def _draft(
    context: PlanningContext,
    proposal: PlanProposal,
) -> AnalysisPlanDraft:
    lineages: dict[str, tuple[tuple[str, str], ...]] = {
        dataset.alias: tuple(
            (source.source_dataset_id, source.source_version)
            for source in dataset.provenance
        )
        for dataset in context.input_datasets
    }
    canonical_steps = []
    for step in proposal.steps:
        if isinstance(step, GenerateDatasetStep):
            lineage: tuple[tuple[str, str], ...] = ()
            provenance = StepProvenance(
                generated=True,
                description="Generated data; no source dataset lineage.",
            )
        else:
            lineage = tuple(
                dict.fromkeys(
                    pair
                    for alias in step_input_aliases(step)
                    for pair in lineages.get(alias, ())
                )
            )
            provenance = StepProvenance(
                source_dataset_ids=tuple(pair[0] for pair in lineage),
                source_versions=tuple(pair[1] for pair in lineage),
                generated=False,
                description="Canonical immutable lineage derived by the server.",
            )
        canonical_steps.append(step.model_copy(update={"provenance": provenance}))
        lineages[step.output_alias] = lineage
    proposal_data = proposal.model_dump(mode="python")
    proposal_data["steps"] = tuple(canonical_steps)
    return AnalysisPlanDraft(
        **proposal_data,
        run_id=context.run_id,
        mode=context.mode,
        input_signature=context.input_signature,
        input_datasets=context.input_datasets,
    )


def _build_plan_data(
    *,
    draft: AnalysisPlanDraft,
    run: AnalysisRun,
    revision: int,
    generation_attempt: int,
    approval_policy: object,
    reports: list[PlanValidationReport],
    invocation: PlannerInvocation,
    privacy: PrivacySummary,
    token_usage: TokenUsage,
    token_usage_by_stage: dict[str, StageTokenUsage],
) -> dict[str, object]:
    from ..models.plans import build_analysis_plan

    plan = build_analysis_plan(
        draft=draft,
        user_id=run.user_id,
        workspace_id=run.workspace_id,
        revision=revision,
        approval_policy=approval_policy,
        diagnostics=PlanDiagnostics(
            generation_attempt=generation_attempt,
            repair_count=generation_attempt - 1,
            validation_warning_count=sum(
                len(report.warnings) for report in reports
            ),
            validation_error_count=sum(
                len(report.errors) for report in reports
            ),
        ),
        model=invocation.model,
        prompt_version=invocation.prompt_version,
        privacy=privacy,
        token_usage=token_usage,
        token_usage_by_stage=token_usage_by_stage,
    )
    return plan.model_dump(mode="python")


def _planner_schema_report(exc: PlannerOutputError) -> PlanValidationReport:
    return PlanValidationReport(
        issues=tuple(
            PlanValidationIssue(
                code=exc.code,
                layer=PlanValidationLayer.STRUCTURAL,
                severity=PlanValidationSeverity.ERROR,
                message=message,
                path=path,
                repairable=True,
            )
            for path, message in (
                exc.schema_issues or (("plan", str(exc)),)
            )
        )
    )


def _clarification(
    reports: list[PlanValidationReport],
    token_usage: TokenUsage,
    token_usage_by_stage: dict[str, StageTokenUsage],
) -> PlanningExecutionResult:
    report = reports[-1]
    user_issue = next(
        (
            issue
            for issue in report.errors
            if issue.layer
            in {
                PlanValidationLayer.REFERENTIAL,
                PlanValidationLayer.TYPE_AND_UNIT,
                PlanValidationLayer.CONCURRENCY,
            }
        ),
        report.errors[0] if report.errors else None,
    )
    question = (
        f"I could not produce a safe plan: {user_issue.message} "
        "Please clarify the intended columns, output, or workbook target."
        if user_issue is not None
        else "Please clarify the intended analysis and output target."
    )
    return PlanningExecutionResult(
        outcome=PlanningOutcome.CLARIFICATION_REQUIRED,
        reports=tuple(reports),
        clarification=question[:1_000],
        token_usage=token_usage,
        token_usage_by_stage=token_usage_by_stage,
    )


def _failed(
    *,
    code: str,
    message: str,
    retryable: bool,
    token_usage: TokenUsage | None = None,
    token_usage_by_stage: dict[str, StageTokenUsage] | None = None,
) -> PlanningExecutionResult:
    return PlanningExecutionResult(
        outcome=PlanningOutcome.FAILED,
        errors=(
            RunIssueSummary(
                code=code,
                message=message,
                retryable=retryable,
            ),
        ),
        token_usage=token_usage or TokenUsage(),
        token_usage_by_stage=token_usage_by_stage or {},
    )


def _sum_usage(
    invocations: list[PlannerInvocation],
    failed_usages: list[TokenUsage] | None = None,
) -> TokenUsage:
    usages = [
        *(item.token_usage for item in invocations),
        *(failed_usages or ()),
    ]
    input_tokens = sum(item.input_tokens for item in usages)
    output_tokens = sum(item.output_tokens for item in usages)
    cost = sum(item.estimated_cost_usd for item in usages)
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        estimated_cost_usd=cost,
    )


def _stage_usage(
    invocations: list[PlannerInvocation],
    failed_stages: list[StageTokenUsage] | None = None,
) -> dict[str, StageTokenUsage]:
    return merge_stage_maps(
        {
            item.stage_usage.stage: item.stage_usage
            for item in invocations
            if item.stage_usage is not None
        },
        {
            item.stage: item for item in (failed_stages or ())
        },
    )


async def _emit_validation_failed(
    reporter: PlanningProgressReporter,
    report: PlanValidationReport,
    *,
    attempt: int,
) -> None:
    await reporter.emit(
        PlanningProgress(
            event_type=AnalysisEventType.PLAN_VALIDATION_FAILED,
            phase=AnalysisRunPhase.PLAN_VALIDATION,
            payload={
                "attempt": attempt,
                "error_count": len(report.errors),
                "error_codes": [issue.code for issue in report.errors[:20]],
                "repairable": report.repairable,
            },
            deduplication_key=f"planning:validation-failed:{attempt}",
        )
    )


def _validate_plan_decision(
    plan: AnalysisPlan,
    command: PlanApprovalCommand,
) -> None:
    if (
        plan.revision != command.expected_revision
        or plan.plan_hash != command.expected_plan_hash
        or plan.input_signature != command.expected_input_signature
    ):
        raise AnalysisPlanConflictError("plan approval is stale")
    if plan.approval.status not in {
        PlanApprovalStatus.PENDING,
        PlanApprovalStatus.APPROVED,
        PlanApprovalStatus.REJECTED,
    }:
        raise AnalysisPlanConflictError("this plan does not require approval")


def _validate_workbook_guards(
    plan: AnalysisPlan,
    guards: tuple[object, ...],
) -> None:
    expected = {
        (
            intent.target.workbook_id,
            intent.target.worksheet_id,
            intent.target.base_workbook_revision,
            intent.target.base_snapshot_hash,
        )
        for intent in plan.write_intents
        if getattr(intent, "kind", None) == "write_workbook"
    }
    supplied = {
        (
            guard.workbook_id,
            guard.worksheet_id,
            guard.workbook_revision,
            guard.snapshot_hash,
        )
        for guard in guards
    }
    if expected != supplied:
        raise AnalysisPlanConflictError(
            "workbook changed after plan generation"
        )


__all__ = ["AnalysisPlanningService"]

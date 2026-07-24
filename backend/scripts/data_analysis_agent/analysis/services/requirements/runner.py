from __future__ import annotations

import logging
from dataclasses import dataclass

from ...models import AnalysisIssue, IssueCode, IssueSeverity, IssueStage
from ...models.request import AnalysisRequest
from ...models.requirements import (
    ANALYSIS_REQUIREMENTS_VERSION,
    AnalysisRequirements,
    REQUIREMENTS_PROMPT_VERSION,
    RequirementsDiagnostics,
    requirements_cache_key,
)
from ...repositories.requirements_cache import (
    RequirementsCache,
    RequirementsCacheError,
)
from .extractor import RequirementsExtractor
from .validation import fallback_extraction, validate_requirements_extraction


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RequirementsRunOutcome:
    artifact: AnalysisRequirements
    warnings: tuple[AnalysisIssue, ...] = ()


class AnalysisRequirementsRunner:
    """Coordinate versioned cache, LLM extraction, and deterministic guards."""

    def __init__(
        self,
        *,
        cache: RequirementsCache,
        extractor: RequirementsExtractor | None = None,
    ) -> None:
        self._cache = cache
        self._extractor = extractor or RequirementsExtractor()

    async def run(self, request: AnalysisRequest) -> RequirementsRunOutcome:
        cache_key = requirements_cache_key(
            query=request.query,
            document_ids=request.document_ids,
            prompt_version=REQUIREMENTS_PROMPT_VERSION,
            model=self._extractor.model,
        )
        warnings: list[AnalysisIssue] = []
        try:
            cached = await self._cache.load(
                user_id=request.user_id,
                cache_key=cache_key,
            )
        except RequirementsCacheError:
            logger.exception("Analysis requirements cache read failed")
            cached = None
            warnings.append(
                AnalysisIssue(
                    code=IssueCode.REQUIREMENTS_CACHE_READ_FAILED,
                    severity=IssueSeverity.WARNING,
                    stage=IssueStage.REQUIREMENTS,
                    message="Cached analysis requirements could not be read.",
                    retryable=True,
                )
            )
        if cached is not None and (
            cached.requirements_version == ANALYSIS_REQUIREMENTS_VERSION
            and cached.selected_document_ids == request.document_ids
            and cached.prompt_version == REQUIREMENTS_PROMPT_VERSION
            and cached.model == self._extractor.model
        ):
            diagnostics = cached.diagnostics.model_copy(update={"cache_hit": True})
            return RequirementsRunOutcome(
                artifact=cached.model_copy(update={"diagnostics": diagnostics}),
                warnings=tuple(warnings),
            )

        used_fallback = False
        attempts = 0
        try:
            extraction, attempts = await self._extractor.extract(request)
        except Exception:
            logger.exception("LLM requirements extraction failed")
            extraction = fallback_extraction(request)
            used_fallback = True
            warnings.append(
                AnalysisIssue(
                    code=IssueCode.REQUIREMENTS_EXTRACTION_FALLBACK,
                    severity=IssueSeverity.WARNING,
                    stage=IssueStage.REQUIREMENTS,
                    message=(
                        "Structured requirements could not be generated; a "
                        "conservative request-level requirement was used."
                    ),
                    retryable=True,
                )
            )

        validated = validate_requirements_extraction(
            request=request,
            extraction=extraction,
            model=self._extractor.model,
            extraction_attempts=attempts,
            used_fallback=used_fallback,
        ).requirements
        canonical = validated.model_copy(
            update={
                "diagnostics": RequirementsDiagnostics(
                    cache_hit=False,
                    extraction_attempts=validated.diagnostics.extraction_attempts,
                    used_fallback=validated.diagnostics.used_fallback,
                    validation_adjustments=(
                        validated.diagnostics.validation_adjustments
                    ),
                    validation_conflicts=(
                        validated.diagnostics.validation_conflicts
                    ),
                )
            }
        )
        if not canonical.diagnostics.used_fallback:
            try:
                await self._cache.save(
                    user_id=request.user_id,
                    cache_key=cache_key,
                    requirements=canonical,
                )
            except RequirementsCacheError:
                logger.exception("Analysis requirements cache write failed")
                warnings.append(
                    AnalysisIssue(
                        code=IssueCode.REQUIREMENTS_CACHE_WRITE_FAILED,
                        severity=IssueSeverity.WARNING,
                        stage=IssueStage.REQUIREMENTS,
                        message=(
                            "Validated analysis requirements could not be cached."
                        ),
                        retryable=True,
                    )
                )
        return RequirementsRunOutcome(
            artifact=canonical,
            warnings=tuple(warnings),
        )

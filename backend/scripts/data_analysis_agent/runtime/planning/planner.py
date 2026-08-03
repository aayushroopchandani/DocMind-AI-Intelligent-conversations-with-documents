from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any, Protocol

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..models.plans import PLANNER_PROMPT_VERSION, PlanProposal
from ..models.runs import TokenUsage
from .context import PlanningContext
from .contracts import PlanValidationIssue


PLANNER_SYSTEM_PROMPT = """You are a data-analysis execution planner. Produce a
strict, typed plan proposal, never executable code or prose. Use only the supplied
dataset aliases and stable column keys. Do not invent row counts that are unknown
until execution. Keep simple filters, sorting, selection, renaming, missing-value
fills, deduplication, aggregation, joins, pivoting and unpivoting on the native
executor. Use Python only for statistical tests, machine learning, or analytical
visualizations that native/frontend engines cannot produce, and declare the reason.

Never request external network access. Ask mode cannot write. Analyse mode may create
artifacts but cannot mutate workbooks. Edit mode may propose workbook writes, and
every workbook write must require final approval. Step provenance is deterministic
server metadata and may be omitted; never invent source identities because the backend
replaces that field from immutable input lineage before validation.
Estimates must be conservative upper bounds. A filter's output_rows must be null
because its result size is not known before execution. Return one JSON
object with exactly: intent, assumptions, steps, write_intents, expected_artifacts.
Do not include plan IDs, user IDs, versions, approval decisions, diagnostics, hashes,
or timestamps; the trusted backend supplies those fields."""

_SCHEMA_PROMPT_OMISSIONS = frozenset(
    {
        "title",
        "description",
        "default",
        "examples",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "pattern",
        "format",
    }
)


class AsyncPlanGenerator(Protocol):
    async def ainvoke(self, input: Any, **kwargs: Any) -> Any: ...


class PlannerInvocation(BaseModel):
    proposal: PlanProposal
    model: str = Field(min_length=1, max_length=200)
    prompt_version: str = PLANNER_PROMPT_VERSION
    token_usage: TokenUsage = Field(default_factory=TokenUsage)

    model_config = ConfigDict(extra="forbid", frozen=True)


class PlannerOutputError(RuntimeError):
    """The model failed to produce a typed proposal; raw output stays private."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        schema_issues: tuple[tuple[str, str], ...] = (),
        token_usage: TokenUsage | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.schema_issues = schema_issues
        self.token_usage = token_usage or TokenUsage()


class AnalysisPlanner(Protocol):
    async def propose(self, context: PlanningContext) -> PlannerInvocation: ...

    async def repair(
        self,
        context: PlanningContext,
        *,
        original: PlanProposal | None,
        issues: tuple[PlanValidationIssue, ...],
    ) -> PlannerInvocation: ...


def planning_model_name() -> str:
    return os.getenv(
        "DATA_ANALYSIS_PLANNER_MODEL",
        "google/gemini-2.5-flash",
    )


@lru_cache(maxsize=1)
def get_planning_llm() -> AsyncPlanGenerator:
    llm = ChatOpenAI(
        model=planning_model_name(),
        base_url=os.getenv(
            "OPENROUTER_BASE_URL",
            "https://openrouter.ai/api/v1",
        ),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=0,
        max_retries=1,
        max_tokens=int(os.getenv("DATA_ANALYSIS_PLANNER_MAX_TOKENS", "8000")),
        timeout=float(os.getenv("DATA_ANALYSIS_PLANNER_TIMEOUT", "60")),
    )
    return llm.with_structured_output(
        PlanProposal,
        # Gemini rejects the complete discriminated-union schema as having too
        # many constrained states. JSON mode remains provider-enforced JSON;
        # the compact contract is sent in-band and Pydantic remains the strict
        # authority after generation.
        method="json_mode",
        include_raw=True,
    )


class TypedAnalysisPlanner:
    """One initial proposal plus one explicit validator-guided repair call."""

    def __init__(
        self,
        generator: AsyncPlanGenerator | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self._generator = generator
        self.model = model or planning_model_name()

    async def propose(self, context: PlanningContext) -> PlannerInvocation:
        payload = {
            "planning_context": _model_context(context),
            "output_contract": _planner_output_contract(),
        }
        return await self._invoke(
            [
                SystemMessage(content=PLANNER_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ]
        )

    async def repair(
        self,
        context: PlanningContext,
        *,
        original: PlanProposal | None,
        issues: tuple[PlanValidationIssue, ...],
    ) -> PlannerInvocation:
        safe_issues = [
            {
                "code": issue.code,
                "layer": issue.layer.value,
                "message": issue.message,
                "path": issue.path,
            }
            for issue in issues[:100]
        ]
        payload = {
            "planning_context": _model_context(context),
            "output_contract": _planner_output_contract(),
            "original_plan": (
                original.model_dump(mode="json") if original is not None else None
            ),
            "validation_errors": safe_issues,
            "instruction": (
                "Return one complete corrected proposal. Change only what is "
                "needed to resolve every validation error."
            ),
        }
        return await self._invoke(
            [
                SystemMessage(content=PLANNER_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ]
        )

    async def _invoke(self, messages: list[Any]) -> PlannerInvocation:
        generator = self._generator or get_planning_llm()
        try:
            response = await generator.ainvoke(messages)
        except (
            OutputParserException,
            ValidationError,
            ValueError,
            TypeError,
        ) as exc:
            raise PlannerOutputError(
                "The planner returned an invalid typed response.",
                code="planner_schema_invalid",
            ) from exc
        except Exception as exc:
            raise PlannerOutputError(
                "The planning model is temporarily unavailable.",
                code="planner_unavailable",
            ) from exc

        raw: object = response
        parsed: object | None = response
        if isinstance(response, dict) and (
            "parsed" in response or "parsing_error" in response
        ):
            parsed = response.get("parsed")
            raw = response.get("raw")
        token_usage = _usage(raw)
        try:
            if parsed is None:
                parsed = _raw_json(raw)
            proposal = (
                parsed
                if isinstance(parsed, PlanProposal)
                else PlanProposal.model_validate(parsed)
            )
        except ValidationError as exc:
            raise PlannerOutputError(
                "The planner returned an invalid typed response.",
                code="planner_schema_invalid",
                schema_issues=_safe_schema_issues(exc),
                token_usage=token_usage,
            ) from exc
        except (ValueError, TypeError) as exc:
            raise PlannerOutputError(
                "The planner returned incomplete or malformed JSON.",
                code="planner_schema_invalid",
                schema_issues=(("plan", "Return one complete JSON object."),),
                token_usage=token_usage,
            ) from exc
        return PlannerInvocation(
            proposal=proposal,
            model=self.model,
            token_usage=token_usage,
        )


def _model_context(context: PlanningContext) -> dict[str, object]:
    return context.model_dump(
        mode="json",
        exclude={"user_id", "workspace_id"},
    )


@lru_cache(maxsize=1)
def _planner_output_contract() -> dict[str, object]:
    contract = _prune_schema(PlanProposal.model_json_schema())
    if not isinstance(contract, dict):  # pragma: no cover - Pydantic contract
        raise RuntimeError("planner output contract must be a JSON object")
    return contract


def _prune_schema(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _prune_schema(item)
            for key, item in value.items()
            if key not in _SCHEMA_PROMPT_OMISSIONS
        }
    if isinstance(value, list):
        return [_prune_schema(item) for item in value]
    return value


def _usage(raw: object) -> TokenUsage:
    metadata = getattr(raw, "usage_metadata", None)
    if not isinstance(metadata, dict):
        return TokenUsage()
    try:
        input_tokens = max(0, int(metadata.get("input_tokens") or 0))
        output_tokens = max(0, int(metadata.get("output_tokens") or 0))
    except (TypeError, ValueError):
        return TokenUsage()
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def _raw_json(raw: object) -> object:
    content = getattr(raw, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("planner response did not contain JSON text")
    return json.loads(content)


def _safe_schema_issues(
    error: ValidationError,
) -> tuple[tuple[str, str], ...]:
    output: list[tuple[str, str]] = []
    for issue in error.errors(include_input=False, include_url=False)[:40]:
        path = ".".join(str(segment) for segment in issue.get("loc", ()))
        message = " ".join(str(issue.get("msg") or "Invalid value.").split())
        output.append((path[:300] or "plan", message[:500]))
    return tuple(output) or (("plan", "The response did not match the contract."),)


__all__ = [
    "AnalysisPlanner",
    "AsyncPlanGenerator",
    "PlannerInvocation",
    "PlannerOutputError",
    "TypedAnalysisPlanner",
    "get_planning_llm",
    "planning_model_name",
]

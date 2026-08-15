from __future__ import annotations

import json
import math
import os
import re
from functools import lru_cache
from typing import Any, Protocol

from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..models.plans import PLANNER_PROMPT_VERSION, PlanProposal
from ..models.runs import StageTokenUsage, TokenUsage
from ..observability import measure_llm_call
from .context import PlanningContext
from .contracts import PlanValidationIssue


PLANNER_SYSTEM_PROMPT = """You are a data-analysis execution planner. Produce a
strict, typed plan proposal, never executable code or prose. Use only the supplied
dataset aliases and stable column keys. Do not invent row counts that are unknown
until execution. Keep simple filters, sorting, selection, renaming, missing-value
fills, deduplication, aggregation, joins, pivoting and unpivoting on the native
executor. This capability profile does not provide Python, charts, images, machine
learning, statistical tests, or arbitrary formulas. Never propose those operations;
explain their unavailability only when the requested work cannot be represented by
the supplied output contract.

Plan Schema 2.0 has no free-form executable expressions. Build filter predicates and
derived columns only from the closed expression AST in the output contract. Use
column_ref nodes with stable keys, typed literal nodes, and only declared operators.
Never return Python, SQL, raw spreadsheet formula text, function names, or code in an
expression. A filter AST must produce a boolean. A derived expression's inferred type
and unit must match output_column. Use safe_divide with an explicit zero_division
policy for division.

Never request external network access. Ask mode cannot write. Analyse mode may create
artifacts but cannot mutate workbooks. Edit mode may propose workbook writes, and
every workbook write must require final approval. Step provenance is deterministic
server metadata and may be omitted; never invent source identities because the backend
replaces that field from immutable input lineage before validation.
Estimates must be conservative upper bounds. A filter's output_rows must be null
because its result size is not known before execution, and rows_scanned must be at
least the known input row count. Copy existing source column keys exactly. Every new
column key, step ID, and alias must be a snake_case identifier containing only ASCII
letters, digits, and underscores; keep human-readable text in labels and titles.
Tabular steps must declare the exact non-empty output schema. Visualization and
compose-response steps produce artifacts, so their expected_schema should be empty.
Use the dedicated confusion_matrix chart type for classification evaluation; it is a
Python visualization, not a generic heatmap.
Use the fewest steps needed for the request. Do not add redundant filters, pivots,
renames, or response steps when one typed operation already expresses the intent.
Use generate_dataset only when the user explicitly requests synthetic, random, mock,
or manually specified new rows. Supply a complete typed generation specification,
an exact positive row_count, a non-negative seed, one bounded rule per expected
column, and only declared constraints. Generate deterministic unique IDs instead of
personal identifiers. Money ranges use integer minor units and an explicit scale.
Never use generate_dataset to combine or summarize existing source datasets. For
cross-source comparisons, transform the existing aliases and join on a verified key.

For a workbook write, copy workbook_id, worksheet_id, revision, snapshot hash, and
source A1 range exactly from the supplied workbook guard and dataset provenance.
Never invent workbook identity fields. Use adjacent_right for requests that say next
to the current table, unless the user explicitly gives an exact output range.

Return one JSON
object with exactly: intent, assumptions, steps, write_intents, expected_artifacts.
Do not include plan IDs, user IDs, versions, approval decisions, diagnostics, hashes,
or timestamps; the trusted backend supplies those fields."""

_SCHEMA_PROMPT_OMISSIONS = frozenset(
    {
        "additionalProperties",
        "discriminator",
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
_EXPRESSION_DEFINITIONS = (
    "LiteralExpression",
    "ColumnExpression",
    "UnaryExpression",
    "BinaryExpression",
    "CompareExpression",
    "SetExpression",
    "BetweenExpression",
    "BooleanExpression",
    "CaseWhenExpression",
    "CoalesceExpression",
    "CastExpression",
    "DatePartExpression",
    "DateTruncExpression",
    "StringTransformExpression",
    "NullCheckExpression",
)
_UNAVAILABLE_STEP_DEFINITIONS = frozenset(
    {"StatisticalTestStep", "TrainModelStep", "VisualizationStep"}
)


class AsyncPlanGenerator(Protocol):
    async def ainvoke(self, input: Any, **kwargs: Any) -> Any: ...


class PlannerInvocation(BaseModel):
    proposal: PlanProposal
    model: str = Field(min_length=1, max_length=200)
    prompt_version: str = PLANNER_PROMPT_VERSION
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    stage_usage: StageTokenUsage | None = None

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
        stage_usage: StageTokenUsage | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.schema_issues = schema_issues
        self.token_usage = token_usage or TokenUsage()
        self.stage_usage = stage_usage


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
            ],
            stage="planning",
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
            ],
            stage="plan_repair",
        )

    async def _invoke(
        self,
        messages: list[Any],
        *,
        stage: str,
    ) -> PlannerInvocation:
        generator = self._generator or get_planning_llm()
        measurement = None
        try:
            with measure_llm_call(
                stage=stage,
                model=self.model,
                prompt_version=PLANNER_PROMPT_VERSION,
            ) as measurement:
                response = await generator.ainvoke(messages)
        except (
            OutputParserException,
            ValidationError,
            ValueError,
            TypeError,
        ) as exc:
            stage_usage = measurement.result if measurement else None
            raise PlannerOutputError(
                "The planner returned an invalid typed response.",
                code="planner_schema_invalid",
                token_usage=(stage_usage.usage if stage_usage else None),
                stage_usage=stage_usage,
            ) from exc
        except Exception as exc:
            stage_usage = measurement.result if measurement else None
            raise PlannerOutputError(
                "The planning model is temporarily unavailable.",
                code="planner_unavailable",
                token_usage=(stage_usage.usage if stage_usage else None),
                stage_usage=stage_usage,
            ) from exc

        raw: object = response
        parsed: object | None = response
        if isinstance(response, dict) and (
            "parsed" in response or "parsing_error" in response
        ):
            parsed = response.get("parsed")
            raw = response.get("raw")
        token_usage = _usage(raw)
        stage_usage = measurement.result if measurement is not None else None
        if stage_usage is None:
            stage_usage = StageTokenUsage(
                stage=stage,
                model=self.model,
                prompt_version=PLANNER_PROMPT_VERSION,
                usage=token_usage,
            )
        elif token_usage.total_tokens > stage_usage.usage.total_tokens:
            stage_usage = stage_usage.model_copy(update={"usage": token_usage})
        token_usage = stage_usage.usage
        try:
            if parsed is None:
                parsed = _raw_json(raw)
            proposal = (
                parsed
                if isinstance(parsed, PlanProposal)
                else PlanProposal.model_validate(
                    _normalize_proposal_payload(parsed)
                )
            )
        except ValidationError as exc:
            raise PlannerOutputError(
                "The planner returned an invalid typed response.",
                code="planner_schema_invalid",
                schema_issues=_safe_schema_issues(exc),
                token_usage=token_usage,
                stage_usage=stage_usage,
            ) from exc
        except (ValueError, TypeError) as exc:
            raise PlannerOutputError(
                "The planner returned incomplete or malformed JSON.",
                code="planner_schema_invalid",
                schema_issues=(("plan", "Return one complete JSON object."),),
                token_usage=token_usage,
                stage_usage=stage_usage,
            ) from exc
        return PlannerInvocation(
            proposal=proposal,
            model=self.model,
            token_usage=token_usage,
            stage_usage=stage_usage,
        )


def _model_context(context: PlanningContext) -> dict[str, object]:
    return context.model_dump(
        mode="json",
        exclude={"user_id", "workspace_id"},
    )


@lru_cache(maxsize=1)
def _planner_output_contract() -> dict[str, object]:
    schema = _compact_planner_schema(PlanProposal.model_json_schema())
    contract = _prune_schema(schema)
    if not isinstance(contract, dict):  # pragma: no cover - Pydantic contract
        raise RuntimeError("planner output contract must be a JSON object")
    return contract


def _compact_planner_schema(schema: dict[str, object]) -> dict[str, object]:
    """Deduplicate recursive AST unions and hide unavailable step variants."""

    expression_refs = frozenset(
        f"#/$defs/{name}" for name in _EXPRESSION_DEFINITIONS
    )

    def compact(value: object) -> object:
        if isinstance(value, dict):
            variants = value.get("oneOf")
            if isinstance(variants, list):
                refs = frozenset(
                    str(item.get("$ref"))
                    for item in variants
                    if isinstance(item, dict) and "$ref" in item
                )
                if refs == expression_refs and len(variants) == len(expression_refs):
                    return {"$ref": "#/$defs/Expression"}
            return {key: compact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [compact(item) for item in value]
        return value

    compacted = compact(schema)
    if not isinstance(compacted, dict):  # pragma: no cover - local invariant
        raise RuntimeError("planner schema compaction failed")
    definitions = compacted.get("$defs")
    if not isinstance(definitions, dict):  # pragma: no cover - Pydantic contract
        raise RuntimeError("planner schema definitions are missing")
    definitions["Expression"] = {
        "oneOf": [
            {"$ref": f"#/$defs/{name}"} for name in _EXPRESSION_DEFINITIONS
        ]
    }
    for name in _UNAVAILABLE_STEP_DEFINITIONS:
        definitions.pop(name, None)

    properties = compacted.get("properties")
    if isinstance(properties, dict):
        steps = properties.get("steps")
        if isinstance(steps, dict):
            items = steps.get("items")
            if isinstance(items, dict) and isinstance(items.get("oneOf"), list):
                unavailable_refs = {
                    f"#/$defs/{name}" for name in _UNAVAILABLE_STEP_DEFINITIONS
                }
                items["oneOf"] = [
                    item
                    for item in items["oneOf"]
                    if not (
                        isinstance(item, dict)
                        and item.get("$ref") in unavailable_refs
                    )
                ]

    for definition_name, property_name in (
        ("ArtifactWriteIntent", "artifact_kind"),
        ("ExpectedArtifact", "kind"),
    ):
        definition = definitions.get(definition_name)
        if not isinstance(definition, dict):
            continue
        definition_properties = definition.get("properties")
        if not isinstance(definition_properties, dict):
            continue
        kind = definition_properties.get(property_name)
        if isinstance(kind, dict) and isinstance(kind.get("enum"), list):
            kind["enum"] = [
                value for value in kind["enum"] if value not in {"chart", "model"}
            ]
    return compacted


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


def _normalize_proposal_payload(value: object) -> object:
    """Normalize harmless provider formatting before strict Pydantic parsing."""

    if not isinstance(value, dict):
        return value
    output = dict(value)
    steps = output.get("steps")
    if isinstance(steps, list):
        output["steps"] = [
            _normalize_step_payload(step) if isinstance(step, dict) else step
            for step in steps
        ]
    intents = output.get("write_intents")
    if isinstance(intents, list):
        output["write_intents"] = [
            _normalize_write_intent_payload(intent)
            if isinstance(intent, dict)
            else intent
            for intent in intents
        ]
    return _normalize_column_identifiers(output)


def _normalize_step_payload(step: dict[str, object]) -> dict[str, object]:
    output = dict(step)
    estimate = output.get("estimate")
    if isinstance(estimate, dict):
        normalized_estimate = dict(estimate)
        for key in (
            "rows_scanned",
            "cells_written",
            "memory_mb",
            "chart_cardinality",
        ):
            normalized_estimate[key] = _coerce_number(
                normalized_estimate.get(key),
                integer=True,
                default=0,
            )
        normalized_estimate["output_rows"] = _coerce_number(
            normalized_estimate.get("output_rows"),
            integer=True,
            default=None,
        )
        for key in (
            "duration_seconds",
            "estimated_cost_usd",
        ):
            normalized_estimate[key] = _coerce_number(
                normalized_estimate.get(key),
                integer=False,
                default=0.0,
            )
        output["estimate"] = normalized_estimate
    if output.get("kind") == "generate_dataset":
        generation = output.get("generation")
        if isinstance(generation, dict):
            normalized_generation = dict(generation)
            normalized_generation["row_count"] = _coerce_number(
                normalized_generation.get("row_count"),
                integer=True,
                default=None,
            )
            normalized_generation["seed"] = _coerce_number(
                normalized_generation.get("seed"),
                integer=True,
                default=None,
            )
            output["generation"] = normalized_generation
    predicates = output.get("predicates")
    if isinstance(predicates, list):
        output["predicates"] = [
            _normalize_predicate_payload(predicate)
            if isinstance(predicate, dict)
            else predicate
            for predicate in predicates
        ]
    return output


def _normalize_predicate_payload(
    predicate: dict[str, object],
) -> dict[str, object]:
    output = dict(predicate)
    if output.get("kind"):
        return output
    operator = str(output.get("operator") or "")
    if "values" in output:
        output["kind"] = "set_membership"
    elif operator in {"is_null", "is_not_null"}:
        output["kind"] = "null_check"
    else:
        output["kind"] = "comparison"
    return output


def _normalize_write_intent_payload(
    intent: dict[str, object],
) -> dict[str, object]:
    output = dict(intent)
    target = output.get("target")
    if not isinstance(target, dict):
        return output
    normalized_target = dict(target)
    aliases = {
        "workbook_revision": "base_workbook_revision",
        "snapshot_hash": "base_snapshot_hash",
    }
    for source, destination in aliases.items():
        if destination not in normalized_target and source in normalized_target:
            normalized_target[destination] = normalized_target.pop(source)
    normalized_target["base_workbook_revision"] = _coerce_number(
        normalized_target.get("base_workbook_revision"),
        integer=True,
        default=0,
    )
    output["target"] = normalized_target
    return output


def _coerce_number(
    value: object,
    *,
    integer: bool,
    default: int | float | None,
) -> int | float | None:
    if value is None or isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number) or number < 0:
        return default
    return math.ceil(number) if integer else number


_COLUMN_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,119}$")
_COLUMN_REFERENCE_FIELDS = frozenset(
    {
        "column_key",
        "source_key",
        "input_column_key",
        "output_key",
        "left_column_key",
        "right_column_key",
        "pivot_column",
        "value_column",
        "target_column",
        "x_column",
        "group_column",
    }
)
_COLUMN_REFERENCE_LIST_FIELDS = frozenset(
    {
        "columns",
        "column_keys",
        "key_columns",
        "referenced_columns",
        "group_by",
        "index_columns",
        "value_columns",
        "feature_columns",
        "y_columns",
    }
)


def _normalize_column_identifiers(value: object) -> object:
    replacements: dict[str, str] = {}
    used: set[str] = set()

    def collect(node: object) -> None:
        if isinstance(node, dict):
            is_column = {"key", "label", "data_type"} <= set(node)
            if is_column and isinstance(node.get("key"), str):
                key = str(node["key"])
                if _COLUMN_SYMBOL_RE.fullmatch(key):
                    used.add(key)
                else:
                    replacements.setdefault(key, _unique_symbol(key, used))
            if (
                {"source_key", "output_key", "output_label"} <= set(node)
                and isinstance(node.get("output_key"), str)
            ):
                key = str(node["output_key"])
                if _COLUMN_SYMBOL_RE.fullmatch(key):
                    used.add(key)
                else:
                    replacements.setdefault(key, _unique_symbol(key, used))
            for item in node.values():
                collect(item)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    def replace(node: object, *, field: str | None = None) -> object:
        if isinstance(node, dict):
            output: dict[str, object] = {}
            is_column = {"key", "label", "data_type"} <= set(node)
            for key, item in node.items():
                if (
                    key == "key"
                    and is_column
                    and isinstance(item, str)
                    and item in replacements
                ):
                    output[key] = replacements[item]
                elif (
                    key in _COLUMN_REFERENCE_FIELDS
                    and isinstance(item, str)
                    and item in replacements
                ):
                    output[key] = replacements[item]
                else:
                    output[key] = replace(item, field=key)
            return output
        if isinstance(node, list):
            if field in _COLUMN_REFERENCE_LIST_FIELDS:
                return [
                    replacements.get(item, item) if isinstance(item, str) else item
                    for item in node
                ]
            return [replace(item, field=field) for item in node]
        return node

    collect(value)
    return replace(value)


def _unique_symbol(value: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()
    if not base:
        base = "column"
    if not (base[0].isalpha() or base[0] == "_"):
        base = f"column_{base}"
    base = base[:120]
    candidate = base
    suffix = 2
    while candidate in used:
        suffix_text = f"_{suffix}"
        candidate = f"{base[: 120 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    used.add(candidate)
    return candidate


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

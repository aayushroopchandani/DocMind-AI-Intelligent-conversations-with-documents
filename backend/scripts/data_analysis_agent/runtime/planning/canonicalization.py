from __future__ import annotations

from ..models.plans import (
    AggregateStep,
    ComposeResponseStep,
    DeduplicateStep,
    DeriveColumnStep,
    FillMissingStep,
    FilterRowsStep,
    GenerateDatasetStep,
    JoinStep,
    PlanColumn,
    PlanProposal,
    PlanStep,
    PlanWriteIntent,
    PivotStep,
    RenameColumnsStep,
    SelectColumnsStep,
    SortRowsStep,
    StepProvenance,
    StatisticalTestStep,
    TrainModelStep,
    UnpivotStep,
    WorkbookWriteIntent,
    VisualizationStep,
    join_output_schema,
    step_input_aliases,
)
from ..models.expressions import map_expression_columns
from .context import PlanningContext


_SCHEMA_PRESERVING_STEPS = (
    FilterRowsStep,
    SortRowsStep,
    FillMissingStep,
    DeduplicateStep,
)


def canonicalize_proposal(
    context: PlanningContext,
    proposal: PlanProposal,
) -> PlanProposal:
    """Inject fields that are derivable from immutable planning context."""

    schemas: dict[str, tuple[PlanColumn, ...]] = {
        dataset.alias: dataset.columns for dataset in context.input_datasets
    }
    producers: dict[str, str | None] = {
        dataset.alias: None for dataset in context.input_datasets
    }
    row_counts: dict[str, int | None] = {
        dataset.alias: dataset.row_count for dataset in context.input_datasets
    }
    lineages: dict[str, tuple[tuple[str, str], ...]] = {
        dataset.alias: tuple(
            (source.source_dataset_id, source.source_version)
            for source in dataset.provenance
        )
        for dataset in context.input_datasets
    }
    seen_step_ids: set[str] = set()
    steps: list[PlanStep] = []

    for proposed_step in proposal.steps:
        step = proposed_step
        if isinstance(step, ComposeResponseStep) and not step.input_aliases:
            step = step.model_copy(
                update={
                    "input_aliases": tuple(
                        dataset.alias for dataset in context.input_datasets
                    )
                }
            )
        step = _canonical_column_references(step, schemas)
        input_aliases = step_input_aliases(step)
        dependencies = tuple(
            dict.fromkeys(
                (
                    *(
                        dependency
                        for dependency in step.depends_on
                        if dependency in seen_step_ids
                    ),
                    *(
                        producer
                        for alias in input_aliases
                        if (producer := producers.get(alias)) is not None
                    ),
                )
            )
        )
        expected_schema = _expected_schema(step, schemas)
        input_rows = sum(row_counts.get(alias) or 0 for alias in input_aliases)
        estimate = step.estimate
        if estimate.rows_scanned < input_rows:
            estimate = estimate.model_copy(update={"rows_scanned": input_rows})

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
                    for alias in input_aliases
                    for pair in lineages.get(alias, ())
                )
            )
            provenance = StepProvenance(
                source_dataset_ids=tuple(pair[0] for pair in lineage),
                source_versions=tuple(pair[1] for pair in lineage),
                generated=False,
                description="Canonical immutable lineage derived by the server.",
            )
        step = step.model_copy(
            update={
                "depends_on": dependencies,
                "expected_schema": expected_schema,
                "estimate": estimate,
                "provenance": provenance,
            }
        )
        steps.append(step)
        seen_step_ids.add(step.step_id)
        schemas[step.output_alias] = step.expected_schema
        producers[step.output_alias] = step.step_id
        lineages[step.output_alias] = lineage
        row_counts[step.output_alias] = _maximum_output_rows(step, row_counts)

    return proposal.model_copy(
        update={
            "steps": tuple(steps),
            "write_intents": _canonical_write_intents(
                context,
                proposal.write_intents,
            ),
        }
    )


def _canonical_column_references(
    step: PlanStep,
    schemas: dict[str, tuple[PlanColumn, ...]],
) -> PlanStep:
    if isinstance(step, FilterRowsStep):
        schema = schemas.get(step.input_alias, ())
        return step.model_copy(
            update={
                "predicate": map_expression_columns(
                    step.predicate,
                    lambda key: _resolve_column_key(key, schema),
                )
            }
        )
    if isinstance(step, SortRowsStep):
        schema = schemas.get(step.input_alias, ())
        return step.model_copy(
            update={
                "keys": tuple(
                    key.model_copy(
                        update={
                            "column_key": _resolve_column_key(
                                key.column_key,
                                schema,
                            )
                        }
                    )
                    for key in step.keys
                )
            }
        )
    if isinstance(step, SelectColumnsStep):
        schema = schemas.get(step.input_alias, ())
        return step.model_copy(
            update={
                "column_keys": tuple(
                    _resolve_column_key(key, schema) for key in step.column_keys
                )
            }
        )
    if isinstance(step, RenameColumnsStep):
        schema = schemas.get(step.input_alias, ())
        return step.model_copy(
            update={
                "renames": tuple(
                    rename.model_copy(
                        update={
                            "source_key": _resolve_column_key(
                                rename.source_key,
                                schema,
                            )
                        }
                    )
                    for rename in step.renames
                )
            }
        )
    if isinstance(step, FillMissingStep):
        schema = schemas.get(step.input_alias, ())
        return step.model_copy(
            update={
                "rules": tuple(
                    rule.model_copy(
                        update={
                            "column_key": _resolve_column_key(
                                rule.column_key,
                                schema,
                            )
                        }
                    )
                    for rule in step.rules
                ),
                "group_by": tuple(
                    _resolve_column_key(key, schema) for key in step.group_by
                ),
                "order_by": tuple(
                    key.model_copy(
                        update={
                            "column_key": _resolve_column_key(
                                key.column_key,
                                schema,
                            )
                        }
                    )
                    for key in step.order_by
                ),
            }
        )
    if isinstance(step, DeduplicateStep):
        schema = schemas.get(step.input_alias, ())
        return step.model_copy(
            update={
                "key_columns": tuple(
                    _resolve_column_key(key, schema) for key in step.key_columns
                ),
                "order_by": tuple(
                    key.model_copy(
                        update={
                            "column_key": _resolve_column_key(
                                key.column_key,
                                schema,
                            )
                        }
                    )
                    for key in step.order_by
                ),
            }
        )
    if isinstance(step, DeriveColumnStep):
        schema = schemas.get(step.input_alias, ())
        return step.model_copy(
            update={
                "expression": map_expression_columns(
                    step.expression,
                    lambda key: _resolve_column_key(key, schema),
                )
            }
        )
    if isinstance(step, AggregateStep):
        schema = schemas.get(step.input_alias, ())
        return step.model_copy(
            update={
                "group_by": tuple(
                    _resolve_column_key(key, schema) for key in step.group_by
                ),
                "metrics": tuple(
                    metric.model_copy(
                        update={
                            "input_column_key": _resolve_column_key(
                                metric.input_column_key,
                                schema,
                            )
                        }
                    )
                    for metric in step.metrics
                ),
            }
        )
    if isinstance(step, JoinStep):
        left = schemas.get(step.left_alias, ())
        right = schemas.get(step.right_alias, ())
        return step.model_copy(
            update={
                "keys": tuple(
                    pair.model_copy(
                        update={
                            "left_column_key": _resolve_column_key(
                                pair.left_column_key,
                                left,
                            ),
                            "right_column_key": _resolve_column_key(
                                pair.right_column_key,
                                right,
                            ),
                        }
                    )
                    for pair in step.keys
                )
            }
        )
    if isinstance(step, PivotStep):
        schema = schemas.get(step.input_alias, ())
        return step.model_copy(
            update={
                "index_columns": tuple(
                    _resolve_column_key(key, schema)
                    for key in step.index_columns
                ),
                "pivot_column": _resolve_column_key(step.pivot_column, schema),
                "value_column": _resolve_column_key(step.value_column, schema),
            }
        )
    if isinstance(step, UnpivotStep):
        schema = schemas.get(step.input_alias, ())
        return step.model_copy(
            update={
                "id_columns": tuple(
                    _resolve_column_key(key, schema) for key in step.id_columns
                ),
                "value_columns": tuple(
                    _resolve_column_key(key, schema)
                    for key in step.value_columns
                ),
            }
        )
    if isinstance(step, StatisticalTestStep):
        schema = schemas.get(step.input_alias, ())
        return step.model_copy(
            update={
                "feature_columns": tuple(
                    _resolve_column_key(key, schema)
                    for key in step.feature_columns
                )
            }
        )
    if isinstance(step, TrainModelStep):
        schema = schemas.get(step.input_alias, ())
        return step.model_copy(
            update={
                "feature_columns": tuple(
                    _resolve_column_key(key, schema)
                    for key in step.feature_columns
                ),
                "target_column": (
                    _resolve_column_key(step.target_column, schema)
                    if step.target_column is not None
                    else None
                ),
            }
        )
    if isinstance(step, VisualizationStep):
        schema = schemas.get(step.input_alias, ())
        return step.model_copy(
            update={
                "x_column": (
                    _resolve_column_key(step.x_column, schema)
                    if step.x_column is not None
                    else None
                ),
                "y_columns": tuple(
                    _resolve_column_key(key, schema) for key in step.y_columns
                ),
                "group_column": (
                    _resolve_column_key(step.group_column, schema)
                    if step.group_column is not None
                    else None
                ),
            }
        )
    return step


def _resolve_column_key(
    value: str,
    schema: tuple[PlanColumn, ...],
) -> str:
    normalized = _normalized_label(value)
    matches = tuple(
        column.key
        for column in schema
        if normalized
        in {
            _normalized_label(column.key),
            _normalized_label(column.label),
        }
    )
    return matches[0] if len(matches) == 1 else value


def _normalized_label(value: str) -> str:
    return " ".join(
        "".join(character if character.isalnum() else " " for character in value)
        .casefold()
        .split()
    )


def _expected_schema(
    step: PlanStep,
    schemas: dict[str, tuple[PlanColumn, ...]],
) -> tuple[PlanColumn, ...]:
    if isinstance(step, _SCHEMA_PRESERVING_STEPS):
        return schemas.get(step.input_alias, step.expected_schema)
    if isinstance(step, SelectColumnsStep):
        source = {column.key: column for column in schemas.get(step.input_alias, ())}
        if all(key in source for key in step.column_keys):
            return tuple(source[key] for key in step.column_keys)
    if isinstance(step, RenameColumnsStep):
        source = schemas.get(step.input_alias, ())
        renames = {item.source_key: item for item in step.renames}
        if all(key in {column.key for column in source} for key in renames):
            return tuple(
                column.model_copy(
                    update={
                        "key": renames[column.key].output_key,
                        "label": renames[column.key].output_label,
                    }
                )
                if column.key in renames
                else column
                for column in source
            )
    if isinstance(step, DeriveColumnStep):
        source = schemas.get(step.input_alias, ())
        return (*source, step.output_column)
    if isinstance(step, AggregateStep):
        source = {column.key: column for column in schemas.get(step.input_alias, ())}
        if all(key in source for key in step.group_by):
            return (
                *(source[key] for key in step.group_by),
                *(metric.output_column for metric in step.metrics),
            )
    if isinstance(step, JoinStep):
        left = schemas.get(step.left_alias, ())
        right = schemas.get(step.right_alias, ())
        expected = join_output_schema(step, left, right)
        if (
            len({column.key for column in expected}) == len(expected)
            and all(len(column.key) <= 120 for column in expected)
        ):
            return expected
    if isinstance(step, UnpivotStep):
        source = {column.key: column for column in schemas.get(step.input_alias, ())}
        if all(key in source for key in step.id_columns):
            return (
                *(source[key] for key in step.id_columns),
                step.variable_column,
                step.value_column,
            )
    return step.expected_schema


def _maximum_output_rows(
    step: PlanStep,
    row_counts: dict[str, int | None],
) -> int | None:
    if isinstance(step, GenerateDatasetStep):
        return step.row_count
    inputs = tuple(row_counts.get(alias) for alias in step_input_aliases(step))
    if not inputs or any(value is None for value in inputs):
        return None
    known = tuple(int(value) for value in inputs if value is not None)
    if isinstance(step, JoinStep):
        return known[0] * known[1]
    if isinstance(step, UnpivotStep):
        return known[0] * len(step.value_columns)
    if isinstance(step, ComposeResponseStep):
        return max(known, default=0)
    return known[0]


def _canonical_write_intents(
    context: PlanningContext,
    intents: tuple[PlanWriteIntent, ...],
) -> tuple[PlanWriteIntent, ...]:
    if len(context.workbook_guards) != 1:
        return intents
    guard = context.workbook_guards[0]
    source_ranges = tuple(
        dict.fromkeys(
            source.range_a1
            for dataset in context.input_datasets
            for source in dataset.provenance
            if source.workbook_id == guard.workbook_id
            and source.worksheet_id == guard.worksheet_id
            and source.range_a1 is not None
        )
    )
    source_range = source_ranges[0] if len(source_ranges) == 1 else None
    canonical: list[PlanWriteIntent] = []
    for intent in intents:
        if not isinstance(intent, WorkbookWriteIntent):
            canonical.append(intent)
            continue
        target_update: dict[str, object] = {
            "workbook_id": guard.workbook_id,
            "worksheet_id": guard.worksheet_id,
            "base_workbook_revision": guard.workbook_revision,
            "base_snapshot_hash": guard.snapshot_hash,
        }
        if source_range is not None:
            target_update["source_range_a1"] = source_range
        canonical.append(
            intent.model_copy(
                update={
                    "target": intent.target.model_copy(update=target_update)
                }
            )
        )
    return tuple(canonical)


__all__ = ["canonicalize_proposal"]

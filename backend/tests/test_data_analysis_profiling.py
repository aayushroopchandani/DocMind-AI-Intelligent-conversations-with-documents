from __future__ import annotations

import threading
import time
import unittest
from typing import Any
from unittest.mock import patch

from db.models.structured_table import StructuredTable
from scripts.data_analysis_agent.analysis.models import (
    DATASET_PROFILER_VERSION,
    DatasetAccessReference,
    DatasetColumn,
    EvidencePackage,
    HydratedDatasetReference,
    IssueCode,
    ProfileFailureReason,
    ProfileQualityWarning,
    ProfiledDataType,
    SemanticRole,
    SourceRegion,
    TableOrientation,
    profile_cache_key,
)
from scripts.data_analysis_agent.analysis.services.profiling import (
    DatasetProfilingRunner,
    DeterministicDatasetProfiler,
)
from scripts.data_analysis_agent.analysis.repositories import (
    MongoDatasetRepository,
    MongoProfileCache,
    ProfileCacheError,
)
from scripts.data_analysis_agent.analysis.services.versioning import (
    raw_dataset_id,
    source_version,
)


DOCUMENT_ID = "c" * 64


def _raw_table(
    table_id: str,
    *,
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    title: str = "Analytical table",
    page_start: int = 1,
    page_end: int = 1,
) -> dict[str, Any]:
    fragments = [
        {"page": page, "bounding_box": [10.0, 20.0, 500.0, 700.0]}
        for page in range(page_start, page_end + 1)
    ]
    return {
        "table_id": table_id,
        "document_id": DOCUMENT_ID,
        "user_id": "user-1",
        "page_start": page_start,
        "page_end": page_end,
        "title": title,
        "extraction_method": "pymupdf",
        "columns": columns,
        "rows": rows,
        "source_fragments": fragments,
    }


def _dataset(raw: dict[str, Any]) -> HydratedDatasetReference:
    table = StructuredTable.model_validate(raw)
    version = source_version(table)
    return HydratedDatasetReference(
        dataset_id=raw_dataset_id(table, version),
        source_version=version,
        table_id=table.table_id,
        document_id=table.document_id,
        title=table.title,
        page_start=table.page_start,
        page_end=table.page_end,
        extraction_method=table.extraction_method,
        columns=tuple(
            DatasetColumn(
                key=column.key,
                label=column.label,
                type=column.type,
                unit=column.unit,
            )
            for column in table.columns
        ),
        row_count=len(table.rows),
        source_regions=tuple(
            SourceRegion(
                page=fragment.page,
                bounding_box=tuple(fragment.bounding_box),
            )
            for fragment in table.source_fragments
        ),
        access=DatasetAccessReference(table_id=table.table_id),
        usable_for_analysis=bool(table.rows),
    )


def _evidence(*datasets: HydratedDatasetReference) -> EvidencePackage:
    return EvidencePackage(
        run_id="run-1",
        status="complete" if datasets else "empty",
        datasets=tuple(datasets),
        retrieved_table_count=len(datasets),
        hydrated_table_count=len(datasets),
    )


class DeterministicDatasetProfilerTests(unittest.TestCase):
    def test_numeric_time_and_quality_statistics_are_deterministic(self) -> None:
        raw = _raw_table(
            "time-series",
            columns=[
                {"key": "year", "label": "Fiscal Year", "type": "string"},
                {
                    "key": "revenue",
                    "label": "Revenue (USD million)",
                    "type": "number",
                },
            ],
            rows=[
                {"year": "FY2020", "revenue": 0},
                {"year": "FY2021", "revenue": 0},
                {"year": "FY2022", "revenue": 2},
                {"year": "FY2024", "revenue": 2},
                {"year": "FY2025", "revenue": 100},
            ],
        )
        table = StructuredTable.model_validate(raw)
        dataset = _dataset(raw)
        profiler = DeterministicDatasetProfiler()

        first = profiler.profile(dataset, table)
        second = profiler.profile(dataset, table)

        self.assertEqual(first, second)
        year = first.columns[0]
        self.assertEqual(year.inferred_type, ProfiledDataType.FISCAL_PERIOD)
        self.assertEqual(year.declared_type, "string")
        self.assertEqual(year.semantic_role, SemanticRole.TIME_PERIOD)
        self.assertEqual(year.time_statistics.minimum_period, "FY2020")
        self.assertEqual(year.time_statistics.maximum_period, "FY2025")
        self.assertEqual(year.time_statistics.missing_intervals, ("FY2023",))

        revenue = first.columns[1]
        self.assertEqual(revenue.semantic_role, SemanticRole.METRIC)
        self.assertEqual(revenue.detected_unit, "USD million")
        self.assertEqual(revenue.numeric_statistics.minimum, 0)
        self.assertEqual(revenue.numeric_statistics.maximum, 100)
        self.assertEqual(revenue.numeric_statistics.zero_count, 2)
        self.assertEqual(revenue.numeric_statistics.potential_outlier_count, 1)
        self.assertEqual(first.duplicate_row_count, 0)
        self.assertTrue(first.periods_in_rows)
        self.assertFalse(first.periods_in_headers)
        self.assertTrue(first.suitable_for_analysis)

    def test_year_columns_are_detected_as_a_wide_time_series(self) -> None:
        raw = _raw_table(
            "wide-table",
            columns=[
                {"key": "metric", "label": "Metric", "type": "string"},
                {"key": "y2022", "label": "2022", "type": "number"},
                {"key": "y2023", "label": "2023", "type": "number"},
                {"key": "y2024", "label": "2024", "type": "number"},
            ],
            rows=[
                {"metric": "Revenue", "y2022": 10, "y2023": 12, "y2024": 15},
                {"metric": "Profit", "y2022": 2, "y2023": 3, "y2024": 4},
            ],
        )

        profile = DeterministicDatasetProfiler().profile(
            _dataset(raw), StructuredTable.model_validate(raw)
        )

        self.assertTrue(profile.periods_in_headers)
        self.assertFalse(profile.periods_in_rows)
        self.assertEqual(profile.orientation, TableOrientation.WIDE_TIME_SERIES)

    def test_common_pdf_table_shapes_are_classified(self) -> None:
        cases = (
            (
                "ordinary",
                [
                    {"key": "year", "label": "Fiscal Year", "type": "string"},
                    {"key": "revenue", "label": "Revenue", "type": "number"},
                    {"key": "profit", "label": "Profit", "type": "number"},
                ],
                [
                    {"year": "FY2022", "revenue": 10, "profit": 2},
                    {"year": "FY2023", "revenue": 12, "profit": 3},
                ],
                TableOrientation.ORDINARY_RECORDS,
                1,
                1,
            ),
            (
                "transposed",
                [
                    {"key": "metric", "label": "Metric", "type": "string"},
                    {"key": "current", "label": "Current", "type": "number"},
                    {"key": "previous", "label": "Previous", "type": "number"},
                ],
                [
                    {"metric": "Revenue", "current": 12, "previous": 10},
                    {"metric": "Profit", "current": 3, "previous": 2},
                    {"metric": "Cost", "current": 9, "previous": 8},
                ],
                TableOrientation.TRANSPOSED,
                1,
                1,
            ),
            (
                "key-value",
                [
                    {"key": "item", "label": "Item", "type": "string"},
                    {"key": "amount", "label": "Amount", "type": "number"},
                ],
                [
                    {"item": "Revenue", "amount": 12},
                    {"item": "Profit", "amount": 3},
                    {"item": "Cost", "amount": 9},
                ],
                TableOrientation.KEY_VALUE,
                1,
                1,
            ),
            (
                "summary",
                [
                    {"key": "item", "label": "Item", "type": "string"},
                    {"key": "amount", "label": "Amount", "type": "number"},
                ],
                [
                    {"item": "Revenue", "amount": 12},
                    {"item": "Total", "amount": 12},
                ],
                TableOrientation.SUMMARY,
                1,
                1,
            ),
            (
                "textual",
                [
                    {"key": "topic", "label": "Topic", "type": "string"},
                    {"key": "detail", "label": "Detail", "type": "string"},
                ],
                [
                    {"topic": "Risk", "detail": "Demand may change."},
                    {"topic": "Policy", "detail": "Controls are reviewed."},
                ],
                TableOrientation.PRIMARILY_TEXTUAL,
                1,
                1,
            ),
            (
                "continuation",
                [
                    {"key": "year", "label": "Year", "type": "string"},
                    {"key": "revenue", "label": "Revenue", "type": "number"},
                ],
                [
                    {"year": "2022", "revenue": 10},
                    {"year": "2023", "revenue": 12},
                ],
                TableOrientation.CONTINUATION,
                1,
                2,
            ),
            (
                "matrix",
                [
                    {"key": "product", "label": "Product", "type": "string"},
                    {"key": "north", "label": "North", "type": "number"},
                    {"key": "south", "label": "South", "type": "number"},
                ],
                [
                    {"product": "A", "north": 10, "south": 12},
                    {"product": "B", "north": 9, "south": 11},
                    {"product": "C", "north": 8, "south": 10},
                ],
                TableOrientation.MATRIX,
                1,
                1,
            ),
        )

        profiler = DeterministicDatasetProfiler()
        for name, columns, rows, expected, page_start, page_end in cases:
            with self.subTest(name=name):
                raw = _raw_table(
                    f"{name}-shape",
                    columns=columns,
                    rows=rows,
                    page_start=page_start,
                    page_end=page_end,
                )
                profile = profiler.profile(
                    _dataset(raw), StructuredTable.model_validate(raw)
                )
                self.assertEqual(profile.orientation, expected)

    def test_string_quality_signals_and_repeated_headers_are_reported(self) -> None:
        raw = _raw_table(
            "messy-table",
            columns=[
                {"key": "category", "label": "Category", "type": "string"},
                {"key": "value", "label": "Value", "type": "number"},
            ],
            rows=[
                {"category": "Category", "value": "Value"},
                {"category": "North", "value": 10},
                {"category": "North", "value": 10},
                {"category": "Total", "value": 20},
                {"category": "Note: unaudited", "value": None},
            ],
        )

        profile = DeterministicDatasetProfiler().profile(
            _dataset(raw), StructuredTable.model_validate(raw)
        )

        self.assertEqual(profile.duplicate_row_count, 1)
        self.assertEqual(profile.repeated_header_row_count, 1)
        self.assertEqual(profile.total_or_subtotal_row_count, 1)
        self.assertEqual(profile.footnote_like_row_count, 1)
        self.assertIn(
            ProfileQualityWarning.REPEATED_HEADER_ROWS,
            profile.quality_warnings,
        )
        self.assertIn(
            ProfileQualityWarning.MIXED_COLUMN_TYPES,
            profile.quality_warnings,
        )

    def test_empty_dataset_produces_a_serializable_unsuitable_profile(self) -> None:
        raw = _raw_table(
            "empty-table",
            columns=[{"key": "value", "label": "Value", "type": "number"}],
            rows=[],
        )

        profile = DeterministicDatasetProfiler().profile(
            _dataset(raw), StructuredTable.model_validate(raw)
        )

        self.assertTrue(profile.is_empty)
        self.assertFalse(profile.suitable_for_analysis)
        self.assertEqual(profile.quality_score, 0)
        self.assertEqual(profile.columns[0].inferred_type, ProfiledDataType.EMPTY)
        self.assertNotIn("rows", profile.model_dump())
        self.assertTrue(profile.model_dump_json())


class _DatasetRepository:
    def __init__(self, tables: tuple[dict[str, Any], ...]) -> None:
        self.tables = tables
        self.calls = 0

    async def load_tables(self, **_kwargs: Any) -> tuple[dict[str, Any], ...]:
        self.calls += 1
        return self.tables


class _MemoryProfileCache:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.load_calls = 0
        self.save_calls = 0

    async def load_many(
        self, *, user_id: str, cache_keys: tuple[str, ...]
    ) -> dict[str, Any]:
        self.load_calls += 1
        return {key: self.values[key] for key in cache_keys if key in self.values}

    async def save_many(self, *, user_id: str, profiles: list[Any]) -> None:
        self.save_calls += 1
        for profile in profiles:
            key = profile_cache_key(
                dataset_id=profile.dataset_id,
                source_version=profile.source_version,
                profiler_version=profile.profiler_version,
            )
            self.values[key] = profile


class _UnavailableProfileCache:
    async def load_many(self, **_kwargs: Any) -> dict[str, Any]:
        raise ProfileCacheError("cache read unavailable")

    async def save_many(self, **_kwargs: Any) -> None:
        raise ProfileCacheError("cache write unavailable")


class _ConcurrencyProfiler(DeterministicDatasetProfiler):
    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self._lock = threading.Lock()

    def profile(self, dataset, table):
        with self._lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        try:
            time.sleep(0.03)
            return super().profile(dataset, table)
        finally:
            with self._lock:
                self.active -= 1


class DatasetProfilingRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_dataset_reuses_cache_after_version_check(self) -> None:
        raw = _raw_table(
            "cached-table",
            columns=[{"key": "value", "label": "Value", "type": "number"}],
            rows=[{"value": 10}, {"value": 20}],
        )
        dataset = _dataset(raw)
        repository = _DatasetRepository((raw,))
        cache = _MemoryProfileCache()
        runner = DatasetProfilingRunner(
            dataset_repository=repository,
            profile_cache=cache,
        )

        first = await runner.run(
            user_id="user-1",
            document_ids=[DOCUMENT_ID],
            evidence=_evidence(dataset),
        )
        second = await runner.run(
            user_id="user-1",
            document_ids=[DOCUMENT_ID],
            evidence=_evidence(dataset),
        )

        self.assertEqual(first.artifact.generated_count, 1)
        self.assertEqual(first.artifact.cache_hit_count, 0)
        self.assertEqual(second.artifact.generated_count, 0)
        self.assertEqual(second.artifact.cache_hit_count, 1)
        self.assertEqual(repository.calls, 2)
        self.assertEqual(first.artifact.profiles, second.artifact.profiles)

    async def test_cache_hit_does_not_hide_a_post_hydration_change(self) -> None:
        raw = _raw_table(
            "changed-after-hydration",
            columns=[{"key": "value", "label": "Value", "type": "number"}],
            rows=[{"value": 10}],
        )
        dataset = _dataset(raw)
        repository = _DatasetRepository((raw,))
        runner = DatasetProfilingRunner(
            dataset_repository=repository,
            profile_cache=_MemoryProfileCache(),
        )
        first = await runner.run(
            user_id="user-1",
            document_ids=[DOCUMENT_ID],
            evidence=_evidence(dataset),
        )
        repository.tables = ({**raw, "rows": [{"value": 999}]},)

        second = await runner.run(
            user_id="user-1",
            document_ids=[DOCUMENT_ID],
            evidence=_evidence(dataset),
        )

        self.assertEqual(first.artifact.status, "complete")
        self.assertEqual(second.artifact.status, "failed")
        self.assertEqual(second.artifact.cache_hit_count, 0)
        self.assertEqual(
            second.artifact.failures[0].reason,
            ProfileFailureReason.SOURCE_VERSION_MISMATCH,
        )

    async def test_changed_dataset_fails_without_blocking_valid_dataset(self) -> None:
        first_raw = _raw_table(
            "valid-table",
            columns=[{"key": "value", "label": "Value", "type": "number"}],
            rows=[{"value": 10}],
        )
        changed_original = _raw_table(
            "changed-table",
            columns=[{"key": "value", "label": "Value", "type": "number"}],
            rows=[{"value": 20}],
        )
        changed_materialized = {
            **changed_original,
            "rows": [{"value": 999}],
        }
        runner = DatasetProfilingRunner(
            dataset_repository=_DatasetRepository(
                (first_raw, changed_materialized)
            ),
            profile_cache=_MemoryProfileCache(),
        )

        result = await runner.run(
            user_id="user-1",
            document_ids=[DOCUMENT_ID],
            evidence=_evidence(_dataset(first_raw), _dataset(changed_original)),
        )

        self.assertEqual(result.artifact.status, "partial")
        self.assertEqual(result.artifact.profiled_count, 1)
        self.assertEqual(len(result.artifact.failures), 1)
        self.assertEqual(
            result.artifact.failures[0].reason,
            ProfileFailureReason.SOURCE_VERSION_MISMATCH,
        )
        self.assertIn(
            IssueCode.DATASET_VERSION_MISMATCH,
            [warning.code for warning in result.warnings],
        )

    async def test_malformed_dataset_does_not_block_valid_dataset(self) -> None:
        valid_raw = _raw_table(
            "valid-source",
            columns=[{"key": "value", "label": "Value", "type": "number"}],
            rows=[{"value": 10}],
        )
        malformed_original = _raw_table(
            "malformed-source",
            columns=[{"key": "value", "label": "Value", "type": "number"}],
            rows=[{"value": 20}],
        )
        malformed_materialized = dict(malformed_original)
        malformed_materialized.pop("columns")
        runner = DatasetProfilingRunner(
            dataset_repository=_DatasetRepository(
                (valid_raw, malformed_materialized)
            ),
            profile_cache=_MemoryProfileCache(),
        )

        result = await runner.run(
            user_id="user-1",
            document_ids=[DOCUMENT_ID],
            evidence=_evidence(
                _dataset(valid_raw),
                _dataset(malformed_original),
            ),
        )

        self.assertEqual(result.artifact.status, "partial")
        self.assertEqual(result.artifact.profiled_count, 1)
        self.assertEqual(
            result.artifact.failures[0].reason,
            ProfileFailureReason.INVALID_TABLE,
        )

    async def test_cpu_profiling_respects_bounded_concurrency(self) -> None:
        raw_tables = tuple(
            _raw_table(
                f"table-{index}",
                columns=[
                    {"key": "value", "label": "Value", "type": "number"}
                ],
                rows=[{"value": index}],
            )
            for index in range(4)
        )
        profiler = _ConcurrencyProfiler()
        runner = DatasetProfilingRunner(
            dataset_repository=_DatasetRepository(raw_tables),
            profile_cache=_MemoryProfileCache(),
            profiler=profiler,
            max_concurrency=2,
        )

        result = await runner.run(
            user_id="user-1",
            document_ids=[DOCUMENT_ID],
            evidence=_evidence(*(_dataset(raw) for raw in raw_tables)),
        )

        self.assertEqual(result.artifact.status, "complete")
        self.assertEqual(result.artifact.profiler_version, DATASET_PROFILER_VERSION)
        self.assertEqual(profiler.maximum_active, 2)

    async def test_cache_failure_does_not_block_profile_generation(self) -> None:
        raw = _raw_table(
            "uncached-table",
            columns=[{"key": "value", "label": "Value", "type": "number"}],
            rows=[{"value": 10}],
        )
        runner = DatasetProfilingRunner(
            dataset_repository=_DatasetRepository((raw,)),
            profile_cache=_UnavailableProfileCache(),
        )

        with patch(
            "scripts.data_analysis_agent.analysis.services.profiling.runner."
            "logger.exception"
        ):
            result = await runner.run(
                user_id="user-1",
                document_ids=[DOCUMENT_ID],
                evidence=_evidence(_dataset(raw)),
            )

        self.assertEqual(result.artifact.status, "complete")
        self.assertEqual(result.artifact.generated_count, 1)
        self.assertEqual(
            [warning.code for warning in result.warnings],
            [
                IssueCode.PROFILE_CACHE_READ_FAILED,
                IssueCode.PROFILE_CACHE_WRITE_FAILED,
            ],
        )


class _Cursor:
    def __init__(self, values: list[dict[str, Any]]) -> None:
        self.values = values

    async def to_list(self, *, length: int) -> list[dict[str, Any]]:
        return self.values[:length]


class _Collection:
    def __init__(self, values: list[dict[str, Any]]) -> None:
        self.values = values
        self.find_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.bulk_calls: list[tuple[list[Any], bool]] = []

    def find(
        self,
        query: dict[str, Any],
        projection: dict[str, Any],
    ) -> _Cursor:
        self.find_calls.append((query, projection))
        return _Cursor(self.values)

    async def bulk_write(self, operations: list[Any], *, ordered: bool) -> None:
        self.bulk_calls.append((operations, ordered))


class MongoProfilingRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_dataset_materialization_is_tenant_and_document_scoped(self) -> None:
        raw = _raw_table(
            "source-table",
            columns=[{"key": "value", "label": "Value", "type": "number"}],
            rows=[{"value": 10}],
        )
        collection = _Collection([raw])
        database = type("Database", (), {"structured_tables": collection})()

        with patch(
            "scripts.data_analysis_agent.analysis.repositories.datasets.get_db",
            return_value=database,
        ):
            tables = await MongoDatasetRepository().load_tables(
                user_id="user-1",
                document_ids=[DOCUMENT_ID],
                table_ids=["source-table"],
            )

        query = collection.find_calls[0][0]
        self.assertEqual(query["user_id"], "user-1")
        self.assertEqual(query["document_id"], {"$in": [DOCUMENT_ID]})
        self.assertEqual(query["table_id"], {"$in": ["source-table"]})
        self.assertEqual(len(tables), 1)

    async def test_profile_cache_reads_and_bulk_upserts_versioned_profiles(
        self,
    ) -> None:
        raw = _raw_table(
            "cache-source",
            columns=[{"key": "value", "label": "Value", "type": "number"}],
            rows=[{"value": 10}],
        )
        table = StructuredTable.model_validate(raw)
        dataset = _dataset(raw)
        profile = DeterministicDatasetProfiler().profile(dataset, table)
        cache_key = profile_cache_key(
            dataset_id=profile.dataset_id,
            source_version=profile.source_version,
            profiler_version=profile.profiler_version,
        )
        collection = _Collection(
            [{"cache_key": cache_key, "profile": profile.model_dump(mode="json")}]
        )
        database = type("Database", (), {"dataset_profiles": collection})()
        cache = MongoProfileCache()

        with patch(
            "scripts.data_analysis_agent.analysis.repositories.profile_cache.get_db",
            return_value=database,
        ):
            loaded = await cache.load_many(
                user_id="user-1",
                cache_keys=[cache_key],
            )
            await cache.save_many(user_id="user-1", profiles=[profile])

        self.assertEqual(loaded[cache_key], profile)
        self.assertEqual(collection.find_calls[0][0]["user_id"], "user-1")
        self.assertEqual(len(collection.bulk_calls[0][0]), 1)
        self.assertFalse(collection.bulk_calls[0][1])


if __name__ == "__main__":
    unittest.main()

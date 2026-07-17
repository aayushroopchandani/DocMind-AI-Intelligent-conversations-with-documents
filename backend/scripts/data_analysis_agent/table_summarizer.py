from __future__ import annotations

import asyncio
import json
import os
from functools import lru_cache
from typing import Any, Protocol, Sequence

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from db.models.structured_table import StructuredTable


TABLE_SUMMARY_SYSTEM_PROMPT = """You create semantic-search metadata for one table.
Write a retrieval-friendly 1-2 sentence summary that states what the table measures,
compares, or catalogs and its important scope. Preserve names, periods, and units.
Return 4-10 specific keywords. Do not invent facts or discuss PDF extraction."""


class TableSummaryOutput(BaseModel):
    short_summary: str = Field(..., min_length=1, max_length=700)
    keywords: list[str] = Field(..., min_length=1, max_length=15)


class _AsyncSummarizer(Protocol):
    async def ainvoke(self, input: Any, **kwargs: Any) -> Any: ...


@lru_cache(maxsize=1)
def get_table_summary_llm() -> _AsyncSummarizer:
    model = os.getenv(
        "DATA_ANALYSIS_TABLE_SUMMARY_MODEL", "google/gemini-2.5-flash-lite"
    )
    llm = ChatOpenAI(
        model=model,
        base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        temperature=0,
        max_retries=2,
        max_tokens=int(os.getenv("DATA_ANALYSIS_TABLE_SUMMARY_MAX_TOKENS", "500")),
        timeout=float(os.getenv("DATA_ANALYSIS_TABLE_SUMMARY_TIMEOUT", "45")),
    )
    return llm.with_structured_output(TableSummaryOutput)


def _representative_rows(
    rows: Sequence[dict[str, Any]], *, max_rows: int = 30
) -> list[dict[str, Any]]:
    if len(rows) <= max_rows:
        return list(rows)
    head_size = max_rows * 2 // 3
    return list(rows[:head_size]) + list(rows[-(max_rows - head_size) :])


def _table_prompt(table: StructuredTable) -> str:
    preview = json.dumps(
        _representative_rows(table.rows),
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    max_preview_chars = int(os.getenv("DATA_ANALYSIS_TABLE_PREVIEW_CHARS", "12000"))
    if len(preview) > max_preview_chars:
        preview = preview[:max_preview_chars].rsplit(",", 1)[0] + "]"
    columns = [
        {
            "key": column.key,
            "label": column.label,
            "type": column.type,
            "unit": column.unit,
        }
        for column in table.columns
    ]
    return (
        f"Title: {table.title}\n"
        f"Pages: {table.page_start}-{table.page_end}\n"
        f"Row count: {len(table.rows)}\n"
        f"Columns: {json.dumps(columns, ensure_ascii=False, separators=(',', ':'))}\n"
        f"Representative rows: {preview}"
    )


def _clean_keywords(values: Sequence[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        keyword = " ".join(str(value).split()).strip(" ,;")
        canonical = keyword.casefold()
        if keyword and canonical not in seen:
            seen.add(canonical)
            output.append(keyword)
    return output[:15]


def compose_table_summary(
    *, short_summary: str, keywords: Sequence[str], deterministic_summary: str
) -> str:
    keyword_text = ", ".join(_clean_keywords(keywords))
    return "\n".join(
        part
        for part in (
            " ".join(short_summary.split()),
            f"Keywords: {keyword_text}" if keyword_text else "",
            deterministic_summary.strip(),
        )
        if part
    )


async def summarize_tables(
    tables: Sequence[StructuredTable],
    *,
    summarizer: _AsyncSummarizer | None = None,
    max_concurrency: int | None = None,
) -> list[StructuredTable]:
    """Generate all LLM summaries concurrently with a bounded request fan-out."""
    if not tables:
        return []
    model = summarizer or get_table_summary_llm()
    concurrency = max_concurrency or int(
        os.getenv("DATA_ANALYSIS_TABLE_SUMMARY_CONCURRENCY", "8")
    )
    semaphore = asyncio.Semaphore(max(1, concurrency))
    attempts = max(
        1, int(os.getenv("DATA_ANALYSIS_TABLE_SUMMARY_ATTEMPTS", "3"))
    )

    async def summarize_one(table: StructuredTable) -> StructuredTable:
        parsed: TableSummaryOutput | None = None
        for attempt in range(attempts):
            try:
                async with semaphore:
                    response = await model.ainvoke(
                        [
                            ("system", TABLE_SUMMARY_SYSTEM_PROMPT),
                            ("human", _table_prompt(table)),
                        ]
                    )
                parsed = (
                    response
                    if isinstance(response, TableSummaryOutput)
                    else TableSummaryOutput.model_validate(response)
                )
                break
            except Exception:
                if attempt + 1 >= attempts:
                    raise
                await asyncio.sleep(0.25 * (2**attempt))

        assert parsed is not None
        table.short_summary = " ".join(parsed.short_summary.split())
        table.keywords = _clean_keywords(parsed.keywords)
        table.summary = compose_table_summary(
            short_summary=table.short_summary,
            keywords=table.keywords,
            deterministic_summary=table.deterministic_summary,
        )
        return table

    return list(await asyncio.gather(*(summarize_one(table) for table in tables)))

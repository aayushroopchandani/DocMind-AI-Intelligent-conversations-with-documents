# AI Services

DocMind’s intelligence layer is a set of cooperating services behind a single streaming chat endpoint, plus table / analysis APIs. Together they power the **research agent**, **data analysis agent**, **cross-document reasoning**, and **quantitative data analysis**.

This document explains **what each service does**, **inputs/outputs**, and **where it lives in the repo**.

Nothing here invents capabilities — every section maps to code under `backend/scripts/`, `backend/utils/`, or `backend/apis/`.

---

## Service Map

```text
                    ┌─────────────────────┐
                    │   Intent Detector   │
                    └──────────┬──────────┘
     ┌─────────────┬───────────┼───────────┬─────────────┐
     ▼             ▼           ▼           ▼             ▼
┌──────────┐ ┌──────────┐ ┌────────┐ ┌────────────────────┐
│ Research │ │Summarize │ │  Quiz  │ │ Data Analysis Agent│
│ Agent/RAG│ │ (outline)│ │pipelines│ │ tables + hybrid   │
└────┬─────┘ └────┬─────┘ └───┬────┘ └─────────┬──────────┘
     │            │           │                │
     └────────────┴─────┬─────┴────────────────┘
                        ▼
        Embedding · Retrieval · Prompts · Memory · Streaming
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
   OpenRouter      Qdrant           MongoDB
   (Gemini)   (chunks/nodes/     (memory/quiz/
               table summaries)   structured tables)
```

---

## 1. Embedding Service

| | |
| --- | --- |
| **Purpose** | Produce dense vectors for semantic search |
| **Library** | `langchain_openai.OpenAIEmbeddings` |
| **Models** | `text-embedding-3-small` |
| **Chunk vectors** | 1536 dimensions |
| **Node vectors** | 512 dimensions (`dimensions=512`) |
| **Code** | `backend/utils/embeddings.py` |

**Flow**

1. Ingestion creates text chunks (and outline nodes).
2. `get_chunk_embedding()` / `get_node_embedding()` embed content.
3. `qdrant_manager` upserts vectors with metadata payloads used later as filters.

---

## 2. Retrieval Service

| | |
| --- | --- |
| **Purpose** | Select the minimum useful grounded context for research answers and **cross-document reasoning** |
| **Retriever** | LangChain `MultiQueryRetriever` over Qdrant |
| **Filters** | `metadata.user_id` + `metadata.doc_id ∈ selected docs` |
| **Post-process** | Deduplicate → balance across docs → enforce final chunk / token budgets |
| **Code** | `backend/scripts/chat_with_pdf.py`, `backend/utils/format_document.py` |

**Tunables** (`config/settings.py`): `RETRIEVAL_CANDIDATES_PER_DOC`, `RETRIEVAL_FINAL_CHUNKS`, `RETRIEVAL_MAX_PER_DOC`, `RETRIEVAL_MAX_CONTEXT_TOKENS`.

---

## 3. Research Agent (Chat / Answer Service)

| | |
| --- | --- |
| **Purpose** | Stream a grounded Markdown investigation with inline citations across one or more PDFs |
| **Main LLM** | `google/gemini-2.5-flash` via OpenRouter (streaming) |
| **Utility LLM** | `google/gemini-2.5-flash-lite` (rewrite, summary, metadata) |
| **Entry point** | `ask_question()` async generator |
| **Code** | `backend/scripts/chat_with_pdf.py` |

**Cross-document behavior:** retrieval balances chunks across selected PDFs; prompts instruct the model to compare / surface conflicts when sources disagree.

**Outputs (SSE)**

- `status` — progress for the UI
- `token` — answer deltas
- `citations` — markers → filename / page / excerpt
- `final` — `DocMindResponse` (status, confidence, follow-ups, contributions)
- `done` / `error`

**Grounding rules** are encoded in `backend/utils/prompts.py`: answer only from context; cite with `[Cn]`; refuse inventing sources.

---

## 4. Intent Detection

| | |
| --- | --- |
| **Purpose** | Route a free-form message to the correct pipeline |
| **Intents** | `general_qa`, `summarization`, `quiz` |
| **Method** | Strong regex heuristics + structured LLM classification |
| **Quiz extras** | scope, formats, difficulty, count, mode, target |
| **Code** | `backend/scripts/intent_detection/` |

Wired in `apis/chats.py` before any pipeline runs. The client also receives an `intent` SSE event for observability / UI affordances.

---

## 5. Summarization Service

| | |
| --- | --- |
| **Purpose** | Produce faithful section / topic / document summaries |
| **Strategy** | Outline node resolve → scope budget → representative chunks → map / hierarchical reduce |
| **Retrieval aids** | Exact + hybrid node search; chunk fetch by `node_id` |
| **Representative selection** | Clustering + centroid pick + MMR fill (`representative_selector.py`) |
| **Index** | Background `summary_index` build per document |
| **Code** | `backend/scripts/intention_pipelines/summarization_pipeline/` |

Key modules:

| File | Role |
| --- | --- |
| `level1_pdf_with_outline.py` | Main streaming summarizer |
| `scope_budget.py` | Section-aware grouping + budgets |
| `representative_selector.py` | MMR / cluster representatives |
| `summary_index.py` | Persist precomputed representatives |
| `utils/getting_outline_for_l1.py` | PDF TOC → node tree |

---

## 6. Quiz Services

| | |
| --- | --- |
| **Purpose** | Generate citation-linked assessment items from documents / chat context |
| **Live scopes** | `context_based`, `topic_based` |
| **Planned scopes** | `structure_based`, `whole_document` (schema-ready) |
| **Formats** | single MCQ, multi-correct MCQ, T/F, fill blank, match |
| **Modes** | practice, rapid_fire, exam_mode |
| **Code** | `backend/scripts/intention_pipelines/quiz_pipeline/` |

**Context-based** resolves “quiz me on what we just discussed” using recent messages, rolling memory, node metadata, and citations.

**Topic-based** retrieves topic-relevant chunks then generates questions via structured LLM output.

Quizzes are persisted via `GeneratedQuiz` models and surfaced to the frontend as a `quiz` SSE event.

---

## 7. Memory

| | |
| --- | --- |
| **Purpose** | Preserve conversational continuity without unbounded history |
| **Recent window** | Last `MEMORY_RECENT_MESSAGES` (default 6) sent verbatim |
| **Rolling summary** | Compact older turns; refresh every `MEMORY_SUMMARY_EVERY` |
| **Storage** | `chat.memory.summary` + `summarized_count` in MongoDB |
| **Code** | `update_chat_summary()` in `chat_with_pdf.py` |

Query rewriting uses both summary and recent dialogue so follow-ups retrieve the right passages **without** changing the user’s original phrasing for the final answer prompt.

---

## 8. Prompt Templates

Centralized in `backend/utils/prompts.py`:

| Prompt | Use |
| --- | --- |
| System + human answer | Grounded streaming QA |
| Rewrite system/human | Standalone retrieval query |
| Summary system/human | Rolling memory compression |
| Metadata system/human | Post-answer structured enrichment |

Prompts explicitly forbid following instructions found *inside* PDFs and forbid inventing citation markers.

---

## 9. Streaming Service

| | |
| --- | --- |
| **Transport** | Server-Sent Events (`text/event-stream`) |
| **Backend** | FastAPI `StreamingResponse` |
| **Frontend path** | Next.js route re-streams to the browser |
| **Headers** | `Cache-Control: no-cache`, `X-Accel-Buffering: no` |

Cancellation-aware: if the client disconnects mid-summary, partial answers can still be shielded and persisted.

---

## 10. Data Analysis Agent & Tooling

**Shipped today**

- Structured **table extraction / validation / summarization / Qdrant indexing** (`scripts/data_analysis_agent/extraction/`)
- Optional **Docling** missed-table recovery in an isolated worker
- LangGraph **query-generation** and **hybrid text + table retrieval** subgraphs (`scripts/data_analysis_agent/reterival/`)
- Intent routing as a lightweight supervisor for research / summarize / quiz

**In progress / planned**

- Full **data analysis agent** orchestration with **LangGraph** (plan → execute → repair → visualize)
- Automatic **charts & dashboards** from analytical findings
- Broader tool calling across Excel/SQL and external data sources
- Stronger **cross-document** compare / timeline synthesis modes

These are documented honestly in the root README and [data-analysis-agent.md](./data-analysis-agent.md) so the public contract stays clear about shipped vs planned capabilities.

---

## Model Matrix

| Role | Model | Provider path |
| --- | --- | --- |
| Streaming answers (research agent) | Gemini 2.5 Flash | OpenRouter |
| Utilities (rewrite, intent helpers, lite tasks) | Gemini 2.5 Flash-Lite | OpenRouter |
| Summarization (configurable) | `SUMMARY_MODEL` / `SUMMARY_UTILITY_MODEL` | OpenRouter |
| Table discovery summaries | `DATA_ANALYSIS_TABLE_SUMMARY_MODEL` (default Flash-Lite) | OpenRouter |
| Embeddings | `text-embedding-3-small` | OpenAI |

---

## Related

- [architecture.md](./architecture.md)
- [data-analysis-agent.md](./data-analysis-agent.md)
- [rag-pipeline.md](./rag-pipeline.md)
- [ingestion-pipeline.md](./ingestion-pipeline.md)
- SVG: [svg/ai-services.svg](./svg/ai-services.svg)

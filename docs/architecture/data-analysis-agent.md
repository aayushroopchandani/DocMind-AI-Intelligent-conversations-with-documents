# Data Analysis Agent & Cross-Document Research

DocMind’s analytical surface is organized around four ideas:

| Pillar | Role |
| --- | --- |
| **Research agent** | Narrative investigation over PDF text (rewrite → retrieve → cite → compose) |
| **Data analysis agent** | LangGraph workflows over structured tables + hybrid text/table retrieval |
| **Cross-document reasoning** | Multi-PDF evidence balancing, compare / conflict, broad retrieval scopes |
| **Data analysis** | Extract → validate → index tables → profile / compute → charts & insights |

This document maps those pillars to code under `backend/scripts/data_analysis_agent/` and the shared RAG stack. Nothing here invents capabilities — shipped vs in-progress is called out explicitly.

For the interactive map see [architecture.html](./architecture.html). Parent overview: [architecture.md](./architecture.md).

---

## Package Layout

```text
backend/scripts/data_analysis_agent/
├── extraction/                 # PDF → structured tables
│   ├── pipeline.py
│   ├── run_ingestion.py        # Sample / CLI ingestion
│   ├── docling_fallback.py
│   ├── docling_worker.py       # Isolated Docling process
│   └── utils/
│       ├── table_extractor.py
│       ├── table_validator.py
│       ├── table_coverage_detector.py
│       ├── table_summarizer.py
│       └── table_vector_store.py
└── reterival/                  # LangGraph retrieval subgraphs
    ├── query_generation.py
    ├── query_generation_subgraph.py
    ├── hybrid_retrieval_subgraph.py
    ├── text_retrieval.py
    ├── table_retrieval.py
    ├── state.py
    └── utils/                  # Hybrid search, sparse index, limits
```

---

## Data Analysis — table pipeline (shipped)

1. **Extract** tables with PyMuPDF into normalized `structured_tables` (typed columns, units, page provenance).
2. **Validate** schema / quality / consistency.
3. **Summarize** each table with a small LLM (keywords + discovery text).
4. **Index** summaries in Qdrant (`structured_tables` collection, 1536-d) for semantic dataset discovery.
5. **Coverage detect** → optional **Docling fallback** on doubtful page ranges (isolated venv / worker).
6. Persist raw table payloads in **MongoDB**; only discovery summaries live in Qdrant.

CLI entry: `python -m scripts.data_analysis_agent.extraction.run_ingestion` (see root README).

---

## Data Analysis Agent — retrieval (shipped subgraphs)

LangGraph subgraphs under `reterival/` prepare evidence for analysis:

### Query generation

- Classifies `retrieval_scope`: `normal` (focused metric / period / entity) vs `broad` (many docs, periods, categories — **cross-document** evidence).
- Emits 2–3 queries each for:
  - **shared** (good for both indexes)
  - **text** (narrative explanations, trends, causes)
  - **table** (metric names, schemas, units, periods)

### Hybrid retrieval

- Searches **PDF text chunks** and **table summaries** in parallel.
- State is checkpoint-friendly (`DataAnalysisRetrievalState`: `user_id`, `chat_id`, `query`, `document_ids`, query lists, retrieved chunks / tables).
- Thread config keys LangGraph on the Mongo `chat_id` with metadata `agent: "data_analysis"`.

---

## Data Analysis Agent — full orchestration (in progress)

Target control flow (also rendered in the root README):

```text
Intent → Scope → Discover datasets → Profile
       → enough data? ──no──→ HITL clarification
       → Plan → Validate plan → Execute analysis subgraph
       → results valid? ──no──→ Repair → re-execute
       → Visualization + Insight subgraphs → Compose response
```

Planned execution engines: profiler, cleaning, transformation, statistics, anomaly, time-series — with schema / result / unit / citation validators before presentation (insight generator, visualization planner, dashboard builder).

---

## Research Agent & Cross-Document Reasoning

The live research path is the streaming RAG pipeline (`backend/scripts/chat_with_pdf.py`):

| Concern | Mechanism |
| --- | --- |
| Multi-doc workspace | Up to `MAX_PDFS_PER_CHAT` (default 4) attachments |
| Tenant isolation | Qdrant filters on `user_id` + selected `doc_id`s |
| Fair evidence | Deduplicate → balance per document → token budget |
| Cross-doc synthesis | Prompt rules: compare / conflict when sources disagree |
| Citations | `[Cn]` → filename, page, excerpt → viewer jump |
| Memory | Rolling summary + recent verbatim turns |

Outline-aware summarization and quiz pipelines reuse the same chunk / node indexes so “learn from these docs” stays on the same evidence graph as research.

Details: [rag-pipeline.md](./rag-pipeline.md).

---

## How the pillars connect

```text
                    ┌─────────────────────────┐
                    │     Intent / Router     │
                    └───────────┬─────────────┘
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
   Research Agent        Data Analysis Agent     Summarize / Quiz
   (text RAG)            (tables + hybrid)
          │                     │
          └──────────┬──────────┘
                     ▼
           Cross-document filters
           (user_id · doc_ids · balance)
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
     Qdrant       MongoDB      Cloudinary
  chunks/nodes/  tables/chats    PDFs
  table summaries
```

---

## Related

- Root README diagrams: **Data Analysis Agent — system view** and **execution flow**
- [ai-services.md](./ai-services.md)
- [ingestion-pipeline.md](./ingestion-pipeline.md) (text/node ingest; table ingest is additional)
- SVG map: [svg/system-architecture.svg](./svg/system-architecture.svg)

# DocMind AI

**Not just chat-with-PDF.** DocMind is a document intelligence workspace for research agents, data-analysis agents, cross-document reasoning, charts/dashboards, and quantitative analysis — grounded in your PDFs, tables, and citations.

[![Next.js](https://img.shields.io/badge/Next.js-16-black?logo=next.js)](https://nextjs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-RAG-1C3C3C?logo=langchain)](https://www.langchain.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-Agents-1C3C3C)](https://www.langchain.com/langgraph)
[![Qdrant](https://img.shields.io/badge/Qdrant-Vector%20DB-DC244C)](https://qdrant.tech/)
[![MongoDB](https://img.shields.io/badge/MongoDB-Atlas%20%2F%20Local-47A248?logo=mongodb)](https://www.mongodb.com/)
[![Clerk](https://img.shields.io/badge/Auth-Clerk-6C47FF?logo=clerk)](https://clerk.com/)
[![Architecture](https://img.shields.io/badge/Architecture-Interactive%20Map-22d3ee)](https://aayushroopchandani.github.io/DocMind-AI-Intelligent-conversations-with-documents/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](#license)

<p align="center">
  <a href="#what-docmind-does">What DocMind Does</a> ·
  <a href="#features">Features</a> ·
  <a href="#tech-stack">Tech Stack</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="https://aayushroopchandani.github.io/DocMind-AI-Intelligent-conversations-with-documents/">Interactive Map</a> ·
  <a href="#ai-services">AI Services</a> ·
  <a href="#installation">Installation</a>
</p>

---

## What DocMind Does

Upload reports, filings, and papers, then work across four tightly connected capabilities:

| Pillar | What it means in DocMind |
| --- | --- |
| **Research agent** | Multi-step investigation over narrative text — rewrite, expand, retrieve, compare, and compose citation-backed findings |
| **Data analysis agent** | LangGraph workflows over extracted tables — discover datasets, profile, plan, execute, validate, and surface quantitative insights |
| **Cross-document reasoning** | Ask across up to 4 PDFs at once; balance evidence per doc; detect agreement, gaps, and conflicts with page-level citations |
| **Data analysis** | Structured table extraction → typed columns / units → semantic table index → stats, anomalies, time series, charts & dashboards |

Supporting surfaces (outline-aware **summarization**, **quizzes**, PDF viewer) sit on the same ingestion + retrieval stack so learning and review stay grounded in the same evidence.

> **Interactive architecture:** [Open the animated system map →](https://aayushroopchandani.github.io/DocMind-AI-Intelligent-conversations-with-documents/)  
> Deep-dive docs: [`docs/architecture/`](docs/architecture/) · agent deep-dive: [`docs/architecture/data-analysis-agent.md`](docs/architecture/data-analysis-agent.md)

---

### Data Analysis Agent — end-to-end architecture

The diagram follows the implemented pipeline from source ingestion through the
durable runtime and LangGraph evidence-preparation graph. Solid arrows are the
main execution path; dashed arrows are asynchronous, optional, or observability
flows.

```mermaid
flowchart TB
    classDef client fill:#0f172a,stroke:#38bdf8,color:#f8fafc,stroke-width:2px
    classDef api fill:#172554,stroke:#60a5fa,color:#eff6ff
    classDef process fill:#132e2a,stroke:#34d399,color:#ecfdf5
    classDef decision fill:#422006,stroke:#fbbf24,color:#fffbeb,stroke-width:2px
    classDef store fill:#2e1065,stroke:#c084fc,color:#faf5ff
    classDef terminal fill:#3f1d2e,stroke:#fb7185,color:#fff1f2
    classDef boundary fill:#1f2937,stroke:#f8fafc,color:#f8fafc,stroke-width:2px

    ANALYST([Analyst or API client]):::client

    subgraph INGEST["A · PDF ingestion and analytical source preparation"]
        direction TB
        PDF[PDF upload]:::client --> ID[SHA-256 identity<br/>tenant ownership and pending-document claim]:::process
        ID --> CLOUDPDF[(Cloudinary<br/>private PDF)]:::store

        ID --> TEXTINGEST[PyMuPDF text ingest<br/>2400-token chunks · 300 overlap<br/>outline and node metadata]:::process
        TEXTINGEST --> EMBED[OpenAI embeddings<br/>dense vectors plus sparse backfill]:::process
        EMBED --> QTEXT[(Qdrant PDF indexes<br/>dense and sparse chunks)]:::store
        TEXTINGEST --> MDOC[(MongoDB documents<br/>nodes · status · provenance)]:::store

        ID --> PRIMARY[PyMuPDF table extraction<br/>cells · columns · pages · node links]:::process
        PRIMARY --> TVAL[Schema and quality validation<br/>accepted · quarantined · rejected]:::process
        TVAL --> TSUM[LLM discovery summary<br/>keywords · schema · units]:::process
        TSUM --> MTABLE[(MongoDB structured_tables<br/>authoritative rows and metadata)]:::store
        TSUM --> QTABLE[(Qdrant structured_tables<br/>dense and sparse summaries)]:::store

        TVAL -. quarantined pages .-> COVER[Coverage detector<br/>flag suspicious page ranges]:::process
        COVER --> MISSED{Possible missed or<br/>complex tables?}:::decision
        MISSED -- no --> INGESTREADY[Table source ready]:::terminal
        MISSED -. yes .-> DOCLING[Isolated Docling worker<br/>bounded page ranges]:::process
        DOCLING --> DVALID[Validate recovered tables]:::process
        DVALID --> MERGE[Content-aware merge and dedupe<br/>summarize additions · vector upsert<br/>replace authoritative table set]:::process
        MERGE --> MTABLE
        MERGE --> QTABLE
        MERGE --> INGESTREADY
    end

    subgraph CONTROL["B · Durable run control plane"]
        direction TB
        ANALYST -->|POST /analysis/runs<br/>Idempotency-Key| API[FastAPI analysis API<br/>auth · tenant scope · request limits]:::api
        API --> RUNSVC[AnalysisRunService<br/>validate request · fingerprint inputs]:::process

        RUNSVC --> PDFCTX[PDF context<br/>selected immutable document IDs]:::process
        MDOC --> PDFCTX

        RUNSVC -->|spreadsheet context| WBCTX[WorkbookContextService<br/>validate snapshot or uploaded version<br/>split selected range into tables]:::process
        WBCTX --> ARTIFACT[ArtifactVersionService<br/>validate · hash · immutable versions]:::process
        ARTIFACT --> BLOBS[(Cloudinary artifact blobs<br/>JSON · CSV · XLSX · snapshots)]:::store
        WBCTX --> CATALOG[(MongoDB dataset_catalog<br/>versioned dataset handles)]:::store

        PDFCTX --> RUNSTATE[(MongoDB analysis_runs<br/>state · version · deadline · lease)]:::store
        CATALOG -->|pinned dataset versions| RUNSTATE
        RUNSVC --> RUNSTATE
        RUNSTATE -->|inputs_ready| WORKER[DurableAnalysisWorker<br/>poll · claim · renew lease · fencing<br/>retry expired or abandoned work]:::process
        WORKER --> ADAPTER[Phase7AnalysisAdapter<br/>cancellation checks · token accounting<br/>stream LangGraph state values]:::process
    end

    subgraph GRAPH["C · LangGraph evidence-preparation graph · isolated by run_id"]
        direction TB
        START((START)):::boundary

        START --> RETRIEVE[Retrieve evidence]:::process
        START --> REQ[Extract analysis requirements<br/>operation · metrics · entities · periods<br/>units · document scope · table need]:::process

        subgraph HYBRID["Hybrid retrieval child graph"]
            direction TB
            RETRIEVE --> QGEN[Query generation<br/>normal or broad scope<br/>shared · text · table queries<br/>relevance signals and table intent]:::process
            QGEN --> RTEXT[PDF text search<br/>tenant plus document filters<br/>dense and sparse retrieval]:::process
            QGEN --> RTABLE[Table-summary search<br/>tenant plus document filters<br/>dense and sparse retrieval]:::process
            QTEXT --> RTEXT
            QTABLE --> RTABLE
            RTEXT --> FUSION[Reciprocal-rank fusion<br/>score · dedupe · diversify · trim]:::process
            RTABLE --> FUSION
        end

        FUSION --> HYDRATE[Hydrate authoritative evidence<br/>resolve table IDs and pinned handles<br/>verify source versions and provenance]:::process
        MTABLE --> HYDRATE
        CATALOG --> HYDRATE
        BLOBS --> HYDRATE
        HYDRATE --> PROFILE[Deterministic dataset profiling<br/>shape · types · semantic roles · units<br/>quality · duplicates · headers · footnotes]:::process

        REQ --> JOIN[Parallel-branch barrier]:::boundary
        PROFILE --> JOIN
        JOIN --> ASSESS[Evidence assessment<br/>deterministic requirement matching<br/>coverage · conflicts · ambiguity resolver]:::process
        ASSESS --> READY{Readiness decision}:::decision

        READY -- ready --> PREPARE
        READY -- clarification required<br/>or unanswerable --> STOP[Terminal evidence outcome<br/>clarification or unanswerable]:::terminal
        READY -- rescue · text extraction<br/>or retrieval repair --> RESCUE[1 · Rescue unused table candidates<br/>hydrate · profile · reassess]:::process

        subgraph COMPLETE["Bounded evidence-completion cascade"]
            direction TB
            RESCUE --> C1{Ready now?}:::decision
            C1 -- no --> TEXTRACT[2 · Extract validated facts<br/>from already-retrieved text<br/>and build derived datasets]:::process
            TEXTRACT --> C2{Ready now?}:::decision
            C2 -- no --> REPAIR[3 · Targeted hybrid repair<br/>only for unmet requirements]:::process
            REPAIR --> REASSESS[Hydrate new tables · profile<br/>extract new text facts · reassess]:::process
            REASSESS --> MORE{Ready, terminal, or<br/>repair attempts remain?}:::decision
            MORE -- retry within bound --> REPAIR
        end

        C1 -- yes --> PREPARE[Select final evidence<br/>build versioned cleaning recipes]:::process
        C2 -- yes --> PREPARE
        MORE -- ready --> PREPARE
        MORE -- clarification<br/>or unanswerable --> STOP

        PREPARE --> NORMALIZE[Deterministic normalization<br/>remove exact duplicates and repeated headers<br/>separate footnotes · parse numbers and periods<br/>reshape when justified · preserve row lineage]:::process
        NORMALIZE --> NCACHE[(MongoDB normalized_datasets<br/>rows · lineage · exclusions · footnotes<br/>source versions and recipe cache keys)]:::store
        NCACHE --> PREPARED[DATASETS_PREPARED<br/>normalized IDs · selected facts<br/>derived dataset IDs · issues]:::boundary

        REQCACHE[(MongoDB phase caches<br/>queries · requirements · profiles<br/>assessment · completion · repair)]:::store
        REQ <--> REQCACHE
        PROFILE <--> REQCACHE
        ASSESS <--> REQCACHE
        RESCUE <--> REQCACHE
    end

    ADAPTER --> START

    subgraph OBSERVE["D · Progress, recovery, and delivery"]
        direction LR
        PROJECT[Milestone projector<br/>small idempotent progress events]:::process
        EVENTS[(MongoDB append-only events<br/>monotonic sequence · replay cursor)]:::store
        SSE[GET /analysis/runs/:id/events<br/>replayable SSE · Last-Event-ID<br/>heartbeats and connection limits]:::api
        RESULT[Run state machine<br/>succeeded · waiting · failed<br/>cancelled · expired]:::process
    end

    ADAPTER -. graph milestones .-> PROJECT
    PROJECT --> EVENTS
    PREPARED --> RESULT
    STOP --> RESULT
    WORKER -. lease recovery and cancellation .-> RESULT
    RESULT --> RUNSTATE
    RESULT --> EVENTS
    EVENTS --> SSE --> ANALYST

    PREPARED -. current implementation boundary .-> LATER[Later phases<br/>typed plan · approval and apply<br/>workbook mutation · charts and narrative]:::terminal
```

> **Current boundary:** the durable runtime returns normalized dataset IDs,
> validated facts / derived dataset references, warnings, errors, token usage,
> and timings. Typed plans, workbook edits, calculations, charts, and narrative
> composition are later phases and are not inferred from `DATASETS_PREPARED`.

### Data Analysis Agent — execution flow

```mermaid
flowchart TD
    S([START]) --> INTENT[Classify Analysis Intent]
    INTENT --> SCOPE[Resolve Scope]
    SCOPE --> DISCOVER[Discover Datasets]
    DISCOVER --> PROFILE[Profile Datasets]

    PROFILE --> CHECK{Enough valid data?}
    CHECK -- No --> HITL[Request Clarification]
    HITL --> DISCOVER

    CHECK -- Yes --> PLAN[Create Structured Plan]
    PLAN --> VALIDATE_PLAN[Validate Plan]

    VALIDATE_PLAN --> EXEC[Execute Analysis Subgraph]
    EXEC --> RESULT_CHECK{Results valid?}

    RESULT_CHECK -- No --> REPAIR[Repair Plan]
    REPAIR --> EXEC

    RESULT_CHECK -- Yes --> VIS[Visualization Subgraph]
    RESULT_CHECK -- Yes --> INSIGHT[Insight Subgraph]

    VIS --> COMPOSE[Compose Response]
    INSIGHT --> COMPOSE
    COMPOSE --> E([END])
```

---

## Features

### Research Agent

| Capability | Description |
| --- | --- |
| **Grounded investigation** | Streaming, citation-backed answers from selected PDFs — not free-form hallucination |
| **Query rewriting** | Follow-ups (“does it apply to interns?”) become standalone retrieval queries |
| **Multi-query expansion** | LangChain `MultiQueryRetriever` improves recall across narrative sections |
| **Outline-aware summarization** | TOC/node tree, hybrid node search, representative chunks, hierarchical map-reduce |
| **Conversation memory** | Rolling chat summary + recent verbatim messages for multi-turn research |
| **Structured enrichment** | Confidence, answer status, follow-ups, and per-document contributions |

### Data Analysis Agent

| Capability | Description |
| --- | --- |
| **Structured table extraction** | Pull tables from PDFs (PyMuPDF) into normalized `structured_tables` with typed columns, units, and page provenance |
| **Docling fallback recovery** | Coverage detection + isolated Docling worker recovers missed / complex tables |
| **Table validation** | Schema, quality, and consistency checks before indexing |
| **Table semantic index** | LLM summaries + keywords embedded into Qdrant for dataset discovery |
| **Hybrid retrieval subgraph** | LangGraph query generation over **text chunks + table summaries** (`normal` vs `broad` scope) |
| **Analysis orchestration** | Plan → profile → clean/transform → statistics / anomaly / time-series → validate → repair |
| **Charts & dashboards** | Visualization planner + dashboard builder turn findings into graphs and auto-composed views |
| **Grounded insights** | Quantitative results stay tied to source pages / table fragments for citation |

### Cross-Document Reasoning

| Capability | Description |
| --- | --- |
| **Multi-document workspace** | Up to 4 PDFs in one workspace with per-user, per-document Qdrant filters |
| **Context balancing** | Deduplicate chunks, cap per-document contribution, enforce token budget |
| **Compare & conflict** | Prompt contract asks the model to surface agreement, gaps, and conflicts across sources |
| **Broad retrieval scope** | Analysis retrieval can classify requests as `broad` when evidence must span many docs / periods / metrics |
| **Citation jump** | Inline `[C1]` markers → filename + page + excerpt → open the page in the viewer |

### Data Analysis (quantitative layer)

| Capability | Description |
| --- | --- |
| **Dataset catalogue** | Normalized tables in MongoDB + discovery summaries in Qdrant |
| **Profiling & cleaning** | Schema / quality checks before compute |
| **Statistics · anomalies · time series** | Analysis engines behind the agent execution subgraph |
| **Insight + visualization** | Insight generator, chart planner, dashboard composer |
| **Sample ingestion** | Bundled annual-report PDFs + `run_ingestion` for end-to-end table pipelines |

### Intent routing & learning

| Capability | Description |
| --- | --- |
| **Intent routing** | Detects `general_qa`, `summarization`, or `quiz` and dispatches specialized pipelines |
| **Semantic search** | OpenAI `text-embedding-3-small` embeddings in Qdrant |
| **Streaming responses** | SSE: `status` → `token` → `citations` → `final` → `done` |
| **Quiz modes** | Practice (guided), rapid-fire (timed bursts), exam (proctored focus monitoring) |
| **Quiz formats** | Single MCQ, multi-correct, T/F, fill-blank, match — easy / medium / hard |

### Document intelligence & platform

- PDF upload (PDF-only, max 4 per chat); Cloudinary private storage
- PyMuPDF parsing + chunking; outline / TOC → hierarchical **node tree**
- Dual Qdrant collections: **chunks** + **nodes** (+ **structured table** summaries)
- Background summary-index build (clustering + MMR); SHA-256 document identity
- Clerk auth, MongoDB workspaces, Next.js BFF (browser never holds backend secrets)
- Split-screen PDF viewer + chat; citation click → page jump; dark-mode UI

---

## Tech Stack

| Layer | Technology | Purpose |
| --- | --- | --- |
| Frontend | Next.js 16, React 19, TypeScript | App router UI, BFF API routes, streaming client |
| UI | Tailwind CSS 4, shadcn/ui, GSAP, react-pdf | Design system, motion, in-browser PDF viewing |
| Auth | Clerk | Session management, protected `/chat` routes |
| Backend API | FastAPI, Uvicorn, Pydantic | REST + SSE endpoints |
| Orchestration | LangChain + LangGraph | Research RAG pipelines + data-analysis agent subgraphs |
| LLMs | OpenRouter → Gemini 2.5 Flash / Flash-Lite | Answers, utilities, intent, quizzes, summaries, table metadata |
| Embeddings | OpenAI `text-embedding-3-small` | Chunk (1536-d), node (512-d), and table-summary vectors |
| Vector DB | Qdrant (embedded path or remote) | Semantic retrieval, node search, table discovery, cross-doc filters |
| Document DB | MongoDB (Motor async) | Users, chats, documents, quizzes, memory, structured tables |
| Object storage | Cloudinary | Private PDF hosting |
| PDF parsing | PyMuPDF + Docling (fallback) | Text, outline tree, table extraction / recovery |
| ML helpers | NumPy, scikit-learn | Clustering / MMR for summary representatives |

---

## Architecture

<p align="center">
  <a href="https://aayushroopchandani.github.io/DocMind-AI-Intelligent-conversations-with-documents/">
    <strong>Launch interactive architecture map →</strong>
  </a>
</p>

Glowing nodes, animated flow lines, zoom/pan, and clickable service details live on the hosted map. Static diagrams for the README are below; written deep-dives are in [`docs/architecture/`](docs/architecture/) (including the [data analysis / research agent guide](docs/architecture/data-analysis-agent.md)).

### System overview

<p align="center">
  <img src="assets/architecture/system-architecture.svg" alt="DocMind system architecture" width="900" />
</p>

<details>
<summary>Mermaid version</summary>

```mermaid
flowchart TB
  User([User]) --> Next[Next.js Frontend + BFF]
  Next --> FastAPI[FastAPI Backend]
  FastAPI --> Router{Intent Router}
  Router --> Research[Research Agent / RAG]
  Router --> Sum[Summarization]
  Router --> Quiz[Quiz Pipelines]
  Router --> Analysis[Data Analysis Agent]
  Research --> CrossDoc[Cross-Doc Reasoning]
  Research --> AI[AI Services]
  Sum --> AI
  Quiz --> AI
  Analysis --> Tables[Structured Tables]
  Analysis --> Charts[Charts · Dashboards · Insights]
  CrossDoc --> AI
  AI --> OR[OpenRouter / Gemini]
  AI --> Qdrant[(Qdrant)]
  AI --> Mongo[(MongoDB)]
  Tables --> Qdrant
  Tables --> Mongo
  FastAPI --> Cloudinary[(Cloudinary)]
```

</details>

### AI services map

<p align="center">
  <img src="assets/architecture/ai-services.svg" alt="DocMind AI services" width="820" />
</p>

### Document ingestion

<p align="center">
  <img src="assets/architecture/ingestion-pipeline.svg" alt="Document ingestion pipeline" width="900" />
</p>

<details>
<summary>Mermaid version</summary>

```mermaid
flowchart LR
  A[Upload PDF] --> B[Cloudinary]
  B --> C[PyMuPDF Loader]
  C --> D[Outline → Node Tree]
  C --> E[Chunking]
  E --> F[Embeddings]
  F --> G[(Qdrant Chunks)]
  D --> H[(MongoDB Nodes)]
  D --> I[Node Embeddings]
  I --> J[(Qdrant Nodes)]
  G --> K[Summary Index<br/>Clustering + MMR]
```

</details>

### RAG pipeline

<p align="center">
  <img src="assets/architecture/rag-pipeline.svg" alt="RAG pipeline" width="320" />
</p>

<details>
<summary>Mermaid version</summary>

```mermaid
flowchart TB
  Q[User Query] --> R[Query Rewrite]
  R --> MQ[MultiQueryRetriever]
  MQ --> E[Embed + Qdrant Search]
  E --> F[Metadata Filters<br/>user_id + doc_ids]
  F --> D[Dedupe + Balance]
  D --> C[Citation Context Builder]
  C --> L[Streaming LLM]
  L --> S[SSE Tokens]
  S --> M[Structured Metadata]
  M --> P[Persist + Memory Update]
```

</details>

### Intent / agent workflow

<p align="center">
  <img src="assets/architecture/agent-workflow.svg" alt="Intent and agent workflow" width="720" />
</p>

<details>
<summary>Mermaid version</summary>

```mermaid
flowchart TB
  U[User Message] --> D[Intent Detector]
  D --> G[Research Agent / RAG]
  D --> S[Summarization]
  D --> Q[Quiz]
  D --> A[Data Analysis Agent]
  Q --> CB[Context-based Quiz]
  Q --> TB[Topic-based Quiz]
  Q --> XB[Structure / Whole-doc<br/>planned]
  A --> HY[Hybrid Text + Table Retrieval]
  A --> EX[Plan · Execute · Visualize]
  G --> XD[Cross-Doc Balance + Citations]
  G --> T[Stream Response]
  S --> T
  CB --> T
  TB --> T
  HY --> T
  EX --> T
  XD --> T
```

</details>

---

## AI Services

<details>
<summary><strong>Embedding Service</strong></summary>

- **Purpose:** Convert PDF chunks and outline nodes into vectors for Qdrant.
- **Models:** `text-embedding-3-small` (chunks 1536-d, nodes 512-d).
- **Input:** Chunk / node text.
- **Output:** Dense vectors + payload metadata (`user_id`, `doc_id`, `node_id`, pages).
- **Location:** `backend/utils/embeddings.py`, `backend/qdrant_manager.py`.

</details>

<details>
<summary><strong>Retrieval Service</strong></summary>

- **Purpose:** Fetch grounded context for research Q&A and cross-document reasoning.
- **Flow:** Rewrite query → MultiQuery expansion → filtered Qdrant search → dedupe → per-doc balancing → token budget.
- **Filters:** Always scoped to the authenticated `user_id` and selected `doc_id`s.
- **Location:** `backend/scripts/chat_with_pdf.py`, `backend/utils/format_document.py`.

</details>

<details>
<summary><strong>Research Agent (Chat / RAG)</strong></summary>

- **Purpose:** Stream grounded investigative answers with citations across one or more PDFs.
- **LLM:** Gemini 2.5 Flash via OpenRouter (streaming).
- **Cross-doc:** Balance evidence per document; surface agreement / conflict when sources diverge.
- **Events:** `status`, `token`, `citations`, `final`, `error`, `done`.
- **Location:** `backend/scripts/chat_with_pdf.py` → `ask_question()`.

</details>

<details>
<summary><strong>Data Analysis Agent</strong></summary>

- **Purpose:** Quantitative workflows over extracted tables + hybrid narrative/table retrieval.
- **Shipped today:** Table extraction, Docling fallback, validation, semantic table index, LangGraph retrieval subgraphs (`query_generation`, `hybrid_retrieval`).
- **In progress:** Full plan → execute → repair → visualize orchestration (see system / execution diagrams above).
- **Location:** `backend/scripts/data_analysis_agent/`.
- **Deep dive:** [`docs/architecture/data-analysis-agent.md`](docs/architecture/data-analysis-agent.md).

</details>

<details>
<summary><strong>Intent Detection</strong></summary>

- **Purpose:** Route each message to the right pipeline.
- **Intents:** `general_qa` | `summarization` | `quiz` (analysis agent routes expand as the agent lands in the stream API).
- **Method:** Regex heuristics + LLM structured classification.
- **Location:** `backend/scripts/intent_detection/`.

</details>

<details>
<summary><strong>Summarization Pipeline</strong></summary>

- **Purpose:** Outline-aware, budgeted summaries for chapters/sections/topics.
- **Highlights:** Hybrid node search, scope budgets, representative selection (clustering + MMR), hierarchical map-reduce, parallel LLM calls.
- **Location:** `backend/scripts/intention_pipelines/summarization_pipeline/`.

</details>

<details>
<summary><strong>Quiz Pipelines</strong></summary>

- **Purpose:** Generate citation-linked quizzes from conversation context or topics.
- **Scopes live today:** `context_based`, `topic_based`.
- **Modes:** practice, rapid_fire, exam_mode.
- **Location:** `backend/scripts/intention_pipelines/quiz_pipeline/`.

</details>

<details>
<summary><strong>Memory</strong></summary>

- **Purpose:** Keep long chats coherent without blowing the context window.
- **Design:** Last *N* messages verbatim + rolling summary refreshed every *M* new messages.
- **Tunables:** `MEMORY_RECENT_MESSAGES`, `MEMORY_SUMMARY_EVERY`.
- **Storage:** `chat.memory` in MongoDB.

</details>

<details>
<summary><strong>Prompt Templates</strong></summary>

- Answer generation (system + human) with strict grounding rules
- Standalone query rewrite
- Rolling conversation summary
- Response metadata enrichment
- **Location:** `backend/utils/prompts.py`

</details>

<details>
<summary><strong>Streaming</strong></summary>

- FastAPI `StreamingResponse` with `text/event-stream`
- Next.js route proxies SSE to the browser
- UI renders progressive Markdown + citation cards

</details>

---

## Folder Structure

```text
DocMind-AI-Intelligent-conversations-with-documents/
├── README.md
├── SETUP_CLOUDINARY_MONGODB.md
├── assets/
│   └── architecture/                  # SVG diagrams embedded in README
├── docs/
│   ├── index.html                     # GitHub Pages — interactive map
│   └── architecture/                  # Deep-dive docs + architecture.html + agent guide
├── backend/
│   ├── main.py                        # FastAPI app + CORS + routers
│   ├── requirements.txt
│   ├── qdrant_manager.py              # Qdrant clients + vector stores
│   ├── apis/
│   │   ├── chats.py                   # Chats, PDF upload, SSE stream
│   │   ├── documents.py               # Node / summary-index status
│   │   ├── users.py                   # Clerk → Mongo sync
│   │   └── deps.py                    # Auth headers + internal secret
│   ├── config/settings.py             # Env-backed settings
│   ├── db/
│   │   ├── mongodb.py
│   │   ├── crud.py
│   │   └── models/                    # User, Chat, Document, Quiz
│   ├── scripts/
│   │   ├── ingest.py                  # PDF → chunks → Qdrant
│   │   ├── chat_with_pdf.py           # RAG ask_question pipeline
│   │   ├── intent_detection/          # Intent router
│   │   ├── data_analysis_agent/       # Tables · hybrid retrieval · analysis agent
│   │   └── intention_pipelines/
│   │       ├── summarization_pipeline/
│   │       └── quiz_pipeline/
│   ├── services/cloudinary_setup.py
│   ├── utils/                         # Embeddings, prompts, schemas
│   └── tests/
└── frontend/
    └── my-app/
        ├── app/                       # Next.js App Router
        │   ├── (auth)/                # Sign-in / sign-up
        │   ├── chat/                  # Workspace
        │   ├── quiz/                  # Practice / rapid-fire / exam
        │   └── api/                   # BFF proxies to FastAPI
        ├── components/
        │   ├── chat/                  # Workspace, viewer, streaming UI
        │   ├── quiz/                  # Quiz experiences
        │   ├── home/                  # Marketing landing
        │   └── ui/                    # shadcn primitives
        ├── lib/                       # API client, types, quiz helpers
        └── proxy.ts                   # Clerk middleware (Next 16)
```

---

## Installation

### Prerequisites

- Python **3.11+**
- Node.js **20+**
- MongoDB (local or Atlas)
- Cloudinary account
- OpenAI API key (embeddings)
- OpenRouter API key (LLMs)
- Clerk application (auth)
- Optional: Docker for remote Qdrant (`docker run -p 6333:6333 qdrant/qdrant`)

### 1. Clone

```bash
git clone https://github.com/aayushroopchandani/DocMind-AI-Intelligent-conversations-with-documents.git
cd DocMind-AI-Intelligent-conversations-with-documents
```

### 2. Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Frontend

```bash
cd frontend/my-app
npm install
```

---

## Environment Variables

### Backend (`backend/.env`)

| Variable | Description | Required |
| --- | --- | --- |
| `MONGODB_URI` | MongoDB connection string | Yes |
| `MONGODB_DB_NAME` | Database name (default `docmind`) | Yes |
| `CLOUDINARY_CLOUD_NAME` | Cloudinary cloud name | Yes |
| `CLOUDINARY_API_KEY` | Cloudinary API key | Yes |
| `CLOUDINARY_API_SECRET` | Cloudinary API secret | Yes |
| `OPENAI_API_KEY` | Embeddings (`text-embedding-3-small`) | Yes |
| `OPENROUTER_API_KEY` | LLM access via OpenRouter | Yes |
| `QDRANT_COLLECTION_NAME` | Chunk vector collection | Yes |
| `QDRANT_COLLECTION_NAME_NODES` | Node vector collection | Yes |
| `QDRANT_PATH` | Embedded Qdrant storage path (or use URL/HOST) | No* |
| `QDRANT_URL` / `QDRANT_HOST` / `QDRANT_PORT` / `QDRANT_API_KEY` | Remote Qdrant | No* |
| `MAX_PDFS_PER_CHAT` | Upload cap (default `4`) | No |
| `INTERNAL_API_SECRET` | Shared secret with Next.js BFF | Recommended |
| `MEMORY_RECENT_MESSAGES` | Verbatim memory window (default `6`) | No |
| `MEMORY_SUMMARY_EVERY` | Summary refresh cadence (default `6`) | No |
| `RETRIEVAL_CANDIDATES_PER_DOC` | Candidates before balancing | No |
| `RETRIEVAL_FINAL_CHUNKS` | Final context chunk count | No |
| `RETRIEVAL_MAX_PER_DOC` | Max chunks per PDF | No |
| `RETRIEVAL_MAX_CONTEXT_TOKENS` | Context token budget | No |
| `SUMMARY_*` | Summarization budget / parallelism knobs | No |
| `DATA_ANALYSIS_TABLE_SUMMARY_MODEL` | Small table-summary model (default `google/gemini-2.5-flash-lite`) | No |
| `DATA_ANALYSIS_TABLE_SUMMARY_CONCURRENCY` | Parallel table-summary calls (default `8`) | No |
| `DATA_ANALYSIS_TABLE_SUMMARY_ATTEMPTS` | Per-table structured-output attempts (default `3`) | No |
| `DATA_ANALYSIS_DOCLING_ENABLED` | Run conditional missed-table fallback (default `true`) | No |
| `DATA_ANALYSIS_DOCLING_PYTHON` | Dedicated Python executable containing Docling | When enabled |
| `DATA_ANALYSIS_DOCLING_TABLE_MODE` | `accurate` (default) or `fast` | No |
| `DATA_ANALYSIS_DOCLING_THREADS` | CPU threads used by the isolated worker (default `4`) | No |
| `DATA_ANALYSIS_DOCLING_DEVICE` | Worker inference device (default `cpu`) | No |
| `DATA_ANALYSIS_DOCLING_PAGE_PADDING` | Context pages around flagged runs (default `1`) | No |
| `DATA_ANALYSIS_DOCLING_MAX_PAGES_PER_JOB` | Maximum pages in one fallback range (default `12`) | No |
| `DATA_ANALYSIS_TEXT_EVIDENCE_MODEL` | Phase 6 structured fact extractor (default `google/gemini-2.5-flash-lite`) | No |
| `DATA_ANALYSIS_TEXT_EVIDENCE_MAX_TOKENS` / `TIMEOUT` / `ATTEMPTS` | Extractor response, timeout, and malformed-output retry bounds | No |
| `DATA_ANALYSIS_TEXT_EVIDENCE_CONCURRENCY` | Parallel document extraction calls, capped at `6` (default `3`) | No |
| `DATA_ANALYSIS_TEXT_EVIDENCE_CHUNKS_PER_DOCUMENT` | Retrieved chunks sent per document, capped at `8` (default `4`) | No |
| `DATA_ANALYSIS_TEXT_EVIDENCE_MAX_CHARS_PER_DOCUMENT` | Extraction prompt text budget, capped at `40000` (default `16000`) | No |
| `DATA_ANALYSIS_TEXT_EVIDENCE_SUCCESS_TTL_DAYS` | Positive extraction-cache TTL (default `30`) | No |
| `DATA_ANALYSIS_TEXT_EVIDENCE_NEGATIVE_TTL_DAYS` | Absent/rejected extraction-cache TTL (default `1`) | No |
| `DATA_ANALYSIS_REPAIR_ATTEMPTS` | Targeted hybrid-retrieval attempts, capped at `2` (default `2`) | No |
| `LANGSMITH_TRACING` | Set to `true` to trace LangGraph and LLM runs | Recommended |
| `LANGSMITH_API_KEY` | LangSmith API key used to upload traces | When tracing |
| `LANGSMITH_PROJECT` | LangSmith project name, e.g. `docmind-data-analysis` | When tracing |

\* Provide either `QDRANT_PATH` (default embedded) **or** remote URL/host settings.

Data-analysis traces use the run name `data_analysis_agent`. Their metadata includes
retrieval counts and fallback status, hydration failures, profile cache hit ratio,
requirements operation/cache/fallback diagnostics, readiness decision, coverage
counts, match-method counts, ambiguity-LLM usage, completion attempts, rescued
datasets, validated text facts, derived datasets, cache hits, and remaining
requirements.
LangSmith captures node/LLM latency and token usage automatically.

### Frontend (`frontend/my-app/.env`)

| Variable | Description | Required |
| --- | --- | --- |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | Clerk publishable key | Yes |
| `CLERK_SECRET_KEY` | Clerk secret key | Yes |
| `NEXT_PUBLIC_CLERK_SIGN_IN_URL` | Sign-in path | Yes |
| `NEXT_PUBLIC_CLERK_SIGN_UP_URL` | Sign-up path | Yes |
| `NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL` | Post sign-in redirect | Yes |
| `NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL` | Post sign-up redirect | Yes |
| `BACKEND_URL` | FastAPI base URL (e.g. `http://localhost:8000`) | Yes |
| `INTERNAL_API_SECRET` | Must match backend when set | Recommended |

> See also [`SETUP_CLOUDINARY_MONGODB.md`](SETUP_CLOUDINARY_MONGODB.md) for a detailed verification walkthrough.

---

## Running Locally

**Terminal 1 — Backend**

```bash
cd backend
source .venv/bin/activate
uvicorn main:app --reload --reload-exclude "$PWD/.venv" --port 8000
```

**Terminal 2 — Frontend**

```bash
cd frontend/my-app
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) → sign in → create a chat → upload PDFs → ask.

API docs (when backend is up): [http://localhost:8000/docs](http://localhost:8000/docs)

### Ingest the bundled data-analysis sample

With MongoDB, Cloudinary, OpenAI embeddings, OpenRouter, and Qdrant configured
in `backend/.env`:

```bash
cd backend
source .venv/bin/activate
python -m scripts.data_analysis_agent.extraction.run_ingestion
```

Install the optional Docling fallback in a separate environment to keep its
PyTorch/model dependencies out of the FastAPI process:

```bash
cd backend
python3.11 -m venv .docling-venv
.docling-venv/bin/pip install -r requirements-docling.txt
export DATA_ANALYSIS_DOCLING_PYTHON="$PWD/.docling-venv/bin/python"
```

The runner uploads the sample PDF to Cloudinary, records its SHA-256 document
in MongoDB, writes 2400/300 text chunks to `QDRANT_COLLECTION_NAME`, stores
normalized tables in MongoDB's `structured_tables` collection, and writes only
their 1536-dimensional discovery summaries to Qdrant's `structured_tables`
collection. Pass a different PDF path or `--user-id` when needed.
After that primary ingestion is ready, a coordinate-based coverage detector
runs in the background. Only doubtful page ranges are sent to the one-at-a-time
Docling subprocess; OCR, image classification/description, chart extraction,
code/formula enrichment, image generation, plugins, and remote services are
disabled. Recovered unique tables use the same MongoDB, summary, embedding, and
Qdrant paths as PyMuPDF tables.

---

## API Overview

All FastAPI routes are intended to be called by the **Next.js BFF**, not the browser directly. Requests carry:

- `X-User-Id` — Clerk user id (verified by Next.js)
- `X-Internal-Secret` — shared secret (when configured)

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `POST` | `/users/sync` | Upsert Clerk user into MongoDB |
| `POST` | `/chats` | Create a new chat |
| `GET` | `/chats/{chat_id}` | Fetch chat + conversation |
| `GET` | `/chats/{user_id}/chats` | List chats for a user |
| `POST` | `/chats/{chat_id}/pdfs` | Upload PDFs (multipart) → Cloudinary + ingest |
| `DELETE` | `/chats/{chat_id}/pdfs/{document_db_id}` | Detach PDF; delete vectors if unused |
| `GET` | `/chats/{chat_id}/documents` | List documents for a chat |
| `POST` | `/chats/{chat_id}/stream` | SSE: intent → RAG / summary / quiz |
| `GET` | `/documents/{document_id}/nodes` | Outline nodes + summary-index status |
| `GET` | `/documents/{document_id}/nodes/status` | Node ingestion readiness |
| `GET` | `/tables?document_id={sha256}` | Paginated normalized tables for a document |
| `GET` | `/tables/{table_id}` | Fetch one normalized table with source positions |

### SSE event types (`/chats/{chat_id}/stream`)

| Event | Meaning |
| --- | --- |
| `status` | Progress message (“Detecting intent”, “Searching…”) |
| `intent` | Detected intent payload |
| `token` | Streamed answer text |
| `citations` | Citation list for the answer |
| `final` | Structured `DocMindResponse` |
| `quiz` | Generated quiz payload |
| `error` | Recoverable / fatal pipeline error |
| `done` | Stream complete |

---

## Future Improvements

Actively being built on top of the shipped table + retrieval layer (`scripts/data_analysis_agent/`):

- **Full LangGraph data-analysis orchestration** — multi-step plan → execute → repair loops (system / execution diagrams above)
- **Research-agent tool loops** — broader multi-hop investigation across narrative + tabular evidence
- **Stronger cross-document reasoning** — explicit compare / conflict / timeline synthesis modes
- **Auto-generated charts & dashboards** — visualization planner + dashboard composer
- Deeper statistics / anomaly / time-series analysis engines
- Structure-based and whole-document quiz scopes (schemas already defined)
- Evaluation harness for retrieval + summarization + analysis faithfulness

Roadmap candidates:

- Excel / CSV analysis agent
- SQL / warehouse agent
- Voice conversations
- Knowledge-graph / GraphRAG overlays
- MCP tool surface for external agents
- Model routing by task cost/latency

---

## Contributing

Contributions are welcome.

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-change`
3. Keep changes focused; match existing patterns in `backend/` and `frontend/my-app/`
4. Add or update tests under `backend/tests/` when touching pipelines
5. Open a pull request with a clear summary and test plan

Please do not commit secrets (`.env` files). Use the env tables above as the contract.


## License

This project is available under the **MIT License** — free to use, modify, and distribute with attribution.

---

<p align="center">
  Built for researchers and analysts — <strong>research, data analysis, charts, and cross-document reasoning</strong> — not just chat-with-PDF.
  <br />
  <a href="https://aayushroopchandani.github.io/DocMind-AI-Intelligent-conversations-with-documents/">Open Interactive Architecture →</a>
</p>

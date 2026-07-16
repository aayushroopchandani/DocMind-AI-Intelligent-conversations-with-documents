# System Architecture

DocMind is a **document intelligence workspace**: a Next.js client talks to a FastAPI backend that routes each user message through intent-aware AI pipelines grounded in the user's uploaded PDFs.

This document describes the software architecture. For AI component details see [ai-services.md](./ai-services.md). For pipeline specifics see [rag-pipeline.md](./rag-pipeline.md) and [ingestion-pipeline.md](./ingestion-pipeline.md). For a visual map, open [architecture.html](./architecture.html).

---

## Design Principles

1. **Grounding first** — Answers come only from retrieved PDF context; missing evidence is stated explicitly.
2. **Intent-aware routing** — One chat surface, multiple specialized pipelines (QA, summarization, quiz).
3. **User isolation** — Vector and document queries always filter by authenticated `user_id`.
4. **BFF security** — The browser never holds Cloudinary, MongoDB, or OpenRouter credentials. Next.js verifies Clerk sessions and proxies with an internal secret.
5. **Streaming UX** — Long LLM work is exposed as SSE progress + tokens, not a single blocking response.
6. **Outline-aware documents** — PDFs with a TOC become a node tree used for summaries, quizzes, and scoped retrieval.

---

## High-Level Components

```text
┌──────────────────────────────────────────────────────────────────┐
│                         Browser (User)                           │
│   Landing · Auth (Clerk) · Chat Workspace · Quiz Experiences     │
└───────────────────────────────┬──────────────────────────────────┘
                                │ HTTPS
┌───────────────────────────────▼──────────────────────────────────┐
│              Next.js 16 (App Router + BFF)                       │
│  · Clerk middleware protects /chat                               │
│  · /api/* proxies to FastAPI with X-User-Id + X-Internal-Secret  │
│  · SSE re-stream to the client                                   │
└───────────────────────────────┬──────────────────────────────────┘
                                │ HTTP (localhost / private network)
┌───────────────────────────────▼──────────────────────────────────┐
│                         FastAPI                                  │
│  users · chats · documents · /chats/{id}/stream (SSE)            │
│                              │                                   │
│                    Intent Detector                               │
│              ┌───────────────┼───────────────┐                   │
│              ▼               ▼               ▼                   │
│         General QA     Summarization        Quiz                 │
│         (RAG)          (outline L1)     (context/topic)          │
└──────┬───────────┬───────────┬───────────┬───────────────────────┘
       │           │           │           │
       ▼           ▼           ▼           ▼
   OpenRouter   Qdrant     MongoDB    Cloudinary
   (Gemini)   (chunks+    (users,     (private
               nodes)      chats,      PDFs)
                           docs)
```

---

## Request Lifecycle (Chat Message)

1. User submits a question in the chat workspace.
2. Frontend `POST`s to Next.js `/api/chats/[chatId]/stream`.
3. Next.js verifies the Clerk session, injects `X-User-Id`, optionally `X-Internal-Secret`, and forwards to FastAPI.
4. FastAPI loads the chat + ready documents; rejects empty / unauthorized sets.
5. **Intent detection** classifies the message.
6. The matching pipeline runs and **yields SSE events**.
7. Assistant text (and structured metadata / quiz payloads) are persisted to MongoDB.
8. Rolling memory may refresh asynchronously after the exchange.

---

## Data Stores

| Store | What lives there | Why |
| --- | --- | --- |
| **MongoDB** | Users, chats, conversation + memory, PDF metadata, outline nodes, generated quizzes | Durable app state, ownership, history |
| **Qdrant (chunks)** | Embedded page chunks + payload filters | Semantic RAG retrieval |
| **Qdrant (nodes)** | Embedded outline titles / nodes | Section targeting for summaries & quizzes |
| **Cloudinary** | Raw PDF binaries (private) | Durable file hosting without bloating Mongo |

Document identity uses a **SHA-256 content hash** (`document_id`) so the same file can be referenced across chats without re-uploading blindly.

---

## Security Model

| Layer | Mechanism |
| --- | --- |
| Identity | Clerk session cookies |
| Route protection | `proxy.ts` — `/chat(.*)` requires auth |
| API boundary | Next.js BFF only; FastAPI CORS limited to the frontend origin |
| Service auth | Optional `INTERNAL_API_SECRET` on FastAPI |
| Tenant isolation | `user_id` on every chat/document/vector filter |
| Document ACL | Stream endpoint verifies `document_ids` belong to the chat |

---

## Frontend Architecture

| Area | Role |
| --- | --- |
| `app/chat` | Multi-PDF workspace: viewer + conversation |
| `app/quiz` | Practice, rapid-fire, exam UIs |
| `components/chat` | Streaming Markdown, citations, PDF tabs, uploader |
| `lib/api.ts` / route handlers | Typed clients + BFF proxies |
| `lib/quiz` | Grading, timers, proctoring hooks |

Desktop uses a **split layout** (PDF | chat). Narrow viewports switch to **Documents / Chat** tabs. Citation clicks navigate the viewer to the cited page.

---

## Backend Architecture

| Module | Responsibility |
| --- | --- |
| `apis/` | Thin HTTP adapters (validation, auth deps, SSE) |
| `scripts/chat_with_pdf.py` | General QA RAG pipeline |
| `scripts/intent_detection/` | Heuristic + LLM intent classification |
| `scripts/intention_pipelines/` | Summarization + quiz specialty pipelines |
| `scripts/ingest.py` | PDF download → parse → chunk → embed |
| `qdrant_manager.py` | Connection, collections, filtered vector stores |
| `db/` | Models + CRUD |
| `utils/` | Prompts, schemas, embedding factories, formatting |

Business logic stays in scripts/utils; routers stay thin — easier to test and reuse from SSE handlers.

---

## Scaling Notes (Current Shape)

- Qdrant can run **embedded** (`QDRANT_PATH`) for local/dev or **remote** (`QDRANT_URL` / host) for shared deployments.
- Summary indexing and quiz persistence are scheduled as **background asyncio tasks** after request-critical work.
- Retrieval and summarization expose env knobs for candidate counts, budgets, and parallelism without code changes.

---

## Related Diagrams

- SVG: [svg/system-architecture.svg](./svg/system-architecture.svg)
- Interactive HTML: [architecture.html](./architecture.html)

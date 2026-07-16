# RAG Pipeline

End-to-end path for **general Q&A** over one or more PDFs in a chat.

Primary implementation: `backend/scripts/chat_with_pdf.py` → `ask_question()`.
HTTP entry: `POST /chats/{chat_id}/stream` when intent is `general_qa`.

---

## Goals

- Answer **only** from selected documents
- Cite every important factual claim with `[Cn]`
- Stay conversational across follow-ups
- Stream tokens early for UX
- Isolate retrieval to the authenticated user

---

## Pipeline Diagram

```text
User question
      │
      ▼
Load chat context
  · selected document_ids
  · rolling memory.summary
  · recent verbatim messages
      │
      ▼
Rewrite standalone retrieval query
  (utility LLM — pronouns / follow-ups)
      │
      ▼
MultiQueryRetriever
  · expands query variants
  · Qdrant similarity search
  · Filter: user_id + doc_ids
      │
      ▼
Post-retrieval shaping
  · deduplicate near-identical chunks
  · balance across documents
  · cap total chunks / approx tokens
      │
      ▼
Format context with citation markers [C1]…
      │
      ▼
Stream main LLM answer
  · system grounding prompt
  · chat summary + recent dialogue
  · document_context blocks
      │
      ▼
Structured enrichment
  · keep only cited [Cn]
  · contributions per PDF
  · confidence / status / follow-ups
      │
      ▼
Persist messages + maybe refresh memory
      │
      ▼
SSE: done
```

---

## Step Details

### 1. Authorization & document selection

The stream handler loads the chat for `X-User-Id`. Only documents with `ingestion_status == "ready"` can be queried. Optional `document_ids` in the body must be a subset of the chat’s attachments.

### 2. Conversation conditioning

From MongoDB:

| Field | Role |
| --- | --- |
| `memory.summary` | Compact older history |
| last *N* messages | Verbatim dialogue for rewrite + answer |

*N* defaults to `MEMORY_RECENT_MESSAGES=6`.

### 3. Query rewriting

`rewrite_standalone_question()` converts follow-ups into a **retrieval-only** query. The user’s original wording remains the question the answer model responds to — so replies stay natural.

### 4. Multi-query retrieval

`create_multi_query_retriever()` wraps a Qdrant retriever:

```text
must=[
  user_id == authenticated user,
  doc_id in selected documents
]
```

Candidate count scales with the number of selected PDFs (`RETRIEVAL_CANDIDATES_PER_DOC`).

### 5. Context assembly

| Function | Effect |
| --- | --- |
| `deduplicate_documents` | Drop redundant chunks |
| `balance_documents` | Fairness across PDFs (`max_total`, `max_per_doc`) |
| `format_documents_with_citations` | Assign stable `[C1]…` markers + excerpts |

### 6. Streaming generation

Main model: Gemini 2.5 Flash (streaming) via OpenRouter.

Prompt contract (`utils/prompts.py`):

- No outside knowledge
- Cite with available markers only
- Compare / conflict across documents when needed
- Exact not-found phrase when evidence is missing

### 7. Structured final payload

After streaming:

1. Parse which `[Cn]` markers appear in the answer.
2. Build deterministic `Citation` and `DocumentContribution` objects from retrieval metadata.
3. Optionally call a structured utility LLM for confidence, status (`complete` / `partial` / `conflicting` / `not_found`), follow-up questions, and contribution wording.
4. Failures fall back to heuristics — the streamed answer is never discarded.

### 8. Persistence & memory

User + assistant messages (with meta) are written to the chat. `update_chat_summary()` may refresh the rolling summary when enough new messages accumulated.

---

## SSE Contract (QA path)

| Type | Payload highlights |
| --- | --- |
| `status` | Human-readable progress |
| `token` | Text chunk |
| `citations` | List of citation objects |
| `final` | Full `DocMindResponse` |
| `error` | Message for UI toast / banner |
| `done` | End of stream |

> Intent routing may emit an `intent` event before this pipeline starts; summarization and quiz paths use overlapping but specialized event sets (`quiz`, summary tokens, etc.).

---

## Failure Modes

| Case | Behavior |
| --- | --- |
| No ready PDFs | HTTP 400 before stream |
| Foreign `document_ids` | HTTP 403 |
| Empty retrieval | Model guided to the not-found phrase |
| Rewrite failure | Fall back to raw question |
| Metadata LLM failure | Heuristic structured response |
| Client disconnect | Cancel; partial summary persistence when applicable |

---

## Related Assets

- SVG: [svg/rag-pipeline.svg](./svg/rag-pipeline.svg)
- Parent: [architecture.md](./architecture.md)
- Sibling: [ingestion-pipeline.md](./ingestion-pipeline.md)

# Ingestion Pipeline

How a PDF becomes searchable, outline-aware, and ready for the **research agent**, **cross-document reasoning**, summarization, and quizzes.

Primary implementation: `backend/scripts/ingest.py` + summarization helpers under `intention_pipelines/summarization_pipeline/utils/`.
HTTP trigger: `POST /chats/{chat_id}/pdfs` in `backend/apis/chats.py`.

**Structured table ingestion** (data analysis layer) is a parallel path under `scripts/data_analysis_agent/extraction/` — see [data-analysis-agent.md](./data-analysis-agent.md).

---

## Goals

- Accept PDF-only uploads (max **4** per chat by default)
- Store durable binaries in **Cloudinary**
- Persist metadata + outline in **MongoDB**
- Index semantic chunks (and nodes) in **Qdrant**
- Prepare **summary indexes** (representative chunks) in the background
- Enable multi-document workspaces for cross-doc research

---

## Pipeline Diagram

```text
Client selects PDF(s)
        │
        ▼
Next.js BFF multipart proxy
        │
        ▼
FastAPI /chats/{id}/pdfs
  · auth + chat ownership
  · enforce MAX_PDFS_PER_CHAT
  · content hash → document_id
        │
        ▼
Upload to Cloudinary (raw / private)
        │
        ▼
MongoDB document record
  ingestion_status = not_ready → ready
        │
        ▼
ingest_pdf(secure_url, …)
  ┌─────────────────────────────┐
  │ Download PDF bytes          │
  │ PyMuPDFLoader → pages       │
  │ build_tree_from_pdf → nodes │
  │ Attach node_id per page     │
  │ RecursiveCharacterTextSplit │
  │   chunk_size=800            │
  │   overlap=100               │
  │ Embed + upsert Qdrant chunks│
  └─────────────────────────────┘
        │
        ├─► Persist nodes on document
        ├─► Embed / index nodes (nodes collection)
        └─► Schedule summary_index build
              clustering + MMR representatives
```

---

## Step Details

### 1. Upload boundary

- Frontend validates PDF type and count.
- Backend re-validates and rejects when the chat would exceed `MAX_PDFS_PER_CHAT`.
- Files are hashed (SHA-256) to produce a stable `document_id`.

### 2. Cloud storage

`services/cloudinary_setup.py` uploads the binary and returns `secure_url`, `public_id`, size, etc. MongoDB stores references — not the PDF bytes.

### 3. Text extraction

`PyMuPDFLoader` yields one LangChain `Document` per page with page metadata.

### 4. Outline → node tree

`build_tree_from_pdf()` derives hierarchical nodes (`node_id`, title, level, page_start/end, parent). Pages map to nodes via `find_node_id()`. Cover pages before the first TOC entry fall back to the first node when present so they remain indexable.

Nodes are stored on the document (`nodes` / `NodeData`) and also embedded into the **nodes** Qdrant collection for hybrid / title search during summarization.

### 5. Chunking

```python
RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=100,
    separators=["\n\n", "\n", ". ", " ", ""],
    add_start_index=True,
)
```

Each chunk receives:

| Metadata | Meaning |
| --- | --- |
| `source` | Filename |
| `doc_id` | Content hash |
| `user_id` | Owner |
| `page_number` | 1-based page |
| `node_id` | Outline section |
| `chunk_index` | Order within node |
| `document_chunk_index` | Global order in the PDF |

### 6. Vector upsert

`qdrant_manager.get_chunk_vector_store(embedding=get_chunk_embedding())` writes into `QDRANT_COLLECTION_NAME` with cosine distance and payload indexes on `metadata.user_id`, `metadata.doc_id`, and `metadata.node_id`.

### 7. Summary index (async)

After ingest, `_schedule_summary_index` builds per-node representatives:

- Scroll embedded chunks for the document
- Cluster embeddings (scikit-learn)
- Pick centroid-near points + MMR diversification
- Persist `summary_index` status (`pending` → `processing` → `ready` / `failed`)

This accelerates later budgeted summarization without re-scanning every chunk.

### 8. Deletion / detach

`DELETE /chats/{chat_id}/pdfs/{document_db_id}` detaches a PDF from a chat. If no chat references the document anymore, `delete_pdf_embeddings()` removes Qdrant vectors for that `user_id` + `document_id`.

---

## Collections & Sizes

| Collection env | Vector size | Content |
| --- | --- | --- |
| `QDRANT_COLLECTION_NAME` | 1536 | Page chunks |
| `QDRANT_COLLECTION_NAME_NODES` | 512 | Outline nodes |

Qdrant connection priority (`qdrant_manager.py`): `QDRANT_PATH` → `QDRANT_URL` → `QDRANT_HOST` → default local `backend/qdrant_storage`.

---

## Readiness Gates

Downstream chat refuses questions until at least one attached document is `ingestion_status == "ready"`.

Node-aware features (level-1 summarization) additionally consult node ingestion / summary-index status via `/documents/{id}/nodes` endpoints.

---

## Related Assets

- SVG: [svg/ingestion-pipeline.svg](./svg/ingestion-pipeline.svg)
- Parent: [architecture.md](./architecture.md)
- Sibling: [rag-pipeline.md](./rag-pipeline.md)
- Table / analysis ingest: [data-analysis-agent.md](./data-analysis-agent.md)

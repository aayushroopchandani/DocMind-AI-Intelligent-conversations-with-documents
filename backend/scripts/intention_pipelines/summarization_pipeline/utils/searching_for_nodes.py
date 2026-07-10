'''
    searching pipeline 

    exact normalised matching
                |
        fuzzy matching
                |
        hybrid search using embeddings 
                |
        llm to find the most relevant nodes        
'''
import math
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from qdrant_client.models import FieldCondition, Filter, MatchValue

from qdrant_manager import get_node_vector_store
from .ingest_nodes import ingest_nodes
from dotenv import load_dotenv
from utils.embeddings import get_node_embedding

load_dotenv()

SEMANTIC_WEIGHT = 0.7
KEYWORD_WEIGHT = 0.3
DEFAULT_TOP_K = 5

def normalize_title(text: str) -> str:
    text = text.lower().strip()

    # Remove words that users may or may not mention
    text = re.sub(
        r"\b("
        r"chapter|section|subsection|part|unit|module|lesson|topic|"
        r"heading|subheading|division|segment|"
        r"appendix|annex|annexure|attachment|"
        r"introduction|overview|summary|conclusion|"
        r"preface|foreword|prologue|epilogue|"
        r"contents|table of contents|index|"
        r"abstract|background|methodology|methods|"
        r"results|findings|discussion|recommendations|"
        r"references|bibliography|glossary|"
        r"case study|example|exercise|"
        r"volume|book|report"
        r")\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )

    # Remove numbering such as 3, 3.2, 4.1.2
    text = re.sub(r"\b\d+(?:\.\d+)*\b", " ", text)

    # Remove punctuation and extra spaces
    text = re.sub(r"[^a-z0-9\s]", " ", text)

    return " ".join(text.split())


def exact_matching(
    target: str,
    nodes: list[dict],
) -> dict:
    normalized_target = normalize_title(target)
    if not normalized_target:
        return {
            "status": "not_found",
            "node_id": None,
        }

    matches = [
        node
        for node in nodes
        if node["normalized_title"] == normalized_target
    ]

    if len(matches) == 1:
        matched_node = matches[0]

        return {
            "status": "matched",
            "node_id": matched_node["node_id"],
            "title": matched_node["title"],
            "node": matched_node,
        }

    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "node_id": None,
            "candidates": [
                {
                    "node_id": node["node_id"],
                    "title": node["title"],
                    "parent_id": node.get("parent_id"),
                    "page_start": node.get("page_start"),
                }
                for node in matches
            ],
        }

    return {
        "status": "not_found",
        "node_id": None,
    }


def _node_id(node: dict[str, Any]) -> str | None:
    node_id = node.get("node_id")
    return str(node_id) if node_id is not None else None


def _tokenize(text: str) -> list[str]:
    normalized = normalize_title(text)
    return normalized.split() if normalized else []


def _sparse_keyword_scores(target: str, nodes: list[dict[str, Any]]) -> dict[str, float]:
    query_tokens = _tokenize(target)
    if not query_tokens:
        return {}

    node_tokens: dict[str, list[str]] = {}
    for node in nodes:
        node_id = _node_id(node)
        if node_id is None:
            continue
        title = str(node.get("normalized_title") or node.get("title", ""))
        tokens = _tokenize(title)
        if tokens:
            node_tokens[node_id] = tokens

    if not node_tokens:
        return {}

    total_nodes = len(node_tokens)
    document_frequency = Counter(
        token
        for tokens in node_tokens.values()
        for token in set(tokens)
    )
    idf = {
        token: math.log((1 + total_nodes) / (1 + frequency)) + 1
        for token, frequency in document_frequency.items()
    }

    query_tf = Counter(query_tokens)
    query_vector = {
        token: frequency * idf.get(token, 1.0)
        for token, frequency in query_tf.items()
    }
    query_norm = math.sqrt(sum(value * value for value in query_vector.values()))
    if query_norm == 0:
        return {}

    normalized_target = normalize_title(target)
    scores: dict[str, float] = {}
    for node in nodes:
        node_id = _node_id(node)
        if node_id is None or node_id not in node_tokens:
            continue

        title = str(node.get("normalized_title") or node.get("title", ""))
        normalized_title = normalize_title(title)
        title_tf = Counter(node_tokens[node_id])
        title_vector = {
            token: frequency * idf.get(token, 1.0)
            for token, frequency in title_tf.items()
        }
        title_norm = math.sqrt(sum(value * value for value in title_vector.values()))
        if title_norm == 0:
            continue

        dot_product = sum(
            query_vector.get(token, 0.0) * title_vector.get(token, 0.0)
            for token in query_vector
        )
        sparse_score = dot_product / (query_norm * title_norm)
        fuzzy_score = SequenceMatcher(None, normalized_target, normalized_title).ratio()
        phrase_boost = (
            1.0
            if normalized_target == normalized_title
            else 0.15
            if normalized_target in normalized_title or normalized_title in normalized_target
            else 0.0
        )
        scores[node_id] = min(
            1.0,
            (0.8 * sparse_score) + (0.2 * fuzzy_score) + phrase_boost,
        )

    return scores


def _normalize_semantic_scores(raw_scores: dict[str, float]) -> dict[str, float]:
    if not raw_scores:
        return {}

    values = list(raw_scores.values())
    if all(0.0 <= value <= 1.0 for value in values):
        return {
            node_id: max(0.0, min(1.0, score))
            for node_id, score in raw_scores.items()
        }

    min_score = min(values)
    max_score = max(values)
    if math.isclose(min_score, max_score):
        return {node_id: 1.0 for node_id in raw_scores}

    score_range = max_score - min_score
    return {
        node_id: (score - min_score) / score_range
        for node_id, score in raw_scores.items()
    }


async def hybrid_search(
    target: str,
    nodes: list[dict],
    doc_id: str,
    user_id: str,
    top_k: int = DEFAULT_TOP_K,
    semantic_weight: float = SEMANTIC_WEIGHT,
    keyword_weight: float = KEYWORD_WEIGHT,
) -> dict:
    target = target.strip()
    if not target or not nodes:
        return {
            "status": "not_found",
            "node_id": None,
            "candidates": [],
        }

    top_k = max(1, top_k)

    await ingest_nodes(nodes, doc_id, user_id)
    vector_store = get_node_vector_store(
        embedding=get_node_embedding(),
    )

    semantic_results = vector_store.similarity_search_with_score(
        target,
        k=max(top_k * 2, top_k),
        filter=Filter(
            must=[
                FieldCondition(
                    key="metadata.user_id",
                    match=MatchValue(value=user_id),
                ),
                FieldCondition(
                    key="metadata.doc_id",
                    match=MatchValue(value=doc_id),
                ),
            ]
        ),
    )

    raw_semantic_scores: dict[str, float] = {}
    for document, score in semantic_results:
        node_id = document.metadata.get("node_id")
        if node_id is not None:
            raw_semantic_scores[str(node_id)] = float(score)

    semantic_scores = _normalize_semantic_scores(raw_semantic_scores)
    keyword_scores = _sparse_keyword_scores(target, nodes)
    nodes_by_id = {
        node_id: node
        for node in nodes
        if (node_id := _node_id(node)) is not None
    }

    keyword_candidate_ids = [
        node_id
        for node_id, _ in sorted(
            keyword_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )[: max(top_k * 2, top_k)]
    ]
    candidate_ids = set(semantic_scores) | set(keyword_candidate_ids)

    total_weight = semantic_weight + keyword_weight
    if total_weight <= 0:
        raise ValueError("At least one hybrid search weight must be positive")

    ranked_candidates = []
    for node_id in candidate_ids:
        node = nodes_by_id.get(node_id)
        if node is None:
            continue

        semantic_score = semantic_scores.get(node_id, 0.0)
        keyword_score = keyword_scores.get(node_id, 0.0)
        final_score = (
            (semantic_weight * semantic_score) + (keyword_weight * keyword_score)
        ) / total_weight
        ranked_candidates.append(
            {
                "node_id": node_id,
                "title": node.get("title"),
                "parent_id": node.get("parent_id"),
                "page_start": node.get("page_start"),
                "page_end": node.get("page_end"),
                "score": round(final_score, 4),
                "semantic_score": round(semantic_score, 4),
                "keyword_score": round(keyword_score, 4),
                "node": node,
            }
        )

    ranked_candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    top_candidates = ranked_candidates[:top_k]

    if not top_candidates:
        return {
            "status": "not_found",
            "node_id": None,
            "candidates": [],
        }

    best_match = top_candidates[0]
    return {
        "status": "matched",
        "node_id": best_match["node_id"],
        "title": best_match["title"],
        "score": best_match["score"],
        "semantic_score": best_match["semantic_score"],
        "keyword_score": best_match["keyword_score"],
        "node": best_match["node"],
        "candidates": [
            {key: value for key, value in candidate.items() if key != "node"}
            for candidate in top_candidates
        ],
    }

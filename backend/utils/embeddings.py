from functools import lru_cache

from langchain_openai import OpenAIEmbeddings


@lru_cache(maxsize=1)
def get_chunk_embedding() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model="text-embedding-3-small",
    )


@lru_cache(maxsize=1)
def get_node_embedding() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model="text-embedding-3-small",
        dimensions=512,
    )

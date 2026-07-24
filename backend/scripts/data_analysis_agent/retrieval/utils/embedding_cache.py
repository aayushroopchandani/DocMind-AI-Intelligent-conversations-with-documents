from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Protocol

from utils.embeddings import get_chunk_embedding


class AsyncDenseEmbeddings(Protocol):
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...


class SingleFlightEmbeddingCache:
    """Reuse overlapping query embeddings across concurrent retrieval branches."""

    def __init__(
        self,
        delegate: AsyncDenseEmbeddings | None = None,
        *,
        max_entries: int = 128,
    ) -> None:
        self._delegate = delegate
        self._max_entries = max(1, max_entries)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._futures: OrderedDict[
            str,
            asyncio.Future[list[float]],
        ] = OrderedDict()
        self._population_tasks: set[asyncio.Task[None]] = set()

    def _ensure_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop = loop
            self._lock = asyncio.Lock()
            self._futures.clear()
            self._population_tasks.clear()
        return self._lock

    @property
    def delegate(self) -> AsyncDenseEmbeddings:
        return self._delegate or get_chunk_embedding()

    def _evict_completed(self) -> None:
        while len(self._futures) > self._max_entries:
            removable = next(
                (
                    text
                    for text, future in self._futures.items()
                    if future.done()
                ),
                None,
            )
            if removable is None:
                return
            self._futures.pop(removable, None)

    async def _populate(
        self,
        texts: tuple[str, ...],
        futures: tuple[asyncio.Future[list[float]], ...],
    ) -> None:
        try:
            vectors = await self.delegate.aembed_documents(list(texts))
            if len(vectors) != len(futures):
                raise RuntimeError(
                    "embedding provider returned an unexpected vector count"
                )
        except Exception as exc:
            async with self._ensure_loop():
                for text, future in zip(texts, futures, strict=True):
                    if not future.done():
                        future.set_exception(exc)
                    if self._futures.get(text) is future:
                        self._futures.pop(text, None)
            return

        async with self._ensure_loop():
            for future, vector in zip(futures, vectors, strict=True):
                if not future.done():
                    future.set_result(list(vector))
            self._evict_completed()

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        loop = asyncio.get_running_loop()
        lock = self._ensure_loop()
        new_texts: list[str] = []
        new_futures: list[asyncio.Future[list[float]]] = []
        requested: list[asyncio.Future[list[float]]] = []
        async with lock:
            for text in texts:
                future = self._futures.get(text)
                if future is None:
                    future = loop.create_future()
                    self._futures[text] = future
                    new_texts.append(text)
                    new_futures.append(future)
                else:
                    self._futures.move_to_end(text)
                requested.append(future)
            if new_texts:
                task = asyncio.create_task(
                    self._populate(
                        tuple(new_texts),
                        tuple(new_futures),
                    )
                )
                self._population_tasks.add(task)
                task.add_done_callback(self._population_tasks.discard)
            self._evict_completed()

        vectors = await asyncio.gather(
            *(asyncio.shield(future) for future in requested)
        )
        return [list(vector) for vector in vectors]

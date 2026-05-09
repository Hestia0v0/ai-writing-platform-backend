import asyncio
from functools import lru_cache
from fastembed import TextEmbedding

EMBEDDING_DIM = 384
_MODEL_NAME = "BAAI/bge-small-en-v1.5"


@lru_cache(maxsize=1)
def _model() -> TextEmbedding:
    return TextEmbedding(_MODEL_NAME)


def embed_sync(text: str) -> list[float]:
    return next(_model().embed([text])).tolist()


async def embed(text: str) -> list[float]:
    return await asyncio.to_thread(embed_sync, text)

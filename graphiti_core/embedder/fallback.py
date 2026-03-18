"""
Fallback embedder — tries a primary embedder (e.g. local llama.cpp),
falls back to a secondary (e.g. OpenAI API) on connection failure.

Note: different models produce incompatible vector spaces. During fallback,
vector search quality degrades (mixed spaces) but BM25 fulltext still works.
This is acceptable for transient Darwin downtime.
"""

import logging
from collections.abc import Iterable

from .client import EmbedderClient

logger = logging.getLogger(__name__)


class FallbackEmbedder(EmbedderClient):
    """Wraps two EmbedderClients: tries primary, falls back on connection errors."""

    def __init__(self, primary: EmbedderClient, fallback: EmbedderClient):
        self.primary = primary
        self.fallback = fallback
        self._fallback_warned = False

    async def create(
        self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]
    ) -> list[float]:
        try:
            return await self.primary.create(input_data)
        except Exception as e:
            if self._is_connection_error(e):
                self._warn_fallback(e)
                return await self.fallback.create(input_data)
            raise

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        try:
            return await self.primary.create_batch(input_data_list)
        except Exception as e:
            if self._is_connection_error(e):
                self._warn_fallback(e)
                return await self.fallback.create_batch(input_data_list)
            raise

    def _is_connection_error(self, e: Exception) -> bool:
        """Check if the error indicates the primary server is unreachable."""
        # openai SDK wraps connection errors in APIConnectionError
        error_type = type(e).__name__
        connection_types = (
            'APIConnectionError',
            'ConnectError',
            'ConnectTimeout',
            'APITimeoutError',
            'ConnectionRefusedError',
            'OSError',
        )
        return error_type in connection_types or 'Connection refused' in str(e)

    def _warn_fallback(self, e: Exception) -> None:
        if not self._fallback_warned:
            logger.warning(
                f'Primary embedder unavailable ({e}), falling back to OpenAI API. '
                'Note: mixed embedding spaces will degrade vector search quality.'
            )
            self._fallback_warned = True
        else:
            logger.debug(f'Primary embedder still down ({e}), using fallback')

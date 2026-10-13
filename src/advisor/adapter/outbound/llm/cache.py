"""The LLM cache over `advisor.llm_cache` (ADR-003): the key is the SHA-256 of the model settings
and the prompt, the value the provider's generations as LangChain serializes them. An entry older
than 7 days is not served; qone-worker prunes those rows. A failing cache never fails a reply."""

import hashlib
import logging
import warnings
from collections.abc import Sequence
from typing import Any

from langchain_core.caches import BaseCache
from langchain_core.load import dumps, loads
from langchain_core.outputs import Generation
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

log = logging.getLogger("advisor.llm")


def cache_key(prompt: str, llm_string: str) -> str:
    return hashlib.sha256(f"{llm_string}\n{prompt}".encode()).hexdigest()


class PostgresLlmCache(BaseCache):
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def lookup(self, prompt: str, llm_string: str) -> Sequence[Generation] | None:
        try:
            with self._pool.connection() as conn:
                row = conn.execute(
                    "SELECT response FROM advisor.llm_cache WHERE key = %s AND created_at > now() - interval '7 days'",
                    (cache_key(prompt, llm_string),),
                ).fetchone()
        except Exception as error:  # noqa: BLE001 - a cache miss, not a failure
            log.warning("llm cache lookup failed", extra={"detail": str(error)})
            return None
        if row is None:
            return None
        with warnings.catch_warnings():  # loads is marked beta; the format is LangChain's own
            warnings.simplefilter("ignore")
            return [loads(g, allowed_objects="core") for g in row["response"]["generations"]]

    def update(self, prompt: str, llm_string: str, return_val: Sequence[Generation]) -> None:
        payload: dict[str, Any] = {"generations": [dumps(g) for g in return_val]}
        try:
            with self._pool.connection() as conn:
                conn.execute(
                    "INSERT INTO advisor.llm_cache (key, response) VALUES (%s, %s) "
                    "ON CONFLICT (key) DO UPDATE SET response = EXCLUDED.response, created_at = now()",
                    (cache_key(prompt, llm_string), Jsonb(payload)),
                )
        except Exception as error:  # noqa: BLE001
            log.warning("llm cache update failed", extra={"detail": str(error)})

    def clear(self, **kwargs: Any) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM advisor.llm_cache")

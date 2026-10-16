"""Shared clients. One connection pool per process; graph handles are cheap."""

from functools import lru_cache

import redis
from anthropic import Anthropic
from falkordb import FalkorDB, Graph

from ringtrace_core.config import get_settings


@lru_cache(maxsize=1)
def get_db() -> FalkorDB:
    pool = redis.ConnectionPool.from_url(get_settings().falkordb_url, decode_responses=True)
    return FalkorDB(connection_pool=pool)


def get_graph() -> Graph:
    return get_db().select_graph(get_settings().graph_name)


@lru_cache
def get_anthropic() -> Anthropic:
    key = get_settings().anthropic_api_key
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set; add it to .env")
    return Anthropic(api_key=key)

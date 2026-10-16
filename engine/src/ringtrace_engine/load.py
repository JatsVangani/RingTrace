"""
Prepared Parquet -> FalkorDB, in batches of 'UNWIND $rows'

Always a fresh load: the graph is dropped first, so reruns never duplicate edges
"""

import time
from pathlib import Path

import polars as pl
from falkordb import FalkorDB, Graph

from ringtrace_engine.schema import create_indexes

BANKS = """
UNWIND $rows AS r
CREATE (:Bank {bank_id: r.bank_id, name: r.bank_name,country: r.bank_country})
"""

ENTITIES = """
UNWIND $rows AS r
CREATE (:Entity {entity_id: r.entity_id, kind: r.entity_kind})
"""

ACCOUNTS = """
UNWIND $rows AS r
MATCH (b:Bank {bank_id: r.bank_id}), 
(e:Entity {entity_id: r.entity_id})
CREATE (a:Account {acct_key: r.acct_key})
SET a += r.props
CREATE (a)-[:AT_BANK]->(b), (a)-[:OWNED_BY]->(e)
"""

TRANSFERS = """
UNWIND $rows AS r
MATCH (s:Account {acct_key: r.src}), (d:Account {acct_key: r.dst})
CREATE (s)-[:SENT {txn_id: r.txn_id, ts: r.ts_ms, amount_usd: r.amount_usd,
                   amount_paid: r.amount_paid, currency_paid: r.currency_paid,
                   amount_received: r.amount_received, currency_received: r.currency_received,
                   format: r.format}]->(d)
"""
# Stored on Bank / Entity nodes instead of on every account.
ON_OTHER_NODES = ("bank_id", "bank_name", "bank_country", "entity_id", "entity_kind")


def batched(graph: Graph, query: str, rows: pl.DataFrame, label: str, size: int) -> None:
    started = time.monotonic()
    done = 0
    for batch in rows.iter_slices(size):
        graph.query(query, {"rows": batch.to_dicts()})
        done += batch.height
        elapsed = time.monotonic() - started
        print(f"\r{label}: {done:,}/{rows.height:,}({elapsed:.0f}s)", end="", flush=True)
        print()


def account_rows(data_dir: Path) -> pl.DataFrame:
    """Keys for the MATCH, everything else (self totals, features) as one 'props' map"""
    accounts = pl.read_parquet(data_dir / "accounts.parquet")
    features = pl.read_parquet(data_dir / "features.parquet")
    merged = accounts.join(features, on="acct_key", how="left")
    props = [c for c in merged.columns if c not in ("acct_key", *ON_OTHER_NODES)]

    return merged.select("acct_key", "bank_id", "entity_id", pl.struct(props).alias("props"))


def load(db: FalkorDB, graph_name: str, data_dir: Path, batch_size: int = 10_000) -> None:
    if graph_name in db.list_graphs():
        db.select_graph(graph_name).delete()
    graph = db.select_graph(graph_name)
    create_indexes(graph)

    accounts = pl.read_parquet(data_dir / "accounts.parquet")
    banks = accounts.unique("bank_id").select("bank_id", "bank_name", "bank_country")
    entities = accounts.unique("entity_id").select("entity_id", "entity_kind")
    transfers = pl.read_parquet(data_dir / "transfers.parquet")

    batched(graph, BANKS, banks, "banks", batch_size)
    batched(graph, ENTITIES, entities, "entities", batch_size)
    batched(graph, ACCOUNTS, account_rows(data_dir), "accounts", batch_size)
    batched(graph, TRANSFERS, transfers, "transfers", batch_size)

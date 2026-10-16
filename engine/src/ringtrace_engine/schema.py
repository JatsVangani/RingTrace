"""Graph indexes. Created on an empty graph, before loading,
so every MATCH by key during the load is an index lookup."""

from falkordb import Graph

INDEXES = [
    "CREATE INDEX FOR (a:Account) ON (a.acct_key)",
    "CREATE INDEX FOR (b:Bank) ON (b.bank_id)",
    "CREATE INDEX FOR (e:Entity) ON (e.entity_id)",
    "CREATE INDEX FOR (c:Case) ON (c.case_id)",
    "CREATE INDEX FOR ()-[s:SENT]-() ON (s.ts)",
]


def create_indexes(graph: Graph) -> None:
    for statement in INDEXES:
        graph.query(statement)

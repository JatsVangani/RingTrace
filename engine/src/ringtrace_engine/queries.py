"""Read-only Cypher behind the agent's tools. Everything runs through GRAPH.RO_QUERY.

Two rules shape the traversals:
- Routing links are not followed: a pair with more than MAX_REPEAT trasnfers is a
  regular relationship (85% of ring transfers are one-offs, vs 5% of the rest).
- Money moves forward in time: each hop only follows transfers within 'window_h'
  of the link that reached the account.
"""

import re

from falkordb import Graph

HOUR_MS = 3_600_000
MAX_REPEAT = 2  # pairs with more transfers than this are routine
HUB_DEGREE = 100  # reach hubs, but don't expand through them
MAX_ROWS = 25

PATTERNS = {
    "out": "(a:Account {acct_key: f.k})-[s:SENT]->(b:Account)",
    "in": "(a:Account {acct_key: f.k})<-[s:SENT]-(b:Account)",
    "both": "(a:Account {acct_key: f.k})-[s:SENT]-(b:Account)",
}

HOP = """
UNWIND $frontier AS f
MATCH {pattern}
WITH f, a, b, startNode(s) = a AS outgoing, collect(s) AS ss
WHERE size(ss) <= $max_repeat
WITH f, a, b, outgoing, [x IN ss WHERE x.ts >= f.lo AND x.ts <= f.hi] AS ss
WHERE size(ss) > 0
UNWIND ss AS s
RETURN a.acct_key,  b.acct_key, outgoing, min(s.ts), max(s.ts), sum(s.amount_usd),
        count(s), b.degree, b.risk_score
"""


def _rows(graph: Graph, cypher: str, params: dict | None = None) -> list[list]:
    return graph.ro_query(cypher, params).result_set


def _hop(graph: Graph, direction: str, frontier: list[dict]) -> list[list]:
    """One step out from 'frontier' ({k, lo, hi}) over non-routine links in [lo, hi]"""
    query = HOP.format(pattern=PATTERNS[direction])
    return _rows(graph, query, {"frontier": frontier, "max_repeat": MAX_REPEAT})


def account_profile(graph: Graph, acct_key: str) -> dict | None:
    rows = _rows(
        graph,
        """
        MATCH (a:Account {acct_key: $k})-[:AT_BANK]->(b:Bank),
        (a)-[:OWNED_BY]->(e:Entity)
        RETURN properties(a), b.name, b.country, e.entity_id, e.kind
        """,
        {"k": acct_key},
    )
    if not rows:
        return None
    props, bank, country, entity_id, kind = rows[0]

    counterparties = _rows(
        graph,
        """
        MATCH (a:Account {acct_key: $k})-[s:SENT]-(c:Account)
        WITH c, startNode(s) = a AS outgoing, count(s) AS n,
        sum(s.amount_usd) AS usd,
        min(s.ts) AS first_ts, max(s.ts) AS last_ts
        RETURN c.acct_key, outgoing, n, usd, first_ts, last_ts
        ORDER BY n <= $max_repeat DESC, usd DESC
        LIMIT $limit
        """,
        {"k": acct_key, "max_repeat": MAX_REPEAT, "limit": MAX_ROWS},
    )
    keys = ("acct_key", "outgoing", "n_txn", "usd", "first_ts", "last_ts")
    return {
        **props,
        "bank": bank,
        "bank_country": country,
        "entity_id": entity_id,
        "entity_kind": kind,
        "counterparties": [dict(zip(keys, r, strict=True)) for r in counterparties],
    }


def money_flow(
    graph: Graph, acct_key: str, direction: str = "both", hops: int = 3, window_h: int = 96
) -> list[dict]:
    """Accounts reached over non-routine, time-consistent links, nearest first"""
    window = window_h * HOUR_MS
    seen = {acct_key}
    found = []
    frontier = [{"k": acct_key, "lo": 0, "hi": 2**62}]
    for hop in range(1, min(hops, 4) + 1):
        reached = []
        for a, b, outgoing, first, last, usd, n, degree, risk in _hop(graph, direction, frontier):
            if b not in seen:
                seen.add(b)
                reached.append(
                    {
                        "acct_key": b,
                        "hop": hop,
                        "via": a,
                        "outgoing": outgoing,
                        "first_ts": first,
                        "last_ts": last,
                        "usd": usd,
                        "n_txn": n,
                        "degree": degree,
                        "risk_score": risk,
                    }
                )
        found += reached
        frontier = [
            {
                "k": r["acct_key"],
                "lo": r["first_ts"] - (0 if direction == "out" else window),
                "hi": r["last_ts"] + (0 if direction == "in" else window),
            }
            for r in reached
            if (r["degree"] or 0) <= HUB_DEGREE
        ]
    return found


def find_cycles(graph: Graph, acct_key: str, max_hops: int = 12, window_h: int = 96) -> list[dict]:
    """Paths that leave the account and come back, each hop no earlier than the last"""
    window = window_h * HOUR_MS
    parent: dict[str, str] = {}
    cycles = []
    frontier = [{"k": acct_key, "lo": 0, "hi": 2**62}]
    for hop in range(1, max_hops + 1):
        arrivals: dict[str, tuple[str, int, int | None]] = {}
        for a, b, _, first, _, usd, _, degree, _ in _hop(graph, "out", frontier):
            if b == acct_key and hop >= 2:
                path = [a]
                while path[-1] in parent:
                    path.append(parent[path[-1]])
                cycles.append(
                    {
                        "path": [acct_key, *path[::-1][1:], acct_key],
                        "hops": hop,
                        "closing_ts": first,
                        "closing_usd": usd,
                    }
                )
            elif b != acct_key and b not in parent:
                if b not in arrivals or first < arrivals[b][1]:
                    arrivals[b] = (a, first, degree)
        parent.update({b: a for b, (a, _, _) in arrivals.items()})
        frontier = [
            {"k": b, "lo": first, "hi": first + window}
            for b, (_, first, degree) in arrivals.items()
            if (degree or 0) <= HUB_DEGREE
        ]
    return cycles[:MAX_ROWS]


def convergence(graph: Graph, acct_keys: list[str], direction: str = "out") -> list[dict]:
    """Accounts that two or more of these accounts pay (out) or are paid by (in)

    No routine filter here: the set is small and chosen,
    and some rings reuse existing relationships
    'n_txn' shows how routine each shared link is
    """
    pattern = "(a)-[s:SENT]->(c:Account)" if direction == "out" else "(a)<-[s:SENT]-(c:Account)"
    rows = _rows(
        graph,
        f"""
        UNWIND $keys AS k
        MATCH (a:Account {{acct_key: k}})
        MATCH {pattern}
        WITH c, collect(DISTINCT a.acct_key) AS shared_by, count(s) AS n_txn,
        sum(s.amount_usd) AS usd
        WHERE size(shared_by) >= 2
        RETURN c.acct_key, shared_by, n_txn, usd, c.degree, c.risk_score
        ORDER BY size(shared_by) DESC, usd DESC
        LIMIT $limit
        """,
        {"keys": acct_keys, "limit": MAX_ROWS},
    )
    keys = ("acct_key", "shared_by", "n_txn", "usd", "degree", "risk_score")
    return [dict(zip(keys, r, strict=True)) for r in rows]


def shared_owner(graph: Graph, acct_keys: list[str]) -> list[dict]:
    """Owner of these accounts that also own other accounts"""
    rows = _rows(
        graph,
        """
        UNWIND $keys AS k
        MATCH (a:Account {acct_key: k})-[:OWNED_BY]->(e:Entity)<-[:OWNED_BY]-(o:Account)
        WHERE o <> a
        WITH e, collect(DISTINCT a.acct_key) AS members, collect(DISTINCT o.acct_key) AS others
        RETURN e.entity_id, e.kind, members, size(others), others[..10]
        LIMIT $limit
        """,
        {"keys": acct_keys, "limit": MAX_ROWS},
    )
    keys = ("entity_id", "kind", "accounts", "other_count", "others_sample")
    return [dict(zip(keys, r, strict=True)) for r in rows]


def timeline(graph: Graph, acct_keys: list[str]) -> list[dict]:
    """Transfers among these accounts, oldest first"""
    rows = _rows(
        graph,
        """
        UNWIND $keys AS k
        MATCH (a:Account {acct_key: k})-[s:SENT]->(b:Account)
        WHERE b.acct_key IN $keys
        RETURN a.acct_key, b.acct_key, s.ts, s.amount_usd, s.currency_paid, s.currency_received
        ORDER BY s.ts
        LIMIT 50
        """,
        {"keys": acct_keys},
    )
    keys = ("src", "dst", "ts", "usd", "currency_paid", "currency_received")
    return [dict(zip(keys, r, strict=True)) for r in rows]


def cypher_readonly(graph: Graph, query: str) -> list[list]:
    """Escape hatch, RO_QUERY refuses writes, payment format stays off-limits (D5)"""
    if re.search(r"\bformat\b", query, re.IGNORECASE):
        raise ValueError("payment format is not available")
    if not re.search(r"\bLIMIT\s+\d+\s*$", query.strip(), re.IGNORECASE):
        query = f"{query.rstrip().rstrip(';')} LIMIT {MAX_ROWS}"
    return _rows(graph, query)

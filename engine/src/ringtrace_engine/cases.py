"""From evidence plus the LLM's verdict to a scored case, and into the graph.

Code decides members, roles, findings and score; the model decides whether it is a ring
and names the pattern. A case whose members mostly overlap an open case updates it.
"""

import json
import time
import uuid

from falkordb import Graph

from ringtrace_core.reason_codes import REASON_CODES
from ringtrace_engine.evidence import Evidence, span_h
from ringtrace_engine.queries import HUB_DEGREE

TIERS = [(0.90, "freeze"), (0.80, "hold_txn"), (0.65, "step_up"), (0.0, "monitor")]
MERGE_JACCARD = 0.5
BURST = 3  # this many one-off links within BURST_H is a burst
BURST_H = 48


def members_of(ev: Evidence) -> set[str]:
    """Seed, its one-off neighbours, anything that forwards or converges, cycle paths."""
    hop1 = {r["acct_key"] for r in ev.flow if r["hop"] == 1 and (r["degree"] or 0) <= HUB_DEGREE}
    chained = {
        r["acct_key"]
        for r in ev.flow
        if r["hop"] == 2
        and r["via"] in (ev.pass_on | ev.upstream)
        and (r["degree"] or 0) <= HUB_DEGREE
    }
    shared = {
        c["acct_key"]
        for c in ev.collectors[:2] + ev.sources[:2]
        if (c["degree"] or 0) <= HUB_DEGREE
    }
    on_cycles = {a for c in ev.cycles for a in c["path"]}
    return {ev.seed} | hop1 | chained | shared | on_cycles


def roles_of(ev: Evidence, members: set[str]) -> dict[str, str]:
    """By position in the flow among members: origin, sink, splitter, or pass-through."""
    out_n: dict[str, int] = dict.fromkeys(members, 0)
    in_n: dict[str, int] = dict.fromkeys(members, 0)
    for r in ev.flow:
        src, dst = (r["via"], r["acct_key"]) if r["outgoing"] else (r["acct_key"], r["via"])
        if src in members and dst in members:
            out_n[src] += 1
            in_n[dst] += 1
    for c in ev.collectors[:2]:
        if c["acct_key"] in members:
            in_n[c["acct_key"]] += len(c["shared_by"])
    for s in ev.sources[:2]:
        if s["acct_key"] in members:
            out_n[s["acct_key"]] += len(s["shared_by"])
    roles = {}
    for m in members:
        if in_n[m] and not out_n[m]:
            roles[m] = "collector"
        elif out_n[m] and not in_n[m]:
            roles[m] = "source"
        elif out_n[m] >= 3:
            roles[m] = "distributor"
        else:
            roles[m] = "intermediary"
    return roles


def findings_of(ev: Evidence, members: set[str]) -> list[dict]:
    """Reason codes the evidence supports, each with the numbers behind it."""
    p = ev.profile
    found: list[tuple[str, str]] = []
    if len(ev.in1) >= BURST and span_h(ev.in1) <= BURST_H:
        found.append(
            ("FAN_IN_BURST", f"{len(ev.in1)} one-off senders within {span_h(ev.in1):.0f} h")
        )
    if len(ev.out1) >= BURST and span_h(ev.out1) <= BURST_H:
        found.append(
            ("FAN_OUT_BURST", f"{len(ev.out1)} one-off recipients within {span_h(ev.out1):.0f} h")
        )
    if ev.cycles:
        c = min(ev.cycles, key=lambda c: c["hops"])
        found.append(("CYCLE", f"{c['hops']}-hop cycle closing with ${c['closing_usd']:,.0f}"))
    if ev.collectors and len(ev.collectors[0]["shared_by"]) >= 2:
        c = ev.collectors[0]
        found.append(
            ("SCATTER_GATHER", f"{len(c['shared_by'])} recipients pay collector {c['acct_key']}")
        )
    if len(ev.in1) >= BURST and len(ev.out1) >= BURST:
        found.append(("GATHER_SCATTER", f"{len(ev.in1)} senders in, {len(ev.out1)} recipients out"))
    layers = len(ev.pass_on) + len(ev.upstream)
    if layers >= 2:
        found.append(
            (
                "LAYERED_FLOW",
                f"{len(ev.upstream)} upstream and {len(ev.pass_on)} onward pass-throughs",
            )
        )
    if "RAPID_PASSTHROUGH" in (p.get("risk_codes") or []):
        found.append(("RAPID_PASSTHROUGH", f"median dwell {p.get('dwell_median_min'):.0f} min"))
    if (p.get("bank_spread") or 0) >= 8:
        found.append(("CROSS_BANK_HOPS", f"counterparties at {p['bank_spread']} banks"))
    if (p.get("currency_switch_share") or 0) > 0:
        found.append(
            (
                "CURRENCY_HOPPING",
                f"currency changes on {p['currency_switch_share']:.0%} of pass-throughs",
            )
        )
    tight = [rows for rows in (ev.in1, ev.out1) if len(rows) >= BURST and span_h(rows) <= 24]
    if tight:
        found.append(
            ("SYNCHRONISED_TIMING", f"{len(tight[0])} links within {span_h(tight[0]):.0f} h")
        )
    for o in ev.owners:
        inside = [a for a in o["accounts"] if a in members]
        if len(inside) >= 2:
            found.append(("SHARED_OWNER", f"entity {o['entity_id']} owns {len(inside)} members"))
            break
    return [
        {"code": code, "detail": detail, "weight": REASON_CODES[code][0]} for code, detail in found
    ]


def tier_of(score: float) -> str:
    return next(name for floor, name in TIERS if score >= floor)


def build_case(ev: Evidence, decision: dict) -> dict:
    members = members_of(ev)
    findings = findings_of(ev, members)
    score = min(1.0, sum(f["weight"] for f in findings))
    value = sum(
        r["usd"] for r in ev.flow if r["acct_key"] in members and r["via"] in members
    ) + sum(c["usd"] for c in ev.collectors[:2] + ev.sources[:2] if c["acct_key"] in members)
    return {
        "case_id": uuid.uuid4().hex[:12],
        "seed": ev.seed,
        "pattern": decision["pattern"],
        "summary": decision["analysis"],
        "members": roles_of(ev, members),
        "findings": findings,
        "score": round(score, 2),
        "tier": tier_of(score),
        "value_at_risk_usd": round(value),
    }


def open_cases(graph: Graph) -> list[tuple[str, set[str]]]:
    rows = graph.ro_query(
        "MATCH (c:Case {status: 'open'})-[:INCLUDES]->(a:Account) "
        "RETURN c.case_id, collect(a.acct_key)"
    ).result_set
    return [(cid, set(keys)) for cid, keys in rows]


def merge_target(graph: Graph, members: set[str]) -> str | None:
    """An open case sharing enough members to be the same ring."""
    best, best_j = None, MERGE_JACCARD
    for cid, keys in open_cases(graph):
        j = len(members & keys) / len(members | keys)
        if j >= best_j:
            best, best_j = cid, j
    return best


def save(graph: Graph, ev: Evidence, decision: dict, case: dict | None, model: str) -> dict:
    """Write the investigation trace, then the case (new or merged). Returns ids."""
    now = int(time.time() * 1000)
    inv_id = uuid.uuid4().hex[:12]
    graph.query(
        """
        CREATE (i:Investigation {inv_id: $inv_id, seed: $seed, model: $model,
                                 started_at: $started, finished_at: $now, outcome: $outcome,
                                 n_steps: $n, analysis: $analysis})
        WITH i
        UNWIND $steps AS s
        CREATE (i)-[:STEP {n: s.n}]->(:ToolCall {tool: s.tool, args_json: s.args,
                                                 result_summary: s.summary, ms: s.ms})
        """,
        {
            "inv_id": inv_id,
            "seed": ev.seed,
            "model": model,
            "started": now - sum(t["ms"] for t in ev.trace),
            "now": now,
            "outcome": "case" if case else "dismissed",
            "n": len(ev.trace),
            "analysis": decision["analysis"],
            "steps": [
                {**t, "n": i + 1, "args": json.dumps(t["args"])} for i, t in enumerate(ev.trace)
            ],
        },
    )
    if case is None:
        return {"inv_id": inv_id, "case_id": None}

    existing = merge_target(graph, set(case["members"]))
    case_id = existing or case["case_id"]
    if existing:  # replace members and findings; keep the id analysts know
        graph.query(
            "MATCH (c:Case {case_id: $id}) OPTIONAL MATCH (c)-[r:INCLUDES]->() DELETE r "
            "WITH c OPTIONAL MATCH (c)-[:EVIDENCE]->(f:Finding) DETACH DELETE f",
            {"id": case_id},
        )
    graph.query(
        """
        MERGE (c:Case {case_id: $id})
        ON CREATE SET c.status = 'open', c.created_at = $now
        SET c.updated_at = $now, c.seed = $seed, c.pattern = $pattern, c.score = $score,
            c.tier = $tier, c.value_at_risk_usd = $value, c.member_count = $n,
            c.summary = $summary
        WITH c
        MATCH (i:Investigation {inv_id: $inv_id}) CREATE (i)-[:PRODUCED]->(c)
        WITH c
        UNWIND $members AS m
        MATCH (a:Account {acct_key: m.acct_key}) CREATE (c)-[:INCLUDES {role: m.role}]->(a)
        WITH DISTINCT c
        UNWIND $findings AS f
        CREATE (c)-[:EVIDENCE]->(:Finding {code: f.code, text: f.detail, weight: f.weight})
        """,
        {
            "id": case_id,
            "now": now,
            "inv_id": inv_id,
            "seed": ev.seed,
            "pattern": case["pattern"],
            "score": case["score"],
            "tier": case["tier"],
            "value": case["value_at_risk_usd"],
            "n": len(case["members"]),
            "summary": case["summary"],
            "members": [{"acct_key": k, "role": r} for k, r in case["members"].items()],
            "findings": case["findings"],
        },
    )
    return {"inv_id": inv_id, "case_id": case_id, "merged": bool(existing)}

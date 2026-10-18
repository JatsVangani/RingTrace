"""One candidate, end to end: gather evidence -> model decides -> case built and saved."""

from falkordb import Graph

from ringtrace_agent.prompts import DECISION_SCHEMA, PATTERNS, SYSTEM
from ringtrace_core.config import get_settings
from ringtrace_core.llm import complete_json
from ringtrace_engine import cases, evidence

HARD_EVIDENCE = {"cycle", "scatter_gather", "gather_scatter"}  # shapes the engine measured directly


def decide(facts: str) -> dict:
    d = complete_json(SYSTEM, facts, DECISION_SCHEMA, max_tokens=300)
    if d.get("pattern") not in PATTERNS:
        d["pattern"] = "none"
    d["is_ring"] = bool(d.get("is_ring")) and d["pattern"] != "none"
    d.setdefault("analysis", "")
    return d


def investigate(graph: Graph, seed: str, write: bool = True) -> dict:
    ev = evidence.gather(graph, seed)
    if ev is None:
        return {"seed": seed, "outcome": "no_such_account"}
    facts = evidence.facts(ev)
    decision = decide(facts)
    shape, _ = evidence.shape_reading(ev)
    if shape in HARD_EVIDENCE:  # a measured cycle, collector or hub beats the model's reading
        decision["is_ring"], decision["pattern"] = True, shape
    case = cases.build_case(ev, decision) if decision["is_ring"] else None
    ids = cases.save(graph, ev, decision, case, get_settings().llm_model) if write else {}
    return {
        "seed": seed,
        "facts": facts,
        "decision": decision,
        "case": case,
        "trace": ev.trace,
        **ids,
    }

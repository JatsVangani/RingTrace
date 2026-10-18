# RingTrace

An AML investigator that works a transaction graph the way an analyst does: start from a
flagged account, follow the money over fresh one-off links, look for where it converges or
loops, and write up a ring case with members, roles, findings and a recommended action.
Every case carries the trace of graph queries behind it, and an analyst's verdict on one case
changes how later investigations are scored.

Built for Graph Hacks: Context for AI Agents (WeMakeDevs x FalkorDB, Oct 2026).
Track 1 (agents on connected data), with memory across investigations for Track 2.

## What it does

1. **Score accounts** with five rule codes over per-account features (currency hopping,
   rapid pass-through, fan-in/fan-out bursts, bank spread). The top of that list is the
   candidate queue. On the top 20, 12 are in a known ring: precision 0.60.
2. **Investigate a candidate.** The engine runs the graph tools in a fixed order and turns
   the results into plain facts: profile, two-hop money flow over one-off links in time
   order, time-ordered cycles back to the seed, convergence of the seed's recipients on a
   collector (or of its senders on a source), earlier cases touching these accounts
   (memory), shared owners. The engine reads the shape itself (cycle, scatter-gather,
   gather-scatter, stack, fan-in, fan-out).
3. **Decide.** A local LLM reads the facts and answers in JSON: is this a ring, which
   pattern, and a two-sentence analysis. When the engine measured a cycle, a collector or a
   hub directly, that reading wins over the model's.
4. **Build the case.** Code picks the members (seed, one-off neighbours, pass-through
   chains, collectors, cycle paths), assigns roles (source, distributor, intermediary,
   collector), lists the findings it can back with numbers, and sums their weights into a
   score and a tier: freeze, hold_txn, step_up or monitor. A case overlapping an open one by
   Jaccard 0.5 or more updates that case instead of opening a new one.
5. **Remember.** An analyst closes a case as a confirmed ring or a false positive. The next
   investigation that touches any of its accounts sees that in its facts, and a confirmed
   link adds a `PRIOR_CONFIRMED_LINK` finding. The trace shows the `recall` step.

On the same top-20 queue the agent kept 8 cases, 7 of them real rings: precision 0.88, with
0.88 of each seed's ring members included on average. It dismissed 5 real rings, all
peripheral members whose own neighbourhood looks like an ordinary fan-in.

## Graph model

FalkorDB, one graph, 518,581 `Account` nodes and 4.49M `SENT` edges from IBM AML HI-Small.

(:Account {acct_key, risk_score, risk_codes, degree, ...})-[:SENT {ts, amount_usd, currency_paid, currency_received}]->(:Account)
(:Account)-[:AT_BANK]->(:Bank {name, country})      (:Account)-[:OWNED_BY]->(:Entity)
(:Investigation)-[:STEP {n}]->(:ToolCall {tool, args_json, result_summary, ms})
(:Investigation)-[:PRODUCED]->(:Case {case_id, pattern, score, tier, value_at_risk_usd, status})
(:Case)-[:INCLUDES {role}]->(:Account)      (:Case)-[:EVIDENCE]->(:Finding {code, text, weight})
(:Verdict {verdict, notes, ts})-[:ON]->(:Case)

Two rules shape every traversal: pairs with more than two transfers are routine and are not
followed (85% of ring transfers are one-offs, against 5% of the rest), and each hop only
follows transfers within 96 hours after the one that reached the account. Labels never enter
the graph; `eval/` is the only code that reads them.

## Setup

Needs Docker (Colima works), `uv`, Python 3.11, about 10 GB free RAM for FalkorDB and the
model together, and roughly 15 GB disk for the dataset, the graph and the model.

```bash
git clone <this repo> && cd RingTrace
uv sync
cp .env.example .env            # defaults work for the local setup
docker compose -f infra/local/docker-compose.yml up -d

The compose file starts two services:

- falkordb on :6379 (browser on :3000).
- llm: llama.cpp server on :8080, which downloads Qwen3-8B Q4_K_M (about 5 GB) from
  Hugging Face on first start into the llm-models volume. Wait for
  curl localhost:8080/health to return ok before investigating. It needs about 6 GB of
  memory; on a laptop you may prefer to run llama-server natively with the same model and
  leave the service off.

Dataset

IBM Transactions for Anti Money Laundering, HI-Small. Download from Kaggle
(https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml)
and put HI-Small_Trans.csv, HI-Small_accounts.csv and HI-Small_Patterns.txt in
data/ibm-aml/. Then:

uv run python scripts/prepare_ibm.py --raw data/ibm-aml --out data/prepared   # USD, account keys
uv run python scripts/build_features.py --data data/prepared                  # features + risk score
uv run python -m eval.ground_truth --raw data/ibm-aml --out data/labels       # labels, eval only
uv run python scripts/load_graph.py --data data/prepared                      # ~20 min

# RingTrace

An AML investigator that works a transaction graph the way an analyst does: start from a
flagged account, follow the money over fresh one-off links, look for where it converges or
loops, and write up a ring case with members, roles, findings and a recommended action.
Every case carries the trace of graph queries behind it, and an analyst's verdict on one case
changes how later investigations are scored.

Built for Graph Hacks: Context for AI Agents (WeMakeDevs x FalkorDB, Oct 2026).
Track 1 (agents on connected data), with memory across investigations for Track 2.

## What it does

1. **Score accounts** with five rule codes over per-account features (currency hopping,
   rapid pass-through, fan-in/fan-out bursts, bank spread). The top of that list is the
   candidate queue. On the top 20, 12 are in a known ring: precision 0.60.
2. **Investigate a candidate.** The engine runs the graph tools in a fixed order and turns
   the results into plain facts: profile, two-hop money flow over one-off links in time
   order, time-ordered cycles back to the seed, convergence of the seed's recipients on a
   collector (or of its senders on a source), earlier cases touching these accounts
   (memory), shared owners. The engine reads the shape itself (cycle, scatter-gather,
   gather-scatter, stack, fan-in, fan-out).
3. **Decide.** A local LLM reads the facts and answers in JSON: is this a ring, which
   pattern, and a two-sentence analysis. When the engine measured a cycle, a collector or a
   hub directly, that reading wins over the model's.
4. **Build the case.** Code picks the members (seed, one-off neighbours, pass-through
   chains, collectors, cycle paths), assigns roles (source, distributor, intermediary,
   collector), lists the findings it can back with numbers, and sums their weights into a
   score and a tier: freeze, hold_txn, step_up or monitor. A case overlapping an open one by
   Jaccard 0.5 or more updates that case instead of opening a new one.
5. **Remember.** An analyst closes a case as a confirmed ring or a false positive. The next
   investigation that touches any of its accounts sees that in its facts, and a confirmed
   link adds a `PRIOR_CONFIRMED_LINK` finding. The trace shows the `recall` step.

On the same top-20 queue the agent kept 8 cases, 7 of them real rings: precision 0.88, with
0.88 of each seed's ring members included on average. It dismissed 5 real rings, all
peripheral members whose own neighbourhood looks like an ordinary fan-in.

## Graph model

FalkorDB, one graph, 518,581 `Account` nodes and 4.49M `SENT` edges from IBM AML HI-Small.

(:Account {acct_key, risk_score, risk_codes, degree, ...})-[:SENT {ts, amount_usd, currency_paid, currency_received}]->(:Account)
(:Account)-[:AT_BANK]->(:Bank {name, country})      (:Account)-[:OWNED_BY]->(:Entity)
(:Investigation)-[:STEP {n}]->(:ToolCall {tool, args_json, result_summary, ms})
(:Investigation)-[:PRODUCED]->(:Case {case_id, pattern, score, tier, value_at_risk_usd, status})
(:Case)-[:INCLUDES {role}]->(:Account)      (:Case)-[:EVIDENCE]->(:Finding {code, text, weight})
(:Verdict {verdict, notes, ts})-[:ON]->(:Case)

Two rules shape every traversal: pairs with more than two transfers are routine and are not
followed (85% of ring transfers are one-offs, against 5% of the rest), and each hop only
follows transfers within 96 hours after the one that reached the account. Labels never enter
the graph; `eval/` is the only code that reads them.

## Setup

Needs Docker (Colima works), `uv`, Python 3.11, about 10 GB free RAM for FalkorDB and the
model together, and roughly 15 GB disk for the dataset, the graph and the model.

```bash
git clone <this repo> && cd RingTrace
uv sync
cp .env.example .env
docker compose -f infra/local/docker-compose.yml up -d

The compose file starts two services:

- falkordb on :6379 (browser on :3000).
- llm: llama.cpp server on :8080, which downloads Qwen3-8B Q4_K_M (about 5 GB) from
  Hugging Face on first start into the llm-models volume. Wait for
  curl localhost:8080
  memory; on a laptop you may prefer to run llama-server natively with the same model and
  leave the service off.

Dataset

IBM Transactions for Anti Money Laundering, HI-Small. Download from Kaggle
(https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml)
and put HI-Small_Trans.csv, HI-Small_accounts.csv and HI-Small_Patterns.txt in
data/ibm-aml/. Then:

uv run python scripts/prepare_ibm.py --raw data/ibm-aml --out data/prepared   # USD, account keys
uv run python scripts/build_features.py --data data/prepared                  # features + risk score
uv run python -m eval.ground_truth --raw data/ibm-aml --out data/labels       # labels, eval only
uv run python scripts/load_graph.py --data data/prepared                      # ~20 min

Run

uv run python scripts/investigate.py --seed 11030:80CB65920       # one account, ~45 s on a laptop CPU
uv run python scripts/investigate.py --top 20                     # the queue, plus agent-vs-baseline numbers
uv run python scripts/verdict.py <case_id> closed_tp --notes "confirmed by SAR review"
uv run streamlit run app/console.py                               # analyst console

The console shows the candidate queue, lets you investigate an account, and opens each case
with its ring diagram (members coloured by role), findings, the investigation trace and the
verdict buttons.

Memory demo

uv run python scripts/investigate.py --seed 11030:80CB65920   # scatter-gather ring, case A
uv run python scripts/verdict.py <case A> closed_tp --notes "scatter-gather ring confirmed"
uv run python scripts/investigate.py --seed 11852:80C8BC8F0   # the ring's collector

The second investigation's trace has a recall step naming case A, its facts carry a
Memory: line, and the new case has a PRIOR_CONFIRMED_LINK finding.

Using a hosted model instead

Any OpenAI-compatible endpoint works. Set in .env:

LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=...

Why a local 8B model

Judging whether a neighbourhood is a ring from raw query output is beyond small models. So
the split is: code gathers evidence and states facts, code builds the case, and the model
only decides ring or not, names the pattern, and explains. In a bake-off on seven candidates
(Qwen2.5-1.5B, Qwen3-4B, LFM2-2.6B and 8B-A1B, Granite 4.0 micro and tiny, Qwen3-8B), only
Qwen3-8B called all seven real rings; the others called three or four. Qwen3's thinking mode
is disabled and the answer is constrained to a JSON object.

Known limits

- Peripheral ring members (an account that receives from seven ring members and sends to one)
  look like ordinary fan-ins from their own vantage point and get dismissed. Starting from
  the ring's hub finds the ring.
- HI-Small contains a handful of single transfers over $100M, so value at risk on a case can
  be dominated by one dataset artefact.
- About 45 s per investigation on a 2019 laptop CPU; almost all of it is the model.

Layout

core/     settings, FalkorDB and LLM clients, reason codes
engine/   features, candidate scoring, graph queries, evidence gathering, case building
agent/    prompt, decision schema, the investigate loop
Layout

core/     settings, FalkorDB and LLM clients, reason codes
engine/   features, candidate scoring, graph queries, evidence gathering, case building
agent/    prompt, decision schema, the investigate loop
eval/     ground truth parsing and agent-vs-baseline metrics (the only code that sees labels)
scripts/  prepare, build features, load graph, investigate, verdict
app/      Streamlit console
infra/    docker compose

Dataset citation

Altman, E., Blanuša, J., von Niederhäusern, L., Egressy, B., Anghel, A., Atasu, K.
"Realistic Synthetic Financial Transactions for Anti-Money Laundering Models." NeurIPS 2023
Datasets and Benchmarks. Data: IBM Transactions for Anti Money Laundering (AML) on Kaggle,
licence CDLA-Sharing-1.0.
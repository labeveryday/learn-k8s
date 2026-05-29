# Lab 03 — Agentic RAG: retrieval as a tool the agent decides to call

**Goal:** turn the hardcoded retrieval step from lab-02 into a **tool** an agent chooses to
invoke — so the agent retrieves only when the question needs it, instead of always. You'll
do it both ways the repo cares about: as a Strands `@tool` for your `agents/` framework, and
as an **MCP tool** a kagent `Agent` calls via `RemoteMCPServer` (Phase 07). By the end you
can explain why making retrieval a *harness tool* (the agent decides) beats a fixed
always-retrieve pipeline.

**Time:** ~45 min · **Cost:** free (local kind)

## The problem (why this exists)

lab-02's pipeline retrieves on *every* call. That's wasteful and sometimes wrong. Ask "what's
the Osaka region code?" → retrieve, good. Ask "what's 2+2?" or "rewrite this sentence" →
retrieving the runbook is pure overhead, adds latency and tokens, and can inject irrelevant
context that *degrades* the answer (the lab-02 noise problem). A fixed pipeline can't tell the
difference because it has no decision step — it always runs all five stages. What you want is
for *retrieval to be optional*: the system looks at the question and decides whether to consult
the store at all. The thing that makes a decision and then acts is an **agent** (Phase 07
lab-03: "the line between a chatbot and an agent is the ability to take an action"). So
retrieval should be one of the agent's **tools**.

## What it replaces / why the naive way fails

| | Fixed pipeline (lab-02) | Agentic RAG (this lab) |
|---|---|---|
| When it retrieves | always, every call | only when the agent decides it's needed |
| Who decides | you, in code | the model, then the runtime executes |
| Cost on a general question | wasted embed + search + context tokens | zero — agent skips the tool |
| Multi-step questions | one shot, one retrieval | can retrieve, read, retrieve again, then answer |
| Where retrieval lives | inline in your script | a reusable **tool** (Strands `@tool` / MCP) |

The naive always-retrieve pipeline isn't *wrong* — it's the right default for a pure Q&A bot
over one corpus. It fails the moment the workload is mixed (some questions need the corpus,
some don't) or multi-hop (answer requires two lookups). Making retrieval a tool is what Phase
07 lab-05 called a **harness tool**: a capability the agent reaches for, gated and observable,
not a hardcoded branch.

## Under the hood (MIT hat): the agent decides, the runtime gates, the tool executes

The mechanism is the **MCP indirection** from Phase 07 lab-03, now with retrieval as the tool.
The agent's loop emits an *intent* ("call `retrieve` with this query"); the runtime checks the
`toolNames` allow-list; the tool runs the lab-02 embed→search elsewhere; the result re-enters
the loop as context. The model never touches Qdrant — it only asks.

```
  question ─► agent loop (LLM)
                  │  "is this an Acme fact? → call retrieve"   ◄── the DECISION (new vs lab-02)
                  ▼
        retrieve(query, top_k)                ── allow-listed tool
                  │  [1] embed query (vllm-embed)
                  │  [2] top-k search (Qdrant)   ← the lab-02 retrieval, now behind a tool
                  ▼
        top-k chunk texts ──► back into the loop as context
                  │
                  ▼
        agent generates a grounded answer        (or, if no tool call, answers directly)
```

The same retrieval logic from lab-02 lives in exactly one place — the tool — and two different
harnesses (Strands, kagent) call it the same way. That's the lab-02 → lab-03 move: **retrieval
went from a step you run to a capability the agent invokes.**

> Note on `agent.tool.memory` (Strands): Strands ships a built-in `memory`/`retrieve` tool, but
> it targets a **Bedrock** knowledge base. Your store is self-hosted vLLM + Qdrant, so you wrap
> *your* retrieval in a custom `@tool` — the same `@tool` pattern as `agents/src/tools/code_reader.py`.
> Same idea as the Strands `knowledge_base_agent` example (classify intent → retrieve → answer),
> pointed at your own platform instead of Bedrock.

## 0. Prereqs

- labs 01–02 complete: `vllm-embed`, Qdrant `runbook` collection (4 points), chat vLLM, and
  the Phase 06 gateway all up.
- Phase 07: kagent installed, with `ModelConfig` `vllm` in the `kagent` namespace
  (`07-kagent/manifests/modelconfig-vllm.yaml`). Check: `kubectl get modelconfig vllm -n kagent`.
- The `agents/` Strands template set up (Phase 07 lab-04): `cd agents && source .venv/bin/activate`.

## Part A — Retrieval as a Strands `@tool` (your `agents/` framework)

### A1. Write the tool

Create `agents/src/tools/retrieve.py` — the lab-02 pipeline's retrieval half, wrapped in the
`@tool` decorator (mirror `agents/src/tools/code_reader.py`):

```python
# agents/src/tools/retrieve.py
import json, os, urllib.request
from strands import tool

EMBED = os.environ.get("EMBED_URL", "http://localhost:8001/v1/embeddings")
QDRANT = os.environ.get("QDRANT_URL", "http://localhost:6333")
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
COLLECTION = "runbook"

def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))

@tool
def retrieve(query: str, top_k: int = 3) -> str:
    """Search the Acme Cloud runbook for chunks relevant to a query.

    Use this whenever the user asks about Acme-specific facts (region codes, limits,
    quirks). Returns the most relevant runbook excerpts, or a note if nothing matched.

    Args:
        query: The natural-language question to search for.
        top_k: How many chunks to return (default 3).
    """
    vec = _post(EMBED, {"input": query, "model": EMBED_MODEL})["data"][0]["embedding"]
    hits = _post(f"{QDRANT}/collections/{COLLECTION}/points/search",
                 {"vector": vec, "limit": top_k, "with_payload": True})["result"]
    # Threshold on score — return only genuinely relevant chunks (the lab-02 lesson).
    good = [h for h in hits if h["score"] >= 0.4]
    if not good:
        return "No relevant runbook entries found."
    return "\n\n---\n\n".join(f"[score {h['score']:.2f}] {h['payload']['text']}" for h in good)
```

Note the **docstring**: in Strands (and MCP) the tool's description is what the model reads to
decide *when* to call it. "Use this whenever the user asks about Acme-specific facts" is the
steering. A vague docstring = an agent that retrieves at the wrong times. The docstring *is*
part of the harness.

### A2. Give it to the agent, pointed at your vLLM

Port-forward the two retrieval backends (the tool calls them directly from your laptop):

```bash
kubectl port-forward svc/vllm-embed 8001:8000 &
kubectl -n vectordb port-forward svc/qdrant 6333:6333 &
kubectl port-forward svc/vllm 8000:8000 &        # chat model for generation
```

In `agents/src/agent.py`, point the model at your vLLM (the Phase 07 lab-04 bridge) and add
the tool:

```python
from strands import Agent
from strands.models.openai import OpenAIModel
from tools.retrieve import retrieve     # your new @tool

MODEL = OpenAIModel(
    client_args={"api_key": "EMPTY", "base_url": "http://localhost:8000/v1"},
    model_id="Qwen/Qwen2.5-0.5B-Instruct",
    params={"max_tokens": 200, "temperature": 0.3},
)
agent = Agent(model=MODEL, tools=[retrieve],
              system_prompt=("Answer questions about Acme Cloud. Use the retrieve tool for "
                             "Acme-specific facts; answer general questions directly. If "
                             "retrieved chunks don't contain the answer, say you don't know."))
```

### A3. Watch the decision happen

Run it and ask two questions — one that needs the corpus, one that doesn't:

```bash
cd agents && python src/agent.py
# > What is the Osaka region code?          (should CALL retrieve → "as-07")
# > What is 17 times 3?                      (should NOT retrieve → answers directly)
```

**What to look for:** for the Acme question, the agent calls `retrieve` (you'll see the tool
invocation in the loop / your `LoggingHook`) and the answer contains `as-07`. For the math
question, **no tool call** — the agent answered from its own ability. *That* is the difference
from lab-02: the agent **decided**. Retrieval is now conditional, driven by the question, not
by your control flow.

> **Platform tie-in:** point the model `base_url` at your Phase 06 gateway
> (`http://localhost:8080/v1` + `default_headers={"Host":"rag.example.com"}`) instead of vLLM
> directly, and this agentic RAG inherits the `rag-route` token budget — your framework, your
> retrieval tool, your governed platform (Phase 07 lab-05's harness, end to end).

## Part B — Retrieval as an MCP tool a kagent Agent calls

Same tool, different harness. Here retrieval runs *in-cluster* as an MCP server, and a kagent
`Agent` (a Kubernetes object, not a laptop process) calls it via `RemoteMCPServer` + `toolNames`
— the exact idiom from Phase 07 lab-03, but the tool is now *your* `retrieve` instead of the
built-in Kubernetes tools.

### B1. Deploy the retrieval MCP server

```bash
kubectl apply -f manifests/retrieval-mcp-server.yaml
kubectl rollout status deploy/retrieval-mcp --timeout=120s
```

Open the manifest: a tiny stdlib MCP server (in a ConfigMap) exposing one tool, `retrieve`,
that runs the *same* embed→Qdrant-search as Part A — reachable as an in-cluster Service at
`:9000/mcp`, talking to `vllm-embed` and `qdrant` by CoreDNS name. It's a **teaching stub**
of the MCP message shape; the manifest header shows the `mcp`-SDK FastMCP path for a server
kagent discovers reliably in production.

**What to look for:** the pod goes Ready quickly (no model to load — it just proxies to the two
services). If it crashloops, `kubectl logs deploy/retrieval-mcp` shows the Python traceback —
read which dependency or env var failed.

### B2. Register it and grant the agent the tool

```bash
kubectl apply -f manifests/rag-agent.yaml
kubectl describe remotemcpserver retrieval -n kagent     # status should list the discovered `retrieve` tool
kubectl describe agent rag-agent -n kagent
```

`rag-agent.yaml` does two things (read both):

- a `RemoteMCPServer` named `retrieve` (well, `retrieval`) pointing at
  `http://retrieval-mcp.default.svc.cluster.local:9000/mcp` over `STREAMABLE_HTTP` — kagent
  *discovers* its tools, same as the built-in server in Phase 07 lab-03.
- an `Agent` `rag-agent` whose `toolNames` allow-lists exactly `retrieve`. Its system prompt
  tells it to retrieve for Acme facts and abstain when the chunks don't answer.

**What to look for:** under the `RemoteMCPServer` status, a discovered tool named `retrieve` —
you didn't type that, kagent asked the server. Under the `Agent` conditions, a healthy reconcile
that reflects the granted tool. If `toolNames` names a tool the server doesn't expose, the
conditions say so (kagent won't grant a phantom tool — the lab-03 lesson).

### B3. Invoke it and trace the tool call

```bash
kubectl -n kagent port-forward svc/kagent 8081:80 &
kagent invoke --agent rag-agent --task "What is the Osaka region code and the block storage volume limit?"
kubectl -n kagent logs deploy/kagent --tail=80 | grep -i -E 'tool|retrieve'
kill %1 2>/dev/null
```

**What to look for:** a log line showing the agent invoking `retrieve` (possibly twice for the
two-part question), then an answer with **`as-07`** and **`10 TiB`** — both from *your* runbook,
fetched by *your* in-cluster tool, by a cluster-managed agent with no laptop process. The MCP
indirection from Phase 07 lab-03 is identical; only the tool changed.

## Break it, then read the error (Kelsey lens): always-retrieve vs decide-to-retrieve

Make the agent retrieve when it shouldn't, and read the damage. In Part A's system prompt,
replace the conditional instruction with an *always* one and re-run the math question:

```python
system_prompt="ALWAYS call the retrieve tool before answering ANY question, then answer."
# > What is 17 times 3?
```

**Read what happens.** The agent now calls `retrieve("17 times 3")`, which returns
*"No relevant runbook entries found."* (your score threshold did its job) or, worse without the
threshold, returns the least-irrelevant runbook chunk — a region-codes paragraph — which the
model may then try to *use*, producing a confused or hedged answer to a trivial question. You
spent an embed + a search + context tokens to make a simple answer *worse*. That's the cost of
deleting the decision: **always-retrieve reintroduces the lab-02 noise problem on every
off-topic query.** The fix isn't a bigger model — it's restoring the agent's discretion (the
conditional prompt + a good tool docstring) and the score threshold. Same Kelsey reflex: the
agent didn't get dumber, you removed the control (the *decision*) that kept retrieval relevant.

> **Optional — evaluate retrieval quality (ties to Phase 11).** "Did it retrieve the right
> chunk?" and "did it retrieve *when it should*?" are measurable. Strands **Evals** has a
> `ToolSelectionAccuracyEvaluator` (did the agent pick the right tool for the input) — point it at
> this agent to score *retrieve-vs-skip* decisions, and add a retrieval-precision check (was the
> gold chunk in the top-k). That turns "feels better" into a number — the sensor side of the
> harness, and the on-ramp to Phase 11.

## Checkpoint — you can now explain…

1. **Why retrieval-as-a-tool beats an always-retrieve pipeline.** The agent *decides* whether
   the question needs the corpus, so general questions skip the cost and avoid noise, and
   multi-hop questions can retrieve more than once. A fixed pipeline has no decision step.
2. **The MCP indirection for retrieval.** Model emits intent → runtime checks `toolNames` →
   the tool runs embed+search elsewhere → result re-enters the loop. The model never touches
   Qdrant; it only asks. Same boundary as any kagent tool.
3. **Why the tool's docstring/description is part of the harness.** It's what the model reads to
   decide *when* to call retrieve. Vague description → retrieval at the wrong times. The
   description steers the decision.
4. **One tool, two harnesses.** The same embed→search retrieval is a Strands `@tool` (in-process,
   for your `agents/` framework) and an MCP tool (in-cluster, for a kagent Agent). The OpenAI
   model bridge and the MCP tool bridge (Phase 07 lab-04) make it the same idea twice.

You can now:
- [ ] Wrap retrieval as a Strands `@tool` and give it to an agent that decides when to use it.
- [ ] Expose retrieval as an MCP tool and let a kagent `Agent` call it via `RemoteMCPServer`.
- [ ] Explain the cost of always-retrieve and how the agent's decision + a score threshold fix
  it.
- [ ] Name where you'd measure retrieval quality (Strands Evals / Phase 11).

## What you proved across Phase 10

You built RAG from pieces you already owned: a second vLLM for **embeddings** + a vector store
(lab-01), the **retrieve→stuff→generate** pipeline routed through your Phase 06 gateway
(lab-02), and finally retrieval as an **agent tool** in both your Strands framework and kagent
(lab-03). RAG isn't a new platform — it's your platform (vLLM, the gateway, kagent, MCP) plus
one new workload, the vector store. That's "give the model *your* data at query time, governed
by *your* platform," end to end.

## Next

→ **Phase 11**: evaluation — measure whether retrieval found the right chunks and whether the
agent retrieved when it should, turning the "Break it" intuitions of this phase into the
sensors that keep a RAG system honest. (On real infra, Phase 09's LKE NodeBalancer + Block
Storage CSI back the Qdrant PVC, and the LKE observability add-on traces these RAG calls.)

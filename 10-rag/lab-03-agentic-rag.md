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
the tool. Here we point straight at vLLM on `8000` to keep Part A simple; the optional
**Platform tie-in** below shows how to route through the Phase 06 gateway instead (and inherit
the `rag-route` token budget you built in lab-02):

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

**What to look for:** for the Acme question, the agent calls `retrieve` — Strands prints a
tool-use line in the loop (your `LoggingHook` shows it too), something like:

```
[tool] retrieve(query='Osaka region code', top_k=3)
```

and the answer contains `as-07`. For the math question, **no such line** — the agent answered
from its own ability. That presence-vs-absence of the `retrieve` line is how you confirm the
decision. *That* is the difference from lab-02: the agent **decided**. Retrieval is now
conditional, driven by the question, not by your control flow.

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

`manifests/retrieval-mcp-server.yaml` is three objects in one file: a **ConfigMap** holding the
server's Python source, a **Deployment** that runs it, and a **Service** that fronts it. The
ConfigMap keeps the lab to one `kubectl apply` and lets you *read* the MCP wire format; in a
real build you'd bake the code into an image. The Python is a **teaching stub** — it hand-rolls
the three MCP JSON-RPC methods (`initialize` / `tools/list` / `tools/call`) over plain HTTP POST
so you can see exactly what an MCP server answers. The retrieval logic inside it is the lab-02
pipeline verbatim: embed the query, search Qdrant, return the chunk texts.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: retrieval-mcp-src        # mounted into the pod at /src; the Deployment runs /src/server.py
  namespace: default
data:
  server.py: |
    import json, os, urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    EMBED_URL = os.environ["EMBED_URL"]        # from the Deployment env below; fail-fast if unset
    EMBED_MODEL = os.environ["EMBED_MODEL"]
    QDRANT_URL = os.environ["QDRANT_URL"]
    COLLECTION = os.environ.get("COLLECTION", "runbook")

    def _http(method, url, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read() or "{}")

    def retrieve(query, top_k=3):              # the SAME embed -> top-k search as Part A's @tool
        vec = _http("POST", EMBED_URL, {"input": query, "model": EMBED_MODEL})["data"][0]["embedding"]
        hits = _http("POST", f"{QDRANT_URL}/collections/{COLLECTION}/points/search",
                     {"vector": vec, "limit": top_k, "with_payload": True})["result"]
        return [h["payload"]["text"] for h in hits]

    TOOLS = [{
        "name": "retrieve",                    # THIS is the tool name kagent discovers + allow-lists in B2
        "description": "Search the Acme Cloud runbook for chunks relevant to a query.",
        "inputSchema": {"type": "object",      # the model reads this schema to know the args
                        "properties": {"query": {"type": "string"},
                                       "top_k": {"type": "integer", "default": 3}},
                        "required": ["query"]},
    }]

    class H(BaseHTTPRequestHandler):
        def _send(self, obj):
            b = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        def do_POST(self):                     # the three MCP methods, hand-rolled so you can read them
            n = int(self.headers.get("Content-Length", 0))
            msg = json.loads(self.rfile.read(n) or "{}")
            mid, method = msg.get("id"), msg.get("method")
            if method == "initialize":         # handshake: protocol version + capabilities
                self._send({"jsonrpc": "2.0", "id": mid, "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "retrieval", "version": "0.1.0"},
                    "capabilities": {"tools": {}}}})
            elif method == "tools/list":       # discovery: kagent calls this to learn `retrieve` exists
                self._send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
            elif method == "tools/call":       # execution: the agent's tool-use lands here
                args = msg["params"]["arguments"]
                texts = retrieve(args["query"], int(args.get("top_k", 3)))
                self._send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "\n\n---\n\n".join(texts)}]}})
            else:
                self._send({"jsonrpc": "2.0", "id": mid, "result": {}})
        def log_message(self, *a):  # quiet
            pass

    ThreadingHTTPServer(("0.0.0.0", 9000), H).serve_forever()   # listens on :9000 — B2 must point here
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: retrieval-mcp
  namespace: default               # NOTE: default ns — the Agent lives in `kagent`, so B2 uses a cross-ns DNS name
  labels:
    app: retrieval-mcp
spec:
  replicas: 1
  selector:
    matchLabels:
      app: retrieval-mcp           # must equal template.labels below (the lab-03 selector trap)
  template:
    metadata:
      labels:
        app: retrieval-mcp
    spec:
      containers:
        - name: mcp
          image: python:3.12-slim  # no app deps — the stub is pure stdlib; just needs a Python
          command: ["python", "/src/server.py"]   # runs the ConfigMap file mounted at /src
          env:                     # these wire the tool to YOUR platform by CoreDNS name (no IPs)
            - name: EMBED_URL
              value: "http://vllm-embed.default.svc.cluster.local:8000/v1/embeddings"
            - name: EMBED_MODEL
              value: "BAAI/bge-small-en-v1.5"        # must match the model lab-01 serves on vllm-embed
            - name: QDRANT_URL
              value: "http://qdrant.vectordb.svc.cluster.local:6333"   # Qdrant is in the vectordb ns
            - name: COLLECTION
              value: "runbook"     # the lab-01/02 collection (4 points)
          ports:
            - containerPort: 9000
          volumeMounts:
            - { name: src, mountPath: /src }         # ConfigMap -> /src, so /src/server.py exists
      volumes:
        - name: src
          configMap:
            name: retrieval-mcp-src                  # the ConfigMap above, by name
---
apiVersion: v1
kind: Service
metadata:
  name: retrieval-mcp
  namespace: default
  labels:
    app: retrieval-mcp
spec:
  selector:
    app: retrieval-mcp             # routes to the Deployment's pods by this label
  ports:
    - name: mcp
      port: 9000                   # the Service port the RemoteMCPServer URL in B2 dials
      targetPort: 9000
```

Two gotchas worth pre-loading: **(1)** the Deployment is in `default` but the kagent `Agent` is
in `kagent`, so B2's `RemoteMCPServer` URL is a *cross-namespace* DNS name
(`retrieval-mcp.default.svc.cluster.local`) — drop the `.default` and discovery resolves to the
wrong namespace. **(2)** the tool name the model uses is `retrieve` (the `TOOLS[0].name` in the
ConfigMap), but the *server* object you register in B2 is named `retrieval` — two different
names, see B2.

```bash
kubectl apply -f manifests/retrieval-mcp-server.yaml   # creates the ConfigMap + Deployment + Service
kubectl rollout status deploy/retrieval-mcp --timeout=120s   # blocks until the pod is Ready
```

This is a **teaching stub** of the MCP message shape, not a spec-complete transport — it lacks
SSE event framing and `Mcp-Session-Id` headers, so a strict kagent build may not finish tool
discovery against it (the manifest header is explicit about this). For a server kagent discovers
reliably in production, bake a real one into an image using the `mcp` SDK's **FastMCP**
(`from mcp.server.fastmcp import FastMCP`; `@mcp.tool()`; `mcp.run(transport="streamable-http")`).
FastMCP is the helper in the official MCP Python SDK for building MCP servers; we use the stdlib
stub here to stay dependency-free.

**What to look for:** the pod goes Ready quickly (no model to load — it just proxies to the two
services). If it crashloops, `kubectl logs deploy/retrieval-mcp` shows the Python traceback —
read which dependency or env var failed.

### B2. Register it and grant the agent the tool

`manifests/rag-agent.yaml` is two CRDs — both in the `kagent` namespace, both `kagent.dev/v1alpha2`
(the exact apiVersion from Phase 07). The first tells kagent *where your tool lives*; the second
is the agent that *may call it*:

```yaml
apiVersion: kagent.dev/v1alpha2
kind: RemoteMCPServer          # registers an external MCP server as a discoverable tool catalog
metadata:
  name: retrieval              # the SERVER object's name (not the tool's) — describe targets this
  namespace: kagent
spec:
  description: "Retrieval over the Acme Cloud runbook vector store (embed + Qdrant search)."
  url: "http://retrieval-mcp.default.svc.cluster.local:9000/mcp"   # cross-ns DNS to B1's Service + /mcp path
  protocol: STREAMABLE_HTTP    # how kagent talks to it; the stub answers JSON-RPC over this
---
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: rag-agent
  namespace: kagent
spec:
  description: "Answers questions about Acme Cloud, retrieving from the runbook when needed."
  type: Declarative            # config-only agent — no custom code, defined entirely by this spec
  declarative:
    modelConfig: vllm          # the ModelConfig from Phase 07 (07-kagent/manifests/modelconfig-vllm.yaml)
    systemMessage: |           # THE decision steering — tells the model when to reach for the tool
      You answer questions about Acme Cloud. You have a `retrieve` tool that searches the
      Acme Cloud runbook. Use it whenever the question is about Acme-specific facts
      (region codes, limits, quirks). If retrieved chunks do not contain the answer, say
      you don't know rather than guessing. For general knowledge, answer directly without
      retrieving.
    tools:
      - type: McpServer        # this tool comes from an MCP server (vs a built-in kagent tool)
        mcpServer:
          apiGroup: kagent.dev
          kind: RemoteMCPServer
          name: retrieval      # points at the RemoteMCPServer object above (the server name)
          toolNames:           # the ALLOW-LIST: only these tools are exposed to the model
            - retrieve         # the TOOL name (TOOLS[0].name from B1) — not the server name
```

Read the two-name distinction carefully, because it's the #1 trip-up here: the **server** object
is `retrieval`; the **tool** it exposes is `retrieve`. The `describe` command below targets the
server (`retrieval`); the `toolNames` allow-list names the tool (`retrieve`). If `toolNames` lists
a name the server doesn't actually expose, kagent won't grant a phantom tool — it'll say so in the
Agent's conditions (the Phase 07 lab-03 lesson). The `systemMessage` is the same decision steering
as Part A's docstring + system prompt: it's what makes the agent *decide* to retrieve, instead of
always retrieving.

```bash
kubectl apply -f manifests/rag-agent.yaml                # creates the RemoteMCPServer + Agent
kubectl describe remotemcpserver retrieval -n kagent     # status should list the discovered `retrieve` tool
kubectl describe agent rag-agent -n kagent               # conditions should show a healthy reconcile + the granted tool
```

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
two-part question) — roughly:

```
... calling tool retrieve args={"query":"Osaka region code","top_k":3} ...
```

then an answer with **`as-07`** and **`10 TiB`** — both from *your* runbook, fetched by *your*
in-cluster tool, by a cluster-managed agent with no laptop process. The MCP indirection from
Phase 07 lab-03 is identical; only the tool changed.

If the grep comes back **empty**, that's either the agent deciding not to call the tool *or* a
broken wiring — distinguish them with `kubectl describe agent rag-agent -n kagent` and read the
conditions (an unhealthy reconcile or an ungranted tool shows up there).

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

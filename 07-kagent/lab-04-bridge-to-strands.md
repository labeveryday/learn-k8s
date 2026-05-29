# Lab 04 — Bridge: your Strands agents on your own platform

**Goal:** connect the agent framework you *already own* (`agents/`, the Strands template)
to the platform you just built — point it at your in-cluster vLLM, see MCP is the same
protocol on both sides, and decide *when* an agent should be a Strands process vs a kagent
object. By the end the two halves of "agents" in this repo stop being separate worlds.

**Time:** ~30 min · **Cost:** free (local kind) — no hosted API key

## The problem (why this exists)

You've now seen an agent two ways: as a **kagent object** (labs 01–03) and, in this repo's
`agents/` directory, as a **Strands process** — your real framework, with multi-model
support, an Agent Hub, Gemini media tools, and AgentCore deployment. The catch: `agents/`
has only ever talked to *hosted* APIs (Anthropic, OpenAI, Bedrock, Gemini). Meanwhile you
spent Phases 04–06 standing up your own model on your own cluster. Those two facts have
never met. This lab makes your framework run on your platform — the "sovereign agentic
stack" the Phase 07 README promised, actually wired together.

## What it bridges (two operational models, one idea)

kagent and Strands aren't competitors; they're two *operational models* for the same thing
(LLM + loop + tools). The lab's real lesson is knowing which to reach for:

| | Strands process (`agents/`) | kagent object (labs 01–03) |
|---|---|---|
| What it is | a Python program you run | a CR a controller reconciles |
| Lives as | a process (laptop, AgentCore) | a Pod the cluster keeps alive |
| Iterate / debug | edit + rerun, rich SDK, `print()` | `kubectl apply`, read conditions/logs |
| Tools | in-process functions **+ MCP** | MCP via `RemoteMCPServer` |
| Best for | dev, content pipelines, fast iteration, AgentCore | always-on, multi-tenant, observable, RBAC'd |

Same agent, different home. You pick based on *operational* needs, not on what the agent
*is*.

## Under the hood (MIT hat): the two bridges that make this trivial

The connection is almost free because two standards do the work:

1. **The model bridge is the OpenAI protocol.** vLLM serves `/v1` in OpenAI format
   (Phase 06). Strands' `OpenAIModel` takes `client_args` that flow straight to the OpenAI
   Python client — including `base_url`. kagent's `ModelConfig` takes `openAI.baseUrl`. So
   *both* front-ends point at the **same** endpoint with the **same** field; only the
   syntax differs. "Self-hosted" isn't a special mode — it's a base URL.
2. **The tool bridge is MCP.** Your `agents/examples/mcp_docs_agent.py` already loads tools
   from MCP servers over stdio; kagent's `RemoteMCPServer` is the same protocol over HTTP.
   The same MCP tool server can feed both.

```
   Strands process (agents/)          kagent Agent (Pod)
        │  OpenAIModel(base_url=…)          │  ModelConfig.openAI.baseUrl
        └──────────────┬───────────────────┘
                       ▼
            vLLM  /v1/chat/completions   ◄── one OpenAI endpoint, two clients
                       ▲
        both speak ────┘  MCP  (stdio in agents/, RemoteMCPServer in kagent)
```

Below all of it: the same Phase 03 stack — Service ClusterIP, kube-proxy, CoreDNS.

## 0. Prereqs

- The cluster from Phases 05–07 with **vLLM running** (Phase 06: Service `vllm` in
  `default`, serving `Qwen/Qwen2.5-0.5B-Instruct`). Check: `kubectl get svc vllm`.
- The `agents/` template set up: `cd agents && python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`.

## 1. Point your Strands template at your own vLLM

Expose the in-cluster model to your laptop, where the Strands process runs:

```bash
kubectl port-forward svc/vllm 8000:8000 &     # vLLM's OpenAI API now on localhost:8000
```

Now aim the template at it. Open `agents/src/agent.py` and replace the `MODEL = …` line
(the file's designated "Model selection" customization point) with a direct `OpenAIModel`
— your `models.py` helper `openai_model()` is the *one* provider that doesn't expose
`base_url`, so construct it directly (or add a `vllm_model()` helper mirroring the others):

```python
from strands.models.openai import OpenAIModel

MODEL = OpenAIModel(
    client_args={
        "api_key": "EMPTY",                       # vLLM ignores it, but the OpenAI client
                                                  #   requires a non-empty string
        "base_url": "http://localhost:8000/v1",   # YOUR vLLM, not api.openai.com
    },
    model_id="Qwen/Qwen2.5-0.5B-Instruct",        # the model your vLLM actually serves
    params={"max_tokens": 256, "temperature": 0.7},
)
```

Run it and ask something:

```bash
cd agents
python src/agent.py
# > In one sentence, what is a Kubernetes Service?
```

**What to look for:** a normal answer — but no API key was used and no token left your
network. The exact same Strands framework, Hub, and hooks you'd use against Anthropic are
now driving *your* model. That's the bridge: your open-source agent, your self-hosted LLM.

> **Platform tie-in (the real payoff).** Point `base_url` at your Phase 06 **gateway**
> (`http://localhost:8080/v1` after `kubectl -n kgateway-system port-forward svc/http
> 8080:80`) instead of vLLM directly, and your Strands agent inherits the gateway's token
> limits and prompt guards *for free* — it doesn't even know it's behind one. The route
> keys on `Host: llm.example.com`, so set it via `client_args["default_headers"] = {"Host":
> "llm.example.com"}`. Your framework is now a *governed* client of your platform.

## 2. MCP is the same protocol on both sides

Open `agents/examples/mcp_docs_agent.py`. It builds an `MCPClient`, calls
`list_tools_sync()`, and hands those tools to the `Agent` — exactly the role
`RemoteMCPServer` + `toolNames` played for kagent in lab-03. Run it to feel the symmetry:

```bash
python examples/mcp_docs_agent.py
# (it loads the Strands + AgentCore docs MCP servers over stdio)
```

**What to look for:** the agent gains tools from an *external* MCP server it didn't define
— the same decoupling lab-03 taught, just stdio instead of `RemoteMCPServer`. One protocol,
two consumers.

> **Optional, advanced:** the built-in `kagent-tool-server` is an MCP server too. Port-
> forward it and connect your *Strands* agent to it over streamable-HTTP MCP (the HTTP
> analog of the stdio client in `mcp_docs_agent.py` — see the Strands "MCP Tools" docs).
> Then your laptop Strands agent and your in-cluster kagent agents call the **same tool
> server**. That's the bridge at the tool layer, not just the model layer.

## 3. The decision: process or object?

You can now run an agent both ways against the same model and tools. Use the table at the
top to choose. The honest rule of thumb:

- **Reach for Strands (`agents/`)** while you're *building* — iterating on prompts/tools,
  generating content (your Gemini media tools), or shipping to **AgentCore**. The SDK and
  Hub make the inner loop fast.
- **Reach for kagent** when the agent must *run unattended* — always-on, multi-tenant,
  observable, restarted by a controller, RBAC-scoped tool access. The same agent, promoted
  to infrastructure.

## 4. (Optional) Run the Strands agent *on* the cluster — the DIY kagent

To see what kagent automates, containerize the Strands agent and run it as a plain
`Deployment` on LKE (Phase 09): a `Dockerfile` over `agents/`, env for `base_url`, a
Service. It works — but *you* now own the restart policy, the status, the scaling, the tool
RBAC. That's precisely the boilerplate kagent's `Agent`/`ModelConfig`/`RemoteMCPServer`
CRDs absorb. Doing it by hand once is the best argument for why kagent exists.

## Break it, then read the error (Kelsey lens)

With the Strands agent pointed at vLLM (Step 1), kill the port-forward and ask again:

```bash
kill %1 2>/dev/null          # stop the vLLM port-forward
# back in the agent: > what is a Pod?
```

**Read the error, that's the lesson.** The Strands process raises a *connection refused* /
APIConnectionError to `localhost:8000` — the framework is healthy, the **model endpoint**
is gone. This is the *exact same* "control plane fine, data plane unreachable" failure you
read as kagent conditions+logs in lab-02 — but now from the **client** side, as a Python
traceback. The skill transfers: identify *which boundary* failed. (If you used the gateway
tie-in, hammer it instead and watch your own agent get `429`s from your own token limit —
your framework, governed by your platform.) Restart the port-forward to recover.

## Checkpoint — you can now explain…

1. **Why connecting your framework to your platform was nearly free.** Two standards: the
   OpenAI protocol (one `base_url` for both Strands and kagent) and MCP (one tool protocol
   for both). "Self-hosted" is a base URL, not a special mode.
2. **When to run an agent as a Strands process vs a kagent object.** Process for building,
   content, AgentCore, fast iteration; object for always-on, observable, multi-tenant,
   RBAC'd. Same agent, operational choice.
3. **What you'd own if you ran the Strands agent on k8s by hand.** Restart, status,
   scaling, tool RBAC — exactly the boilerplate kagent's CRDs absorb.

You can now:
- [ ] Point any Strands `OpenAIModel` at a self-hosted vLLM via `client_args` `base_url`.
- [ ] Explain MCP as the shared tool layer between `agents/` and kagent.
- [ ] Choose process-vs-object for a given agent and justify it.

## What you proved across Phase 07

You ran an agent as a Kubernetes object (labs 01–03) *and* connected your own Strands
framework to the same model and tool layer (this lab) — proving "agent" is one idea with
two operational homes, both riding the platform you built: your model (Phase 06), your
gateway's policy (Phase 06), your cluster's machinery (Phase 03). That's a self-hosted
agentic platform on Akamai, end to end.

## Next

→ `lab-05-agent-harness.md`: now that the agent runs on your platform, engineer the
*harness* around it — the guides and sensors that turn "it runs" into "it's reliable," with
your gateway acting as a shared harness layer.

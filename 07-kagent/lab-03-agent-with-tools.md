# Lab 03 — Tools via MCP: the moment a chatbot becomes an agent

**Goal:** grant your agent real tools from the built-in `RemoteMCPServer`, then trace what
actually happens when the agent *calls* one — the agent's loop decides to use a tool,
kagent routes the call to the MCP server, the result flows back into the loop. By the end
you can explain the **MCP indirection** and why tool access being a Kubernetes object beats
a function call in a script.

**Time:** ~30 min · **Cost:** free (local kind)

## The problem (why this exists)

The agent from lab-02 can only *talk*. Ask it "which pods are not Running?" and it will
guess — it has no way to look. An LLM that can only emit text is a chatbot. The line
between a chatbot and an **agent** is the ability to take an action in the world, read the
result, and reason over it. To cross that line your agent needs **tools**.

But how do you give a *cluster object* a tool? In `agents/`, a tool is a Python function
you import and register in the same process. There's no process here to import into — the
agent runs as a Pod the controller manages. The tool has to arrive a different way.

## What it replaces, and why the old way was insufficient

In `agents/`, tools are **in-process functions**. That's simple but it welds the tool to
the agent: every agent re-implements (or copy-pastes) the same tools, the tool runs with
the agent's privileges, and you can't reuse a tool across agents without sharing code.

kagent uses **MCP (Model Context Protocol)** instead — the standard the README's supporting
cast called out. Tools live in a separate **MCP server**; the agent calls them over a
protocol. You register that server as a `RemoteMCPServer` object, and the controller
*discovers* what tools it offers. The win is **decoupling**:

| | in-process tool (`agents/`) | MCP tool (kagent) |
|---|---|---|
| Where the tool runs | inside the agent process | in a separate MCP server |
| Reuse across agents | copy the code | reference one `RemoteMCPServer` |
| Who can call it | whoever has the code | controlled by `toolNames` allow-list per agent |
| Discovery | you maintain a list | controller discovers + reports in status |

## 1. Use the built-in Kubernetes tool server (you didn't author it)

The Helm install in lab-01 already created a `RemoteMCPServer` named `kagent-tool-server`
in the `kagent` namespace — the built-in Kubernetes tools. Confirm it and read what it
exposes:

```bash
kubectl get remotemcpserver -n kagent
kubectl describe remotemcpserver kagent-tool-server -n kagent   # status lists the discovered tools
```

**What to look for:** under `status`, a list of **discovered tools**. You did not type that
list — the controller connected to the MCP server, asked it "what tools do you offer," and
recorded the answer. That discovery step *is* MCP doing its job: the catalog is owned by the
server, not by you. Note the exact tool names; you'll allow-list a subset next.

> Want a *custom* server? Model it the same way: a `RemoteMCPServer` (kagent.dev/v1alpha2)
> pointing at any MCP endpoint over `STREAMABLE_HTTP`/`SSE`, or a kmcp `MCPServer` that
> kagent runs in-cluster for you. The built-in server is the simplest correct path, so
> this lab uses it. (The old `ToolServer` kind was removed in v1alpha2 — if a blog tells
> you to create a `ToolServer`, it's stale.)

## 2. Grant a subset of tools to the agent

```bash
kubectl apply -f manifests/agent-with-tools.yaml
```

Open the manifest. It's `k8s-helper` from lab-02 plus a `tools` block. Read the reference
shape, because it's the heart of the lab:

```yaml
    tools:
      - type: McpServer
        mcpServer:
          apiGroup: kagent.dev
          kind: RemoteMCPServer
          name: kagent-tool-server
          toolNames:
            - list_pods
            - get_pod
            - list_events
```

Three things this says:

- `type: McpServer` — this tool comes from an MCP server (not an in-process function).
- `mcpServer` — a **structured reference** to a cluster object: kind `RemoteMCPServer`,
  named `kagent-tool-server`, in apiGroup `kagent.dev`. The agent points *at* the catalog;
  it doesn't contain the tools.
- `toolNames` — the **allow-list**. The server may expose dozens of tools; this agent gets
  exactly these three. Changing the agent's capabilities is now a `kubectl apply`, not a
  rebuild.

> If `kubectl describe remotemcpserver kagent-tool-server -n kagent` showed different tool
> names than `list_pods` / `get_pod` / `list_events`, edit the `toolNames` in
> `agent-with-tools.yaml` to match before applying — kagent only grants tools that exist.

```bash
kubectl describe agent k8s-helper -n kagent
```

**What to look for:** the Agent re-reconciles and its status reflects the granted tools. If
you listed a tool the server doesn't expose, the conditions will tell you — kagent won't
silently grant a phantom tool. (Reconcile plane again — the lab-02 skill, reused.)

## 3. Ask it to *do* something it couldn't before

```bash
kubectl -n kagent port-forward svc/kagent 8081:80 &
kagent invoke --agent k8s-helper \
  --task "List the pods in the default namespace and tell me which are not Running."
kill %1 2>/dev/null
```

Same agent that *guessed* in lab-02 now *checks*. Watch the tool call happen:

```bash
kubectl -n kagent logs deploy/kagent --tail=80 | grep -i tool
```

**What to look for:** a log line showing the agent invoking `list_pods` (and possibly
`get_pod`), then the answer reflecting **real cluster state** rather than a guess. The model
didn't "know" your pods — it decided to call a tool, got facts back, and reasoned over them.

## Under the hood (MIT hat): the MCP indirection

This is the mechanism worth burning in. When the agent's LLM loop runs your task, here's the
data path — and notice the **indirection** at every hop, because that indirection is exactly
what decoupling buys you:

```
  your task ──► kagent controller ──► Agent runtime Pod (kagent-adk: the LLM loop)
                                              │
                  ┌───────────────────────────┘
                  │ 1. loop asks the model; model replies "call list_pods"
                  │ 2. runtime issues an MCP tool call (allowed by toolNames)
                  ▼
        RemoteMCPServer "kagent-tool-server"  ──► runs list_pods against the K8s API
                  │
                  │ 3. tool result (the pod list) returns over MCP
                  ▼
        back into the LLM loop  ──► model reasons over the result ──► final answer
```

Walk the indirection:

1. **The model never touches your cluster.** It only emits an *intent* — "I want to call
   `list_pods`." It's text, not access. The runtime decides whether to honor it.
2. **`toolNames` is the gate.** The runtime will only forward calls to tools this agent was
   allow-listed for. Ask for a tool that isn't granted and the call is refused — not because
   the model is well-behaved, but because the *object* says so. That's why tool access being
   a CRD field matters: it's enforceable policy, not a code convention.
3. **The tool runs in the MCP server, not the agent.** The actual `list_pods` work — hitting
   the Kubernetes API — happens in `kagent-tool-server`, with *its own* ServiceAccount and
   RBAC. The agent never gets cluster credentials; it gets *results*. Below this, the tool's
   call to the API server is plain authenticated HTTP — the Phase 03 control plane you
   already know.
4. **The result re-enters the loop.** The pod list goes back to the model as context, and
   the model produces the final sentence. The "loop" is: ask model → maybe call tool →
   feed result back → ask model again, until it answers.

That round trip — model proposes, runtime gates, MCP server executes, result returns — is
*the* difference between a chatbot and an agent. And every hop is a clean boundary you can
secure, swap, or share independently.

## 4. Why CRDs beat a script here

Three things you got for free by modeling this as Kubernetes objects:

- **RBAC** — the *tool server's* access is a ServiceAccount + Role, audited like anything
  else. The agent never holds those creds; it only receives results. A script would run the
  tool with the script's own privileges.
- **Reconciliation** — delete the agent Pod and the controller rebuilds it, tools and all.
- **Composition** — many agents can share one `RemoteMCPServer` and one `ModelConfig`. No
  copy-pasted tool code, no duplicated endpoints.

## Break it: revoke a tool, then read the failure

Remove a tool from the agent's `toolNames` (or mistype one) and re-apply, then invoke a task
that needs it:

```bash
kubectl describe agent k8s-helper -n kagent
kubectl -n kagent port-forward svc/kagent 8081:80 &
kagent invoke --agent k8s-helper \
  --task "List the pods in the default namespace and tell me which are not Running."
kill %1 2>/dev/null
kubectl -n kagent logs deploy/kagent --tail=80 | grep -i tool
```

**Read the error, that's the lesson.** The agent does *not* crash — it reports a tool
failure and falls back to reasoning without that capability. The *reason* is in the
tool-call log line: the call to the missing tool was refused or unresolved. This proves the
indirection from the under-the-hood section is real: the model wanted the tool, but the
**allow-list** (not the model) decided it couldn't have it. Debugging an agent is reading
*which* tool call failed and *why* — the same "read the boundary that failed" skill as the
two failure planes in lab-02, now applied to the tool plane. Restore the tool and re-apply.

## Checkpoint — you can now explain…

1. **What turns a chatbot into an agent?** The ability to call a tool, read the result, and
   reason over it — the model proposing an action and the runtime executing it.
2. **What does MCP decouple, and why does that matter?** The tool from the agent. Tools live
   in an MCP server with their own RBAC; agents reference the server and are allow-listed to
   specific tools via `toolNames`. Reuse, isolation, and enforceable access all follow.
3. **What does the controller turn the `tools` block into at runtime?** A gated routing
   path: the agent's loop emits a tool intent → runtime checks `toolNames` → forwards to the
   `RemoteMCPServer` → server runs the tool under its own RBAC → result returns to the loop.

You can now:
- [ ] Reference a `RemoteMCPServer` from an `Agent` and allow-list specific tools.
- [ ] Trace an MCP tool call from the LLM loop through kagent to the server and back.
- [ ] Explain why tool access as a CRD field is enforceable policy, not a convention.

## What you proved in Phase 07

You ran an agent with **no Python process**: a `ModelConfig` pointed at your own vLLM
(Phase 06), an `Agent` object the controller keeps alive (the process→object shift from
lab-01), and real **tools via MCP** — all observable, RBAC-scoped, and composable like any
workload. That's "agents in production," riding the same Phase 03 machinery underneath.

## Next

→ **Phase 08**: a different workload type entirely — WebAssembly with Spin — for the
lightweight glue around all of this.

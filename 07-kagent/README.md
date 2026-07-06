# 07: kagent, AI agents as Kubernetes resources

> Floor 3a. Run agents the k8s-native way, as CRDs the cluster reconciles, and point
> them at the vLLM you're already serving.

## The problem this phase solves

You already have a working agent framework in this repo: `agents/`. The logic is fine. The
operational model is the problem: an agent there is a Python process you run and
babysit. It has no status you can query, no automatic restart, no rollback, no scaling, and
no observability beyond the `print()` lines you remembered to add. Every piece of cluster
machinery you learned in Phases 03–06 (Deployments, conditions, RBAC, logs, events)
doesn't apply, because the agent isn't a cluster object.

kagent closes that gap. It keeps the idea of the agent (an LLM + a loop + tools) and
changes the operational model: the `Agent` becomes a Kubernetes object that a controller
reconciles into a running, observable, restartable workload. It's the same move Kubernetes
made when it turned a binary-you-run into a Deployment-it-manages.

```
   agents/agent.py            kagent Agent CR
   (imperative script)   ──►  (declarative object the controller reconciles
        │                      into a workload it keeps alive + reports on)
   you run + babysit
```

## Prereqs

- Phases 03–06. Strongest if vLLM (Phase 04/06) is reachable in-cluster so your agents
  use your model, not a hosted API.
- No prior **MCP** (Model Context Protocol) knowledge needed; lab-03 introduces it.
  For a preview, skim `agents/examples/mcp_docs_agent.py`; kagent wires MCP servers in as
  `RemoteMCPServer` resources.

## The three nouns

| Kind | What it is | Mental model |
|---|---|---|
| `ModelConfig` | *which* LLM + how to reach it (your vLLM, or a hosted API) | a connection string, as an object |
| `Agent` | the agent: system prompt, which model, which tools | the "Deployment" of the agent world |
| `RemoteMCPServer` | an external MCP server whose tools the agent may call | a registered tool catalog |

## Objectives

1. Build the agent loop by hand (plain Python against your vLLM) so nothing after it is a black box.
2. Install the kagent controller and read its CRDs; internalize the process→object shift.
3. Point an `Agent` at your in-cluster vLLM via an OpenAI-compatible `ModelConfig`.
4. Give the agent tools through the built-in `RemoteMCPServer`, and trace the MCP call.
5. Debug agents the only way you can: by reading status conditions and logs, not
   `print()`.
6. (Bridge) Map concepts back to your local `agents/` framework.
7. (Harness) Engineer the guides + sensors that make an agent reliable (`agent = model +
   harness`) and see your gateway act as a shared, deployed harness (lab-05).

## Labs

| Lab | Idea | The mechanism it teaches |
|---|---|---|
| 00 | `lab-00-the-agent-loop-by-hand.md`: build an agent in ~60 lines of plain Python against your vLLM | the loop itself: model proposes a tool call, your code executes it, the result re-enters the context; every framework below wraps this |
| 01 | `lab-01-install-kagent.md`: install the controller (Helm), tour the CRDs | controller reconciles an `Agent` CR into a runtime Pod; an agent with no model never becomes ready |
| 02 | `lab-02-modelconfig-agent.md`: `ModelConfig` → vLLM, define an `Agent`, chat with it | two failure planes: reconcile (read conditions) vs runtime (read logs) |
| 03 | `lab-03-agent-with-tools.md`: point the agent at the built-in `RemoteMCPServer`, watch it use a tool | the MCP indirection: model proposes → `toolNames` gates → server executes → result returns to the loop |
| 04 | `lab-04-bridge-to-strands.md`: run this repo's `agents/` Strands framework on your in-cluster vLLM; choose process vs object | the two bridges: OpenAI `base_url` for the model, MCP for tools (the same standards on both sides) |
| 05 | `lab-05-agent-harness.md`: engineer the harness around the agent (guides + sensors, budgets, verification, the steering loop) | `agent = model + harness`; the gateway (token limit + prompt guard) is a shared, deployed harness |

## How it fits the stack

kagent is a new top floor, not a new building. The agent runtime Pod's model call is
plain HTTP to a Service ClusterIP, resolved by CoreDNS and DNAT'd by `kube-proxy`: the same
Phase 03 stack. The tool server hits the Kubernetes API as authenticated HTTP, the same
control plane. Everything new here sits on machinery you already own.

```
Agent CR ─► controller reconciles ─► runtime Pod (LLM loop) ─► vLLM /v1   (Phase 06)
                                            │ tool call (MCP)
                                            ▼
                                   RemoteMCPServer ─► K8s API   (Phase 03 control plane)
```

## The payoff

An agent that runs in your cluster, on your model, calling your MCP tools, with no
external API key. That's the "sovereign / self-hosted agentic platform on Akamai"
story in one demo.

> Heads up: kagent is young and moves fast. We pin versions and read the live CRD schema
> (`kubectl explain`) and kagent.dev rather than blog posts, which go stale
> (e.g. the `ToolServer` kind was removed in `v1alpha2`).

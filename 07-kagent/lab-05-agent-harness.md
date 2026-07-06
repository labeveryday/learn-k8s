# Lab 05: Harness engineering (agent = model + harness)

**Goal:** name the discipline that turns a working agent into a reliable one, the
**harness**, and engineer it deliberately. Map its two halves (guides + sensors) onto the
pieces you already have in `agents/`, kagent, and your gateway, then add the ones you're
missing. By the end you'll stop reaching for a bigger model and start engineering the
scaffolding around the one you have.

**Time:** ~40 min · **Cost:** free (local kind)

## The problem (why this exists)

Your lab-04 agent runs on your own model. "Runs" is not "reliable." A raw LLM loop is
non-deterministic, doesn't know your context, and, in the line worth remembering, is "biased
towards its first plausible solution." Left alone it will confirm its own wrong answer
without testing, loop on a failing approach, and blow past any budget. The instinct is to
swap in a bigger model. That helps marginally, costs more, and you'll hit the same failure
modes one notch up. The leverage is in everything around the model, and engineering
that is a discipline with a name.

> **How to run this lab:** unlike labs 01–04, this one is read-and-map, not type-and-run.
> The code blocks below are skeletons that show where a control hooks in; they're design
> sketches, not complete drop-in files. Your job is to understand each guide/sensor and map
> it onto pieces you already have, not to copy-paste a finished implementation. The "Break
> it" at the end is a thought experiment, not a command.

## What it is: `agent = model + harness`

The one practical claim: reliability comes from the controls around the model, not a
bigger model. The framing is Birgitta Böckeler's: an agent = a model + a harness,
where the harness is "everything in an AI agent except the model itself." It's the third
wave of the craft:

```
prompt engineering (2022)  →  context engineering  →  HARNESS engineering
  "say it better"             "give it the facts"      "build the controls around it"
```

A harness has a dual purpose: (1) raise the odds the agent is right the first time,
and (2) give it a feedback loop to self-correct before a human ever looks. You stop
waiting for a better model and engineer the system that molds the one you have.

## The mental model: guides + sensors (feedforward + feedback)

Borrowed from control systems: the agent is a governor, regulated two ways.

- **Guides (feedforward)** steer it before it acts: the system prompt, the tool set you
  expose, context you inject, budgets, allow-lists. You shape the action space up front.
- **Sensors (feedback)** observe after it acts and force self-correction: tests,
  linters, an LLM-as-judge (a second model call that grades the first one's output), a
  verification checklist, traces.

Each comes in two flavors:

| Check type | What it is | Speed | Examples in this repo |
|---|---|---|---|
| **Computational** | deterministic, pass/fail | ms–s | a unit test, a linter, the gateway's token `429` |
| **Inferential** | semantic, non-deterministic | slow, costs tokens | LLM-as-judge, the gateway's prompt guard |

```
                 ┌──────────── steering loop (you) ───────────┐
                 │   recurring failure → improve the harness    │
                 ▼                                              │
   guides ──►  ┌──────────────┐  ──► action ──►  sensors ──────┘
 (feedforward) │    MODEL      │                 (feedback)
  prompt,tools │  (the loop)   │      tests, judge, traces,
  context,     └──────────────┘      verify-before-done
  budgets
```

Two laws make this rigorous:

- **Ashby's Law of Requisite Variety:** "a regulator must have at least as much variety as
  the system it governs." Translation: an agent you've constrained (fewer tools, a
  defined topology, a clear allow-list) is far easier to harness than an open-ended one.
  Constraint is a feature.
- **The steering loop:** when a failure recurs, you don't retry; you "improve the
  feedforward and feedback controls to make the issue less probable in the future." The
  human's job moves from doing the task to engineering the harness. A good harness
  doesn't eliminate human input; it directs it to where it matters most.

## You've already built harness pieces; now name them

Harness engineering isn't a tool you install; it's a discipline that names and connects
parts you've been assembling all track. Map them:

| Harness role | In your Strands `agents/` | In kagent / your platform |
|---|---|---|
| Model abstraction (guide) | `models.py` multi-provider wrappers | `ModelConfig` |
| Tool set (guide) | MCP clients, auto-loaded `src/tools/` | `RemoteMCPServer` + `toolNames` allow-list |
| Context mgmt (guide) | `SlidingWindowConversationManager` | per-agent `systemMessage` |
| Budgets (guide) | `max_tokens` (add `max_iterations`) | gateway token rate-limit (the `429`) |
| Prompt guard (inferential guide) | *(none yet; add one)* | gateway `AgentgatewayPolicy` promptGuard |
| Observability (sensor) | `hooks/` + Agent Hub metrics/sessions | `status` conditions, logs, events |
| Prompt iteration (steering) | Hub versioned prompts | `kubectl apply` the changed `Agent` |

The headline for your platform: the gateway is a shared, deployed harness. Every agent
behind it inherits a computational sensor (the token `429`) and an inferential guide (prompt
guards) for free: harness controls applied once, at the platform, not re-coded per agent.
That's the "self-hosted agentic platform on Akamai" story with a sharper point: the platform
is part of the harness.

**See the guides you already wrote as YAML.** Three rows of that table are kagent CRDs you
applied in labs 02–03; the harness was hiding in fields you already shipped. Re-read them
through the guide/sensor lens:

```yaml
# ModelConfig (manifests/modelconfig-vllm.yaml) - the "model abstraction" guide.
# Swapping the model the agent uses is a field, not a code change.
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: vllm
  namespace: kagent
spec:
  provider: OpenAI
  model: "Qwen/Qwen2.5-0.5B-Instruct"
  apiKeySecret: vllm-api-key          # the key lives in a Secret, not the manifest
  apiKeySecretKey: api-key
  openAI:
    # THE harness seam: point this at vLLM directly (now) OR at the Phase 06 gateway
    # host instead, and every agent call inherits the token 429 + prompt guards. One field
    # decides whether the platform regulates this agent.
    baseUrl: "http://vllm.default.svc.cluster.local:8000/v1"
---
# Agent (manifests/agent-with-tools.yaml) - two guides in one object.
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: k8s-helper
  namespace: kagent
spec:
  description: "A concise Kubernetes helper that can inspect the cluster."
  type: Declarative
  declarative:
    modelConfig: vllm
    systemMessage: |                   # GUIDE (context mgmt): the per-agent prompt, steered up front
      You are a concise Kubernetes assistant with read access to the cluster.
      Use your tools to check real state before answering. Answer in one or two sentences.
    tools:
      - type: McpServer
        mcpServer:
          apiGroup: kagent.dev
          kind: RemoteMCPServer        # tools come from a deployed MCP server (decoupled from the agent)
          name: kagent-tool-server
          toolNames:                   # GUIDE (tool set): the ALLOW-LIST - Ashby's variety, made literal
            - list_pods                #   the agent CANNOT call anything not on this list,
            - get_pod                  #   so its action space is small enough to regulate
            - list_events
```

- **`openAI.baseUrl`** is the most load-bearing field in this lab: it's where the
  platform harness (Step 3) attaches. Same field name as Strands' `client_args["base_url"]`
  (lab-04): point either at the gateway and the regulation switches on with zero code.
- **`toolNames`** is requisite variety as a YAML list. Omit it and the agent inherits every
  tool the `RemoteMCPServer` discovered, a larger, harder-to-harness action space. Listing
  three names is a feedforward guide. Gotcha: the names must match what your kagent version
  exposes; confirm with `kubectl describe remotemcpserver kagent-tool-server -n
  kagent`, not a guess.
- **`systemMessage`** is your context guide. Changing it and re-`apply`-ing is the kagent
  half of the steering loop (last table row): you iterate the harness, not the run.

## 1. Add a guide: inject context on behalf of the agent

The principle: context engineering on behalf of agents. Don't make the agent discover
its environment; hand it over. Add a hook to your lab-04 Strands agent (mirror
`src/hooks/logging_hook.py`) that, at startup, injects the available tools and a short
house "how we work here" preamble into context. Strands' lifecycle hooks split across two
modules: `strands.hooks` has `AgentInitializedEvent` (startup) and `AfterInvocationEvent`
(after a response); the tool-level `BeforeToolInvocationEvent` / `AfterToolInvocationEvent`
(the pair `src/hooks/logging_hook.py` already imports) live in `strands.experimental.hooks`.
Those are the hooks a harness hangs off of. (Strands has been promoting these out of
`experimental` and renaming them across versions; match whatever your installed SDK and
`logging_hook.py` use.)

```python
# src/hooks/context_hook.py  - a feedforward GUIDE
from strands.hooks import HookProvider, HookRegistry, AgentInitializedEvent

class ContextInjectionHook(HookProvider):
    """Map tools + house rules into the agent on startup so it doesn't guess."""
    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(AgentInitializedEvent, self.on_start)
    def on_start(self, event: AgentInitializedEvent) -> None:
        # prepend available tools + best-practice notes to the system prompt
        ...
```

**What to look for:** the agent stops fishing for "what can I do here?" and starts from a
known environment. You reduced its error surface before it took a single action.

## 2. Add a sensor: verify before declaring done

Models confirm their own work. Add a verification step the agent must pass before it
answers (the plan → build → verify → fix loop). For an ops agent, the cheapest sensor is
computational: after it proposes a `kubectl` change, run a `--dry-run=server` and feed any
error back into the loop. The agent doesn't get to say "done" until the deterministic check
is green.

**What to look for:** an answer that survived a real check, not the model's first guess. The
verify-then-fix cycle is the highest-value sensor you can add.

## 3. Wire in the platform sensors you already built

Route this agent through the Phase 06 gateway (the lab-04 `base_url` tie-in), and two
harness controls switch on with zero new code:

- **Token budget (computational sensor):** a runaway agent hits a hard `429`. Resource
  control isn't a number in your script; it's enforced at the platform.
- **Prompt guard (inferential guide):** the `AgentgatewayPolicy` blocks a bad prompt
  before it reaches the model.

The prompt guard is the inferential guide you built in Phase 06 lab-04, and it's pure
harness: a policy that attaches to your route. Worth re-reading now that you
have the vocabulary for it:

```yaml
# AgentgatewayPolicy (Phase 06, manifests/kgateway-prompt-guard.yaml) - a shared GUIDE.
# It isn't a route; it ATTACHES to one, so every agent behind that route inherits it.
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: llm-prompt-guard
  namespace: default
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute                  # the harness control attaches to the ROUTE, not each agent
      name: llm
  backend:
    ai:
      promptGuard:
        request:                       # evaluated on the INBOUND prompt, before the model sees it
          - response:
              message: "Rejected: request appears to contain a US SSN."
            regex:
              action: Reject           # CRD enum is [Mask, Reject] - PascalCase; all-caps REJECT fails validation
              matches:
                - pattern: '\b\d{3}-\d{2}-\d{4}\b'
                  name: ssn
```

- This is a feedforward guide (it steers before the model acts) and inferential
  (it's pattern-matching the prompt's meaning, not a pass/fail unit test). Deployed once on
  the route, it guards every agent behind it: the "apply harness controls at the platform"
  point made concrete.
- Field gotcha carried over from Phase 06: `action` is PascalCase (`Reject`, not
  `REJECT`), and `matches` is a list of objects (`{pattern, name}`), not a bare string.
  The schema wins over any blog snippet.

```bash
kubectl -n kgateway-system port-forward svc/http 8080:80 &   # local 8080 → the "http" Gateway Service :80 in kgateway-system; & backgrounds it
# point the agent's base_url at the gateway (lab-04, Step 1 tie-in), then run a task
# that loops - watch it get cut off by YOUR token limit, not by luck.
```

`svc/http` is the kgateway proxy Service for the `http` Gateway (Phase 05/06), the same
front door your gateway labs used. Sending the agent's `base_url` through it wires
the platform's sensor (`429`) and guide (prompt guard) onto your own agent.

**What to look for:** your own agent, governed by your own platform. That's requisite
variety in action: the platform regulates the agent.

## 4. Add a budget + a loop guard

Two more guides, both cheap:

- A `max_iterations` cap so the loop can't run forever (Strands exposes turn/iteration
  limits; pair it with `max_tokens`).
- **Loop detection:** a `BeforeToolInvocationEvent` hook (`strands.experimental.hooks`, the
  same event `logging_hook.py` uses) that tracks repeated identical tool calls and breaks
  the "doom loop" after N: the agent reconsiders instead of retrying the same failing edit.

**What to look for:** an agent that gives up on a dead end instead of
burning your budget on the same mistake.

## 5. Memory and context: the harness decides what the model remembers

Lab-00 put a number on this: every turn, your loop re-sent the whole `messages` list, and the
payload size you printed grew with it. The model is stateless; the messages list IS the agent's
memory, and it lives in your code, not the model. On your server the wall is close and exact:
`--max-model-len 1024` means system prompt + history + tool results + the reply must fit in
1024 tokens. Hit it on purpose. Run your lab-00 `agent.py` and keep feeding it follow-up
questions (wrap the loop in `input()` if you haven't), and within a handful of turns:

```
requests.exceptions.HTTPError: 400 Client Error: Bad Request
# body: "This model's maximum context length is 1024 tokens. However, you requested 1112 tokens
#        (912 in the messages, 200 in the completion). Please reduce the length..."
```

The server did the arithmetic and refused. Nothing "forgot" gracefully; the request failed.
Deciding what to do before that line is a harness job, and there are three horizons to manage:

- **In-conversation state: the messages list.** Two standard moves when it outgrows the
  window. **Truncation**: drop the oldest turns, keep the system prompt (drop that and the
  agent loses its instructions, not its memories). **Compaction**: replace older turns with a
  summary of them, written by another model call. Note the trade printed on the label:
  compaction spends tokens to save tokens, and the summary keeps what the summarizer thought
  mattered, not what turns out to matter.
- **Cross-session state.** You've built this twice already: kagent sessions (lab-02) persist a
  conversation on the server side, and `12-agent-deploy/lab-04` gives its review agent a ledger
  on a PVC so state survives the Pod. Same question one level up: what survives a restart,
  instead of what survives a turn.
- **Retrieved memory.** Phase 10's RAG is memory the agent queries instead of carries: the
  corpus stays in Qdrant, and only the top-k relevant chunks enter the window per question.
  Retrieval is the only horizon that scales past the window, because the window never holds
  the whole store.

Make the first horizon concrete. Add compaction to your lab-00 loop; it's ~15 lines. Insert
before the `chat()` call inside the loop:

```python
KEEP_LAST = 2                 # recent turns stay verbatim

def compact(messages):
    # messages[0] is the system prompt; keep it and the last KEEP_LAST turns untouched.
    old, recent = messages[1:-KEEP_LAST], messages[-KEEP_LAST:]
    transcript = "\n".join(f"{m['role']}: {m.get('content') or ''}" for m in old)
    summary = chat([{"role": "user",
                     "content": "Summarize this conversation in 3 sentences, "
                                "keeping any facts, file names, and results:\n" + transcript}]
                   )["choices"][0]["message"]["content"]
    return [messages[0],
            {"role": "user", "content": "Summary of the conversation so far: " + summary}
           ] + recent

# in the loop, before calling chat():
if len(messages) > 8:
    messages = compact(messages)
    print(f"--- compacted to {len(messages)} messages")
```

Run it past the point that 400'd before. Then show yourself what compaction costs: early in the
conversation, tell the agent a specific detail (a made-up incident number, say `INC-4471`), let
several turns pass so compaction fires, then ask for the number back.

**What to look for:** the loop survives past the old context wall, and the answer to the
`INC-4471` question depends entirely on whether the summarizer kept it. If the summary says
"the user reported an incident" without the number, the detail is gone, with full confidence
on both sides. That's the honest version of every "the agent forgot" bug report: someone's
truncation or compaction policy dropped it.

The harness thesis, applied: memory policy is harness, not model. The same
`Qwen/Qwen2.5-0.5B-Instruct` with keep-everything, truncate-oldest, or compact-at-8 is three
different agents with three different failure modes, and none of that lives in the weights.
When a framework advertises "memory," this section is what it's doing underneath; now you can
ask which horizon it manages and what its drop policy is.

## 6. Close the steering loop with traces

Your Agent Hub already records metrics/sessions; kagent has logs/events. Those are your
traces. When you see a failure pattern (same tool fails, same prompt class derails),
the move is to add or tighten a harness control so that class of failure can't recur,
not to tweak the prompt and hope. That's the steering loop: humans iterate the harness,
not the individual run. (LangChain automates this with a trace analyzer that reads
failures and proposes harness changes.)

## Break it, then read the error

A thought experiment, not a command (this lab is design, not type-and-run). Picture turning
a sensor off and watching the failure return: remove the Step 2 verification step (or
the Step 3 token budget), then give the agent a task it tends to fumble:

Read what happens. Without the verify sensor, the agent returns a wrong answer with full
confidence; the model didn't get worse, you removed the control that caught it. Without the
budget, it loops until something else stops it. The lesson is the whole discipline in one
move: most "the agent is dumb" failures are missing-harness failures. The fix is rarely
a bigger model and rarely a longer prompt; it's a guide or a sensor you didn't add. Put the
control back and the same model succeeds.

## Checkpoint: you can now explain…

1. **What is `agent = model + harness`?** The harness is everything around the model:
   guides (feedforward: prompt, tools, context, budgets) and sensors (feedback: tests,
   judges, verification, traces). Engineering it is the third wave after prompt and
   context engineering.
2. **Computational vs inferential checks?** Deterministic/fast (tests, the token `429`) vs
   semantic/slow (LLM-as-judge, prompt guards). A good harness uses both, and "keeps quality
   left" (checks early).
3. **What is the steering loop, and why does requisite variety matter?** When a failure
   recurs you improve the harness, not the run; and a constrained agent (fewer tools,
   clear allow-list, defined topology) has a small enough action space to regulate.
4. **Why is the platform part of the harness?** The gateway applies a token sensor and a
   prompt-guard guide to every agent behind it: harness controls deployed once, shared.
5. **The three memory horizons.** In-conversation (the messages list; truncation vs
   compaction, and what each drops), cross-session (kagent sessions, the 12-agent-deploy
   ledger), and retrieved (RAG, the only one that scales past the context window).

You can now:
- [ ] Classify any agent control as a guide or a sensor, computational or inferential.
- [ ] Add a verification sensor and a budget/loop guide to a Strands agent.
- [ ] Read vLLM's context-length 400 as arithmetic, and add a compaction step that trades
      summary tokens for headroom, knowing what it silently drops.
- [ ] Name which harness pieces your `agents/` template, kagent, and the gateway each provide.
- [ ] Respond to a recurring failure by changing the harness, not just the prompt.

## What you proved across Phase 07

Agent as a k8s object (01–03), your own framework on your own model (04), and now the
discipline that makes either one reliable (05): a deliberately engineered harness of
guides and sensors, with your platform doing part of the regulating. That's the difference
between "I got an agent running" and "I operate agents."

## Further reading (the sources this lab synthesizes)

- Martin Fowler / Birgitta Böckeler, *Harness Engineering*:
  <https://martinfowler.com/articles/harness-engineering.html>
- LangChain, *Improving Deep Agents with Harness Engineering*:
  <https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering>
- Heeki Park, *Building an Agent Harness*:
  <https://heeki.medium.com/building-an-agent-harness-31942331d605>

## Next

→ **Phase 08**: WebAssembly with Spin, the millisecond glue around all of this. (A Wasm
shim is itself a harness component: a cheap, fast place to put a deterministic guide or
sensor in front of the model.)

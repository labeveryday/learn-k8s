# Lab 05 — Harness engineering: agent = model + harness

**Goal:** name the discipline that turns a working agent into a *reliable* one — the
**harness** — and engineer it deliberately. Map its two halves (guides + sensors) onto the
pieces you already have in `agents/`, kagent, and your gateway, then add the ones you're
missing. By the end you'll stop reaching for a bigger model and start engineering the
scaffolding around the one you have.

**Time:** ~40 min · **Cost:** free (local kind)

## The problem (why this exists)

Your lab-04 agent *runs* on your own model. "Runs" is not "reliable." A raw LLM loop is
non-deterministic, doesn't know your context, and — the line worth tattooing — is *"biased
towards its first plausible solution."* Left alone it will confirm its own wrong answer
without testing, loop on a failing approach, and blow past any budget. The instinct is to
swap in a bigger model. That helps marginally, costs more, and you'll hit the same failure
modes one notch up. **The leverage is in everything around the model**, and engineering
that is a discipline with a name.

## What it is: `agent = model + harness`

Birgitta Böckeler's framing, now shared across the field: **an agent = a model + a
harness**, where the harness is *"everything in an AI agent except the model itself."* It's
the third wave of the craft:

```
prompt engineering (2022)  →  context engineering  →  HARNESS engineering
  "say it better"             "give it the facts"      "build the controls around it"
```

A harness has a **dual purpose**: (1) raise the odds the agent is right the *first* time,
and (2) give it a feedback loop to *self-correct* before a human ever looks. You stop
waiting for a better model and engineer the system that molds the one you have.

## The mental model: guides + sensors (feedforward + feedback)

Borrowed straight from control systems — the agent is a *governor*, regulated two ways:

- **Guides (feedforward)** — steer it *before* it acts: the system prompt, the tool set you
  expose, context you inject, budgets, allow-lists. You shape the action space up front.
- **Sensors (feedback)** — observe *after* it acts and force self-correction: tests,
  linters, an LLM-as-judge, a verification checklist, traces.

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

- **Ashby's Law of Requisite Variety:** *"a regulator must have at least as much variety as
  the system it governs."* Translation: an agent you've **constrained** (fewer tools, a
  defined topology, a clear allow-list) is far easier to harness than an open-ended one.
  Constraint is a feature.
- **The steering loop:** when a failure *recurs*, you don't just retry — you *"improve the
  feedforward and feedback controls to make the issue less probable in the future."* The
  human's job moves from doing the task to **engineering the harness**. "A good harness
  doesn't eliminate human input — it directs it to where it matters most."

## You've already built harness pieces — now name them

Harness engineering isn't a tool you install; it's a discipline that names and connects
parts you've been assembling all track. Map them:

| Harness role | In your Strands `agents/` | In kagent / your platform |
|---|---|---|
| Model abstraction (guide) | `models.py` multi-provider wrappers | `ModelConfig` |
| Tool set (guide) | MCP clients, auto-loaded `src/tools/` | `RemoteMCPServer` + `toolNames` **allow-list** |
| Context mgmt (guide) | `SlidingWindowConversationManager` | per-agent `systemMessage` |
| Budgets (guide) | `max_tokens` (add `max_iterations`) | gateway **token rate-limit** (the `429`) |
| Prompt guard (inferential guide) | — *(add)* | gateway `AgentgatewayPolicy` **promptGuard** |
| Observability (sensor) | `hooks/` + Agent **Hub** metrics/sessions | `status` conditions, logs, events |
| Prompt iteration (steering) | Hub **versioned prompts** | `kubectl apply` the changed `Agent` |

The headline for *your* platform: **the gateway is a shared, deployed harness.** Every agent
behind it inherits a computational sensor (the token `429`) and an inferential guide (prompt
guards) *for free* — harness controls applied once, at the platform, not re-coded per agent.
That's the "self-hosted agentic platform on Akamai" story with a sharper point: the platform
*is* part of the harness.

## 1. Add a guide: inject context on behalf of the agent

The principle: *"context engineering on behalf of agents"* — don't make the agent discover
its environment, hand it over. Add a hook to your lab-04 Strands agent (mirror
`src/hooks/logging_hook.py`) that, at startup, injects the available tools and a short
house "how we work here" preamble into context. Strands' lifecycle events live in
`strands.hooks` — `AgentInitializedEvent` (startup), `BeforeToolCallEvent` /
`AfterToolCallEvent`, and `AfterInvocationEvent` (after a response) are the hooks a harness
hangs off of.

```python
# src/hooks/context_hook.py  — a feedforward GUIDE
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

Models confirm their own work. Add a **verification step** the agent must pass before it
answers (the *plan → build → verify → fix* loop). For an ops agent, the cheapest sensor is
*computational*: after it proposes a `kubectl` change, run a `--dry-run=server` and feed any
error back into the loop. The agent doesn't get to say "done" until the deterministic check
is green.

**What to look for:** an answer that survived a real check, not the model's first guess. The
verify-then-fix cycle is the single highest-value sensor you can add.

## 3. Wire in the platform sensors you already built

Route this agent through the Phase 06 **gateway** (the lab-04 `base_url` tie-in), and two
harness controls switch on with zero new code:

- **Token budget (computational sensor):** a runaway agent hits a hard `429`. Resource
  control isn't a number in your script — it's enforced at the platform.
- **Prompt guard (inferential guide):** the `AgentgatewayPolicy` blocks a bad prompt
  *before* it reaches the model.

```bash
kubectl -n kgateway-system port-forward svc/http 8080:80 &
# point the agent's base_url at the gateway (lab-04, Step 1 tie-in), then run a task
# that loops — watch it get cut off by YOUR token limit, not by luck.
```

**What to look for:** your own agent, governed by your own platform. That's requisite
variety in action — the platform regulates the agent.

## 4. Add a budget + a loop guard

Two more guides, both cheap:

- A `max_iterations` cap so the loop can't run forever (Strands exposes turn/iteration
  limits; pair it with `max_tokens`).
- **Loop detection:** a `BeforeToolCallEvent` hook that tracks repeated *identical* tool
  calls and breaks the "doom loop" after N — the agent reconsiders instead of retrying the
  same failing edit.

**What to look for:** an agent that gives up *intelligently* on a dead end instead of
burning your budget on the same mistake.

## 5. Close the steering loop with traces

Your Agent Hub already records metrics/sessions; kagent has logs/events. Those are your
**traces**. When you see a failure *pattern* (same tool fails, same prompt class derails),
the move is *not* "tweak the prompt and hope" — it's **add or tighten a harness control** so
that class of failure can't recur. That's the steering loop: humans iterate the *harness*,
not the individual run. (LangChain automates exactly this with a trace-analyzer that reads
failures and proposes harness changes.)

## Break it, then read the error (Kelsey lens)

Turn a sensor **off** and watch the failure return. Remove the Step 2 verification step (or
the Step 3 token budget), then give the agent a task it tends to fumble:

**Read what happens.** Without the verify sensor, the agent confidently returns a wrong
answer — *the model didn't get worse, you removed the control that caught it.* Without the
budget, it loops until something else stops it. The lesson is the whole discipline in one
move: **most "the agent is dumb" failures are missing-harness failures.** The fix is rarely
a bigger model and rarely a longer prompt — it's a guide or a sensor you didn't add. Put the
control back and the same model succeeds.

## Checkpoint — you can now explain…

1. **What is `agent = model + harness`?** The harness is everything around the model —
   guides (feedforward: prompt, tools, context, budgets) and sensors (feedback: tests,
   judges, verification, traces) — and engineering it is the third wave after prompt and
   context engineering.
2. **Computational vs inferential checks?** Deterministic/fast (tests, the token `429`) vs
   semantic/slow (LLM-as-judge, prompt guards). A good harness uses both, and "keeps quality
   left" (checks early).
3. **What is the steering loop, and why does requisite variety matter?** When a failure
   recurs you improve the *harness*, not the run; and a constrained agent (fewer tools,
   clear allow-list, defined topology) has a small enough action space to actually regulate.
4. **Why is the platform part of the harness?** The gateway applies a token sensor and a
   prompt-guard guide to *every* agent behind it — harness controls deployed once, shared.

You can now:
- [ ] Classify any agent control as a guide or a sensor, computational or inferential.
- [ ] Add a verification sensor and a budget/loop guide to a Strands agent.
- [ ] Name which harness pieces your `agents/` template, kagent, and the gateway each provide.
- [ ] Respond to a recurring failure by changing the harness, not just the prompt.

## What you proved across Phase 07

Agent as a k8s object (01–03), your own framework on your own model (04), and now the
**discipline that makes either one reliable** (05): a deliberately engineered harness of
guides and sensors, with your platform doing part of the regulating. That's the difference
between "I got an agent running" and "I operate agents."

## Further reading (the sources this lab synthesizes)

- Martin Fowler / Birgitta Böckeler — *Harness Engineering*:
  <https://martinfowler.com/articles/harness-engineering.html>
- LangChain — *Improving Deep Agents with Harness Engineering*:
  <https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering>
- Heeki Park — *Building an Agent Harness*:
  <https://heeki.medium.com/building-an-agent-harness-31942331d605>

## Next

→ **Phase 08**: WebAssembly with Spin — the millisecond glue around all of this. (A Wasm
shim is itself a harness component: a cheap, fast place to put a deterministic guide or
sensor in front of the model.)

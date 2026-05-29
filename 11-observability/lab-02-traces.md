# Lab 02 — Traces: follow ONE request across every service

**Goal:** wire Strands' native OpenTelemetry so an agent call emits **spans**, run an OTel
Collector + Tempo as the trace backend, and view a single request's **span tree** in
Grafana — agent loop → tool (MCP) → model call (vLLM) through the gateway. By the end you
can point at the exact hop where latency hides, and you'll have built the telemetry the
`07/lab-05` "traces as feedback" steering loop actually runs on.

**Time:** ~50 min · **Cost:** free (local kind)

## The problem (why this exists)

Lab-01's metrics told you p95 latency doubled. They cannot tell you *where*. Was it the
model? The gateway adding overhead? A slow MCP tool the agent called mid-loop? A metric is
an **aggregate** — it sums over all requests and loses the per-request causal chain. To
answer "where did the time go in *this* request," you need the request itself, stitched
together across every service it touched, with a timing on each hop.

## What it replaces / why the naive way fails

The naive answer is "read the logs." But your request crosses three processes — the agent,
the gateway, vLLM — each with its own log stream, its own clock, and no shared ID linking a
line in one to a line in another. You'd be manually correlating timestamps across three
logs and guessing. A **trace** fixes this structurally: every service stamps its work with
the *same* trace ID and a parent/child span relationship, so the request reassembles itself
into one timeline. Logs are per-service and flat; a trace is cross-service and a tree.

## Under the hood (MIT hat): spans, context propagation, and the collector

A **span** is one unit of work with a start, a duration, and attributes (e.g. "model
invoke, 1.8s, 128 tokens"). A **trace** is a tree of spans sharing one trace ID. The magic
that makes it cross-service is **context propagation**: the trace ID + parent span ID ride
along *with* the request (HTTP headers, the `traceparent` header), so when the agent calls
vLLM, vLLM's span knows it's a child of the agent's span. That shared ID is the entire
trick — it's what a log line lacks.

```
 Strands agent                       OTel Collector            Tempo
 (strands-agents[otel])              (receive/batch/route)     (trace TSDB)
        │  emits spans (OTLP) ─────────────►│  ──── OTLP ──────►│
        │  trace_id=abc, span tree          │  batch + fan-out  │  index by trace_id
        ▼                                   ▼                   ▼
  ┌──────────── one trace abc ────────────┐                 Grafana reads it
  │ Agent span        (3.1s)              │                 as a waterfall
  │ └─ Cycle span     (3.0s)              │
  │    ├─ Tool span (MCP)   (0.4s)        │  ◄── "the tool was fast"
  │    └─ LLM span (vLLM)   (2.5s) ◄──────┼───  "the MODEL is the cost"
  └───────────────────────────────────────┘
```

Two design points worth internalizing:
- **Why a Collector instead of exporting straight to Tempo?** The Collector is a decoupling
  layer: producers speak OTLP to *one* endpoint, and the Collector fans out to whatever
  backend(s) you run. Swap Tempo for Jaeger and not one agent changes.
- **Why a trace, not a log, locates latency.** The span tree *is* the timeline. You read
  the widest bar and you've found the bottleneck — no clock-correlation across logs.

Strands emits this hierarchy natively over OpenTelemetry: **Agent span** (whole
invocation) → **Cycle span** (each event-loop turn) → **LLM spans** (model invokes, with
token usage) and **Tool spans** (MCP calls, with timing). That's the same loop you've run
since `07/lab-04` — now instrumented.

## 0. Prereqs

- Lab-01 done (the `monitoring` namespace with Prometheus + Grafana exists).
- vLLM reachable and the `agents/` Strands template in a venv (`07/lab-04`, Step 0).

## 1. Run a trace backend: OTel Collector + Tempo

Install Tempo (the trace store) and the Collector (the funnel) into `monitoring`:

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm repo update

# Tempo single-binary (dev shape). Pin it — check artifacthub.io/packages/helm/grafana/tempo for latest.
helm install tempo grafana/tempo \
  --version 1.23.0 \
  --namespace monitoring \
  -f manifests/tempo-values.yaml

# OTel Collector. Pin it — check artifacthub.io/packages/helm/opentelemetry-helm/opentelemetry-collector for latest.
helm install otel-collector open-telemetry/opentelemetry-collector \
  --version 0.157.2 \
  --namespace monitoring \
  -f manifests/otel-collector-values.yaml
```

Read the values: `tempo-values.yaml` **explicitly enables** the OTLP receivers — recent
Tempo charts ship with *all* ingest protocols **off**, so a missing receiver silently drops
every span (a classic "where are my traces" trap). `otel-collector-values.yaml` defines a
`traces` pipeline: receive OTLP (4317/4318) → `batch` → export to `tempo:4317`.

Wire Tempo into Grafana as a datasource (the sidecar hot-loads it):

```bash
kubectl apply -f manifests/grafana-datasource-tempo.yaml
```

**What to look for:**

```bash
kubectl -n monitoring get pods | grep -E 'tempo|otel-collector'   # both Running
# In Grafana → Connections → Data sources → Tempo → "Test" → green.
```

Note the Tempo query URL is `:3200` (the single-binary chart moved it from the old 3100).

## 2. Instrument the Strands agent with native OpenTelemetry

Install the OTel extra and point the agent at the Collector. Strands does the rest — it
emits the Agent/Cycle/LLM/Tool span tree automatically once telemetry is set up.

```bash
cd agents && source .venv/bin/activate
pip install 'strands-agents[otel]'
```

Expose the Collector's OTLP/HTTP port to your laptop (where the Strands process runs) and
set the standard OTel env var:

```bash
kubectl -n monitoring port-forward svc/otel-collector-opentelemetry-collector 4318:4318 &
export OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318"
kubectl -n default port-forward svc/vllm 8000:8000 &     # the model, as in 07/lab-04
```

In your agent (the `agents/src/agent.py` you pointed at vLLM in `07/lab-04`), turn on
telemetry **before** creating the `Agent` — two lines:

```python
from strands.telemetry import StrandsTelemetry

strands_telemetry = StrandsTelemetry()
strands_telemetry.setup_otlp_exporter()   # spans → OTEL_EXPORTER_OTLP_ENDPOINT (the Collector)
strands_telemetry.setup_meter(enable_otlp_exporter=True)  # also export Strands METRICS (used in lab-03)

# ... then build the Agent as before (model=vLLM OpenAIModel, tools=[...]) ...
```

Run one task that *uses a tool* so the trace has a Tool span to show, then ask a question:

```bash
python src/agent.py
# > What time is it, and explain a Kubernetes Service in one sentence.
```

**What to look for:** the Collector's logs prove receipt before you even open Grafana —

```bash
kubectl -n monitoring logs deploy/otel-collector-opentelemetry-collector | grep -iE 'spans|Trace'
# the `debug` exporter prints a span count per batch — your spans arrived.
```

## 3. Read ONE request's span tree in Grafana

```bash
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80 &
# Browser: http://localhost:3000 → Explore → datasource "Tempo" → Search → run.
```

Pick your trace and expand it. You'll see the Strands hierarchy:

```
Agent span                      ← the whole invocation (total tokens, final answer)
└─ Cycle span                   ← one event-loop turn
   ├─ Tool span  (current_time) ← the MCP/tool call: name, args, result, duration
   └─ LLM span   (vLLM invoke)  ← model call: prompt, completion, token usage, duration
```

**What to look for — read the widths, not the names.** The **LLM span** is almost certainly
the widest bar: on a CPU model, the *model call* dominates, the tool is a blip. That single
visual answers "why is it slow?" — and it's an answer no metric and no log could give you,
because only the trace preserves the per-request, cross-service timeline. If you routed the
agent through the **gateway** (`07/lab-04` tie-in), you'll also see the gateway hop's
contribution — proving the gateway adds negligible latency, or catching it if it doesn't.

## 4. The payoff: this IS the steering loop's sensor

`07/lab-05` defined the **steering loop**: when a failure *recurs*, you don't retry — you
improve the harness, and "traces" are the feedback that tells you *which* control to add.
You now have *real* traces. When you see a *pattern* in the span trees — the same tool span
erroring, one prompt class always spawning extra cycles, the LLM span ballooning on certain
inputs — that's the signal to add a guide or sensor (a budget, a loop guard, a prompt
guard). The trace is the **sensor**; the harness change is the **response**. Lab-01 metrics
told you *that* it's slow; this trace tells you *where*, which is what the loop needs to act.

## 5. Break it, then read the error (Kelsey lens)

Break the export path and watch traces vanish *without* the agent failing:

```bash
kill %1 2>/dev/null    # kill the OTLP port-forward to the Collector
# back in the agent:  > what is a Pod?
```

**Read it.** The agent **still answers** — the model call succeeds — but no new trace shows
up in Tempo, and the Strands process logs an OTLP export error (connection refused to
`localhost:4318`). The lesson: **telemetry is a side channel, not the request path.** A
broken exporter blinds you but doesn't break the platform — which is exactly why a silent
telemetry outage is dangerous (you think it's healthy because requests still 200, but
you've gone dark). This is the trace-side twin of lab-01's `up == 0`: there, the *target*
was gone and Prometheus shouted; here, the *producer's export* is gone and you must notice
the absence. Restore the port-forward to recover.

## Checkpoint — you can now explain…

1. **What is a span vs a trace, and what makes traces cross-service?** A span is one timed
   unit of work; a trace is a tree of spans sharing a trace ID. **Context propagation** —
   the trace/parent IDs riding along with the request — is what links spans across the
   agent, gateway, and model.
2. **Why does a trace locate latency where a log can't?** The span tree *is* the timeline;
   the widest bar is the bottleneck. Logs are per-service and flat, with no shared ID, so
   you'd be correlating clocks by hand.
3. **Why run an OTel Collector instead of exporting to Tempo directly?** Decoupling:
   producers speak OTLP to one endpoint; the Collector fans out to any backend. Swap
   Tempo→Jaeger with zero agent changes.
4. **How does this close the `07/lab-05` loop?** Traces are the steering loop's feedback
   sensor — a *pattern* across span trees tells you which harness control to add.

You can now:
- [ ] Stand up an OTel Collector + Tempo and register Tempo in Grafana.
- [ ] Turn on Strands native OTel with `StrandsTelemetry` + `OTEL_EXPORTER_OTLP_ENDPOINT`.
- [ ] Read an agent request's Agent→Cycle→LLM/Tool span tree and name the slow hop.
- [ ] Diagnose a blind spot caused by a broken exporter (not a broken request).

## Tie back / forward

The span context rides the same Phase 03 HTTP path your requests already use — OTLP is just
more traffic DNAT'd by kube-proxy to the Collector's ClusterIP. You can now answer "is it
up?" (lab-01) and "why is it slow?" (this lab). The last question — "**is it any good?**" —
is next: → `lab-03-quality-cost.md` adds a quality score (Strands Evals, LLM-as-judge) and a
token/cost panel, then ties all three signals to the harness steering loop.

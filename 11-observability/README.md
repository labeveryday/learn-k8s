# 11 · Observability: see the AI platform you built

> The maturity capstone. You built a platform other people call: vLLM (04), a gateway
> (05/06), agents (07), Wasm glue (08), on real LKE (09). You cannot see it. This
> phase makes the invisible visible: metrics, traces, and a quality signal, and closes
> the harness "traces as feedback" loop from `07/lab-05`.

## The big idea

You can hit your platform with a `curl` and get a 200. That tells you almost nothing. You
can't see how many tokens it's burning, what its latency looks like under load, where in
the request path time disappears, or whether the answers are any good. The platform is a
black box you happen to own.

Observability is how you open the box. It is three signals answering three questions:

| Question | Signal | What it is | Where it lives |
|---|---|---|---|
| **Is it up?** | **Metrics** | counts/gauges over time (tokens, queue depth, latency) | Prometheus + Grafana |
| **Why is it slow?** | **Traces** | one request followed across every service | OTel Collector + Tempo |
| **Is it any good?** | **Evals** | a quality score on the agent's output | Strands Evals → a metric |

A metric tells you p95 latency doubled. It does not tell you where: the model? the
gateway? a slow MCP tool? Only a trace, one request stitched across services, shows
you that. Neither tells you whether the answer was correct; that needs an eval.
Three signals, three questions, no overlap.

There are two tools, and they split cleanly. **Prometheus/Grafana** is infra
observability (is it up / saturated / in SLO, labs 01–03). **Langfuse** is LLM-native
observability (per-request LLM traces plus token cost, output quality, and prompt
management in one pane, lab-04). Langfuse collapses the traces + cost + quality answers for
LLM apps; it has no infra metrics. Grafana for the platform, Langfuse for the model.

## What it replaces / why the naive way fails

So far your visibility has been `kubectl logs`, `kubectl describe`, and `print()`. Those
are point-in-time and per-object. They can't answer aggregate questions ("tokens/sec
over the last hour"), cross-service questions ("which hop ate the latency"), or
semantic questions ("was the answer good"). `07/lab-05` called your Agent Hub
metrics and kagent logs your traces and promised a real steering loop; this phase
delivers the telemetry pipeline that loop runs on.

## How it fits the stack

Observability wraps the whole stack as a sidecar rather than adding a floor to it. It taps
every layer you already built and reads what they emit, without changing how a single
request flows.

```
   client ─► gateway (05/06) ─► vLLM (04/06)        ◄── the request path (unchanged)
                │  /stats/prometheus      │  /metrics
                ▼                         ▼
            Prometheus  ◄── scrape (pull) ──┘         metrics  : is it up?
                │
   agent (07) ─OTLP─► OTel Collector ─► Tempo         traces   : why is it slow?
                │
   Strands Evals ─► a 0-1 score ─► a Prometheus metric  quality: is it good?
                │
                └──────────► Grafana (one pane of glass) ──► YOU
                                                              │
                              steering loop (07/lab-05) ◄─────┘
```

(OTLP = OpenTelemetry's wire protocol: how spans travel from a producer to a collector.)

## Prereqs

- The local **kind** cluster from Phases 05–07 (cluster `kind`, context `kind-kind`),
  with **vLLM** running (Service `vllm` in `default`, OpenAI API on :8000) and the
  kgateway **Gateway `http`** in `kgateway-system`.
- `helm` and `kubectl` (from `00-prep`).
- For lab-02/03: the `agents/` Strands template set up in a venv (see `07/lab-04`).

> Local kind teaches the concepts. On real infra, Phase 09 LKE offers a managed
> Grafana/Prometheus add-on: same dashboards, no operator to babysit. Learn the mechanism
> here; flip on the add-on there.

## Labs

| Lab | Idea | The mechanism it teaches |
|---|---|---|
| 01 | `lab-01-metrics.md`: install kube-prometheus-stack, scrape vLLM's `/metrics` and the gateway's Envoy stats, build the LLM golden-signals dashboard | exporter → scrape (pull) → TSDB → Grafana; a ServiceMonitor is a CRD the Operator compiles into scrape config |
| 02 | `lab-02-traces.md`: wire Strands native OpenTelemetry, run an OTel Collector + Tempo, trace ONE agent request across its span tree | span context propagation; why a trace (not a log) shows where latency hides; ties to `07/lab-05` traces-as-feedback |
| 03 | `lab-03-quality-cost.md`: token/cost accounting from vLLM metrics, a quality score via Strands Evals (LLM-as-judge), and two SLOs | the three questions (up/slow/good) and how they feed the harness steering loop |
| 04 | `lab-04-langfuse.md`: self-host **Langfuse** on LKE (Object + Block Storage), repoint the lab-02 Strands traces at it, get cost + quality + prompt management in one LLM-native pane. **Requires Phase 09 LKE (real cluster + credits): do this after 09, or skip on kind.** | Langfuse is an OTLP backend (one-var swap from Tempo); Grafana = infra, Langfuse = LLM-native |

## The one idea to carry out

**You can't operate what you can't see.** A platform with no metrics is a guess, with no
traces is a black box, with no evals is a liability. Wire all three and the same platform
becomes something you can run: set SLOs on it, debug it at 2am, and feed its own
failures back into the harness that makes it better. That's the difference between "I
deployed an AI platform" and "I operate one."

> Start with `lab-01-metrics.md`. Every chart/image is version-pinned with a
> `# check <upstream> for latest` note; every manifest in `manifests/` is referenced by
> the lab that applies it.

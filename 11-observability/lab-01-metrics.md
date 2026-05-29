# Lab 01 — Metrics: turn the platform into numbers over time

**Goal:** install the Prometheus + Grafana stack, scrape vLLM's native `/metrics` and the
gateway's Envoy stats, and build a dashboard for the **LLM golden signals**. By the end
you can watch your platform's queue depth, token throughput, and latency move in real time
— and explain every link in the pipeline that puts them on a graph.

**Time:** ~45 min · **Cost:** free (local kind)

## The problem (why this exists)

Right now your only window into vLLM is `kubectl logs` and a `curl` that returns a 200.
That answers "is the Pod alive?" and nothing else. It can't tell you tokens/sec, how deep
the request queue is, or what p95 latency looks like under load — all of which are
*time-series* questions about *aggregate* behavior, and none of which a point-in-time log
line can answer. You need a system that samples the platform continuously and remembers.

## What it replaces / why the naive way fails

The naive move is to scrape logs or poll an endpoint from a script. Both break the moment
you have more than one Pod or want history: logs aren't aggregatable, and a polling script
is a fragile, unmonitored exporter you now have to operate. Prometheus inverts this — it
**pulls** structured numeric samples from every target on a schedule into a purpose-built
time-series database, and Grafana queries that. You stop writing collectors and start
declaring *what* to scrape.

## Under the hood (MIT hat): exporter → scrape → TSDB → query

The whole pipeline is four stages, and the key insight is that it's **pull**, not push:

```
 vLLM :8000/metrics        ┌────────────┐ scrape(pull)  ┌──────────────┐  PromQL  ┌─────────┐
 (the EXPORTER) ───────────│ ServiceMon │──────────────►│  Prometheus  │◄─────────│ Grafana │
 gateway :19000            │  (a CRD)   │  every 15s    │  (TSDB)      │          └─────────┘
 /stats/prometheus         └────────────┘               └──────────────┘
        ▲                        ▲                            ▲
   process exposes          Operator COMPILES           samples stored as
   current values           it into scrape config        (metric{labels}, t, value)
```

1. **The exporter** is just an HTTP endpoint returning lines like
   `vllm:num_requests_running 3`. vLLM is its *own* exporter — no sidecar. The gateway's
   Envoy exposes the same format on its admin port. A metric is a name + labels + a float.
2. **The scrape** is Prometheus pulling that endpoint every 15s and recording each value
   with a timestamp. Pull (not push) is deliberate: Prometheus controls the cadence, and a
   target that stops answering becomes a visible **`up == 0`** — a dead push-client just
   goes silent.
3. **The TSDB** stores each sample as `metric_name{label=...} @timestamp = value`. That
   shape is what makes `rate()`, `histogram_quantile()`, and aggregation cheap.
4. **The query** layer is PromQL; Grafana is a PromQL client that draws the results.

The one piece that's Kubernetes-native: the **ServiceMonitor**. You don't hand Prometheus
a config file. You apply a ServiceMonitor *CRD*, and the **Prometheus Operator** (installed
by the chart) watches for it and **compiles** it into Prometheus' scrape config, then
reloads. Same request/grant pattern as everywhere in this track: you write a portable
*request* ("scrape Services like this"), a controller *grants* it.

## 0. Prereqs

vLLM running and the gateway up (Phases 05/06). Verify:

```bash
kubectl get svc vllm -n default
kubectl -n kgateway-system get gateway http
```

## 1. Install kube-prometheus-stack (Operator + Prometheus + Grafana, one chart)

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

# Pin the version. Check artifacthub.io/packages/helm/prometheus-community/kube-prometheus-stack for latest.
helm install monitoring prometheus-community/kube-prometheus-stack \
  --version 86.0.1 \
  --namespace monitoring --create-namespace \
  -f manifests/kps-values.yaml
```

This single chart installs the **Prometheus Operator** (and its CRDs: `ServiceMonitor`,
`PodMonitor`, `PrometheusRule`), a Prometheus server, Grafana, Alertmanager, and the
node/kube-state exporters. The values file (`manifests/kps-values.yaml`) does two
lab-critical things: it flips `serviceMonitorSelectorNilUsesHelmValues: false` so
Prometheus discovers ServiceMonitors in **any** namespace (our vLLM is in `default`), and
it disables the control-plane scrape jobs that don't exist on a kind node.

**What to look for** — wait for the Operator to bring up Prometheus and Grafana:

```bash
kubectl -n monitoring get pods -w
# Expect: prometheus-monitoring-kube-prometheus-prometheus-0  Running
#         monitoring-grafana-...                              Running
kubectl get crd | grep coreos.com    # servicemonitors, podmonitors, prometheusrules now exist
```

The CRDs appearing is the lesson: the Operator just taught your cluster the noun
"ServiceMonitor."

## 2. Scrape vLLM's native metrics with a ServiceMonitor

vLLM exposes Prometheus metrics on the **same port as its API** (8000) at `/metrics`,
prefixed `vllm:`. Confirm the raw endpoint first — never trust a scrape you haven't seen
by hand:

```bash
kubectl -n default port-forward svc/vllm 8000:8000 &
curl -s http://localhost:8000/metrics | grep -E '^vllm:(num_requests|prompt_tokens_total|generation_tokens_total|time_to_first_token)' | head
# vllm:num_requests_running 0.0
# vllm:num_requests_waiting 0.0
# vllm:prompt_tokens_total 0.0
# vllm:generation_tokens_total 0.0
# vllm:time_to_first_token_seconds_bucket{le="0.001"} 0.0   ... (a histogram)
```

Now hand that endpoint to the Operator:

```bash
kubectl apply -f manifests/servicemonitor-vllm.yaml
```

Read it: the `selector.matchLabels` is `app: vllm` (the Service's label), and `port: http`
is the Service **port name** — a ServiceMonitor scrapes by port *name*, not number, which
is why the vllm Service names its port `http`.

**What to look for** — open the Prometheus UI and read its target list:

```bash
kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090 &
# Browser: http://localhost:9090/targets  → find serviceMonitor/default/vllm  State=UP
```

Then run a query (`http://localhost:9090/graph`): `vllm:num_requests_running`. A flat zero
line is success — it means the scrape is working and the model is idle. The series existing
*at all* proves the pipeline end to end: exporter → ServiceMonitor → Operator → scrape →
TSDB.

## 3. Scrape the gateway (Envoy) with a PodMonitor

The gateway's proxy exposes Envoy stats on its **admin port 19000** at
`/stats/prometheus` — a port the routable Gateway Service does *not* publish. So you scrape
the **Pod** directly with a PodMonitor:

```bash
# Confirm the proxy Pod's labels (kgateway's exact label can vary by version):
kubectl -n kgateway-system get pods --show-labels | grep -i http
kubectl apply -f manifests/podmonitor-gateway.yaml
```

If your proxy Pod's label differs from `gateway.networking.k8s.io/gateway-name=http`, edit
`matchLabels` in the manifest to match what `--show-labels` printed — a PodMonitor that
selects nothing produces no target, silently.

**What to look for:** back in `http://localhost:9090/targets`, a
`podMonitor/kgateway-system/gateway-proxy` target in state `UP`. Query
`envoy_http_downstream_rq_xx` — those are the request counters by HTTP status class you'll
use for the error-rate SLO in lab-03.

## 4. Build the LLM golden-signals dashboard

For HTTP, the golden signals are latency/traffic/errors/saturation. For an LLM they
specialize: **latency = time-to-first-token**, **traffic = tokens/sec**, **saturation =
queue depth (`num_requests_waiting`)**, plus the thing only LLMs have — **token cost**.
Import the dashboard (it's stored as code, as a ConfigMap):

```bash
kubectl apply -f manifests/grafana-dashboard-llm.yaml
```

The Grafana **sidecar** watches for ConfigMaps labeled `grafana_dashboard: "1"` and loads
the embedded JSON — no clicking, no export/import dance. Log in:

```bash
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80 &
# Browser: http://localhost:3000  (user: admin / pass: prom-operator)
# Dashboards → "LLM Golden Signals (vLLM)"
```

The panels and their PromQL:
- **In-flight** — `vllm:num_requests_running` vs `vllm:num_requests_waiting` (gauges).
- **TTFT p50/p95/p99** — `histogram_quantile(0.95, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[5m])))`.
  (TTFT is a *histogram*; you read percentiles out of the `_bucket` series — a raw gauge
  can't give you p95.)
- **Throughput** — `rate(vllm:generation_tokens_total[1m])`.
- **Cost / quality / error** — placeholders wired in lab-03.

## 5. Break it #1 — send load and watch the lines move

A dashboard on an idle system is a flat line. Make it react:

```bash
kubectl apply -f manifests/load-generator.yaml    # 60 chat requests, 10 concurrent
```

**Read the dashboard while it runs.** `num_requests_waiting` climbs above zero — that's the
**queue**: more requests arrived than vLLM's batch can serve at once, so they wait. TTFT
p95 rises in lockstep (queued requests wait longer for their first token), and generation
tok/s spikes. This is *saturation* you can finally *see* — the exact signal that, in lab-03,
tells you when to scale or shed load. Clean up: `kubectl delete -f manifests/load-generator.yaml`.

## 6. Break it #2 — kill a scrape target and read `up == 0`

Now break the *pipeline*, not the load, and read how Prometheus reports it:

```bash
kubectl -n default scale deploy/vllm --replicas=0      # the exporter is now gone
# wait ~30s for two scrape intervals, then in http://localhost:9090/targets:
#   serviceMonitor/default/vllm  State = DOWN, Error = "connection refused"
# and query:  up{job="vllm"}   → 0
```

**Read it, that's the lesson.** Prometheus didn't crash and the dashboard didn't go blank —
the **`up`** synthetic metric flipped to `0` and the target went red with *connection
refused*. This is the payoff of **pull**: a target that dies is *loud* (`up == 0` you can
alert on), whereas a push-based system would just stop receiving data and you'd never know
if it was idle or dead. Restore it:

```bash
kubectl -n default scale deploy/vllm --replicas=1
```

## Checkpoint — you can now explain…

1. **What is the metrics pipeline?** Exporter (an HTTP `/metrics` endpoint) → Prometheus
   **pulls** it on a schedule → stores samples in a TSDB → Grafana queries with PromQL.
   vLLM and Envoy are their own exporters.
2. **What is a ServiceMonitor (vs a PodMonitor)?** A CRD the Prometheus Operator compiles
   into scrape config. ServiceMonitor scrapes via a Service port *name*; PodMonitor scrapes
   Pods directly — needed when the port (Envoy's 19000) isn't published by any Service.
3. **Why pull beats push for monitoring?** Prometheus owns the cadence and a dead target is
   visible as `up == 0` — a silent push client is indistinguishable from an idle one.
4. **What are the LLM golden signals?** Latency = TTFT, traffic = tokens/sec, saturation =
   queue depth (`num_requests_waiting`), plus token cost — and TTFT percentiles come out of
   a *histogram*, not a gauge.

You can now:
- [ ] Install kube-prometheus-stack and reach Prometheus + Grafana.
- [ ] Write a ServiceMonitor/PodMonitor and confirm the target is `UP`.
- [ ] Read queue depth and TTFT p95 react to real load.
- [ ] Diagnose a dead target by reading `up == 0`, not a blank graph.

## Tie back / forward

This rides the Phase 03 stack you own: the ServiceMonitor resolves to the vLLM Service's
ClusterIP, and the scrape is plain HTTP DNAT'd by kube-proxy — same machinery as `lab-04`.
Metrics tell you p95 *doubled*; they can't tell you *where* the time went. That's the next
lab: → `lab-02-traces.md` follows **one** request across the gateway, the agent loop, and
the model to show you exactly which hop is slow.

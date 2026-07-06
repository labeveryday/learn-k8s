# Lab 01: Metrics: turn the platform into numbers over time

**Goal:** install the Prometheus + Grafana stack, scrape vLLM's native `/metrics` and the
gateway's Envoy stats, and build a dashboard for the **LLM golden signals** (golden signals
= the four numbers Google's SRE book says you always watch: latency, traffic, errors,
saturation). By the end you can watch your platform's queue depth, token throughput, and
latency move in real time, and explain every link in the pipeline that puts them on a graph.

**Time:** ~45 min · **Cost:** free (local kind)

## The problem (why this exists)

Right now your only window into vLLM is `kubectl logs` and a `curl` that returns a 200.
That answers "is the Pod alive?" and nothing else. It can't tell you tokens/sec, how deep
the request queue is, or what p95 latency looks like under load (p95 = the latency 95% of
requests beat, the slow tail you feel). These are time-series questions about aggregate
behavior, and a point-in-time log line can't answer them. You need a system that samples
the platform continuously and remembers.

## What it replaces / why the naive way fails

The naive move is to scrape logs or poll an endpoint from a script. Both break the moment
you have more than one Pod or want history: logs aren't aggregatable, and a polling script
is a fragile, unmonitored exporter you now have to operate. Prometheus inverts this: it
**pulls** structured numeric samples from every target on a schedule into a purpose-built
time-series database, and Grafana queries that. You stop writing collectors and start
declaring *what* to scrape.

## The pipeline underneath: exporter → scrape → TSDB → query

The whole pipeline is four stages, built on pull rather than push:

```
 vLLM :8000/metrics        ┌────────────┐ scrape(pull)  ┌──────────────┐  PromQL  ┌─────────┐
 (the EXPORTER) ───────────│ ServiceMon │──────────────►│  Prometheus  │◄─────────│ Grafana │
 gateway :19000            │  (a CRD)   │  every 15s    │  (TSDB)      │          └─────────┘
 /stats/prometheus         └────────────┘               └──────────────┘
        ▲                        ▲                            ▲
   process exposes          Operator COMPILES           samples stored as
   current values           it into scrape config        (metric{labels}, t, value)
```

1. **The exporter** is an HTTP endpoint returning lines like
   `vllm:num_requests_running 3`. vLLM is its own exporter, no sidecar. The gateway's
   Envoy exposes the same format on its admin port. A metric is a name + labels + a float.
2. **The scrape** is Prometheus pulling that endpoint every 15s and recording each value
   with a timestamp. Pull, not push, is deliberate: Prometheus controls the cadence, and a
   target that stops answering becomes a visible `up == 0`; a dead push-client goes
   silent.
3. **The TSDB** (time-series database) stores each sample as
   `metric_name{label=...} @timestamp = value`. That shape is what makes `rate()`,
   `histogram_quantile()`, and aggregation cheap.
4. **The query** layer is PromQL (Prometheus' query language); Grafana is a PromQL client
   that draws the results.

The one piece that's Kubernetes-native: the **ServiceMonitor**. You don't hand Prometheus
a config file. You apply a ServiceMonitor *CRD*, and the **Prometheus Operator** (installed
by the chart) watches for it and compiles it into Prometheus' scrape config, then
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

- `--version 86.0.1` pins the chart so the install is reproducible; don't float to
  `latest` or a future chart can move CRD versions out from under the lab.
- `--namespace monitoring --create-namespace` lands the whole stack in its own namespace
  (the namespace doesn't have to exist yet).
- `-f manifests/kps-values.yaml` overrides the chart's defaults with the values below.
  Helm flags layer left-to-right; this file is the deviation from upstream defaults.

This single chart installs the **Prometheus Operator** (and its CRDs: `ServiceMonitor`,
`PodMonitor`, `PrometheusRule`), a Prometheus server, Grafana, Alertmanager, and the
node/kube-state exporters. The values file does only what *this cluster* needs different
from the chart's defaults; here is the whole thing (`manifests/kps-values.yaml`):

```yaml
prometheus:
  prometheusSpec:
    # THE lab-critical flag. By default the operator only discovers ServiceMonitors that
    # carry the chart's release label; flipping these to false makes it discover ANY
    # ServiceMonitor/PodMonitor/PrometheusRule in ANY namespace. Our vLLM is in `default`,
    # not `monitoring`; without this its target silently never appears (#1 "where's my
    # target?" cause).
    serviceMonitorSelectorNilUsesHelmValues: false
    podMonitorSelectorNilUsesHelmValues: false
    ruleSelectorNilUsesHelmValues: false
    retention: 6h                 # how long the TSDB keeps samples; short, since kind has no real PV
    resources:
      requests:
        cpu: "250m"
        memory: 512Mi

grafana:
  adminPassword: "prom-operator"  # known password so the lab logs in deterministically; change in prod
  defaultDashboardsEnabled: true
  sidecar:
    dashboards:
      enabled: true               # turn ON the dashboard sidecar (section 4 depends on this)
      label: grafana_dashboard    # it imports any ConfigMap carrying THIS label key...
      searchNamespace: ALL        # ...found in ANY namespace

# kind is one box, not a cloud fleet: these control-plane components either don't exist or
# aren't reachable on a kind node, so leaving them enabled produces permanently-DOWN
# targets that would muddy section 6's "is this target really down?" lesson. Disable them.
kubeControllerManager:
  enabled: false
kubeScheduler:
  enabled: false
kubeProxy:
  enabled: false
kubeEtcd:
  enabled: false
```

Two fields earn their keep here. The `serviceMonitorSelectorNilUsesHelmValues: false`
trio is the difference between "my ServiceMonitor works" and "I applied it and nothing
happened": a beginner trap because the failure is silent (no error, no target). The Grafana
`sidecar.dashboards` block is what makes section 4's apply-a-ConfigMap trick work at all;
with `enabled: false` your dashboard YAML would apply cleanly and never show up in Grafana.

**What to look for:** wait for the Operator to bring up Prometheus and Grafana:

```bash
kubectl -n monitoring get pods -w
# Expect: prometheus-monitoring-kube-prometheus-prometheus-0  Running
#         monitoring-grafana-...                              Running
kubectl get crd | grep coreos.com    # servicemonitors, podmonitors, prometheusrules now exist
```

The CRDs appearing is the lesson: the Operator taught your cluster the noun
"ServiceMonitor."

## 2. Scrape vLLM's native metrics with a ServiceMonitor

vLLM exposes Prometheus metrics on the same port as its API (8000) at `/metrics`,
prefixed `vllm:`. Confirm the raw endpoint first; never trust a scrape you haven't seen
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

Now hand that endpoint to the Operator. Here is the ServiceMonitor you're applying
(`manifests/servicemonitor-vllm.yaml`); read it before you apply it:

```yaml
apiVersion: monitoring.coreos.com/v1   # the CRD group the Prometheus Operator owns (not a built-in)
kind: ServiceMonitor                   # NOT scrape config you hand Prometheus; a request the Operator compiles
metadata:
  name: vllm
  namespace: default                   # a ServiceMonitor lives WITH the Service it scrapes, in `default`
  labels:
    app: vllm
spec:
  selector:                            # WHICH Services this scrapes, by LABEL, not by name
    matchLabels:
      app: vllm                        # must equal the vllm Service's labels (06-ai-gateway); mismatch → zero targets
  namespaceSelector:
    matchNames:
      - default                        # restrict the search to `default` (where vllm lives)
  endpoints:
    - port: http                       # the Service PORT *NAME*, not 8000; see gotcha below
      path: /metrics                   # the HTTP path to pull (vLLM's metrics share the API port)
      interval: 15s                    # pull cadence; Prometheus controls this, the target doesn't
```

```bash
kubectl apply -f manifests/servicemonitor-vllm.yaml
```

Two fields do the real work, and both are silent failures when wrong:

- **`spec.selector.matchLabels` matches *Services*, not Pods.** A ServiceMonitor selects a
  Service by label; that Service's `selector` in turn finds the Pods. If `app: vllm` here
  doesn't equal the vllm Service's labels, the Operator compiles a scrape job that matches
  nothing: no error, no target. Same label-match indirection as everywhere else in K8s.
- **`port: http` is the Service port *name*, not the number `8000`.** A ServiceMonitor
  scrapes by port *name*, which is why the vllm Service names its port `http`. Put `8000`
  here and the Operator can't resolve it; the target never appears.

**What to look for:** open the Prometheus UI and read its target list:

```bash
kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090 &
# Browser: http://localhost:9090/targets  → find serviceMonitor/default/vllm  State=UP
```

Then run a query (`http://localhost:9090/graph`): `vllm:num_requests_running`. A flat zero
line is success: the scrape is working and the model is idle. The series existing
*at all* proves the pipeline end to end: exporter → ServiceMonitor → Operator → scrape →
TSDB.

## 3. Scrape the gateway (Envoy) with a PodMonitor

The gateway's proxy exposes Envoy stats on its admin port 19000 at
`/stats/prometheus`, a port the routable Gateway Service does not publish. So you scrape
the Pod directly with a PodMonitor:

Here is the PodMonitor (`manifests/podmonitor-gateway.yaml`); note how it differs from
the ServiceMonitor: it selects *Pods* and names a *target port number*, because we're
reaching a port no Service publishes:

```yaml
apiVersion: monitoring.coreos.com/v1   # same Operator CRD group as the ServiceMonitor
kind: PodMonitor                        # scrapes Pods DIRECTLY (bypasses the Service); see why below
metadata:
  name: gateway-proxy
  namespace: kgateway-system            # lives WITH the proxy Pod, in kgateway-system
  labels:
    app: kgateway
spec:
  selector:
    matchLabels:
      gateway.networking.k8s.io/gateway-name: http   # the label kgateway puts on the proxy Pod for Gateway `http`
  namespaceSelector:
    matchNames:
      - kgateway-system                 # search only kgateway-system
  podMetricsEndpoints:
    - targetPort: 19000                 # Envoy's ADMIN/stats port: a NUMBER here, not a name
      path: /stats/prometheus           # Envoy's Prometheus-format stats path
      interval: 15s
```

```bash
# Confirm the proxy Pod's labels (kgateway's exact label can vary by version):
kubectl -n kgateway-system get pods --show-labels | grep -i http
kubectl apply -f manifests/podmonitor-gateway.yaml
```

The two fields that make this a *Pod*Monitor and not a ServiceMonitor:

- **`targetPort: 19000`**: Envoy exposes its Prometheus stats on the admin port `19000`,
  and the routable Gateway Service only fronts `:80`/`:443`. There is no Service port to
  name, so a ServiceMonitor couldn't reach this; a PodMonitor hits the Pod's port
  directly. This is the whole reason this object exists.
- **`selector.matchLabels`** must match a label the proxy Pod carries. kgateway's
  exact label varies by version: if `--show-labels` shows something other than
  `gateway.networking.k8s.io/gateway-name=http` (e.g. `app.kubernetes.io/name=http`), edit
  `matchLabels` to match. A PodMonitor that selects nothing produces no target, silently:
  same trap as the ServiceMonitor.

**What to look for:** back in `http://localhost:9090/targets`, a
`podMonitor/kgateway-system/gateway-proxy` target in state `UP`. Query
`envoy_http_downstream_rq_xx`: those are the request counters by HTTP status class you'll
use for the error-rate SLO in lab-03 (SLO = service-level objective, a target you hold the
platform to, defined there).

## 4. Build the LLM golden-signals dashboard

For HTTP, the golden signals are latency/traffic/errors/saturation. For an LLM they
specialize: **latency = time-to-first-token**, **traffic = tokens/sec**, **saturation =
queue depth (`num_requests_waiting`)**, plus the thing only LLMs have, **token cost**.
The dashboard is stored as code: a ConfigMap whose `data` holds the Grafana JSON. The
mechanism is two fields, so look at the wrapper (`manifests/grafana-dashboard-llm.yaml`,
JSON abbreviated):

```yaml
apiVersion: v1
kind: ConfigMap                          # an ordinary ConfigMap; no Grafana CRD involved
metadata:
  name: grafana-dashboard-llm
  namespace: monitoring                  # where Grafana (and its sidecar) runs
  labels:
    grafana_dashboard: "1"               # THE trigger: the sidecar (kps-values.yaml) imports any ConfigMap with this label
data:
  llm-golden-signals.json: |             # key name is arbitrary; the VALUE is a full Grafana dashboard JSON
    {
      "title": "LLM Golden Signals (vLLM)",
      "uid": "llm-golden-signals",
      "refresh": "10s",
      "panels": [
        {
          "type": "timeseries",
          "title": "In-flight requests (running vs waiting)",
          "datasource": { "type": "prometheus", "uid": "prometheus" },  # which datasource the panel queries
          "targets": [
            { "expr": "vllm:num_requests_running", "legendFormat": "running" },
            { "expr": "vllm:num_requests_waiting", "legendFormat": "waiting (queue)" }
          ]
        }
        // ... five more panels, full PromQL listed below
      ]
    }
```

```bash
kubectl apply -f manifests/grafana-dashboard-llm.yaml
```

Two things make this work, and both are easy to miss because they're *not* the JSON:

- **`labels.grafana_dashboard: "1"`** is the only reason Grafana ever sees this. The
  sidecar you enabled in `kps-values.yaml` (`sidecar.dashboards.label: grafana_dashboard`)
  watches for exactly this label. Drop the label and the ConfigMap applies fine and does
  nothing.
- **`data.<key>` is the literal Grafana dashboard JSON**, not a reference to a file. The
  key name (`llm-golden-signals.json`) is cosmetic; the *value* is the entire dashboard. The
  per-panel `datasource.uid: "prometheus"` is what points each query at the Prometheus the
  chart installed.

This is "dashboards as code": the dashboard lives in git, not in someone's browser, with no
clicking and no export/import dance. Log in:

```bash
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80 &
# Browser: http://localhost:3000  (user: admin / pass: prom-operator, the chart's default; change it for anything real)
# Dashboards → "LLM Golden Signals (vLLM)"
```

The panels and their PromQL:
- **In-flight**: `vllm:num_requests_running` vs `vllm:num_requests_waiting` (gauges).
- **TTFT p50/p95/p99**: `histogram_quantile(0.95, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[5m])))`.
  (TTFT is a *histogram*; you read percentiles out of the `_bucket` series, and a raw gauge
  can't give you p95.)
- **Throughput**: `rate(vllm:generation_tokens_total[1m])`.
- **Cost / quality / error**: placeholders wired in lab-03.

## 5. Break it #1: send load and watch the lines move

A dashboard on an idle system is a flat line. Make it react with a throwaway Job
(`manifests/load-generator.yaml`):

```yaml
apiVersion: batch/v1
kind: Job                                  # a Job runs to COMPLETION then stops; right shape for a one-shot load burst
metadata:
  name: llm-load
  namespace: default
spec:
  completions: 1                           # run the pod once
  backoffLimit: 0                          # do NOT retry on failure; one shot, so a bug doesn't loop forever
  template:
    spec:
      restartPolicy: Never                 # required for Jobs (no restart on exit)
      containers:
        - name: load
          image: curlimages/curl:8.11.1    # a tiny image that's just curl; pinned for reproducibility
          command: ["/bin/sh", "-c"]
          args:
            - |
              BASE="http://vllm.default.svc.cluster.local:8000/v1/chat/completions"  # in-cluster DNS for the vllm Service
              echo "firing 60 requests, 10 at a time, at $BASE"
              for round in $(seq 1 6); do          # 6 rounds...
                for i in $(seq 1 10); do            # ...of 10 backgrounded curls each = 60 total, 10 concurrent
                  curl -s -o /dev/null "$BASE" \
                    -H 'Content-Type: application/json' \
                    -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"Explain Kubernetes Services in three sentences."}],"max_tokens":128}' &
                done
                wait                                # block until this round's 10 finish before the next round
                echo "round $round done"
              done
              echo "load complete"
```

```bash
kubectl apply -f manifests/load-generator.yaml    # 60 chat requests, 10 concurrent
```

The fields that make this generate *visible* saturation rather than a trickle:

- **The `& ... wait` pattern** is what creates concurrency: ten `curl`s launched in the
  background, then `wait` blocks for all ten. Ten in flight against vLLM's batch is what
  pushes `num_requests_waiting` above zero; drop the `&` and they'd run serially, the queue
  would stay empty, and the dashboard wouldn't move.
- **`BASE` uses the in-cluster DNS name** `vllm.default.svc.cluster.local`: the Job runs as
  a Pod, so it reaches the Service the same way any other Pod does (lab-04), no port-forward.
- **`"model": "Qwen/Qwen2.5-0.5B-Instruct"`** must match the model vLLM serves (the
  Phase 05/06 vLLM); a wrong name returns an error instead of a completion and the latency
  panels stay flat.

**Read the dashboard while it runs.** `num_requests_waiting` climbs above zero: that's the
**queue**, where more requests arrived than vLLM's batch can serve at once, so they wait.
TTFT p95 rises in lockstep (queued requests wait longer for their first token), and
generation tok/s spikes. This is saturation you can see: the exact signal that, in lab-03,
tells you when to scale or shed load. Clean up: `kubectl delete -f manifests/load-generator.yaml`.

## 6. Break it #2: kill a scrape target and read `up == 0`

Now break the *pipeline*, not the load, and read how Prometheus reports it:

```bash
kubectl -n default scale deploy/vllm --replicas=0      # the exporter is now gone
# wait ~30s for two scrape intervals, then in http://localhost:9090/targets:
#   serviceMonitor/default/vllm  State = DOWN, Error = "connection refused"
# and query:  up{job="vllm"}   → 0
```

**Read it, that's the lesson.** Prometheus didn't crash and the dashboard didn't go blank:
the `up` synthetic metric flipped to `0` and the target went red with *connection
refused*. This is the payoff of **pull**: a target that dies is loud (`up == 0` you can
alert on), whereas a push-based system would stop receiving data and you'd never know
if it was idle or dead. Restore it:

```bash
kubectl -n default scale deploy/vllm --replicas=1
```

## Checkpoint: you can now explain…

1. **What is the metrics pipeline?** Exporter (an HTTP `/metrics` endpoint) → Prometheus
   **pulls** it on a schedule → stores samples in a TSDB → Grafana queries with PromQL.
   vLLM and Envoy are their own exporters.
2. **What is a ServiceMonitor (vs a PodMonitor)?** A CRD the Prometheus Operator compiles
   into scrape config. ServiceMonitor scrapes via a Service port *name*; PodMonitor scrapes
   Pods directly, needed when the port (Envoy's 19000) isn't published by any Service.
3. **Why pull beats push for monitoring?** Prometheus owns the cadence and a dead target is
   visible as `up == 0`; a silent push client is indistinguishable from an idle one.
4. **What are the LLM golden signals?** Latency = TTFT, traffic = tokens/sec, saturation =
   queue depth (`num_requests_waiting`), plus token cost; and TTFT percentiles come out of
   a *histogram*, not a gauge.

You can now:
- [ ] Install kube-prometheus-stack and reach Prometheus + Grafana.
- [ ] Write a ServiceMonitor/PodMonitor and confirm the target is `UP`.
- [ ] Read queue depth and TTFT p95 react to real load.
- [ ] Diagnose a dead target by reading `up == 0`, not a blank graph.

## Tie back

This rides the Phase 03 stack you own: the ServiceMonitor resolves to the vLLM Service's
ClusterIP, and the scrape is plain HTTP DNAT'd by kube-proxy, the same machinery as `lab-04`.
Metrics tell you p95 *doubled*; they can't tell you *where* the time went. That's the next lab.

## Next

→ `lab-02-traces.md`: metrics tell you p95 latency doubled but not *where*; a trace follows one request across every hop to show which service ate the time.

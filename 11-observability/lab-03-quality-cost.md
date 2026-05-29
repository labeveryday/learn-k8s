# Lab 03 — Quality + cost: is it fast, cheap, AND good?

**Goal:** complete the observability triangle. Derive **token cost** from vLLM's metrics
(a Grafana panel), add a **quality** signal with Strands Evals (LLM-as-judge) surfaced as a
metric, and define two **SLOs** (p95 latency, error rate). By the end you can answer all
three operating questions about your platform — is it **up**, why is it **slow**, is it
**good** — and you can explain how those answers feed the harness steering loop. This is the
maturity capstone of the whole track.

**Time:** ~50 min · **Cost:** free (local kind)

## The problem (why this exists)

Lab-01 (metrics) and lab-02 (traces) answer "is it up?" and "why is it slow?" Two
production questions remain, and they're the ones that get a platform shut down. **Cost:**
an LLM platform's bill scales with *tokens*, and you've been counting tokens without
attaching a dollar figure or a budget — a runaway agent loop (the thing `07/lab-05`'s
budget guard exists for) is invisible until the invoice arrives. **Quality:** a fast, cheap
answer that is *wrong* is worse than no answer. Latency and token graphs are silent on
correctness. You can be green on every dashboard and still be shipping garbage.

## What it replaces / why the naive way fails

The naive quality check is "a human eyeballed a few outputs and they looked fine." That
doesn't scale, isn't continuous, and produces no metric you can trend or SLO. And the naive
cost check is "we'll notice on the bill" — too late, and unattributable. This lab makes both
**measured and continuous**: cost is a PromQL expression over the token counters you already
scrape, and quality is an **LLM-as-judge** that scores outputs on a rubric and emits a
number — the same *inferential sensor* the harness lab named, now wired into your telemetry.

## Under the hood (MIT hat): three questions, one steering loop

Observability matured is three signals, each answering one question, all feeding one loop:

```
   ┌─ is it UP?    ── metrics (lab-01) ── vllm:* gauges, up==0
   │
   ├─ why SLOW?    ── traces  (lab-02) ── span tree, widest bar
   │
   └─ is it GOOD?  ── evals   (THIS lab) ─ LLM-as-judge score 0..1
        │
        ▼  all three land in Grafana + Prometheus
   SLOs (a promise + a number)  ──►  alert when breached
        │
        ▼  a RECURRING breach is the trigger (07/lab-05)
   steering loop: improve the HARNESS (budget, guard, prompt, tool) — not the run
```

Two mechanisms to internalize:
- **Cost is derived, not measured.** vLLM has no "dollars" metric. You take the token
  *counters* it does expose and multiply by a price you set in PromQL —
  `rate(tokens_total) * price`. Cost observability is arithmetic on metrics you already have.
- **Quality needs a judge.** Correctness is *inferential* (semantic, non-deterministic), so
  the sensor is itself a model. Strands' `OutputEvaluator` runs a **judge LLM** against a
  rubric and returns a `score` (0.0–1.0) + a `reason`. You push that score as a metric so it
  trends next to latency and cost — the same panel surface, a different kind of truth.

## 0. Prereqs

Labs 01 and 02 done: Prometheus/Grafana up, vLLM scraped, the LLM dashboard imported, and
the Strands agent instrumented with `StrandsTelemetry`.

## 1. Cost accounting from vLLM token metrics

You already scrape `vllm:prompt_tokens_total` and `vllm:generation_tokens_total` (lab-01).
Turn them into money. The dashboard you imported in lab-01 (`manifests/grafana-dashboard-llm.yaml`)
isn't built in a browser — it's a **ConfigMap** carrying the dashboard JSON, and the Grafana
sidecar imports any ConfigMap labeled `grafana_dashboard: "1"`. That's "dashboards as code":
the panel lives in git. Here is the cost panel, straight from that file's `panels[]` JSON:

```json
{
  "type": "timeseries",
  "title": "Estimated cost ($/min)  —  edit the price constants in the expr",
  "fieldConfig": { "defaults": { "unit": "currencyUSD" } },   // Grafana renders the axis as $
  "targets": [
    {
      "expr": "(rate(vllm:prompt_tokens_total[1m]) * 60 * 0.0000005) + (rate(vllm:generation_tokens_total[1m]) * 60 * 0.0000015)",
      "legendFormat": "$/min (in*0.5e-6 + out*1.5e-6 per token)"
    }
  ]
}
```

That `expr` is the whole trick — pulled out and annotated:

```promql
(rate(vllm:prompt_tokens_total[1m])     * 60 * 0.0000005)   # input tokens  @ $0.50 / 1M
+ (rate(vllm:generation_tokens_total[1m]) * 60 * 0.0000015)  # output tokens @ $1.50 / 1M
# rate(...[1m]) = tokens/sec averaged over 1m; *60 -> tokens/min; the tiny float is $/token.
# 0.0000005 = $0.50 / 1,000,000 tokens;  0.0000015 = $1.50 / 1,000,000 tokens.
```

Gotcha: the two prices are **per-token**, not per-million — `0.0000005`, not `0.50`. Off by
six zeros and your cost panel is off by a million. The `*60` is what makes this a *$/min*
panel (matching the title's `unit: currencyUSD`); drop it and you'd be reading $/sec.

Edit the two constants to *your* economics. On self-hosted vLLM the "price" is really your
amortized GPU-hour cost per token (Phase 09's LKE GPU bill ÷ tokens it can produce) — which
is exactly the number that justifies self-hosting vs a hosted API in a blog or a talk.

**What to look for:** re-run the lab-01 load generator and watch the cost panel rise with
throughput. You now have a live $/min on your inference — the metric a `429` token-limit
(Phase 06) is *protecting*, made visible. Generation tokens cost more than prompt tokens;
the panel shows you which half of the bill a workload drives.

## 2. A quality signal: Strands Evals (LLM-as-judge)

Score the agent's *output* with a judge model against a rubric. Strands Evals provides
`OutputEvaluator` for exactly this. Install and write a tiny eval harness:

```bash
cd agents && source .venv/bin/activate
pip install strands-agents-evals    # the Strands Evals SDK; imports as `strands_evals`
                                    # check pypi.org/project/strands-agents-evals for latest (0.2.0+)
```

```python
# eval_quality.py — score agent answers with an LLM judge, against a rubric.
from strands import Agent
from strands.models.openai import OpenAIModel
from strands_evals import Case, Experiment
from strands_evals.evaluators import OutputEvaluator

# Your in-cluster vLLM as both the agent's model AND the judge (one model, no API key).
vllm = OpenAIModel(
    client_args={"api_key": "EMPTY", "base_url": "http://localhost:8000/v1"},
    model_id="Qwen/Qwen2.5-0.5B-Instruct",
)

def get_response(case: Case) -> str:
    agent = Agent(model=vllm, system_prompt="You are a concise Kubernetes expert.",
                  callback_handler=None)
    return str(agent(case.input))

cases = [
    Case[str, str](name="svc", input="What is a Kubernetes Service, in one sentence?",
                   expected_output="A stable virtual IP + DNS name in front of a set of Pods."),
]

judge = OutputEvaluator(
    model=vllm,                       # judge with the same self-hosted model
    rubric="""Score the answer for correctness and concision about Kubernetes.
              1.0 = correct and concise; 0.5 = partially correct; 0.0 = wrong or off-topic.""",
    include_inputs=True,
)

experiment = Experiment[str, str](cases=cases, evaluators=[judge])
reports = experiment.run_evaluations(get_response)
reports[0].run_display()              # prints score (0..1) + the judge's reason
```

```bash
kubectl -n default port-forward svc/vllm 8000:8000 &   # local :8000 -> vllm Service :8000;
                                                       # matches the base_url in the script.
                                                       # '&' backgrounds it so the prompt returns.
python eval_quality.py
```

The script never talks to the cluster directly — it hits `http://localhost:8000/v1`, which the
port-forward tunnels to the in-cluster `vllm` Service. Same model serves the agent *and* the
judge, so there's no second deployment and no API key (`api_key: "EMPTY"`).

**What to look for:** each result has a **`score`** (0.0–1.0), a **`test_pass`** boolean,
and a **`reason`** — the judge's written justification. That `reason` is the difference
between "quality dropped" and "quality dropped *because answers stopped citing the Service's
DNS name*." A computational check (lab-01's `429`) can't give you that; an inferential one
can. (Tiny CPU judge models are noisy — the *mechanism* is the lesson; use a stronger judge
in prod.)

## 3. Surface the quality score as a metric

A score in your terminal isn't observable. Push it to Prometheus so it trends beside
latency and cost. Add a **Pushgateway** (for short-lived eval jobs that Prometheus can't
scrape directly) and emit `strands_eval_score` — the exact series the dashboard's
**"Quality score"** panel (also in `grafana-dashboard-llm.yaml`) already queries:

```json
{
  "type": "stat",
  "title": "Quality score (Strands eval, 0-1)",
  "fieldConfig": { "defaults": { "min": 0, "max": 1, "unit": "percentunit" } },  // 0..1 as %
  "targets": [
    { "expr": "avg_over_time(strands_eval_score[15m])", "legendFormat": "avg quality" }
  ]
}
```

The metric name in the panel's `expr` (`strands_eval_score`) must match the name you push
*exactly* — that's the contract between the eval job and the dashboard. `avg_over_time(...[15m])`
smooths the noisy one-shot scores into a trend.

```bash
# The pushgateway chart ships its ServiceMonitor DISABLED by default — enable it so the
# kube-prometheus Operator scrapes the pushed metric. Check artifacthub.io for the latest chart.
helm install pushgw prometheus-community/prometheus-pushgateway \
  --namespace monitoring \                  # same ns as Prometheus, so the Operator can find it
  --set serviceMonitor.enabled=true         # creates a ServiceMonitor CRD -> Operator scrapes it
kubectl -n monitoring port-forward svc/pushgw-prometheus-pushgateway 9091:9091 &  # local push port
```

- `helm install pushgw <chart>` names the release `pushgw`; that's why the Service is
  `pushgw-prometheus-pushgateway` (release name + chart name) — the port-forward target must
  match exactly.
- `--set serviceMonitor.enabled=true` is the load-bearing flag. The Pushgateway *receives*
  pushed metrics but does not scrape itself; the ServiceMonitor is the CRD that tells the
  kube-prometheus Operator "scrape this endpoint." Without it the metric arrives at the
  gateway and is never collected — the `Quality score` panel stays "No data."
- The `:9091` port-forward is for the *push* side: your `eval_quality.py` POSTs the score to
  `localhost:9091`. The *scrape* side (Operator → gateway) stays in-cluster.

Append to `eval_quality.py` after the run:

```python
import requests
# Per strands-agents-evals 0.2.0 the per-case score lives here. If your installed
# version differs, inspect with reports[0].run_display() or vars(reports[0].case_results[0]).
score = reports[0].case_results[0].evaluation_output.score   # the judge's 0..1 score
requests.post("http://localhost:9091/metrics/job/strands_eval",   # /metrics/job/<job> = the Pushgateway API
              data=f"strands_eval_score {score}\n")                # Prometheus text format: "<name> <value>\n"
```

The pushed metric name (`strands_eval_score`) is exactly what the panel's `expr` queries
above. The `/metrics/job/strands_eval` path is the Pushgateway's grouping key — it labels the
series with `job="strands_eval"`, which is why the Operator's scrape (and the panel) find it.

The `--set serviceMonitor.enabled=true` above is what makes Prometheus ingest
`strands_eval_score` — the chart does **not** scrape itself by default, so without that flag
the metric is pushed but never collected.

> Alternative (no Pushgateway): in lab-02 you called
> `strands_telemetry.setup_meter(enable_otlp_exporter=True)`. Strands' OTLP **meter** can
> emit a custom gauge straight through the OTel Collector to Prometheus — same destination,
> one fewer component. Pushgateway is shown here because it's the most explicit "see the
> number arrive" path for a one-shot eval job.

**What to look for:** the dashboard's **"Quality score (Strands eval, 0–1)"** stat panel
goes from "No data" to a number. All three signals — cost, latency, quality — now live on
one pane.

## 4. Define two SLOs

An SLO is a promise with a number: a target you hold the platform to. Encode two as
Prometheus rules. Here is the whole CRD (`manifests/prometheusrule-slo.yaml`) — a
`PrometheusRule` is the same Operator-watched-CRD pattern as the ServiceMonitor: you
*declare* the rule and the Operator compiles it into Prometheus' rule files and reloads.

```yaml
apiVersion: monitoring.coreos.com/v1   # the CRD group the kube-prometheus Operator owns
kind: PrometheusRule                   # not Prometheus-native — the Operator translates it
metadata:
  name: llm-slo
  namespace: default                   # rule can live in ANY ns; the selector below is what binds it
  labels:
    app: vllm
    # THE load-bearing label: stock kube-prometheus-stack's ruleSelector matches release=<name>.
    # Our kps-values.yaml sets ruleSelectorNilUsesHelmValues=false so any namespace is scanned;
    # without this 'release' label the Operator would ignore the rule and it'd never load.
    release: monitoring
spec:
  groups:
    - name: llm-slo.rules              # the group name you'll see in Prometheus UI > Status > Rules
      rules:
        # --- Recording rule: precompute p95 TTFT into a NEW series, cheap to graph/alert on.
        - record: llm:ttft_p95_seconds # 'record:' = derived series name (the ':' is the recording-rule convention)
          expr: histogram_quantile(0.95, sum by (le) (rate(vllm:time_to_first_token_seconds_bucket[5m])))

        # --- SLO 1: latency. Page if p95 TTFT stays above 5s for 5 minutes.
        - alert: LLMLatencySLOBreached # 'alert:' = an alert rule (vs 'record:'); this is the alert NAME
          expr: llm:ttft_p95_seconds > 5   # reuses the recording rule above — don't recompute the quantile
          for: 5m                      # the condition must hold continuously 5m -> 'pending' then 'firing'
          labels:
            severity: warning          # routed by Alertmanager; attaches to the alert
          annotations:
            summary: "vLLM p95 time-to-first-token over 5s SLO"
            description: "p95 TTFT = {{ $value }}s (SLO: < 5s) for 5m."   # {{ $value }} = the breaching number

        # --- SLO 2: error rate. Page if gateway 5xx exceeds 1% for 5 minutes.
        - alert: LLMErrorSLOBreached
          expr: |
            sum(rate(envoy_http_downstream_rq_xx{envoy_response_code_class="5"}[5m]))
              / clamp_min(sum(rate(envoy_http_downstream_rq_xx[5m])), 1) > 0.01   # 5xx / all > 1%
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "Gateway 5xx rate over 1% SLO"
            description: "5xx fraction = {{ $value }} (SLO: < 0.01) for 5m."
```

```bash
kubectl apply -f manifests/prometheusrule-slo.yaml
```

The fields that make it work:
- **`record:` vs `alert:`** — a recording rule precomputes an expensive expression into a
  new series (`llm:ttft_p95_seconds`); the alert rule then references that cheap series
  instead of recomputing the p95 quantile on every evaluation. SLO 1 does exactly this.
- **`for: 5m`** is what turns a *spike* into an *SLO breach*. The condition has to stay true
  for the full window before the alert leaves `pending` and starts `firing` — so a one-off
  blip doesn't page you.
- **`clamp_min(..., 1)`** in SLO 2 guards against divide-by-zero: with no traffic the
  denominator would be 0, so it's clamped to a floor of 1 and the fraction reads 0, not `NaN`.
- Beginner gotcha: the **`release: monitoring`** label is not decoration. On stock
  kube-prometheus-stack the Operator only picks up `PrometheusRule`s whose labels match its
  `ruleSelector`. Drop that label (or mistype the release name) and `kubectl apply` succeeds,
  the object exists — but it **never reaches Prometheus** and no group shows up under Status >
  Rules. A silent failure, exactly like the ServiceMonitor's label trap.

**What to look for:**

```bash
# Prometheus UI → Status → Rules: the llm-slo.rules group is present and green.
# Query the recording rule directly:  llm:ttft_p95_seconds
```

Run the load generator hard enough and watch `LLMLatencySLOBreached` move from `inactive`
→ `pending` (the `for: 5m` clock is counting) → `firing` (it elapsed). Watch this in
Prometheus UI → **Status → Alerts** (not Rules — the alert state machine lives there). That
state machine *is* the SLO.

## 5. Break it, then read the error (Kelsey lens)

Make a *quality* regression that every other signal misses:

```bash
# In eval_quality.py, change the system_prompt= argument inside get_response()'s Agent(...)
# call (NOT the judge's rubric) to something that tanks answers:
#   system_prompt="Answer every question with a haiku about the weather."
python eval_quality.py
```

**Read it.** Latency is fine. Token cost is fine. The Pod is `up`. The trace is a clean span
tree. **And the answer is useless** — `strands_eval_score` drops toward 0 and the judge's
`reason` says "off-topic." This is the entire argument for the quality signal in one move:
**a platform can be green on every infra metric and still be failing its users.** Up, fast,
and cheap is necessary and *not sufficient*. Only the eval caught it. Restore the prompt.

## 6. Close the loop: this is the maturity capstone

You now hold all three answers, and they map straight onto the `07/lab-05` steering loop:

| Question | Signal | When it breaches, the harness response |
|---|---|---|
| Is it **up**? | metrics — `up`, queue depth | scale / shed load; check the `429` token guard |
| Why **slow**? | traces — the widest span | tighten the slow tool, cap iterations, cache |
| Is it **good**? | evals — judge score + reason | fix the prompt, add a verify sensor, swap model |

A *one-off* breach is noise; a *recurring* one is the trigger to change the **harness** — a
budget, a loop guard, a prompt guard, a verification step — not to retry the run. That's the
loop `07/lab-05` described; this lab gives it the three real sensors it was always missing.
**That is the difference between deploying an AI platform and operating one.**

## Checkpoint — you can now explain…

1. **Where does cost observability come from?** It's *derived* — `rate(token_counter) *
   price` in PromQL. vLLM exposes tokens, not dollars; you supply the price (your amortized
   GPU cost per token, self-hosted).
2. **Why does quality need an LLM-as-judge?** Correctness is inferential/semantic, so the
   sensor is a model scoring against a rubric (`OutputEvaluator` → score + reason) — a
   deterministic check can't judge meaning.
3. **What is an SLO and how do you encode one?** A promise with a number (p95 TTFT < 5s,
   5xx < 1%), encoded as a `PrometheusRule` the Operator compiles; it alerts only after the
   condition holds for `for:`.
4. **What are the three observability questions and how do they feed the harness?** Up
   (metrics), slow (traces), good (evals); a recurring breach of any one is the steering
   loop's cue to improve a harness control, not to retry.

You can now:
- [ ] Build a token-cost panel and tune it to your own price/economics.
- [ ] Score agent outputs with Strands Evals and surface the score as a metric.
- [ ] Encode latency and error SLOs as PrometheusRules and watch one fire.
- [ ] State why "up, fast, cheap" is necessary but not sufficient — and what catches the gap.

## Tie back

Every signal here rides machinery you already own: the cost panel reads counters scraped via
the lab-01 ServiceMonitor (Phase 03 Service/DNAT underneath), the SLO error rule reads the
Envoy metrics from your Phase 06 gateway, and the quality score runs your Phase 06 vLLM as
the judge — no external API. On real infra, **Phase 09 LKE's managed Grafana/Prometheus
add-on** gives you this whole stack without operating the Operator; the dashboards, the
ServiceMonitors, and the PrometheusRules you wrote here apply unchanged.

Across Phases 04–10 you *built* an AI platform; here you made it **observable** — and an
observable platform you can hold to an SLO and improve through its own feedback is the
deliverable that *is* a senior platform engineer's job.

## Next

→ `lab-04-langfuse.md`: labs 01–03 built the **infra** observability pane (Grafana) and the
vendor-neutral mechanism by hand. Now add the **LLM-native** pane — self-host **Langfuse** on
LKE and point these *same* OTLP traces at it for per-request token cost, quality scores, and
prompt management in one place. Grafana for the platform; Langfuse for the model.

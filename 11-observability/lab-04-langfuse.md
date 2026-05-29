# Lab 04 — Langfuse: LLM-native observability on your cluster

**Goal:** self-host **Langfuse** on your LKE cluster, repoint the *same* Strands traces from
lab-02 at it with one env var, and get the LLM-native view your Prometheus/Grafana stack
can't give: per-request LLM traces **plus** token cost, output quality scores, and prompt
management — in one pane. By the end you can say exactly where Langfuse *complements* the
infra stack (and where it doesn't), and you'll have run a real six-component app on Akamai
Object + Block Storage.

**Time:** ~60 min · **Cost:** 💸 LKE (Block Storage PVCs + Object Storage bucket) — tear down after

## The problem (why this exists)

labs 01–03 gave you three answers with three tools: Grafana (is it **up**?), Tempo (why
**slow**?), and a hand-built Pushgateway score (is it **good**?). But notice what Tempo
*doesn't* know: it shows you spans and timings, and nothing about **tokens, cost, the actual
prompt/completion text, or output quality**. For LLM apps that's most of what you care about,
and you bolted a thin version of it together by hand in lab-03. There's a purpose-built tool
that does **traces + token cost + evals + prompt management** in one place — **Langfuse** —
and the honest question isn't "Langfuse *or* Grafana," it's "what does each one own?"

## What it replaces / why the naive way fails

It does **not** replace your metrics stack. The clean split — and the spine of this lab:

| | Grafana / Prometheus (labs 01–03) | Langfuse (this lab) |
|---|---|---|
| Answers | is it **up / saturated / in SLO**? | what did this LLM call **cost, say, and was it good**? |
| Unit | time-series (queue depth, TTFT, 5xx) | a **trace**: one request's LLM/tool/retrieval tree |
| Token cost | PromQL arithmetic you wrote (lab-03) | **native**, from a built-in price table |
| Quality | a Pushgateway hack (lab-03) | **native** LLM-as-judge scores on traces |
| Prompts / datasets | — (impossible) | versioned prompts, datasets, experiments |
| Infra metrics | ✅ the whole point | ❌ none — no scrape, no SLOs, no queue depth |

The naive instinct — "I'll read LLM quality and cost off Grafana" — fails because those
signals *aren't in infra metrics*; lab-03 proved you can hand-build them, and Langfuse is the
batteries-included version you graduate to. So: **Grafana = infra observability, Langfuse =
LLM-native observability.** Two complementary panes.

## Under the hood (MIT hat): a six-component system, and one OTLP swap

**(a) Langfuse v3 is not a container — it's a distributed system.** Two app tiers (web +
worker) and four stateful backends, and this is where Akamai earns its keep:

```
   Langfuse Web (UI/API)  ─┐
   Langfuse Worker         │── PostgreSQL  (transactional: projects, users)   ◄─┐ Block Storage
                           │── ClickHouse  (OLAP: traces/observations/scores)  ◄─┘ (CSI PVCs)
                           │── Redis/Valkey (queue + cache)
                           └── S3 blob store (raw events, large payloads)       ◄── Object Storage
```

ClickHouse is *why v3 exists*: a column store makes "scan millions of LLM calls, group by
model, sum the cost" fast — the analytics a row store (Postgres) chokes on. That's also why
this is an **LKE** lab: on kind the Object Storage and Block Storage are stubs (the Phase 09
gap), and the ~16 GiB footprint is contrived. On LKE, **Linode Object Storage** is the S3 blob
backend and **Block Storage** (the `linode-block-storage` CSI from Phase 09) backs the
ClickHouse + Postgres PVCs — one workload exercising both Akamai storage primitives. (Count
check: with the bundled MinIO turned off in favor of Object Storage, **five** of the six
components run *in* the cluster — the S3 blob store is the sixth, now external.)

**(b) Ingestion is just OTLP — so it's a one-env-var change from lab-02.** Langfuse *is* an
OpenTelemetry backend: it accepts OTLP/HTTP at `/api/public/otel`, authenticated by HTTP
Basic auth (`base64(public_key:secret_key)`). Your lab-02 agent already exports OTLP via
`StrandsTelemetry` — you just change the endpoint:

```
   Strands agent ── StrandsTelemetry / OTLP exporter ──►  ┌─ OTel Collector → Tempo   (lab-02)
                         (same spans, swap the endpoint)   └─ Langfuse /api/public/otel (this lab)
```

This is the literal proof of lab-02's promise ("swap the trace backend with zero agent
changes"). Langfuse maps the `gen_ai.*` spans Strands emits into a trace of **observations**,
and *derives* token usage, **cost** (matching the model name against its price table), the
prompt/completion text, and latency. Scores/evals are a layer Langfuse adds *after* ingestion
(LLM-as-judge, SDK, or manual) — they are **not** sent by Strands.

## 0. Prereqs

- An **LKE cluster** (Phase 09) with the `linode-block-storage` StorageClass and `helm`.
  (kind works for a toy demo but the footprint + storage story only make sense on LKE.)
- A **Linode Object Storage** bucket + access key/secret (for the blob store), and its
  regional S3 endpoint.
- The lab-02 Strands agent, instrumented with `StrandsTelemetry` and reachable to your model.

## 1. Generate the required secrets

Langfuse needs three secrets (and the bundled datastores need passwords):

```bash
kubectl create namespace langfuse
echo "SALT=$(openssl rand -base64 32)"
echo "NEXTAUTH_SECRET=$(openssl rand -base64 32)"
echo "ENCRYPTION_KEY=$(openssl rand -hex 32)"   # MUST be 64 hex chars
```

Put these (and DB passwords) into `manifests/langfuse-values.yaml`. For real use, source them
from a `Secret` rather than inline values — the lab inlines them for readability.

## 2. Install Langfuse via the official Helm chart

```bash
helm repo add langfuse https://langfuse.github.io/langfuse-k8s && helm repo update
# chart 1.5.32 / Langfuse v3.175.0 at time of writing — check artifacthub.io/packages/helm/langfuse-k8s/langfuse for latest
helm install langfuse langfuse/langfuse -n langfuse \
  --version 1.5.32 \
  -f manifests/langfuse-values.yaml
kubectl -n langfuse get pods,pvc
```

**What to look for:** the `langfuse-web` and `langfuse-worker` pods reach `Ready`, and the
**Postgres + ClickHouse PVCs are `Bound` on `linode-block-storage`** (real Akamai Block
Storage, not a kind hostPath). If a PVC won't Bind, its persistence key is a Bitnami
*subchart passthrough* — run `helm get values langfuse -n langfuse` and check the subchart's
`persistence.*` path. The bundled **MinIO is disabled** — `langfuse-values.yaml` sets
`s3.deploy: false` and points the blob store at your **Linode Object Storage** bucket instead.
ClickHouse's chart default is HA and won't fit a learning pool (`replicaCount: 3`,
`resourcesPreset: 2xlarge`); the values file sets **`clusterEnabled: false` + `replicaCount: 1`**
to run a single node. (Heads-up: the chart pins `bitnamilegacy/*` images by default — if a
pull fails, override that subchart's `image.repository`.)

## 3. Open the UI and get project keys

```bash
kubectl -n langfuse port-forward svc/langfuse-web 3000:3000 &
# open http://localhost:3000 → create an org + project → Settings → API Keys
#   copy the public key (pk-lf-...) and secret key (sk-lf-...)
```

## 4. Repoint lab-02's Strands traces at Langfuse (the one-var swap)

In the lab-02 telemetry setup, point the *same* OTLP exporter at Langfuse instead of the
Collector — Langfuse is just another OTLP target:

```python
import os, base64
from strands.telemetry import StrandsTelemetry

LF_AUTH = base64.b64encode(f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()).decode()
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:3000/api/public/otel"  # your port-forwarded Langfuse
os.environ["OTEL_EXPORTER_OTLP_HEADERS"]  = f"Authorization=Basic {LF_AUTH},x-langfuse-ingestion-version=4"
StrandsTelemetry().setup_otlp_exporter()      # same call as lab-02 — only the endpoint changed
```

(The SDK appends `/v1/traces` to the generic endpoint; if you prefer, set
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=.../api/public/otel/v1/traces` explicitly. Langfuse's v3
`get_client()` pattern — `LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY` env vars — is the documented
alternative and auto-wires the exporter.) Run the agent and ask it something.

**What to look for:** in Langfuse, the **same `Agent → cycle → model-invoke → tool` span tree**
you saw in Tempo (lab-02) — but now each LLM span carries the **model, token counts,
prompt/completion text, and a derived cost**. That's the payoff: identical instrumentation,
an LLM-aware destination. You just proved lab-02's "OTLP is portable" claim by doing it.

## 5. Cost + quality, natively (absorbing lab-03's LLM side)

- **Cost:** open a trace → Langfuse shows per-observation token usage and a cost figure it
  *infers* from its model price table (not provider billing — accurate only if the model name
  matches and the price entry is current).
- **Quality:** add an **LLM-as-judge** evaluator in Langfuse (Evaluations → set a judge model
  + rubric) and it scores observations automatically — the same "is it good?" signal you
  hand-pushed via Pushgateway in lab-03, with no plumbing. Scores attach to the trace.

## 6. The things the metrics stack simply can't do

- **Prompt management:** create a versioned prompt in Langfuse, deploy a new version, and (if
  you wire the SDK's prompt fetch) roll it back — version control for prompts, impossible in
  Prometheus.
- **Datasets + experiments:** define a small test set, run the agent over it, and compare
  scores across prompt/model versions — offline eval, next to your live traces.

## Break it, then read the error (Kelsey lens)

Repoint the agent at Langfuse but use a **wrong/empty secret key** (or drop the auth header),
then run it:

```python
os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = "Authorization=Basic d3Jvbmc="   # bogus creds
```

**Read it.** The agent answers normally — and **no trace appears in Langfuse.** Langfuse
**401s** the OTLP push and the OTel exporter drops the spans; nothing in the agent surfaces an
error. This is lab-02's "silent exporter" blind spot with an auth twist: telemetry is
*fire-and-forget*, so a bad endpoint or bad credentials fails **invisibly** on the app side.
The readable artifact lives at the **destination**, not the agent — that's where you look:

```bash
kubectl -n langfuse logs deploy/langfuse-web | grep -i -E 'auth|401|unauthor'
```

Lesson: a fire-and-forget exporter fails silently *at the source*, so when traces vanish you
debug the **receiver's** logs, not the app's. (The other classic: pointing at
`/api/public/otel` vs `/api/public/otel/v1/traces` wrong, so the SDK posts to the wrong path.)
Fix the key, re-run, watch the trace land.

## Checkpoint — you can now explain…

1. **What does Langfuse own vs Grafana?** Langfuse = LLM-native (per-request traces + token
   cost + quality scores + prompt/dataset management); Grafana/Prometheus = infra (up,
   saturation, SLOs). Complementary panes, no overlap.
2. **Why is pointing Strands at Langfuse a one-line change?** Langfuse is an OTLP backend; the
   lab-02 exporter just needs a new endpoint + Basic-auth header. Proof of OTLP portability.
3. **Why six components, and why LKE?** web + worker + Postgres + ClickHouse (OLAP, the reason
   v3 exists) + Redis + S3 blob store; the footprint + Object/Block Storage only make sense on
   real infra — Langfuse exercises both Akamai storage primitives in one app.
4. **Why is Langfuse's cost figure approximate?** It's *inferred* from a model price table by
   name-matching, not pulled from provider billing.

You can now:
- [ ] Self-host Langfuse on LKE with the official Helm chart, backed by Object + Block Storage.
- [ ] Repoint Strands OTLP traces from Tempo to Langfuse with one env change.
- [ ] Read token cost + an LLM-as-judge score on a real trace, and version a prompt.
- [ ] Say precisely which observability question each pane answers — and which it can't.

## Tie back / forward

This closes the observability picture: lab-01 (metrics) + lab-02 (traces) + lab-03 (a
hand-built quality signal) taught the **vendor-neutral mechanism**; Langfuse is the
**LLM-native pane** you graduate to, and the scores it produces feed the **`07/lab-05` harness
steering loop** — a recurring low score is the cue to change a guide or sensor, not to retry.
On real infra, your Block Storage backs Langfuse's analytics and Object Storage holds its
events — the same Phase 09 primitives, now serving your *observability* plane.

## Sources

- Langfuse self-hosting + Helm: <https://langfuse.com/self-hosting/deployment/kubernetes-helm> ·
  chart repo <https://github.com/langfuse/langfuse-k8s> ·
  blob storage <https://langfuse.com/self-hosting/deployment/infrastructure/blobstorage>
- Langfuse OpenTelemetry ingestion: <https://langfuse.com/integrations/native/opentelemetry> ·
  Strands integration: <https://langfuse.com/integrations/frameworks/strands-agents>

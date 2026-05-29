# Lab 04 — Langfuse: LLM-native observability on your cluster

**Goal:** self-host **Langfuse** on your LKE cluster, repoint the *same* Strands traces from
lab-02 at it with one env var, and get the LLM-native view your Prometheus/Grafana stack
can't give: per-request LLM traces **plus** token cost, output quality scores, and prompt
management — in one pane. By the end you can say exactly where Langfuse *complements* the
infra stack (and where it doesn't), and you'll have run a real six-component app on Akamai
Object + Block Storage.

**Time:** ~60 min · **Cost:** 💸 LKE (Block Storage PVCs + Object Storage bucket) — tear down after

> **Requires Phase 09 LKE** (a real cluster + credits): labs 01–03 run on local kind, but
> this capstone needs LKE's Object + Block Storage. Do it *after* Phase 09 — or skip on kind.

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
model, sum the cost" fast — that scan-and-aggregate workload is **OLAP** (vs **OLTP**, the
single-record reads/writes Postgres is built for), the analytics a row store chokes on. That's also why
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

Langfuse needs three app secrets (and the bundled datastores need passwords):

```bash
kubectl create namespace langfuse
echo "SALT=$(openssl rand -base64 32)"           # hashes API keys at rest
echo "NEXTAUTH_SECRET=$(openssl rand -base64 32)" # signs the web-UI session/JWT
echo "ENCRYPTION_KEY=$(openssl rand -hex 32)"     # encrypts stored integration secrets — MUST be 64 hex chars
```

- `ENCRYPTION_KEY` is `-hex 32` (32 bytes → **64 hex chars**), *not* `-base64`. Wrong length is
  the #1 install failure here: the worker crash-loops on boot with an opaque key-length error.
- All three are read at startup; rotating `ENCRYPTION_KEY` later orphans every secret it
  already encrypted, so generate once and keep it.

These three values land in the `langfuse:` block of the Helm values file (next section). For
real use, source them from a `Secret` rather than inline — the lab inlines them for readability.

## 2. The values file — what each block does

The chart is one `helm install`, but the *values file* is where all the architecture
decisions live: which of the six components run in-cluster, where their data lands, and how
the blob store is swapped to Akamai. Here is the whole `manifests/langfuse-values.yaml`, then
the fields that carry weight:

```yaml
langfuse:
  # the three step-1 secrets — note each is an OBJECT with a `.value`, not a bare string
  salt:
    value: "REPLACE_WITH_openssl_rand_base64_32"           # openssl rand -base64 32
  nextauth:
    secret:
      value: "REPLACE_WITH_openssl_rand_base64_32"         # openssl rand -base64 32
  encryptionKey:
    value: "REPLACE_WITH_openssl_rand_hex_32"              # openssl rand -hex 32 → 64 hex chars
  web:
    replicas: 1            # UI/API tier — light; one replica is fine for a lab
  worker:
    replicas: 1            # async ingestion/eval tier — also light

# --- Bundled datastores: keep, but single-node + on Akamai Block Storage -------------
postgresql:
  deploy: true            # OLTP store (projects, users) runs IN the cluster
  auth:
    password: "REPLACE_pg_password"
  primary:
    persistence:
      enabled: true
      storageClass: linode-block-storage   # Phase 09 CSI → a real Block Storage volume on LKE
      size: 10Gi

clickhouse:
  deploy: true            # OLAP store (traces/observations/scores) — the reason v3 exists
  auth:
    password: "REPLACE_ch_password"
  replicaCount: 1         # default is 3 (HA); one node is all a learning pool can hold
  clusterEnabled: false   # THE HA lever — replicaCount:1 + clusterEnabled:false = one non-HA node
  resourcesPreset: small  # default 2xlarge won't schedule on a small node pool
  persistence:            # Bitnami subchart passthrough — see the gotcha below if the PVC won't Bind
    enabled: true
    storageClass: linode-block-storage
    size: 20Gi            # ClickHouse is disk-heavy even for a lab

redis:                    # bundled Valkey (Redis-compatible) — queue + cache
  deploy: true
  auth:
    password: "REPLACE_redis_password"

# --- Blob store: DISABLE bundled MinIO, use Linode Object Storage (S3-compatible) -----
s3:
  deploy: false           # do NOT run the bundled MinIO; point at external Object Storage
  bucket: "langfuse-events"                        # create this bucket in Object Storage FIRST
  region: "us-ord-1"                               # your Linode Object Storage region
  endpoint: "https://us-ord-1.linodeobjects.com"   # the regional S3 endpoint
  forcePathStyle: true    # Linode Object Storage is S3-compatible; path-style addressing
  # credentials are OBJECTS with a `.value` — NOT plain strings (same shape as the secrets above)
  accessKeyId:
    value: "REPLACE_linode_obj_access_key"
  secretAccessKey:
    value: "REPLACE_linode_obj_secret_key"
```

The three blocks that make this a *cluster-friendly* install — and where beginners slip:

- **`s3.deploy: false`** is the Akamai swap. Left at the default `true`, the chart runs a
  bundled MinIO Pod (the sixth component, in-cluster). Setting it `false` and filling
  `bucket/region/endpoint/credentials` repoints the blob store at your **Linode Object
  Storage** bucket — so **five** components run in-cluster and the blob store is external. The
  bucket must **already exist**; the chart won't create it.
- **ClickHouse single-node** is `replicaCount: 1` **and** `clusterEnabled: false` together —
  one without the other still tries to form/expect a cluster. The chart default is HA
  (`replicaCount: 3`, `resourcesPreset: 2xlarge`), which simply won't schedule on a learning
  pool. `resourcesPreset: small` shrinks the request to fit.
- **Credentials are objects, not strings.** `salt`, `nextauth.secret`, `encryptionKey`,
  `accessKeyId`, `secretAccessKey` each take a `.value:` subkey (or a `.secretKeyRef` to a real
  Secret in prod). Writing `salt: "..."` instead of `salt: {value: "..."}` is a silent
  mis-set the chart ignores — a classic gotcha.
- **`storageClass: linode-block-storage`** ties the Postgres + ClickHouse PVCs to the Phase 09
  CSI driver, so each `persistence` block provisions a *real* Akamai Block Storage volume. On
  kind there's no such class and the PVCs sit `Pending` — the whole reason this is an LKE lab.

## 3. Install Langfuse via the official Helm chart

```bash
helm repo add langfuse https://langfuse.github.io/langfuse-k8s && helm repo update
# chart 1.5.32 / Langfuse v3.175.0 at time of writing — check artifacthub.io/packages/helm/langfuse-k8s/langfuse for latest
helm install langfuse langfuse/langfuse -n langfuse \
  --version 1.5.32 \                     # pin the chart — subchart key paths move between versions
  -f manifests/langfuse-values.yaml      # the dissected file above; overrides the chart defaults
kubectl -n langfuse get pods,pvc
```

- `helm install <release> <chart>` creates a named release (`langfuse`) from `langfuse/langfuse`.
- `--version` pins the *chart* (not the app) — important here because the Bitnami subchart key
  paths (`persistence.*`, `resourcesPreset`) drift between chart versions.
- `-f` layers your values *over* the chart defaults; anything you don't set keeps its default
  (which is why leaving `s3.deploy` unset would silently run MinIO).

**Expected (happy path):** the `langfuse-web` and `langfuse-worker` pods reach `Ready`, and
the **Postgres + ClickHouse PVCs are `Bound` on `linode-block-storage`** (real Akamai Block
Storage, not a kind hostPath). The values file already arranged this: MinIO disabled in favor
of your Object Storage bucket, and ClickHouse trimmed to a single node that fits a learning pool.

**If something fails:**
- *A PVC won't Bind* — its persistence key is a Bitnami *subchart passthrough*. Run
  `helm get values langfuse -n langfuse` and check the subchart's `persistence.*` path.
- *An image pull fails* — the chart pins `bitnamilegacy/*` images by default; override that
  subchart's `image.repository`.

## 4. Open the UI and get project keys

```bash
kubectl -n langfuse port-forward svc/langfuse-web 3000:3000 &   # tunnel local :3000 → the web Service's :3000; & backgrounds it
# open http://localhost:3000 → create an org + project → Settings → API Keys
#   copy the public key (pk-lf-...) and secret key (sk-lf-...)
```

`port-forward svc/langfuse-web 3000:3000` proxies your laptop's `localhost:3000` to the
`langfuse-web` Service inside the cluster (left = local port, right = Service port). The `&`
runs it in the background so you keep your shell; kill it later with `kill %1` or `fg` then
Ctrl-C. The `pk-lf-` / `sk-lf-` pair is the project-scoped credential the agent authenticates
its OTLP push with in the next step.

## 5. Repoint lab-02's Strands traces at Langfuse (the one-var swap)

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

(`x-langfuse-ingestion-version` pins Langfuse's OTLP ingestion format — required by v3. The
SDK appends `/v1/traces` to the generic endpoint; if you prefer, set
`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=.../api/public/otel/v1/traces` explicitly. Langfuse's v3
`get_client()` pattern — `LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY` env vars — is the documented
alternative and auto-wires the exporter.) Run the agent and ask it something.

**What to look for:** in Langfuse, the **same `Agent → cycle → model-invoke → tool` span tree**
you saw in Tempo (lab-02) — but now each LLM span carries the **model, token counts,
prompt/completion text, and a derived cost**. That's the payoff: identical instrumentation,
an LLM-aware destination. You just proved lab-02's "OTLP is portable" claim by doing it.

## 6. Cost + quality, natively (absorbing lab-03's LLM side)

- **Cost:** open a trace → Langfuse shows per-observation token usage and a cost figure it
  *infers* from its model price table (not provider billing — accurate only if the model name
  matches and the price entry is current).
- **Quality:** add an **LLM-as-judge** evaluator in Langfuse (Evaluations → set a judge model
  + rubric) and it scores observations automatically — the same "is it good?" signal you
  hand-pushed via Pushgateway in lab-03, with no plumbing. Scores attach to the trace.

## 7. The things the metrics stack simply can't do

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

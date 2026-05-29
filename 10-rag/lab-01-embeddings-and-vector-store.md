# Lab 01 — Embeddings + a vector store: giving the model a memory it can search

**Goal:** stand up the two pieces RAG needs that you don't have yet — a way to turn text
into vectors (a second vLLM, in *embedding* mode) and a place to store + search those
vectors (Qdrant) — then ingest a tiny corpus end to end. By the end you can say what an
embedding *is*, what an approximate-nearest-neighbor index *does*, and why vector search
finds things keyword search misses.

**Time:** ~40 min · **Cost:** free (local kind, CPU models)

## The problem (why this exists)

Your vLLM (Phase 04/06) only knows what it was trained on. Ask it about *your* runbook,
*your* incident from last Tuesday, *your* internal region codes, and it confidently makes
something up — the data simply isn't in its weights. Fine-tuning would bake your data in,
but that's a training job you re-run every time a document changes, and it still can't cite
a source. You need a way to hand the model *your* data **at query time**. The first half of
that is a searchable memory: a store you can ask "what do I have that's relevant to this
question?" and get back the right paragraphs. That store doesn't index *words* — it indexes
*meaning*. This lab builds it.

## What it replaces / why the naive way fails

The naive memory is keyword search (grep, a `LIKE '%term%'`, a Lucene index). It matches
*strings*. Ask "how do I cap a customer's spend?" and a keyword index returns nothing if
your doc says "token rate limit" — zero shared words, identical meaning. You'd spend forever
maintaining synonym lists. The other naive option — stuff the *entire* corpus into every
prompt — dies on the context window (Phase 06's `--max-model-len 1024`) and the token bill.

The fix is to index **semantic similarity**, not lexical overlap. That requires two new
capabilities your platform lacks:

| Need | Phase 06 had | This lab adds |
|---|---|---|
| Turn text → a comparable vector | a *chat* vLLM (`/v1/chat/completions`) | a *second* vLLM in **embedding** mode (`/v1/embeddings`) |
| Store + nearest-neighbor search vectors | nothing | **Qdrant**, a vector DB |

Note the symmetry with Phase 06: it's the *same* vLLM image, the *same* OpenAI protocol —
just a different endpoint and a different model. "Embeddings" isn't a new system; it's vLLM
doing a different job.

## Under the hood (MIT hat)

### What an embedding *is*

An embedding model is a transformer with the text-generation head removed. Instead of
predicting the next token, it runs your text through the network and **pools** the hidden
states into one fixed-length vector — for `BAAI/bge-small-en-v1.5`, 384 floating-point
numbers. That vector is a *point in a 384-dimensional semantic space*. The model was trained
so that texts meaning similar things land close together and unrelated texts land far apart.
"cap a customer's spend" and "token rate limit" end up near each other **despite sharing no
words**, because the model learned the concepts, not the strings.

This is exactly why the embedding vLLM runs in `--runner pooling`, not generative mode: you
want the pooled hidden-state vector, not next-token text. (Some architectures can do both;
you must say which — hence the explicit flag.)

```
"token rate limit"  ──► [embedding vLLM, pooling] ──► [0.02, -0.31, ..., 0.08]   (384 floats)
                                                            │
"cap customer spend"──► [embedding vLLM, pooling] ──► [0.03, -0.29, ..., 0.07]   ← lands NEAR it
"the cat sat down"  ──► [embedding vLLM, pooling] ──► [0.91,  0.12, ..., -0.4]   ← lands FAR away
```

### What an ANN index *does*

Storing vectors is easy; *searching* them fast is the hard part. To find the chunk most
similar to a query you compare the query vector against every stored vector by **cosine
similarity** (the cosine of the angle between them; 1.0 = identical direction, 0 =
unrelated) or dot product. Doing that exhaustively across millions of vectors is too slow,
so Qdrant builds an **HNSW** index — Hierarchical Navigable Small World graph — that finds
the *approximate* nearest neighbors by walking a graph instead of scanning everything.
"Approximate" is the trade: you accept a tiny chance of missing the true top result in
exchange for sub-millisecond search. That's the "ANN" (approximate nearest neighbor) in
vector DBs.

```
query text ─► embed ─► query vector ─┐
                                      ▼
              Qdrant HNSW graph ── walk to nearest points ──► top-k chunk IDs + scores
              (cosine distance)                                     │
                                                                    ▼
                                                       payload: the original chunk text
```

Below all of it is Phase 03 machinery: the embedding vLLM and Qdrant are each a Deployment
+ Service; the ingest Job reaches them by CoreDNS name, DNAT'd by kube-proxy. Nothing new
underneath — just two new workloads.

## 0. Prereqs

The kind cluster from Phases 05–07, with the **chat** vLLM already running (Phase 06):

```bash
kubectl config current-context          # expect kind-kind
kubectl get svc vllm                     # the Phase 06 chat model (Qwen) — still needed in lab-02
helm version                             # you installed Helm in 00-prep / used it in Phase 05–07
```

**What to look for:** context `kind-kind` and a `vllm` Service in `default`. If the chat
vLLM is gone, redo `06-ai-gateway/lab-01`. This lab adds a *second* vLLM next to it.

## 1. Deploy the embedding vLLM

This is a *second* vLLM — same `vllm/vllm-openai-cpu` image as the Phase 06 chat server, only
the model and one flag change. Here is the whole manifest (`manifests/embed-vllm.yaml`); the
diffs from the chat deploy are the entire lesson:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-embed              # a SECOND vLLM, alongside the Phase 06 `vllm` chat server
  namespace: default
  labels:
    app: vllm-embed
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-embed           # owns the pod by this label (must match template.labels below)
  template:
    metadata:
      labels:
        app: vllm-embed
    spec:
      containers:
        - name: vllm
          image: vllm/vllm-openai-cpu:latest-x86_64   # SAME image as the chat vLLM; arm64 → :latest-aarch64
          args:
            - "--model"
            - "BAAI/bge-small-en-v1.5"   # 384-dim, ~33M-param English embedding model; HF model ID, CPU-friendly
            - "--runner"
            - "pooling"          # THE flag: serve embeddings, not chat (older form: --task embed)
            - "--dtype"
            - "float32"          # CPU has no bf16/fp16 path — must be float32
          ports:
            - containerPort: 8000
          readinessProbe:
            httpGet:
              path: /health      # vLLM flips /health to 200 only AFTER weights load — gates "Ready"
              port: 8000
            initialDelaySeconds: 30
            periodSeconds: 10
            failureThreshold: 60 # 60×10s ≈ 10 min grace for HF download + CPU load before it's failed
          resources:
            requests:            # smaller than the chat model — embeddings are cheaper to serve
              cpu: "1"
              memory: 2Gi
            limits:
              cpu: "2"
              memory: 4Gi
---
apiVersion: v1
kind: Service
metadata:
  name: vllm-embed              # in-cluster DNS name the ingest Job dials: vllm-embed.default.svc...
  namespace: default
  labels:
    app: vllm-embed
spec:
  selector:
    app: vllm-embed             # routes to the pod above by label — Service↔Pod glue (lab-04)
  ports:
    - name: http
      port: 8000                # cluster port; the pod's containerPort is also 8000
      targetPort: 8000
```

The three fields that carry the lesson:

- **`--runner pooling`** — *the* flag. It tells vLLM to return the pooled hidden-state vector
  instead of generating text. (Older vLLM spelled this `--task embed`; the task form still
  works, but pin the current `--runner pooling`.) Some architectures (e.g. Qwen) can do **both**
  chat and embedding, so you MUST be explicit — leave it off and an ambiguous model may come up
  in generative mode and `/v1/embeddings` returns a `400`.
- **`--model BAAI/bge-small-en-v1.5`** — a HuggingFace model ID (like the Qwen one in Phase 04).
  Its `384` output dimension is a contract you'll re-state in two more places (Steps 2 and 4);
  `intfloat/e5-small-v2` is a drop-in alternative, also 384-dim.
- **`--dtype float32`** — gotcha: on CPU there is no bf16/fp16 path, so you must pin float32.
  Drop it on a CPU node and load can fail. The smaller `resources` (vs the chat model) are the
  other tell that embeddings are the lighter job — the rollout is quicker than the Qwen one.

> **Beginner gotcha:** `selector.matchLabels` (`app: vllm-embed`) must equal
> `template.metadata.labels` *and* the Service `selector` — all three say `app: vllm-embed`.
> That one label wires Deployment→Pod→Service. Mismatch it and the Service routes to nothing
> (lab-03/04).

Apply it and wait for the model to load:

```bash
kubectl apply -f manifests/embed-vllm.yaml          # create the Deployment + Service from desired state
kubectl rollout status deploy/vllm-embed --timeout=600s  # block until the pod is Ready (weights loaded)
```

`--timeout=600s` is generous on purpose: the readiness probe above won't pass until the model
downloads and loads, which on CPU can take minutes.

**What to look for:** `rollout status` blocks while the pod is `Running` but `0/1 READY`
(weights downloading + loading), then prints `successfully rolled out`. That Running→Ready
gap is the model loading — same pattern as the chat vLLM in Phase 06, just shorter.

## 2. Prove it serves `/v1/embeddings`

```bash
kubectl port-forward svc/vllm-embed 8001:8000 &

curl -s http://localhost:8001/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"input":"token rate limit","model":"BAAI/bge-small-en-v1.5"}' \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); v=d["data"][0]["embedding"]; print("dim:",len(v)); print("first 5:",v[:5]); print("usage:",d.get("usage"))'
```

**What to look for:** `dim: 384` and five small floats. That `384` is load-bearing — it's
the vector size your Qdrant collection must be created with (Step 4). Also note the response
shape: `{"data":[{"embedding":[...]}], "usage":{...}}` — the **same OpenAI envelope** as
chat, just an `embedding` array instead of a `message`. One protocol, two endpoints.

Prove *semantic* similarity with your own eyes — embed two phrases that share no words but
mean the same thing, and one that's unrelated, then compare cosine similarity:

```bash
curl -s http://localhost:8001/v1/embeddings -H 'Content-Type: application/json' \
  -d '{"input":["cap a customer'\''s spend","token rate limit","the cat sat on the mat"],"model":"BAAI/bge-small-en-v1.5"}' \
  | python3 -c '
import sys,json,math
d=json.load(sys.stdin)["data"]
def cos(a,b): return sum(x*y for x,y in zip(a,b))/(math.hypot(*a)*math.hypot(*b))
v=[x["embedding"] for x in d]
print("spend  vs ratelimit:", round(cos(v[0],v[1]),3))
print("spend  vs cat:      ", round(cos(v[0],v[2]),3))'
kill %1 2>/dev/null
```

**What to look for:** the "spend vs ratelimit" score is clearly higher than "spend vs cat",
even though *spend/ratelimit share no words* — expect roughly `0.8+` vs `~0.3–0.5`. Exact
numbers vary run to run; the **gap** is the point. That gap is the entire reason vector search
beats keyword search — you just measured meaning, not string overlap.

## 3. Install Qdrant (the vector store)

```bash
helm repo add qdrant https://qdrant.github.io/qdrant-helm   # register the chart repo
helm repo update                                            # pull its latest index
# Pin the chart. check github.com/qdrant/qdrant-helm/releases for the latest.
helm install qdrant qdrant/qdrant --version 1.18.0 \
  --namespace vectordb --create-namespace -f manifests/qdrant-values.yaml
kubectl -n vectordb rollout status statefulset/qdrant --timeout=300s
```

- `helm install qdrant qdrant/qdrant` — release name `qdrant`, from chart `qdrant/qdrant`.
- `--version 1.18.0` — pin the chart so it can't drift under you on a re-pull (chart 1.18.0
  ships Qdrant server v1.18.0).
- `--namespace vectordb --create-namespace` — install into (and create) a dedicated namespace.
  The ingest Job in Step 4 reaches it across namespaces by DNS: `qdrant.vectordb.svc...`.
- `-f manifests/qdrant-values.yaml` — feed the value overrides below to the chart's templates.

Qdrant is a **StatefulSet**, not a Deployment like every workload in 04–07 — because it has a
stable identity and a PVC behind it (vectors are state). That's why rollout status targets
`statefulset/qdrant`, not `deploy/`.

Here is the values file you just passed (`manifests/qdrant-values.yaml`) — these keys *override*
the chart's defaults, they aren't a full Qdrant config:

```yaml
replicaCount: 1

# Pin the server image too, so the chart can't drift under you on a re-pull.
image:
  tag: "v1.18.0"        # match the chart's appVersion explicitly; check hub.docker.com/r/qdrant/qdrant/tags

persistence:
  size: 1Gi             # the PVC backing the StatefulSet — vectors are STATE and must survive a restart

resources:              # modest, so Qdrant co-exists with two vLLM pods on a laptop kind node
  requests:
    cpu: "100m"
    memory: 256Mi
  limits:
    cpu: "1"
    memory: 1Gi

service:
  type: ClusterIP       # kind has no real LoadBalancer — keep it ClusterIP and port-forward (below)
```

- `persistence.size: 1Gi` is what makes Qdrant durable: the chart turns it into a
  `volumeClaimTemplate` on the StatefulSet, and kind's default `standard`/`local-path`
  storageClass satisfies it. On LKE (Phase 09) you'd swap to `linode-block-storage`.
- `service.type: ClusterIP` is the kind-vs-cloud gotcha: a `LoadBalancer` type would sit
  `<pending>` forever on kind, so you keep it cluster-internal and reach it with
  `port-forward`. On LKE you'd front it with a NodeBalancer instead.

Confirm it's reachable:

```bash
kubectl -n vectordb port-forward svc/qdrant 6333:6333 &   # tunnel localhost:6333 → the Service's 6333; & = background
curl -s http://localhost:6333/collections | python3 -m json.tool   # empty list — no data yet
kill %1 2>/dev/null                                        # stop that backgrounded port-forward
```

`port-forward <svc> 6333:6333` is `LOCAL:REMOTE` — since the ports match it looks redundant, but
the left side is the port on *your* machine and the right is the Service port. The `&` runs it in
the background so the next line can use the tunnel; `kill %1` tears it down.

**What to look for:** a `200` with `"collections": []`. An empty store is the correct
starting state — you haven't ingested anything. (pgvector is the valid alternative here:
the `vector` extension on Postgres gives you the same cosine search bolted onto a relational
DB. Use it when you already run Postgres; use Qdrant when search *is* the workload.)

## 4. Ingest the corpus: chunk → embed → upsert

Two manifests carry this step. First the **ConfigMap** (`manifests/ingest-configmap.yaml`):
it packs *two files* — the corpus you're indexing and the script that indexes it — so the Job
pod can mount them as files without baking anything into an image. Here it is, trimmed to its
shape (the corpus is the fictional runbook; the script is the load-bearing part):

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: rag-ingest            # the Job mounts this same ConfigMap by name (two volumes, below)
  namespace: default
data:
  corpus.md: |                # "your data" the model was never trained on — deliberately niche
    # Acme Cloud internal runbook (fictional — NOT in any model's training data)

    ## Region codes
    Acme Cloud regions use a 5-character code: ... The Osaka region is `as-07`. ...

    ## NodeBalancer quirk
    The Acme NodeBalancer health check defaults to TCP on port 80. If your pods serve
    HTTPS only, the balancer marks them DOWN even when healthy. Fix: set the check
    protocol to `http` and the path to `/healthz` ...

    ## Block storage limit
    A single Acme block volume caps at 10 TiB. To exceed that, stripe volumes with LVM ...

    ## GPU node pool gotcha
    Acme GPU nodes taint themselves `acme.cloud/gpu=present:NoSchedule` on boot. ...

  ingest.py: |                # the canonical RAG ingest, stdlib-only (urllib+json → no pip step)
    """Ingest the corpus into Qdrant: chunk -> embed -> upsert. Stdlib only."""
    import json, os, urllib.request, re

    EMBED_URL = os.environ["EMBED_URL"]      # vllm-embed.../v1/embeddings (set by the Job)
    EMBED_MODEL = os.environ["EMBED_MODEL"]  # BAAI/bge-small-en-v1.5
    QDRANT_URL = os.environ["QDRANT_URL"]    # qdrant.vectordb.svc...:6333
    COLLECTION = os.environ.get("COLLECTION", "runbook")
    DIM = int(os.environ.get("DIM", "384"))  # MUST match the embedding model's output size

    def http(method, url, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read() or "{}")

    def embed(text):
        # vLLM speaks OpenAI's /v1/embeddings: {input, model} -> {data:[{embedding:[...]}]}
        out = http("POST", EMBED_URL, {"input": text, "model": EMBED_MODEL})
        return out["data"][0]["embedding"]

    # 1. CHUNK: split the corpus on markdown headings. Each "## section" is one chunk.
    raw = open("/corpus/corpus.md").read()
    chunks = [c.strip() for c in re.split(r"(?=^## )", raw, flags=re.M) if c.strip()]
    print(f"chunked into {len(chunks)} pieces")

    # 2. CREATE the collection with the matching vector size + cosine distance.
    #    PUT is idempotent in Qdrant; re-running the Job just recreates it.
    http("PUT", f"{QDRANT_URL}/collections/{COLLECTION}",
         {"vectors": {"size": DIM, "distance": "Cosine"}})

    # 3. EMBED each chunk and UPSERT the vector + its source text as payload.
    points = []
    for i, chunk in enumerate(chunks):
        vec = embed(chunk)
        assert len(vec) == DIM, f"embedding dim {len(vec)} != collection dim {DIM}"
        points.append({"id": i, "vector": vec, "payload": {"text": chunk}})
    http("PUT", f"{QDRANT_URL}/collections/{COLLECTION}/points?wait=true", {"points": points})
    print(f"upserted {len(points)} vectors of dim {DIM} into '{COLLECTION}'")
```

The script is the canonical RAG ingest in ~20 lines — the three numbered comments are the
whole game:

1. **Chunk** (`re.split(r"(?=^## )", ...)`) — a *lookahead* split on `## ` headings keeps the
   heading with its body, so each `## section` becomes one chunk. A chunk is the *unit you
   retrieve and stuff into a prompt later*, so it has to be small enough to fit several into the
   context window.
2. **Create the collection** — `PUT /collections/runbook` with `size: DIM` (384) and
   `distance: Cosine`. **`size` MUST equal the embedding dimension from Step 2.** This is the
   #1 RAG setup bug; you'll trigger it deliberately in "Break it." `PUT` is idempotent, so
   re-running the Job just recreates the collection.
3. **Embed + upsert** — for each chunk, call `/v1/embeddings`, assert the vector length matches
   `DIM` (the contract check), then `PUT .../points` with the vector **and the original text as
   `payload`**. Storing the source text alongside the vector is what lets retrieval return
   readable chunks (Step 5), not just numbers.

> **Beginner gotcha:** a ConfigMap is *not* a place for secrets and is capped at ~1 MiB — fine
> for a tiny corpus + script, wrong for real document sets (you'd mount a PVC or pull from object
> storage instead). And the data lives only in the cluster: editing this file does nothing until
> you `kubectl apply` it again.

Now the **Job** (`manifests/ingest-job.yaml`) that mounts those two files and runs the script
once. A Job (not a Deployment) is correct here: it runs to completion and stops:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: rag-ingest
  namespace: default
spec:
  backoffLimit: 2              # retry the pod up to 2× on failure, then mark the Job Failed
  ttlSecondsAfterFinished: 3600  # auto-delete the finished Job (and its pod) after 1h — tidy
  template:
    spec:
      restartPolicy: Never     # required for Jobs (Never or OnFailure) — a Job pod must be able to end
      containers:
        - name: ingest
          image: python:3.12-slim     # plain Python; the stdlib-only script needs no pip install
          command: ["python", "/script/ingest.py"]   # run the file mounted from the ConfigMap
          env:                 # these env vars are exactly what ingest.py reads via os.environ
            - name: EMBED_URL
              value: "http://vllm-embed.default.svc.cluster.local:8000/v1/embeddings"  # cross-pod by CoreDNS
            - name: EMBED_MODEL
              value: "BAAI/bge-small-en-v1.5"
            - name: QDRANT_URL
              value: "http://qdrant.vectordb.svc.cluster.local:6333"   # note: vectordb namespace
            - name: COLLECTION
              value: "runbook"
            - name: DIM
              value: "384"     # MUST match the embedding model output + the collection size
          volumeMounts:
            - { name: corpus, mountPath: /corpus }   # → /corpus/corpus.md (script reads this path)
            - { name: script, mountPath: /script }   # → /script/ingest.py (command runs this path)
      volumes:
        - name: corpus
          configMap:
            name: rag-ingest   # same ConfigMap, one key per volume so each lands as its own file
            items:
              - { key: corpus.md, path: corpus.md }
        - name: script
          configMap:
            name: rag-ingest
            items:
              - { key: ingest.py, path: ingest.py }
```

- The `env` block is the *other* end of the script's `os.environ` reads — change a Service name
  or namespace here and the script silently dials the wrong place. Note `QDRANT_URL` crosses
  namespaces (`.vectordb.`) while `EMBED_URL` stays in `.default.` — CoreDNS resolves both.
- The two `volumes` mount the *same* ConfigMap twice, selecting one `key` each via `items`, so
  the corpus lands at `/corpus/corpus.md` and the script at `/script/ingest.py` — the exact
  paths the container's `command` and the script's `open()` expect.

> **Beginner gotcha:** `restartPolicy: Never` is mandatory for a Job (the default `Always` is
> rejected) — a batch task must be allowed to *finish*. And a Job won't re-run on `apply` if it
> already completed; to re-ingest you `kubectl delete job rag-ingest` first (which the "Break it"
> section does).

Apply both and wait for the Job to finish:

```bash
kubectl apply -f manifests/ingest-configmap.yaml   # ship the corpus + script into the cluster
kubectl apply -f manifests/ingest-job.yaml          # create the Job → it spawns one pod that runs ingest.py
kubectl wait --for=condition=complete job/rag-ingest --timeout=300s   # block until the Job's pod succeeds
kubectl logs job/rag-ingest                         # the script's print() output (logs follow the Job's pod)
```

`kubectl wait --for=condition=complete` is the Job equivalent of `rollout status` — it blocks
until the Job reports the `Complete` condition (or times out), so the next command sees finished
work. `kubectl logs job/...` resolves the Job to its pod and streams that pod's stdout.

**What to look for:** logs like `chunked into 4 pieces` and `upserted 4 vectors of dim 384
into 'runbook'`. Verify the store now has data:

```bash
kubectl -n vectordb port-forward svc/qdrant 6333:6333 &
curl -s http://localhost:6333/collections/runbook | python3 -c 'import sys,json; d=json.load(sys.stdin)["result"]; print("points:",d["points_count"],"dim:",d["config"]["params"]["vectors"]["size"])'
kill %1 2>/dev/null
```

**What to look for:** `points: 4 dim: 384`. (The python one-liner is just plumbing — it pulls
two fields out of Qdrant's collection-info JSON: the point count and the configured vector
size.) The corpus is now a searchable vector index.

## 5. Run a real vector search by hand

This is the retrieval half of RAG, naked — no LLM yet. Embed a question, search Qdrant,
read what comes back.

Steps 2–4 killed their port-forwards with `kill %1`, so **(re)start both** — this step needs
the embed model (to embed the question) *and* Qdrant (to search) live:

```bash
kubectl port-forward svc/vllm-embed 8001:8000 &
kubectl -n vectordb port-forward svc/qdrant 6333:6333 &

EMB=$(curl -s http://localhost:8001/v1/embeddings -H 'Content-Type: application/json' -d '{"input":"why are my HTTPS pods marked down by the load balancer?","model":"BAAI/bge-small-en-v1.5"}')
echo "$EMB" | python3 -c '
import sys,json,urllib.request
vec=json.load(sys.stdin)["data"][0]["embedding"]
req=urllib.request.Request("http://localhost:6333/collections/runbook/points/search",
  data=json.dumps({"vector":vec,"limit":2,"with_payload":True}).encode(),
  headers={"Content-Type":"application/json"})
for h in json.load(urllib.request.urlopen(req))["result"]:
    print(round(h["score"],3), h["payload"]["text"][:80].replace(chr(10)," "))'
kill %1 %2 2>/dev/null   # stop both port-forwards
```

**What to look for:** the **top hit is the "NodeBalancer quirk" chunk** — even though the
question says "load balancer" and the doc says "NodeBalancer," "HTTPS pods marked down" and
"marks them DOWN ... serve HTTPS only" share no exact phrasing. Vector search matched on
*meaning*. The `score` is the cosine similarity from the under-the-hood section, now a real
number you can rank by. **This ranked list is exactly what lab-02 feeds to the LLM.**

## Break it, then read the error (Kelsey lens)

The classic RAG setup failure: a dimension mismatch. Edit `manifests/ingest-configmap.yaml`,
change the collection's `DIM` env (or hardcode a wrong size in the `PUT /collections` call)
to `768`, re-run the Job:

```bash
kubectl delete job rag-ingest
kubectl apply -f manifests/ingest-configmap.yaml   # with DIM mismatched to 768
kubectl apply -f manifests/ingest-job.yaml
kubectl logs job/rag-ingest
```

**Read the error, don't skim it.** One of two things happens, both diagnostic:

- The script's own `assert len(vec) == DIM` fires: `embedding dim 384 != collection dim 768`
  — the model emits 384 floats but you told Qdrant to expect 768.
- Or Qdrant rejects the upsert with a `400`: *"Wrong input: Vector dimension error: expected
  dim: 768, got 384."*

Either way the lesson is the same and it's the most common RAG bug there is: **the embedding
model's output dimension and the collection's vector size are one contract.** They are set in
two different places (the model card and the `PUT /collections` call) and nothing checks them
for you until ingest fails. Whenever a vector DB rejects a write, suspect the dimension
first. Fix `DIM` back to `384` and re-run.

## Checkpoint — you can now explain…

1. **What an embedding is.** A fixed-length vector (here 384 floats) that places text as a
   *point in semantic space*, produced by an embedding model running in pooling mode. Similar
   meanings → nearby points, regardless of shared words.
2. **What an ANN index does.** It finds the approximate nearest vectors by cosine/dot
   similarity fast — Qdrant uses an HNSW graph — trading a tiny chance of a miss for
   sub-millisecond search instead of scanning every vector.
3. **Why vector search beats keyword search.** Keyword search matches strings; vector search
   matches meaning. You measured it: "cap spend" scored close to "token rate limit" with zero
   shared words, and a runbook question retrieved the right chunk despite different phrasing.
4. **Why the embedding dimension is a contract.** The model's output size and the collection's
   vector size must match exactly, set in two places, checked only at write time.

You can now:
- [ ] Run a second vLLM in `--runner pooling` mode and call `/v1/embeddings`.
- [ ] Install Qdrant via Helm and create a collection with the right dimension + distance.
- [ ] Ingest a corpus (chunk → embed → upsert) and run a vector search that returns ranked,
  readable chunks.
- [ ] Diagnose a dimension-mismatch error from either side of the contract.

## Tie back / forward

This is pure Phase 03 underneath — two Deployments, two Services, a Job, a ConfigMap, all
reached by CoreDNS — wearing a new hat. You built the **retrieval** half of RAG: a searchable
semantic memory. Next:

→ `lab-02-the-rag-pipeline.md`: wire retrieval to generation. Embed the question, take the
top-k chunks you just learned to fetch, stuff them into a chat prompt, and send it to the Qwen
vLLM **through your Phase 06 gateway** — so RAG inherits the token limits and guards you
already built.

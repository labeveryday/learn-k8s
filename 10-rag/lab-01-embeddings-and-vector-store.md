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

```bash
kubectl apply -f manifests/embed-vllm.yaml
kubectl rollout status deploy/vllm-embed --timeout=600s
```

Read the manifest — the differences from the Phase 06 chat deploy are the whole lesson:

- `--model BAAI/bge-small-en-v1.5` — a small (384-dim, ~33M param) English embedding model,
  CPU-friendly. `intfloat/e5-small-v2` is a drop-in alternative, also 384-dim.
- `--runner pooling` — **the flag that matters.** It tells vLLM to serve embeddings, not
  chat. (Older vLLM spelled this `--task embed`; the task form still works, but pin the
  current `--runner pooling`.) Without it, an ambiguous architecture might come up in
  generative mode and `/v1/embeddings` would 400.
- Smaller `resources` than the chat model — embeddings are cheaper, and the small model
  loads faster, so the rollout is quicker than the Qwen one.

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
even though *spend/ratelimit share no words*. That gap is the entire reason vector search
beats keyword search — you just measured meaning, not string overlap.

## 3. Install Qdrant (the vector store)

```bash
helm repo add qdrant https://qdrant.github.io/qdrant-helm
helm repo update
# Pin the chart. check github.com/qdrant/qdrant-helm/releases for the latest.
helm install qdrant qdrant/qdrant --version 1.18.0 \
  --namespace vectordb --create-namespace -f manifests/qdrant-values.yaml
kubectl -n vectordb rollout status statefulset/qdrant --timeout=300s
```

`qdrant-values.yaml` pins the server image, asks for a 1Gi PVC (vectors are state), and keeps
the Service `ClusterIP` (kind has no real LoadBalancer). Confirm it's reachable:

```bash
kubectl -n vectordb port-forward svc/qdrant 6333:6333 &
curl -s http://localhost:6333/collections | python3 -m json.tool   # empty list — no data yet
kill %1 2>/dev/null
```

**What to look for:** a `200` with `"collections": []`. An empty store is the correct
starting state — you haven't ingested anything. (pgvector is the valid alternative here:
the `vector` extension on Postgres gives you the same cosine search bolted onto a relational
DB. Use it when you already run Postgres; use Qdrant when search *is* the workload.)

## 4. Ingest the corpus: chunk → embed → upsert

```bash
kubectl apply -f manifests/ingest-configmap.yaml
kubectl apply -f manifests/ingest-job.yaml
kubectl wait --for=condition=complete job/rag-ingest --timeout=300s
kubectl logs job/rag-ingest
```

The Job runs `ingest.py` from the ConfigMap. Open it — it's the canonical RAG ingest in ~20
lines:

1. **Chunk** — split `corpus.md` on `## ` headings. Each section becomes one chunk. A chunk
   is the *unit you retrieve and stuff into a prompt later*, so it has to be small.
2. **Create the collection** — `PUT /collections/runbook` with `size: 384` and
   `distance: Cosine`. **The size MUST equal the embedding dimension from Step 2.** This is
   the #1 RAG setup bug; you'll trigger it deliberately in "Break it."
3. **Embed + upsert** — for each chunk, call `/v1/embeddings`, then
   `PUT /collections/runbook/points` with the vector *and the original text as `payload`*.
   Storing the source text alongside the vector is what lets retrieval return readable
   chunks, not just numbers.

**What to look for:** logs like `chunked into 4 pieces` and `upserted 4 vectors of dim 384
into 'runbook'`. Verify the store now has data:

```bash
kubectl -n vectordb port-forward svc/qdrant 6333:6333 &
curl -s http://localhost:6333/collections/runbook | python3 -c 'import sys,json; d=json.load(sys.stdin)["result"]; print("points:",d["points_count"],"dim:",d["config"]["params"]["vectors"]["size"])'
```

**What to look for:** `points: 4 dim: 384`. The corpus is now a searchable vector index.

## 5. Run a real vector search by hand

This is the retrieval half of RAG, naked — no LLM yet. Embed a question, search Qdrant,
read what comes back:

```bash
EMB=$(curl -s http://localhost:6333/ >/dev/null; curl -s http://localhost:8001/v1/embeddings -H 'Content-Type: application/json' -d '{"input":"why are my HTTPS pods marked down by the load balancer?","model":"BAAI/bge-small-en-v1.5"}')
# (start the embed port-forward if you killed it: kubectl port-forward svc/vllm-embed 8001:8000 &)
echo "$EMB" | python3 -c '
import sys,json,urllib.request
vec=json.load(sys.stdin)["data"][0]["embedding"]
req=urllib.request.Request("http://localhost:6333/collections/runbook/points/search",
  data=json.dumps({"vector":vec,"limit":2,"with_payload":True}).encode(),
  headers={"Content-Type":"application/json"})
for h in json.load(urllib.request.urlopen(req))["result"]:
    print(round(h["score"],3), h["payload"]["text"][:80].replace(chr(10)," "))'
kill %1 2>/dev/null
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

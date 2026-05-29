# Lab 02 — The RAG pipeline: retrieve, stuff, generate

**Goal:** connect the retrieval you built in lab-01 to generation. Embed a question, pull
the top-k chunks from Qdrant, stuff them into a chat prompt, and send it to the Qwen vLLM —
routed **through your Phase 06 gateway** so RAG inherits the token budget and prompt guards
for free. By the end you can draw the five-step RAG data path, explain why you retrieve
top-k instead of the whole corpus, and read the failure mode when retrieval comes back empty.

**Time:** ~35 min · **Cost:** free (local kind)

## The problem (why this exists)

lab-01 gave you a searchable memory, but a ranked list of chunks isn't an answer — it's
evidence. The model still has to *read* that evidence and respond in natural language. And
the model can't see your vector store; it only sees the prompt you send it. So the missing
piece is the **hand-off**: take the chunks retrieval found and put them where the model will
actually look — inside the prompt. Get that hand-off right and the model answers from *your*
data. Get it wrong (send too much, send nothing, send it in a confusing shape) and the model
ignores it or invents an answer.

## What it replaces / why the naive way fails

The naive "give the model my data" is to paste the **entire corpus** into the prompt every
time. That fails on two hard limits you already met:

- **The context window.** Your Qwen vLLM runs with `--max-model-len 1024` (Phase 06). A real
  corpus is millions of tokens; it doesn't fit, full stop. Even with a huge window, you'd pay
  to re-send the whole corpus on every single question.
- **Signal-to-noise.** Even when it fits, burying the one relevant paragraph in 50 irrelevant
  ones makes the model's job *harder*, not easier — attention gets diluted ("lost in the
  middle").

RAG's answer: don't send the corpus, send the **top-k most relevant chunks** — the small,
ranked list lab-01 produced. Retrieval is the filter that turns "millions of tokens I can't
send" into "three paragraphs that fit and matter." That's the entire reason the retrieval
step exists.

The other thing this replaces is **fine-tuning**. You could bake the runbook into the model's
weights instead. RAG vs fine-tuning is a real fork — see under-the-hood — but the headline:
RAG changes what the model *sees*; fine-tuning changes what the model *is*.

## Under the hood (MIT hat)

### The five-step pipeline and its data path

```
  question ──► [1] embed (vllm-embed /v1/embeddings) ──► query vector
                                                              │
                              [2] top-k search (Qdrant HNSW)  ▼
                                  ◄── chunks + scores ────────┘
  [3] STUFF chunks into a prompt template
        ┌────────────────────────────────────────────────┐
        │ "Use ONLY this context: <chunk1><chunk2>...      │
        │  Question: <question>. If not in context, say    │
        │  you don't know."                                │
        └────────────────────────────────────────────────┘
              │
  [4] POST /v1/chat/completions  ──►  GATEWAY (Phase 06)  ──►  Qwen vLLM
              │                          │ token meter + prompt guard apply HERE
  [5] answer ◄────────────────────────────────────────────────┘  grounded in YOUR chunks
```

Steps 1–2 are lab-01. Steps 3–5 are this lab. Step 4 is the deliberate choice: the generation
call goes through the **gateway**, not straight to vLLM, so the same token rate-limit and
prompt guard you built in Phase 06 govern RAG too — *the platform is the harness* (Phase 07
lab-05), now applied to a RAG workload.

### Chunking and the context window — why top-k, not all

A **chunk** is the atomic unit of retrieval: you embed chunks, you retrieve chunks, you stuff
chunks. Chunk too big and one chunk eats your whole window and dilutes relevance; chunk too
small and a single fact gets split across two chunks and neither retrieves well. There's no
universal right size — it's the first knob you tune. `top_k` is the second: how many chunks
you stuff. More k = more recall (you're likelier to include the answer) but more tokens and
more noise. You retrieve **top-k** precisely because the window is finite and signal beats
volume — the whole pipeline is a budget allocation problem against `--max-model-len`.

### The prompt template — where grounding lives

The template is not decoration; it's the control surface for **grounding** — tying the answer
to the provided evidence. The instruction *"answer ONLY from the context; if it's not there,
say you don't know"* is what (tries to) stop the model from falling back on its training data
or inventing. Change that one sentence and you change whether the model hallucinates or
abstains — you'll prove exactly that in "Break it."

### RAG vs fine-tuning (when each)

| | RAG (this lab) | Fine-tuning |
|---|---|---|
| Changes | what the model *sees* (the prompt) | what the model *is* (the weights) |
| Update your data | re-ingest (lab-01) — seconds | re-train — a GPU job |
| Cite sources | yes — you have the chunk + payload | no — knowledge is diffuse in weights |
| Good for | facts that change, citations, freshness | style/format/behavior, domain *skills* |

They're not exclusive: fine-tune for *how* to answer, RAG for *what* facts to use. For
"answer from my docs," reach for RAG first — it's cheaper, fresher, and auditable.

## 0. Prereqs

- lab-01 complete: `vllm-embed` running, Qdrant in `vectordb` with the `runbook` collection
  populated (4 points, dim 384). Check: `kubectl -n vectordb exec ...` or re-run the lab-01
  Step 4 verify.
- The **chat** vLLM (Qwen) from Phase 06 running: `kubectl get svc vllm`.
- The Phase 06 **gateway** path: the `http` Gateway in `kgateway-system` and the
  `AgentgatewayBackend` named `vllm-ai`. Check: `kubectl get agentgatewaybackend vllm-ai`.
  If missing: `kubectl apply -f ../06-ai-gateway/manifests/kgateway-ai-backend.yaml`.

## 1. Add the RAG route to the gateway

```bash
kubectl apply -f manifests/rag-route.yaml
kubectl get httproute rag -o wide
```

This adds an HTTPRoute `rag` (hostname `rag.example.com`) that reuses the Phase 06 `vllm-ai`
backend, plus an `AgentgatewayPolicy` token budget scoped to *this route only*. Read why it's
a separate route, not just reusing `llm.example.com`: a distinct route is a distinct policy
surface — RAG's prompts are large (you stuff chunks in), so RAG gets its own, more generous
token budget without touching bare-chat callers.

**What to look for:** under `status.parents[].conditions`, `Accepted=True` and
`ResolvedRefs=True`. If `ResolvedRefs=False`, the `vllm-ai` backend is missing — that's the
prereq above. Same status-reading skill as Phase 05 lab-01's "ghost" Gateway.

## 2. Run the full pipeline against the gateway

Forward the embedding model and the gateway (two port-forwards — retrieval hits the embed
model directly; generation goes through the gateway):

```bash
kubectl port-forward svc/vllm-embed 8001:8000 &
kubectl -n vectordb port-forward svc/qdrant 6333:6333 &
kubectl -n kgateway-system port-forward svc/http 8080:80 &
```

Now the pipeline as one script. Read it — it's the five steps, each labeled:

```bash
python3 - <<'PY'
import json, urllib.request
EMBED="http://localhost:8001/v1/embeddings"; QDRANT="http://localhost:6333"; GW="http://localhost:8080/v1/chat/completions"
def post(url, body, headers=None):
    req=urllib.request.Request(url, data=json.dumps(body).encode(),
        headers={"Content-Type":"application/json", **(headers or {})})
    return json.load(urllib.request.urlopen(req))

question = "What is the Osaka region code, and why might my HTTPS pods show as down?"

# [1] embed the question
qvec = post(EMBED, {"input": question, "model": "BAAI/bge-small-en-v1.5"})["data"][0]["embedding"]
# [2] top-k search
hits = post(f"{QDRANT}/collections/runbook/points/search",
            {"vector": qvec, "limit": 3, "with_payload": True})["result"]
context = "\n\n---\n\n".join(h["payload"]["text"] for h in hits)
print("RETRIEVED top-k scores:", [round(h["score"],3) for h in hits])
# [3] stuff into a prompt template (grounding instruction lives here)
prompt = (f"Use ONLY the context below to answer. If the answer is not in the context, "
          f"say you don't know.\n\nContext:\n{context}\n\nQuestion: {question}")
# [4] generate THROUGH THE GATEWAY (Host header selects the rag route)
ans = post(GW, {"model":"Qwen/Qwen2.5-0.5B-Instruct",
                "messages":[{"role":"user","content":prompt}], "max_tokens":150},
           headers={"Host":"rag.example.com"})
# [5] answer
print("\nANSWER:\n", ans["choices"][0]["message"]["content"])
print("\nusage:", ans["usage"])
PY
```

**What to look for, in order:**

1. `RETRIEVED top-k scores:` — three cosine scores, descending. The retrieval from lab-01,
   now feeding generation.
2. `ANSWER:` — it should contain **`as-07`** (Osaka) and the **NodeBalancer HTTPS health-check**
   fix. Neither fact is in Qwen's training data — they're in *your* corpus. The model answered
   from chunks you handed it. **That is RAG working.**
3. `usage:` — the token count. Note `prompt_tokens` is large: that's the stuffed context. This
   number is what the gateway's token budget meters — RAG is expensive per call *because* you
   send context, which is exactly why metering it at the gateway matters.

## 3. Confirm the gateway is actually in the path

The answer alone doesn't prove traffic went *through* the gateway. Prove it — hammer the route
past its token budget and watch the gateway, not vLLM, cut you off:

```bash
for i in $(seq 1 8); do
  curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8080/v1/chat/completions \
    -H 'Content-Type: application/json' -H 'Host: rag.example.com' \
    -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"summarize the entire runbook in great detail"}],"max_tokens":200}'
done
```

**What to look for:** the first calls return `200`, then a `429` once you cross the
`rag-token-budget` (2000 tokens/min). That `429` is your Phase 06 token rate-limit governing a
RAG workload — the platform-as-harness story, made concrete. A naive RAG script calling vLLM
directly would have no such ceiling; routing through the gateway gave you one for free.

## Break it, then read the error (Kelsey lens): the hallucination / empty-retrieval failure

This is *the* RAG failure mode. Ask something **not in the corpus** and watch what the
grounding instruction does — then remove it and watch the model invent.

First, with the grounding instruction (the "say you don't know" line) **kept**:

```bash
python3 - <<'PY'
import json, urllib.request
EMBED="http://localhost:8001/v1/embeddings"; QDRANT="http://localhost:6333"; GW="http://localhost:8080/v1/chat/completions"
def post(u,b,h=None):
    r=urllib.request.Request(u,data=json.dumps(b).encode(),headers={"Content-Type":"application/json",**(h or {})})
    return json.load(urllib.request.urlopen(r))
q="What is the Acme Cloud refund policy for unused credits?"   # NOT in the runbook
qv=post(EMBED,{"input":q,"model":"BAAI/bge-small-en-v1.5"})["data"][0]["embedding"]
hits=post(f"{QDRANT}/collections/runbook/points/search",{"vector":qv,"limit":3,"with_payload":True})["result"]
ctx="\n\n---\n\n".join(h["payload"]["text"] for h in hits)
print("top-k scores:", [round(h["score"],3) for h in hits])   # note: LOW scores — nothing relevant
grounded=(f"Use ONLY the context below. If the answer is not in the context, say you don't know.\n\nContext:\n{ctx}\n\nQuestion: {q}")
print("\nGROUNDED:", post(GW,{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":grounded}],"max_tokens":100},{"Host":"rag.example.com"})["choices"][0]["message"]["content"])
ungrounded=f"Answer this question: {q}\n\n(Context, possibly irrelevant:)\n{ctx}"
print("\nUNGROUNDED:", post(GW,{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":ungrounded}],"max_tokens":100},{"Host":"rag.example.com"})["choices"][0]["message"]["content"])
PY
```

**Read both outputs, that's the lesson:**

- **The top-k scores are low** — the question has no good match in the runbook, so retrieval
  returns the *least-bad* chunks, but none are actually relevant. Retrieval doesn't fail
  loudly; it returns whatever's nearest. **A low top score is your signal that retrieval found
  nothing useful** — and a production RAG system thresholds on it (drop hits below, say, 0.4)
  rather than blindly stuffing them.
- **GROUNDED** should say something like *"I don't know / the context doesn't cover refunds."*
  The grounding instruction did its job: no evidence → abstain.
- **UNGROUNDED** will likely **hallucinate** a confident, plausible, *fabricated* refund policy
  — because you removed the instruction that told it to stay on the evidence, so it fell back
  to its training-data priors and made something up.

The failure mode has two halves and you just saw both: **retrieval quality** (garbage chunks
in → garbage answer; threshold the score) and **grounding** (the prompt template is what
converts "no good evidence" into "I don't know" instead of a confident lie). Most "RAG
hallucinated" complaints are one of these two — not a model problem, a *pipeline* problem you
control. Same Kelsey reflex as every phase: the system didn't get dumber; you removed the
control that caught the failure.

## Checkpoint — you can now explain…

1. **The five-step RAG pipeline.** embed question → top-k vector search → stuff chunks into a
   prompt template → generate via `/v1/chat/completions` → grounded answer. Steps 1–2 are
   retrieval (lab-01); 3–5 are generation (this lab).
2. **Why top-k, not the whole corpus.** The context window is finite (`--max-model-len`), the
   corpus isn't, and stuffing irrelevant text dilutes the answer. Retrieval is the filter that
   makes the prompt both fit and focus.
3. **Where grounding lives and why it matters.** In the prompt template's "answer only from
   context / else say you don't know" instruction. Remove it and the model hallucinates from
   its priors; keep it and the model abstains when retrieval comes up empty.
4. **RAG vs fine-tuning.** RAG changes what the model sees (cheap, fresh, citable, for *facts*);
   fine-tuning changes what the model is (a training job, for *behavior/skills*). Not
   exclusive.
5. **Why the generation call goes through the gateway.** So RAG inherits the Phase 06 token
   budget and prompt guard — the platform is a shared harness; you saw RAG get a `429`.

You can now:
- [ ] Assemble the full retrieve → stuff → generate pipeline against your own models.
- [ ] Write a grounding prompt template and explain what each clause does.
- [ ] Route RAG generation through the gateway and show it inheriting a token limit.
- [ ] Diagnose a hallucination as a retrieval-quality or grounding failure, not a model
  failure.

## Tie back / forward

The generation call is the *exact* Phase 06 path — `/v1/chat/completions` → `http` Gateway →
`AgentgatewayBackend` → Qwen vLLM → kube-proxy → Pod — with retrieval bolted on the front. You
added no new gateway machinery; you reused it. Next:

→ `lab-03-agentic-rag.md`: stop *always* retrieving. Make `retrieve` a **tool** an agent
decides to call — as a Strands `@tool` in your `agents/` framework and as an MCP tool a kagent
Agent calls via `RemoteMCPServer` (Phase 07). Retrieval becomes a harness tool, not a hardcoded
step.

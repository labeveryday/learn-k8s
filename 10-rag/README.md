# 10 · RAG: give the model your data at query time

> Floor 3b. Retrieval-Augmented Generation as a workload on the platform you built in
> 05–09. It reuses your vLLM, gateway, and kagent, and adds one new piece: a vector
> store.

## The big idea

An LLM only knows its training data. Ask your Qwen vLLM about your runbook, your region
codes, or your last incident, and it confidently invents an answer: that data isn't in its
weights. You have two ways to fix that: **fine-tune** (a training job that bakes your data
into the weights, re-run on every change, can't cite a source) or **RAG** (hand the model
your data at query time, in the prompt). RAG wins for facts that change and answers that
must be grounded.

RAG is a five-step pipeline, and every piece is something you already have except one:

```
  question
     │  [1] EMBED the question         ──► a SECOND vLLM (embedding model)   ← new endpoint, same image
     ▼
  query vector
     │  [2] TOP-K SEARCH               ──► a VECTOR STORE (Qdrant)           ← THE one new piece
     ▼
  top-k relevant chunks
     │  [3] STUFF into a prompt template (grounding instruction)
     ▼
  [4] GENERATE  ──► /v1/chat/completions ──► your Phase 06 GATEWAY ──► Qwen vLLM
     ▼
  [5] grounded answer
```

Offline, once, you do the mirror of [1]+[2]: chunk your corpus, embed each chunk, and
**upsert** the vectors into the store. At query time you embed the question, retrieve the
nearest chunks, and stuff them into the prompt. The model learned nothing; you
changed what it sees.

## Why this phase exists

You spent 05–09 building an inference platform: a token-aware **gateway** (06), agents as
**kagent** objects (07), your own **Strands** framework bridged to it (07 lab-04), and the
**LKE** path for real infra (09). RAG is the first application of that platform, assembled
almost entirely from parts you already operate:

| RAG needs | You already have | New in Phase 10 |
|---|---|---|
| Turn text → vectors | vLLM (the image, the OpenAI protocol) | run a **second** vLLM in `--runner pooling` (embeddings) |
| Generate the answer | the Qwen chat vLLM (04/06) | nothing new |
| Govern the generation call | the Phase 06 gateway (token limit, prompt guard) | a dedicated `rag` route |
| Let an agent *decide* to retrieve | kagent + MCP, Strands `@tool` (07) | retrieval wrapped as a tool |
| Store + search vectors | nothing | **a vector DB (Qdrant)** |

The one new component is the vector store. Everything else is your platform, reused.

## Prereqs

- Phases 05–07 on the local **kind** cluster (cluster `kind`, context `kind-kind`): the
  Phase 06 **gateway** (`http` Gateway in `kgateway-system`, the `vllm-ai`
  `AgentgatewayBackend`) and the **chat vLLM** (`vllm` Service, Qwen). Phase 07 (kagent +
  `ModelConfig vllm`) for lab-03.
- Helm (from `00-prep`; used throughout 05–07).
- The `agents/` Strands template (Phase 07 lab-04) for lab-03 Part A.

Phases 10–11 run on the same local kind cluster as 05–07. The LKE path (Phase 09) is where
this goes for production: NodeBalancer in front of Qdrant, the Block Storage CSI behind its PVC
(vectors are state), and the LKE observability add-on tracing RAG calls.

## Labs

| Lab | File | Idea | The mechanism it teaches |
|---|---|---|---|
| 01 | `lab-01-embeddings-and-vector-store.md` | run a second vLLM (embedding model) + install Qdrant; chunk → embed → upsert a tiny corpus | what an embedding is (a point in semantic space), what an ANN/HNSW index does (cosine nearest-neighbor), why vector search beats keyword search |
| 02 | `lab-02-the-rag-pipeline.md` | embed question → top-k search → stuff chunks → generate through the Phase 06 gateway | chunking + the context window (why top-k, not the whole corpus), the grounding prompt template, RAG vs fine-tuning, the hallucination/empty-retrieval failure |
| 03 | `lab-03-agentic-rag.md` | expose retrieval as a tool an agent decides to call: a Strands `@tool` and an MCP tool for a kagent `Agent` | the agent decides when to retrieve (vs always-retrieve); retrieval as a harness tool; (optional) evaluating retrieval quality with Strands Evals |

## Manifests

| File | What it is |
|---|---|
| `manifests/embed-vllm.yaml` | the **embedding** vLLM Deployment + Service (`--runner pooling`, `BAAI/bge-small-en-v1.5`) |
| `manifests/qdrant-values.yaml` | Helm values for the **Qdrant** vector DB (install command in lab-01) |
| `manifests/ingest-configmap.yaml` | the tiny corpus + the stdlib **ingest** script (chunk → embed → upsert) |
| `manifests/ingest-job.yaml` | the one-shot **ingest Job** |
| `manifests/rag-route.yaml` | the **gateway route** for RAG generation (`rag.example.com`) + a token-budget policy |
| `manifests/retrieval-mcp-server.yaml` | the in-cluster **retrieval MCP server** (one tool: `retrieve`) for lab-03 |
| `manifests/rag-agent.yaml` | the `RemoteMCPServer` + kagent **`Agent`** that calls `retrieve` |

## The one idea to carry out

**RAG gives an LLM your data without changing the model.** You change what the model sees at
query time; its weights never move. The whole pipeline is your existing platform (vLLM, the
gateway, kagent/MCP) plus one new workload (a vector store), wired in three steps you can debug
one layer at a time.

## The payoff

A self-hosted Q&A system over your documents, on your models, governed by your gateway:
no external API, no fine-tuning, updatable by re-ingesting in seconds. In lab-03 it
becomes agentic: retrieval is a tool your agents reach for on their own. That's the
sovereign AI platform on Akamai story, with an application sitting on top of it.

> **Prove each layer before you go agentic:** get one chunk retrieved and one grounded answer
> back before you add the agent. A RAG bug is almost always a retrieval bug or a prompt bug.
> Prove each layer returns what you think it does, and the hallucinations stop being mysterious.

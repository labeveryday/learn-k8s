# Lab 01: Run vLLM (or ollama) in Docker First

**What you'll build:** an LLM inference server running locally in a single container, either vLLM
serving TinyLlama on CPU (Option A) or ollama as a lighter fallback (Option B), exposing an
**OpenAI-compatible HTTP API** on a port you can `curl`. Nothing here touches Kubernetes yet.
Before you wrap it in Pods, Services, and probes, prove one thing: the container runs, loads a
model, and answers requests. Every later lab in this phase is this same container scheduled by
Kubernetes, so debugging it here, where no cluster is in the way, is the cheapest place to find
out it works.

> **The one idea:** an LLM server is a Pod. A big Pod, but a Pod. Before you
> believe that, you have to see it run as a plain process in a plain container. K8s adds
> scheduling, health checks, and a stable address; it does not change what's inside the
> container. So: container first, cluster second.

## The shape: model on disk, server reads it, you talk to the server

```
model weights (on disk) ──mounted──► container (vllm/ollama serves) ──HTTP :8000/:11434──► you (curl)
```

The non-obvious part for newcomers: the server does not contain the model. The image is
the serving engine; the weights are gigabytes of data you supply separately, downloaded
to a host folder and **bind-mounted** in (Option A), or pulled into a named volume by the
engine itself (Option B). This separation is why the Kubernetes version (`vllm.yaml`)
uses an **initContainer + PersistentVolumeClaim**: the same split, cluster-native.

## Option A: vLLM CPU

vLLM needs the model weights on disk; it doesn't download them for you. Get them first (same model the `vllm.yaml` initContainer fetches):

```bash
pip install -U huggingface_hub                                                                                      # the HF download client; -U upgrades if already present
python -c "from huggingface_hub import snapshot_download; snapshot_download('TinyLlama/TinyLlama-1.1B-Chat-v1.0', local_dir='$HOME/models/tinyllama')"   # pull all weight files into ~/models/tinyllama
```

- `snapshot_download(repo, local_dir=...)` fetches the whole model repo (config, tokenizer,
  safetensors weights) into the folder you name; that folder is what the server reads.
- `TinyLlama/TinyLlama-1.1B-Chat-v1.0` is a deliberately tiny (1.1B-param) chat model chosen so
  it loads in RAM on a laptop. This is the same repo string the `vllm.yaml` initContainer
  runs in-cluster, so what you prove here transfers 1:1 to the K8s lab.

**What you should see:** progress bars, then `~/models/tinyllama/` populated with a few files
including a `*.safetensors`. That directory existing is the precondition for everything below;
the container reads from it.

Now bind-mount that folder into the container and serve it:

```bash
docker run --rm -it \
  -p 8000:8000 \                                   # publish container :8000 to host :8000 (the API port)
  -v $HOME/models/tinyllama:/models/tinyllama \    # bind-mount the weights you just downloaded INTO the container
  vllm/vllm-openai:latest \                         # the serving engine image (large: ~5-10 GB, first pull is slow)
  --model /models/tinyllama \                       # load the model from the mounted path (NOT a HF repo name)
  --dtype float32 \                                 # weight number format - full precision (see below)
  --device cpu \                                    # no GPU on a Mac; run on CPU
  --max-model-len 1024                              # cap context window to 1024 tokens so it fits in RAM
```

- `--rm -it` removes the container on exit and gives you an interactive terminal so you can
  watch the startup logs and Ctrl-C to stop it. (Drop `-it`, add `-d`, to run it detached.)
- `-v host:container` is the **bind mount**: the weights live on your host, the container
  reads them. The path on the right (`/models/tinyllama`) is what you pass to `--model`.
- These four serving flags are identical to the `args:` in `vllm.yaml`; that manifest adds
  only `--host=0.0.0.0` and `--port=8000` (vLLM defaults to `0.0.0.0:8000` here, so docker run
  doesn't need them spelled out).

The three serving flags: `--device cpu` (no GPU here), `--dtype float32` (the number format for the weights; CPU can't do float16 well, so use full precision), `--max-model-len 1024` (cap context to 1024 tokens so it fits in RAM).

**What you should see:** a long startup (weights load, then `Application startup complete` /
a Uvicorn line about serving on `0.0.0.0:8000`). First startup is slow because vLLM is reading
the whole model into memory. **Gotcha:** if it OOMs or hangs, lower `--max-model-len` further;
context length is the biggest RAM lever on CPU.

First startup is slow (model load). Once ready:

```bash
# OpenAI-compatible endpoints:
curl http://localhost:8000/v1/models                  # lists the loaded model - the readiness signal vllm.yaml probes hit

curl http://localhost:8000/v1/chat/completions \      # the chat endpoint - same shape as OpenAI's
  -H 'Content-Type: application/json' \
  -d '{
    "model": "/models/tinyllama",
    "messages": [{"role": "user", "content": "Say hi in 5 words."}],
    "max_tokens": 32
  }'
```

- `/v1/models` is the cheapest "is it up?" check; it returns instantly once the server is ready.
  This is the path `vllm.yaml`'s startup/readiness/liveness probes `httpGet`: a healthy
  `/v1/models` here is what Kubernetes keys its health on later.
- `/v1/chat/completions` with `messages` + `max_tokens` is the OpenAI chat API contract; the
  `model` field must be the path you loaded from (next paragraph).

vLLM identifies the model by the path you loaded it from, so the `model` field echoes `/models/tinyllama`, not a friendly name. Check `/v1/models` above if unsure what to pass.

**What you should see:** `/v1/models` returns JSON with `"id": "/models/tinyllama"`; the chat
call returns a `choices[0].message.content` with a short reply. That JSON shape is what matters:
any OpenAI client works unchanged:

Same API shape as OpenAI's: point LangChain, llama-index, or the Python `openai` SDK at it with `base_url=http://localhost:8000/v1`.

## Option B: ollama (fallback / lighter)

You already have ollama. It also speaks an OpenAI-compatible API:

```bash
# If ollama runs on your Mac (not in Docker):
ollama pull tinyllama                                  # download the model into ollama's local store
ollama run tinyllama "hello"                           # one-shot prompt to confirm it generates

# Its HTTP API:
curl http://localhost:11434/v1/models                  # ollama's OpenAI-compatible model list (note port 11434, not 8000)
curl http://localhost:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "tinyllama", "messages": [{"role":"user","content":"hi"}]}'
```

- ollama listens on **11434**, not 8000; keep the ports straight, since the K8s manifests do too
  (`ollama.yaml` exposes 11434, `vllm.yaml` exposes 8000).
- Unlike vLLM, ollama refers to the model by a **friendly name** (`tinyllama`), not a path,
  because it manages its own model store.

**What you should see:** `ollama run` streams a reply in the terminal; the `curl` calls return
the same OpenAI-shaped JSON as Option A. Same contract, lighter engine.

For K8s, you'll want ollama *in a container* instead. There's an official image:

```bash
docker run -d --name ollama -p 11434:11434 -v ollama:/root/.ollama ollama/ollama   # detached; named VOLUME (not bind mount) for the model store
docker exec ollama ollama pull tinyllama                                            # pull the model INTO the running container's volume
curl http://localhost:11434/api/tags                                               # ollama's native model list - the path ollama.yaml probes hit
```

- `-d --name ollama` runs it detached with a stable name so `docker exec`/`docker logs` can
  target it.
- `-v ollama:/root/.ollama` is a **named volume** (note: no `/` or `$HOME` prefix), so models
  survive container restarts. In `ollama.yaml` this same path (`/root/.ollama`) is backed by a
  PersistentVolumeClaim, the cluster-native equivalent of this volume.
- `docker exec ollama ollama pull tinyllama` runs the pull *inside* the already-running
  container, since the image starts empty.
- `/api/tags` is ollama's native health/list endpoint, the exact path `ollama.yaml`'s
  probes use (vs. the OpenAI-shim `/v1/models`).

**What you should see:** `/api/tags` returns a JSON `models` array containing `tinyllama` once
the pull finishes. That's the container serving from its volume, the shape `ollama.yaml`
deploys.

Pick whichever you can comfortably run. The rest of Phase 4 assumes one of the two is working locally.

## Practice

> A *token* is a chunk of text (~¾ of a word). **Tokens/sec** = how many it generates per second, the throughput metric for a serving engine. **Batching** = merging several in-flight requests so the GPU/CPU does more work per pass (the "continuous batching" the README describes).

1. Send a request with `max_tokens=256`. Time it. What's your tokens/sec?
2. Open two concurrent request streams. Does throughput scale or collapse? (Hint: with CPU vLLM, batching helps; ollama is single-stream by default.)
3. Look at container RAM usage (`docker stats`). How much does the model occupy?

## Next

→ `lab-02-on-kubernetes.md`: the container works; now Kubernetes schedules it. **Path A**
points the cluster at the ollama you ran on your Mac (via an ExternalName Service, no
image pulls); **Path B** runs vllm/ollama as actual Pods using the manifests whose probes,
ports, and model paths you matched by hand.

# Lab 01 — vLLM as a backend: the workload the AI gateway points at

> Deploy an OpenAI-compatible vLLM server into kind and confirm it answers — *before* any gateway is in front of it. You can't route to, meter, or guard a backend that isn't there.

**Time:** ~20 min · **Cost:** free (local kind, CPU model)

## The problem

Everything in this phase — token metering, model routing, prompt guards — is a thing a
gateway does *to LLM traffic on its way to a model*. None of it means anything until a
model is actually running and speaking a protocol the gateway can read. So before you
touch a gateway, you need the **backend**: a server that accepts an OpenAI-shaped request
and returns an OpenAI-shaped response, complete with the one field the whole phase hinges
on — a token count. This lab stands that server up and proves it answers, so that when
lab-02 puts a gateway in front of it, you already know exactly what the gateway is
parsing.

## What it replaces, and why a Service alone wasn't enough

In `04-vllm` you ran vLLM as a bare server. Here it becomes a **Kubernetes workload** — a
`Deployment` (so it self-heals and is reschedulable) plus a `Service` (so it has a stable
in-cluster name). That's the same Phase 03 machinery you already own; nothing new yet.

The point to hold onto: a `Service` gives you a stable name and load-balances *bytes*. It
has no idea those bytes are an OpenAI chat request. That's fine for now — vLLM itself does
the protocol work. The gateway in lab-02 is what adds protocol awareness *on top of* this
Service. This lab establishes the floor: a plain L4/L7 endpoint that happens to serve an
LLM API.

## Under the hood (MIT hat): what vLLM actually exposes

`vllm/vllm-openai-cpu:latest-x86_64` is an inference **server** that emulates the OpenAI
HTTP API. When the container starts it does two slow things, in order, which is why the
rollout takes minutes on CPU:

```
container start
   │  1. pull model weights from Hugging Face (Qwen/Qwen2.5-0.5B-Instruct)
   │  2. load weights into RAM + build the KV cache (no GPU → float32 on CPU)
   ▼
/health returns 200   ← readiness probe flips Ready only now
   │
serves:  /v1/models             (what's loaded)
         /v1/completions        (raw text in → text out; base models)
         /v1/chat/completions   (messages[] in → message out; CHAT models)
```

Two facts that drive the rest of the phase:

- **The endpoint you hit depends on the model's *type*.** Qwen2.5-0.5B-**Instruct** is
  chat-tuned, so it knows how to consume a `messages` array and `/v1/chat/completions`
  works. A plain base model (e.g. `facebook/opt-125m`) only serves `/v1/completions` and
  **400s** on a chat request. The model decides the contract; the server enforces it.
- **Every successful response carries a `usage` block** — `prompt_tokens`,
  `completion_tokens`, `total_tokens`. vLLM computes this from its own tokenizer. This is
  the field a *plain* Service cannot see (it only moves bytes) and the field an AI gateway
  reads to meter by tokens. The whole reason Phase 06 exists is sitting in this JSON.

Below all of that, it's the stack you already know: the `Service` ClusterIP DNATs to the
vLLM Pod via kube-proxy; CoreDNS resolves `vllm.default.svc.cluster.local`.

## Step 0 — Reuse your Phase 05 cluster

```bash
kubectl config current-context        # expect kind-kind (cluster "kind" from Phase 05)
kubectl get gatewayclass              # kgateway and/or kong should be present
```

**What to look for:** a current context of `kind-kind` and at least one `GatewayClass`
listed. If you tore the cluster down, redo `05-gateway-api/lab-01` and `lab-02` first —
this phase builds directly on that front door.

## Step 1 — Deploy vLLM (CPU, tiny chat model)

```bash
kubectl apply -f manifests/vllm-deploy.yaml
kubectl rollout status deploy/vllm --timeout=900s
```

The manifest is a vanilla Deployment + Service. The non-obvious bits, and why they're
there:

- `image: vllm/vllm-openai-cpu:latest-x86_64` — the prebuilt CPU server image. kind has
  no GPU. On Apple Silicon / arm64 nodes edit the tag to `vllm/vllm-openai-cpu:latest-aarch64`.
- `--dtype float32` — CPU has no bf16/fp16 path, so weights load in full precision.
- `--max-model-len 1024` — caps context to keep RAM in check on a laptop.
- `failureThreshold: 60` on the readiness probe + the long `--timeout` — the first
  rollout has to download weights from Hugging Face *and* load them without a GPU. That's
  minutes, not seconds.

**What to look for:** `rollout status` blocks until the probe passes, then prints
`deployment "vllm" successfully rolled out`. While you wait, `kubectl get pods` shows the
Pod `Running` but `0/1 READY` — the container is up, but `/health` won't return 200 until
the weights are loaded. That gap between "Running" and "Ready" *is* the model loading;
watch it flip.

## Step 2 — Confirm it serves the OpenAI API

```bash
kubectl port-forward svc/vllm 8000:8000 &

curl -s http://localhost:8000/v1/models | python3 -m json.tool
```

**What to look for:** a JSON list with one entry whose `id` is
`Qwen/Qwen2.5-0.5B-Instruct`. That string is the exact value callers must put in the
`model` field — the server validates against it (you'll prove that in "Break it").

Now send a real chat completion:

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"Kubernetes is"}],"max_tokens":16}' \
  | python3 -m json.tool
```

**What to look for:** the `choices[0].message.content` text *and* — more important — the
`usage` block:

```json
"usage": { "prompt_tokens": 30, "completion_tokens": 16, "total_tokens": 46 }
```

**That token count is the whole reason Phase 06 exists.** A normal HTTP gateway forwarding
this response sees only a 200 and some bytes. An AI gateway reads `usage.total_tokens` and
can charge, limit, or log against it. Burn that distinction in now — every later lab is a
variation on "the gateway parsed this JSON."

## Step 3 — Stop the port-forward

```bash
kill %1 2>/dev/null
```

From here on, traffic reaches vLLM *through a gateway*, not a port-forward. The
port-forward was a debugging shortcut to talk to the backend directly; production callers
never do that.

## Break it, then read the error (Kelsey lens)

Ask for a model the server didn't load, and read exactly what comes back:

```bash
kubectl port-forward svc/vllm 8000:8000 &
curl -s http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4","messages":[{"role":"user","content":"hi"}],"max_tokens":4}'
kill %1 2>/dev/null
```

**Read the error, don't skim it.** vLLM returns a `404` with a body like
`"The model 'gpt-4' does not exist."` This tells you something architectural: **request
validation happens at the server**, against the set of models it actually loaded (the
list you saw in Step 2). The `model` field isn't a hint — it's checked.

This is the failure the gateway will later own. In lab-04 the gateway routes on this same
`model` field *before* the request reaches any vLLM, so a caller asking for an unknown
model can be matched, rejected, or sent elsewhere at the door — instead of every backend
having to defend itself. Same field, validated one floor higher.

## Checkpoint — you can now explain…

- **Why the backend comes first.** A gateway meters, routes, and guards traffic *to a
  model*; with no model speaking the OpenAI protocol there is nothing to meter, route, or
  guard. vLLM is the workload; the gateway is a floor above it.
- **Why a chat model and not a base model.** `/v1/chat/completions` consumes a `messages`
  array; only an instruct/chat-tuned model (Qwen) honors it. A base model 400s — the
  model type decides the contract.
- **What the `usage` block is and why it matters.** It's vLLM's own token accounting
  (`prompt`, `completion`, `total`). A plain Service can't see it; an AI gateway reads it
  to bill and limit by tokens. This single field is the difference the rest of the phase
  is built on.
- **What the unknown-model 404 reveals.** The server validates `model` against what it
  loaded — and in lab-04 the gateway will validate/route on that same field one layer up.

You can now:
- [ ] Run vLLM as a Deployment + Service and explain what each gives you.
- [ ] Point to `usage.total_tokens` and say why a plain gateway can't act on it.
- [ ] Describe what the chat endpoint requires of the model, and read the 404 a wrong
  model name produces.

## Next

→ `lab-02-kgateway-ai.md`: route to this vLLM through kgateway's AI Gateway and meter
callers by **tokens** — the field you just found, now enforced at the front door.

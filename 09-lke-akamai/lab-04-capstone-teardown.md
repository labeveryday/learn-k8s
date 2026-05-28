# Lab 04 — Capstone: the whole stack on real infra, then tear it down

**Goal:** assemble every floor of the platform on the LKE cluster — Gateway → AI gateway →
vLLM on GPU — send one client request down through all of them, then delete everything and
*verify the cloud resources are actually gone*. The lesson is as much "decommission
correctly" as "deploy."

**Time:** ~40 min + teardown · **Cost:** 💸💸 highest of the track — finish and destroy the SAME DAY

## The problem

You've run each tool in isolation — kgateway in Phase 05, the AI gateway in 06, vLLM in
04/this phase. But a platform isn't five tools; it's one **request path** that crosses all
of them. You haven't yet seen a single call enter through a real public load balancer, get
metered by an AI gateway, and be answered by a model on a real GPU — end to end, on infra
that bills you. And you haven't faced the other half of running real infra: deleting it so
cleanly that the meter actually stops. A cluster you can create but can't fully tear down
is a recurring bill.

## What it ties together

Each floor is something you already built; the capstone is the *composition* on real
hardware (the PLATFORM-TRACK.md diagram, now live):

```
client
  │  real Akamai NodeBalancer (Phase 09 lab-02 mechanism)
  ▼
kgateway "http" Gateway  ──► Envoy data plane           (Phase 05)
  │  HTTPRoute → AI Backend
  ▼
agentgateway AI Backend  ──► token rate limit, model routing   (Phase 06)
  │
  ▼
vLLM on a GPU node       ──► answers the OpenAI request  (Phase 09 lab-03)
```

Every arrow is a mechanism you traced in an earlier lab. Nothing new is introduced here —
the skill being tested is wiring known floors together and reasoning about the whole path.

## Part A — Stack it up

You've applied each of these on kind already; the manifests are reused with the LKE tweaks
from labs 02–03.

### Step 1 — Gateway API + kgateway (Phase 05)

Install the Gateway API CRDs and kgateway via Helm **first** — the GatewayClass must exist
before the Gateway, or the Gateway has no controller to program it (the "ghost Gateway"
lesson from `05-gateway-api/lab-01`). Then apply Phase 05's `http` Gateway into
`kgateway-system`:

```bash
KGW=v2.3.1   # match the pin you used in Phase 05

helm upgrade -i --create-namespace --namespace kgateway-system --version ${KGW} \
  kgateway-crds oci://cr.kgateway.dev/kgateway-dev/charts/kgateway-crds
helm upgrade -i --namespace kgateway-system --version ${KGW} \
  kgateway oci://cr.kgateway.dev/kgateway-dev/charts/kgateway \
  --set agentGateway.enabled=true \
  --set agentGateway.enableAlphaAPIs=true
kubectl -n kgateway-system rollout status deploy/kgateway

# Phase 05's Gateway "http", in namespace kgateway-system:
kubectl apply -f ../05-gateway-api/manifests/kgateway-gateway.yaml
kubectl -n kgateway-system get gateway http
```

`--set agentGateway.enabled=true` is what makes kgateway double as the *AI* gateway in
step 3; `enableAlphaAPIs=true` turns on the alpha AI Backend CRD.

**What to look for:** `kubectl -n kgateway-system get gateway http` should show
`PROGRAMMED: True` and — because this is LKE, not kind — an actual address. On kind that
address came from a port-forward; here kgateway's data-plane Service is itself a
`LoadBalancer`, so the CCM gives it a **real NodeBalancer IP**. The Phase 05 abstraction
and the Phase 09 cloud integration are now stacked.

### Step 2 — vLLM on GPU (Phase 09 lab-03)

```bash
kubectl apply -f manifests/vllm-gpu.yaml
kubectl rollout status deploy/vllm-gpu --timeout=600s
```

This assumes you completed lab-03 (GPU pool, device plugin, `hf-token` Secret). If you
tore those down, redo lab-03 steps 1–3 first.

### Step 3 — AI gateway route + token limit (Phase 06)

```bash
kubectl apply -f ../06-ai-gateway/manifests/kgateway-ai-backend.yaml
kubectl apply -f ../06-ai-gateway/manifests/kgateway-ai-route.yaml
kubectl apply -f ../06-ai-gateway/manifests/kgateway-token-ratelimit.yaml
```

> ⚠️ **You must edit Phase 06's *shared* `kgateway-ai-backend.yaml` before applying — and
> this mutates a file Phase 06 also uses.** The kind version of that Backend points at the
> kind vLLM (`Qwen/Qwen2.5-0.5B-Instruct`, host `vllm.default.svc.cluster.local`). For the
> GPU stack, change two fields:
> - `spec.ai.provider.openai.model` → `meta-llama/Llama-3.2-1B-Instruct` (the GPU model
>   that `vllm-gpu` actually serves)
> - `spec.ai.provider.host` → `vllm-gpu` (the GPU Service, instead of the kind `vllm`)
>
> If the Backend's `model` and the model vLLM serves disagree, vLLM 404s the request.
>
> **Because this is the same file Phase 06 reads, you have now diverged it from 06's kind
> values.** When you're done with Phase 09 (after teardown), revert those two fields to
> `Qwen/Qwen2.5-0.5B-Instruct` and `vllm.default.svc.cluster.local` — otherwise a later
> trip back to Phase 06 on kind will point the Backend at a GPU model/host that doesn't
> exist there and silently fail. `git checkout ../06-ai-gateway/manifests/kgateway-ai-backend.yaml`
> restores it cleanly.

**What to look for:** the `AgentgatewayBackend` and the HTTPRoute apply without error. The
route attaches to the `http` Gateway (`parentRef`) and forwards to the AI Backend, which
now targets your GPU vLLM. The token-ratelimit object is the metering you'll exercise in
the final request.

### Step 4 — An agent (Phase 07) and a Wasm shim (Phase 08), optional

If your GPU node pool has the Spin shim, redeploy the `SpinApp` and kagent `ModelConfig`
pointing at the gateway host. Otherwise demo them on kind — the platform *shape* is what
matters, not running all five floors on the meter at once.

### Step 5 — One request, all the way down

```bash
export GW_IP=$(kubectl -n kgateway-system get svc http \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')   # a real NodeBalancer

curl -s http://$GW_IP/v1/chat/completions -H 'Host: llm.example.com' \
  -H 'Content-Type: application/json' \
  -d '{"model":"meta-llama/Llama-3.2-1B-Instruct","messages":[{"role":"user","content":"Name the layers of an AI platform:"}],"max_tokens":64}' \
  | python3 -m json.tool
```

**What to look for:** a chat completion in the response. Trace the path it just took: your
machine → Akamai **NodeBalancer** (`$GW_IP`) → kgateway's Envoy, which matched the `Host`
header and the `/v1/chat/completions` route → the **AI gateway** (token metered) → **vLLM
on the GPU**. Every floor of the diagram, on real infrastructure, in one curl. The `Host:
llm.example.com` header is what the HTTPRoute matches on — drop it and the Gateway won't
know which route to use.

## Part B — Tear it down (do not skip)

Delete in reverse so dependencies unwind cleanly, and **confirm the cloud resources are
actually gone** — a deleted PVC with a `retain` StorageClass leaves the volume behind, and
a deleted cluster does not always reap its NodeBalancers.

### Step 1 — App + gateway objects

```bash
# Delete only what this capstone applied — not the whole shared 06 folder:
kubectl delete -f ../06-ai-gateway/manifests/kgateway-token-ratelimit.yaml --ignore-not-found
kubectl delete -f ../06-ai-gateway/manifests/kgateway-ai-route.yaml --ignore-not-found
kubectl delete -f ../06-ai-gateway/manifests/kgateway-ai-backend.yaml --ignore-not-found
kubectl delete -f manifests/vllm-gpu.yaml --ignore-not-found
helm uninstall kgateway -n kgateway-system
helm uninstall kgateway-crds -n kgateway-system
```

### Step 2 — The cluster (kills nodes, GPU pool, and the Gateway's NodeBalancer)

```bash
linode-cli lke cluster-delete $LKE_ID
```

### Step 3 — Verify NOTHING lingers (this is where surprise bills come from)

```bash
linode-cli nodebalancers list     # expect empty
linode-cli volumes list           # 'retain' volumes outlive the cluster — delete them:
# linode-cli volumes delete <volume-id>
linode-cli linodes list           # expect no learn-k8s-platform nodes
```

**Read these as the real teardown test.** Deleting the *cluster* deletes the Kubernetes
control plane and worker nodes — but cloud resources the CCM and CSI created are separate
Linode objects. The `linode-block-storage-retain` volume from lab-02 is *designed* to
survive (that was the data-safety feature); here that same feature means it keeps billing
until you delete it by hand. If any command above still lists resources, delete them
manually. **A cluster being gone does not guarantee its NodeBalancer and retained volumes
are gone** — and don't forget to revert the Phase 06 Backend edit (see step 3's warning in
Part A).

## Checkpoint — you can now explain…

- [ ] **What "the stack" means concretely.** One request path: NodeBalancer → kgateway
  Envoy (route match) → AI gateway (token metering) → vLLM on GPU. Five floors you built
  separately, composed on real infra.
- [ ] **Why install order matters.** GatewayClass/CRDs before the Gateway, vLLM before the
  Backend that targets it — each layer's controller must exist before the object it
  programs.
- [ ] **Why deleting the cluster isn't deleting everything.** CCM-created NodeBalancers
  and CSI `retain` volumes are independent Linode objects; they survive cluster deletion
  and keep billing until explicitly removed.
- [ ] **Why a shared manifest edit is a trap.** Mutating Phase 06's `kgateway-ai-backend.yaml`
  in place diverges it from its kind values; revert it so a return to Phase 06 doesn't
  silently break.

## What you proved in Phase 09 — and the whole track

You took a five-floor AI platform — Gateway API, AI gateway, vLLM, kagent, Spin — off of
free kind and onto real Akamai infrastructure: NodeBalancer, Block Storage, GPU pool. You
watched one request cross every layer, then tore it down responsibly. Across the track you
saw the through-line: kind faked the cloud-facing objects (no-op LoadBalancer, local PVC,
no GPU), and LKE made each one real via a specific controller — CCM, CSI, device plugin —
that you can now name and debug. You can build, run, debug, *and* decommission an AI
platform, one floor at a time.

> **Kelsey:** "The engineer who can delete the cluster sleeps better than the one who only
> knows how to create it."

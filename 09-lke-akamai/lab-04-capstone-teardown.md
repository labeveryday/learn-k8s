# Lab 04: Capstone, the whole stack on real infra, then tear it down

**Goal:** assemble every floor of the platform on the LKE cluster (Gateway → AI gateway →
vLLM on GPU), send one client request down through all of them, then delete everything and
verify the cloud resources are gone. The lesson is as much "decommission
correctly" as "deploy."

**Time:** ~40 min + teardown · **Cost:** 💸💸 highest of the track, finish and destroy the SAME DAY

## The problem

You've run each tool in isolation: kgateway in Phase 05, the AI gateway in 06, vLLM in
04/this phase. A platform is one request path that crosses all five. You haven't yet
seen a single call enter through a real public load balancer, get
metered by an AI gateway, and be answered by a model on a real GPU, end to end, on infra
that bills you. And you haven't faced the other half of running real infra: deleting it so
cleanly that the meter stops. A cluster you can create but can't fully tear down
is a recurring bill.

## What it ties together

Each floor is something you already built; the capstone is the composition on real
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

Every arrow is a mechanism you traced in an earlier lab. Nothing new is introduced here;
the skill being tested is wiring known floors together and reasoning about the whole path.

## Part A: Stack it up

You've applied each of these on kind already; the manifests are reused with the LKE tweaks
from labs 02–03.

If you opened a new terminal since the earlier labs, re-derive `$KUBECONFIG` and `$LKE_ID`
first; they don't survive a new shell, and an empty `$LKE_ID` on `cluster-delete` (Part B)
is the destructive surprise this phase warns about:

```bash
export KUBECONFIG=$PWD/lke-kubeconfig.yaml   # point kubectl at THIS cluster's kubeconfig, not ~/.kube/config
export LKE_ID=$(linode-cli lke clusters-list --json | python3 -c \
  'import sys,json;print([c["id"] for c in json.load(sys.stdin) if c["label"]=="learn-k8s-platform"][0])')
echo "cluster id: $LKE_ID"   # must be non-empty before any linode-cli lke command
```

The one-liner asks Linode for all clusters as JSON, then picks the `id` whose `label` is
`learn-k8s-platform`, deriving the ID instead of hardcoding it so you never paste a stale or
wrong cluster ID into `cluster-delete`. If the `echo` prints blank, the label didn't match
(typo, or the cluster's gone): do not run any `linode-cli lke` delete with an empty `$LKE_ID`.

### Step 1: Gateway API + kgateway (Phase 05)

Install the Gateway API CRDs and kgateway via Helm first: the GatewayClass must exist
before the Gateway, or the Gateway has no controller to program it (the "ghost Gateway"
lesson from `05-gateway-api/lab-01`). Then apply Phase 05's `http` Gateway into
`kgateway-system`:

```bash
KGW=v2.3.1   # match the pin you used in Phase 05; if you don't remember, v2.3.1 is a safe pin

helm upgrade -i --create-namespace --namespace kgateway-system --version ${KGW} \
  kgateway-crds oci://cr.kgateway.dev/kgateway-dev/charts/kgateway-crds   # CRDs first: the Gateway/Backend kinds must be REGISTERED before any chart references them
helm upgrade -i --namespace kgateway-system --version ${KGW} \
  kgateway oci://cr.kgateway.dev/kgateway-dev/charts/kgateway \
  --set agentGateway.enabled=true \        # turn kgateway into the AI gateway too (used in step 3)
  --set agentGateway.enableAlphaAPIs=true  # enable the alpha AgentgatewayBackend/Policy CRDs
kubectl -n kgateway-system rollout status deploy/kgateway   # blocks until the controller is Ready
```

- `helm upgrade -i` (`-i` = `--install`) upserts: installs the release if absent, upgrades it
  if present, so re-running is safe.
- `--version ${KGW}` pins the chart to **v2.3.1** (the Phase 05 pin). Don't float this: the
  AgentgatewayBackend/Policy CRDs are alpha and their field shape changes between versions.
- Two charts, in order: `kgateway-crds` registers the custom resource kinds, then `kgateway`
  installs the controller that acts on them. Reversed, the controller install would reference
  kinds the apiserver doesn't know yet.

The two `--set agentGateway.*` flags are the whole reason this single install covers both the
Phase 05 and Phase 06 floors: `enabled=true` makes kgateway double as the AI gateway in
step 3; `enableAlphaAPIs=true` turns on the alpha AI Backend/Policy CRDs (`agentgateway.dev/v1alpha1`)
those manifests use. Leave either off and step 3's `apply` fails with "no matches for kind."

Now apply Phase 05's Gateway, the listener that fronts the whole stack (`../05-gateway-api/manifests/kgateway-gateway.yaml`):

```yaml
apiVersion: gateway.networking.k8s.io/v1   # the GA Gateway API group (Gateway is v1, stable)
kind: Gateway
metadata:
  name: http
  namespace: kgateway-system               # lives WITH the controller; routes attach from any ns
spec:
  gatewayClassName: kgateway               # binds to the kgateway GatewayClass the Helm chart installed; no class, no controller programs it ("ghost Gateway")
  listeners:
  - name: http
    port: 80                               # the public :80 listener the NodeBalancer fronts
    protocol: HTTP
    allowedRoutes:
      namespaces:
        from: All                          # let HTTPRoutes in ANY namespace attach (the Phase 06 'llm' route lives in default)
```

```bash
kubectl apply -f ../05-gateway-api/manifests/kgateway-gateway.yaml
kubectl -n kgateway-system get gateway http
```

**Gotcha:** `from: All` is what lets the `llm` HTTPRoute (in `default`, applied in step 3)
bind to this Gateway (in `kgateway-system`). Tighten it to `Same` and the cross-namespace
route silently won't attach: the request 404s and nothing in the Gateway's status says why.

**What to look for:** `kubectl -n kgateway-system get gateway http` should show
`PROGRAMMED: True` and, because this is LKE, not kind, an actual address. On kind that
address came from a port-forward; here kgateway's data-plane Service is itself a
`LoadBalancer`, so the CCM gives it a real NodeBalancer IP. The Phase 05 abstraction
and the Phase 09 cloud integration are now stacked.

### Step 2: vLLM on GPU (Phase 09 lab-03)

This is the Phase 06 vLLM Deployment, GPU edition: real model, GPU dtype, and a
`nvidia.com/gpu` limit. Here is the load-bearing half of `manifests/vllm-gpu.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-gpu
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-gpu
  template:
    metadata:
      labels:
        app: vllm-gpu                       # must equal the selector above (the lab-03 Deployment trap)
    spec:
      containers:
        - name: vllm
          image: vllm/vllm-openai:latest
          args:
            - "--model"
            - "meta-llama/Llama-3.2-1B-Instruct"   # the model name the AI Backend in step 3 MUST match exactly
            - "--dtype"
            - "bfloat16"                            # GPU-native dtype (the CPU image in lab-03 used float32)
            - "--max-model-len"
            - "8192"
          env:
            - name: HUGGING_FACE_HUB_TOKEN          # Llama-3.2 is GATED; vLLM needs the token to pull it
              valueFrom:
                secretKeyRef:
                  name: hf-token                    # the Secret you created in lab-03 Step 3
                  key: token
                  optional: false                   # fail fast if the Secret is missing (don't start, then 401 mysteriously)
          ports:
            - containerPort: 8000
          readinessProbe:
            httpGet: { path: /health, port: 8000 }
            initialDelaySeconds: 60                  # model load is slow; don't probe for the first minute
            periodSeconds: 10
            failureThreshold: 60                     # ~10 min of grace before the Pod is marked unhealthy
          resources:
            limits:
              nvidia.com/gpu: 1                      # THE GPU request; this extended resource is what lands the Pod on the GPU node
              memory: 12Gi
            requests:
              cpu: "2"
              memory: 8Gi
---
apiVersion: v1
kind: Service
metadata:
  name: vllm-gpu                                     # the Service name step 3's Backend 'host' points at
  namespace: default
spec:
  selector:
    app: vllm-gpu
  ports:
    - name: http
      port: 8000
      targetPort: 8000
```

```bash
kubectl apply -f manifests/vllm-gpu.yaml
kubectl rollout status deploy/vllm-gpu --timeout=600s   # 10-min timeout: pulling the image + downloading the gated model is slow
```

- `nvidia.com/gpu: 1` is the only thing scheduling this onto the GPU node: no
  `nodeSelector` or toleration is needed because LKE does not auto-taint GPU nodes. The GPU
  shows up as an *extended resource* (advertised by the device plugin from lab-03 step 2); the
  scheduler only places this Pod on a node that has one free.
- **Gotcha:** the model name is a contract. `--model meta-llama/Llama-3.2-1B-Instruct` here
  must be byte-for-byte the `model` you set in the AI Backend (step 3). Mismatch → vLLM 404s
  the request, and the error surfaces at the gateway, not here.
- **Gotcha:** the long `--timeout=600s`. First boot pulls the image and downloads the gated
  model from Hugging Face; the `readinessProbe`'s `initialDelaySeconds: 60` + `failureThreshold: 60`
  give it ~10 min before it's declared failed. A short timeout will look like a failure mid-download.

This assumes you completed lab-03 (GPU pool, device plugin, `hf-token` Secret). If you
tore those down, redo lab-03 steps 1–3 first; without the `hf-token` Secret the Pod stays
`Pending`/`CreateContainerConfigError` because of `optional: false` above.

### Step 3: AI gateway route + token limit (Phase 06)

> ⚠️ **`kgateway-ai-backend.yaml` is shared with Phase 06 and points at the kind vLLM.**
> You must edit two fields before applying, then revert them after teardown. Do it as four beats:
>
> **1. Edit** these two fields in `../06-ai-gateway/manifests/kgateway-ai-backend.yaml`:
> ```yaml
> spec:
>   ai:
>     provider:
>       openai:
>         model: "meta-llama/Llama-3.2-1B-Instruct"   # was Qwen/Qwen2.5-0.5B-Instruct
>       host: vllm-gpu                                 # was vllm.default.svc.cluster.local
> ```
> The `model` must match what `vllm-gpu` serves, or vLLM 404s the request; `host`
> must point at the GPU Service.

Here is each of the three objects you're about to apply, so you can see what they wire
together. The AI Backend below is shown with the GPU edits from beat 1 already applied;
the committed file has `Qwen/Qwen2.5-0.5B-Instruct` and `vllm.default.svc.cluster.local`.

`kgateway-ai-backend.yaml`, describes vLLM to the gateway as an OpenAI-protocol endpoint:

```yaml
apiVersion: agentgateway.dev/v1alpha1      # the agentgateway AI CRD group (alpha, enabled by the Helm --set in step 1)
kind: AgentgatewayBackend
metadata:
  name: vllm-ai                            # the name the HTTPRoute's backendRef points at
  namespace: default
spec:
  ai:
    provider:
      openai:
        model: "meta-llama/Llama-3.2-1B-Instruct"   # EDITED (beat 1): must equal vllm-gpu's --model
      # For a SELF-HOSTED endpoint host/port are SIBLINGS of `openai`, not on the HTTPRoute backendRef:
      host: vllm-gpu                                 # EDITED (beat 1): the GPU Service from step 2 (was vllm.default.svc.cluster.local)
      port: 8000                                     # the vLLM Service port
```

- The `openai` provider tells agentgateway to speak the OpenAI chat protocol; vLLM serves that
  same API, so the gateway can talk to your model as it would to api.openai.com.
- **`host`/`port` live on the `provider` block, NOT on the route's `backendRef`.** That is the
  one structural surprise of this CRD: the route names the Backend; the Backend itself
  holds the address. (A hosted provider would drop host/port and add
  `spec.ai.policies.auth.secretRef` for the API key: same object, different target.)
- Local vLLM needs no API key, so no auth block is present.

`kgateway-ai-route.yaml`, the HTTPRoute that attaches to the `http` Gateway and forwards to that Backend:

```yaml
apiVersion: gateway.networking.k8s.io/v1   # standard Gateway API HTTPRoute (GA)
kind: HTTPRoute
metadata:
  name: llm
  namespace: default                       # different ns from the Gateway; works because of 'from: All' (step 1)
spec:
  parentRefs:
    - name: http
      namespace: kgateway-system           # attach to the Gateway from step 1 (cross-namespace)
  hostnames:
    - "llm.example.com"                     # the Host header the request in step 5 MUST send, or no route matches
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /v1/chat/completions     # the OpenAI chat path this route claims
      backendRefs:
        - group: agentgateway.dev           # NOT a plain Service; points at the AI Backend kind
          kind: AgentgatewayBackend
          name: vllm-ai                      # matches the Backend above
```

- The only thing that makes this an AI route rather than a plain HTTP one is the
  `backendRef` `group`/`kind`: it targets an `AgentgatewayBackend`, not a `Service`. Everything
  else is plain Gateway API.
- `hostnames` + the `parentRef` are the two halves of routing: the Gateway accepts the
  connection on `:80`, then this route claims requests whose `Host` is `llm.example.com` and
  path starts with `/v1/chat/completions`.

`kgateway-token-ratelimit.yaml`, meter the route by tokens, the thing a plain HTTP gateway can't express:

```yaml
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: llm-token-budget
  namespace: default
spec:
  targetRefs:
    - group: gateway.networking.k8s.io     # the policy ATTACHES to a route...
      kind: HTTPRoute
      name: llm                            # ...specifically the 'llm' route above
  traffic:
    rateLimit:
      local:                               # local (in-Envoy) limiter, no external rate-limit service
        - tokens: 500                      # budget is TOKENS, not requests; read from the LLM response's usage field
          unit: Minutes                    # 500 tokens per minute (unit enum: Seconds | Minutes | Hours)
```

- **`tokens:` not `requests:` is the whole point.** agentgateway reads each response's `usage`
  field and accumulates prompt + completion tokens against the budget, so a 5-token reply and a
  500-token reply cost differently: metering by work done, not call count. (Swap `tokens:` for
  `requests:` on the same entry to get a classic per-request limit.)
- The policy is **attached by reference** (`targetRefs` → the `llm` HTTPRoute), not embedded in
  the route. Apply order matters loosely: if the route doesn't exist yet the policy has nothing
  to bind to and sits dormant until it appears.

> **2. Apply** the three manifests (Backend first, since the route's backendRef and the policy's
> targetRef both reference objects that should already exist):

```bash
kubectl apply -f ../06-ai-gateway/manifests/kgateway-ai-backend.yaml      # the AgentgatewayBackend (vllm-ai)
kubectl apply -f ../06-ai-gateway/manifests/kgateway-ai-route.yaml        # the HTTPRoute (llm) → backendRef vllm-ai
kubectl apply -f ../06-ai-gateway/manifests/kgateway-token-ratelimit.yaml # the AgentgatewayPolicy attached to llm
```

> **3. You have now diverged this file from Phase 06's kind values.**
>
> **4. REVERT after teardown** (Part B): otherwise a later return to Phase 06 on kind
> points the Backend at a GPU model/host that doesn't exist there and silently fails:
> ```bash
> git checkout ../06-ai-gateway/manifests/kgateway-ai-backend.yaml
> ```

**What to look for:** the `AgentgatewayBackend` and the HTTPRoute apply without error. The
route attaches to the `http` Gateway (`parentRef`) and forwards to the AI Backend, which
now targets your GPU vLLM. The token-ratelimit object is the metering you'll exercise in
the final request.

### Step 4: An agent (Phase 07) and a Wasm shim (Phase 08), optional

If your GPU node pool has the Spin shim, redeploy the `SpinApp` and kagent `ModelConfig`
pointing at the gateway host. Otherwise demo them on kind; the platform shape is what
matters, not running all five floors on the meter at once.

### Step 5: One request, all the way down

`Host: llm.example.com` below is the hostname Phase 06's HTTPRoute matches on (a Phase 05/06
route value, not arbitrary); without it the Gateway can't pick a route and returns a 404.

```bash
export GW_IP=$(kubectl -n kgateway-system get svc http \
  -o jsonpath='{.status.loadBalancer.ingress[0].ip}')   # the NodeBalancer IP the CCM assigned to kgateway's data-plane Service

curl -s http://$GW_IP/v1/chat/completions -H 'Host: llm.example.com' \
  -H 'Content-Type: application/json' \
  -d '{"model":"meta-llama/Llama-3.2-1B-Instruct","messages":[{"role":"user","content":"Name the layers of an AI platform:"}],"max_tokens":64}' \
  | python3 -m json.tool
```

- The `svc http` here is kgateway's data-plane Service (Envoy), not the Gateway object;
  it's a `LoadBalancer` Service, so `.status.loadBalancer.ingress[0].ip` is the real
  NodeBalancer address. (Empty `$GW_IP`? The NodeBalancer is still provisioning; wait and
  re-run.)
- `-H 'Host: llm.example.com'` is load-bearing, not cosmetic: it's the `hostnames` value
  the `llm` HTTPRoute matches on. Drop it and the Gateway has no route to pick → 404.
- The `"model"` in the body must match both vLLM's `--model` and the AI Backend's `model`,
  the same contract as steps 2 and 3.
- `| python3 -m json.tool` pretty-prints the JSON response.

**What to look for:** a chat completion in the response. Trace the path it took: your
machine → Akamai NodeBalancer (`$GW_IP`) → kgateway's Envoy, which matched the `Host`
header and the `/v1/chat/completions` route → the AI gateway (token metered) → vLLM
on the GPU. Every floor of the diagram, on real infrastructure, in one curl. The `Host:
llm.example.com` header is what the HTTPRoute matches on; drop it and the Gateway won't
know which route to use.

## Part B: Tear it down (do not skip)

Delete in reverse so dependencies unwind cleanly, and confirm the cloud resources are
gone: a deleted PVC with a `retain` StorageClass leaves the volume behind, and
a deleted cluster does not always reap its NodeBalancers.

### Step 1: App + gateway objects

```bash
# Delete only what this capstone applied, not the whole shared 06 folder.
# Reverse of the apply order (policy → route → backend → vLLM), so nothing references a
# missing target on the way out:
kubectl delete -f ../06-ai-gateway/manifests/kgateway-token-ratelimit.yaml --ignore-not-found  # --ignore-not-found = don't error if already gone (safe to re-run)
kubectl delete -f ../06-ai-gateway/manifests/kgateway-ai-route.yaml --ignore-not-found
kubectl delete -f ../06-ai-gateway/manifests/kgateway-ai-backend.yaml --ignore-not-found
kubectl delete -f manifests/vllm-gpu.yaml --ignore-not-found
helm uninstall kgateway -n kgateway-system        # remove the controller FIRST...
helm uninstall kgateway-crds -n kgateway-system    # ...then its CRDs (reverse of install order)
```

- `--ignore-not-found` makes each delete idempotent: re-running the block won't error on
  objects that are already gone. Handy when a partial teardown left some objects behind.
- **Uninstall order mirrors install, reversed:** controller before CRDs. Removing CRDs first
  can wedge the controller (and uninstalling the CRD chart deletes the custom-resource types,
  which can strand any leftover AgentgatewayBackend/Policy objects). The `kubectl delete`s above
  already removed those instances, so the CRD chart comes off cleanly.

### Step 2: The cluster (kills nodes, GPU pool, and the Gateway's NodeBalancer)

```bash
echo "$LKE_ID"   # confirm non-empty; an empty $LKE_ID here is a bad delete
linode-cli lke cluster-delete $LKE_ID   # destroys control plane + ALL node pools (incl. the GPU pool) for THIS cluster id
```

`cluster-delete` takes the cluster id, not its label, which is why you derived
`$LKE_ID` instead of typing it. An empty `$LKE_ID` makes this a malformed/ambiguous call; a
wrong one deletes the wrong cluster. If `$LKE_ID` is blank, re-derive it with the one-liner
from the top of Part A before deleting.

### Step 3: Verify NOTHING lingers (this is where surprise bills come from)

```bash
linode-cli nodebalancers list     # expect empty; the CCM should have reaped the Gateway's NodeBalancer on Service delete
linode-cli volumes list           # 'retain' volumes OUTLIVE the cluster by design; list them, then delete by hand:
# copy an ID from the volumes-list output above into <volume-id>; if the list is empty,
# there's nothing to delete and you can skip this line:
# linode-cli volumes delete <volume-id>   # the only command here that BILLS if skipped; retained volumes keep charging
linode-cli linodes list           # expect no learn-k8s-platform nodes (cluster-delete removed them)
```

These three `list` commands are the teardown test, not cleanup: deleting the cluster
removed the Kubernetes objects, but the NodeBalancer (CCM-created) and any
`linode-block-storage-retain` volume (CSI-created, lab-02) are independent Linode resources.
`nodebalancers` and `linodes` should already be empty; `volumes` is the one that commonly isn't,
because `retain` was a deliberate data-safety choice in lab-02, and that same feature is what
keeps it billing here until you `volumes delete` it.

**Read these as the real teardown test.** Deleting the cluster deletes the Kubernetes
control plane and worker nodes, but cloud resources the CCM and CSI created are separate
Linode objects. The `linode-block-storage-retain` volume from lab-02 is designed to
survive (that was the data-safety feature); here that same feature means it keeps billing
until you delete it by hand. If any command above still lists resources, delete them
manually. A cluster being gone does not guarantee its NodeBalancer and retained volumes
are gone, and don't forget to revert the Phase 06 Backend edit (see step 3's warning in
Part A).

## Checkpoint: you can now explain…

- [ ] **What "the stack" means concretely.** One request path: NodeBalancer → kgateway
  Envoy (route match) → AI gateway (token metering) → vLLM on GPU. Five floors you built
  separately, composed on real infra.
- [ ] **Why install order matters.** GatewayClass/CRDs before the Gateway, vLLM before the
  Backend that targets it: each layer's controller must exist before the object it
  programs.
- [ ] **Why deleting the cluster isn't deleting everything.** CCM-created NodeBalancers
  and CSI `retain` volumes are independent Linode objects; they survive cluster deletion
  and keep billing until explicitly removed.
- [ ] **Why a shared manifest edit is a trap.** Mutating Phase 06's `kgateway-ai-backend.yaml`
  in place diverges it from its kind values; revert it so a return to Phase 06 doesn't
  silently break.

## What you proved in Phase 09, and the whole track

You took a five-floor AI platform (Gateway API, AI gateway, vLLM, kagent, Spin) off of
free kind and onto real Akamai infrastructure: NodeBalancer, Block Storage, GPU pool. You
watched one request cross every layer, then tore it down responsibly. Across the track you
saw the through-line: kind faked the cloud-facing objects (no-op LoadBalancer, local PVC,
no GPU), and LKE made each one real via a specific controller (CCM, CSI, device plugin)
that you can now name and debug. You can build, run, debug, and decommission an AI
platform, one floor at a time.

> The engineer who can delete the cluster sleeps better than the one who only knows how to
> create it.

## Next

→ **Phase 10** (`10-rag/`): with the platform live on real LKE, build the first real application on it: RAG Q&A over your own data, from the vLLM + gateway you already run plus one new piece (a vector store).

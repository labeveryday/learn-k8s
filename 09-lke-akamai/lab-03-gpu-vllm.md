# Lab 03 — A GPU node pool + vLLM: how hardware becomes a schedulable resource

**Goal:** add a GPU node pool, then trace the exact chain that lets the Kubernetes
scheduler place a pod on a GPU — driver → device plugin → advertised resource → matched
limit. Run the *same* vLLM Deployment from Phase 06 on a real accelerator and prove the
Service contract was hardware-agnostic all along.

**Time:** ~30 min · **Cost:** 💸💸 GPU nodes are the priciest line item — delete same day

## The problem

The scheduler knows how to place pods by CPU and memory — those are built-in countable
resources every node reports. A GPU is not built in. The kernel sees a PCI device; the
scheduler sees nothing. So even with a physical GPU bolted to a node, a pod that needs one
has no way to *ask* for it and the scheduler has no way to *count* it. Requesting CPU and
memory will never steer a pod to the card.

On kind there was no GPU at all — you ran a tiny CPU model just to learn vLLM's API shape.
The shape never changed; only the hardware was fake. Now you need the real thing, which
means solving the question kind let you skip: **how does a GPU become a resource the
scheduler can hand out?**

## What it replaces, and why "just request a GPU" doesn't work

You might expect `resources.limits.nvidia.com/gpu: 1` to work the way `cpu: 2` does — out
of the box. It doesn't, and the reason is the lesson:

| | CPU / memory | GPU |
|---|---|---|
| Who reports capacity | kubelet, automatically | **a device plugin** you install |
| Resource name | `cpu`, `memory` (built-in) | `nvidia.com/gpu` (vendor-defined) |
| Visible to scheduler? | always | only after the plugin advertises it |

CPU and memory are first-class because the kubelet measures them itself. A GPU is an
*extended resource*: Kubernetes core knows nothing about NVIDIA cards. The
**device-plugin API** is the extension point — a DaemonSet that runs on each node,
discovers the hardware, and reports it to the kubelet under a vendor-chosen name. Until
that plugin runs, `nvidia.com/gpu` is an unknown string and any pod requesting it stays
`Pending`.

## Under the hood (MIT hat): the path from silicon to schedulable

Three pieces have to line up. On standard (non-enterprise) LKE, the **first is already
done for you**:

```
1. NVIDIA driver         ──► present on the GPU node (LKE installs it in the node image)
        │                     lets the OS/CUDA actually talk to the card
        ▼
2. device plugin DaemonSet ──► runs on the GPU node, finds the card,
        │                       tells the kubelet: "this node has nvidia.com/gpu: 1"
        ▼  kubelet adds it to the node's Capacity/Allocatable
3. scheduler ──► sees a pod with resources.limits.nvidia.com/gpu: 1
        │         finds a node Allocatable that satisfies it → binds the pod there
        ▼
   pod lands on the GPU node; the plugin injects the device into the container
```

Two consequences worth holding onto:

- **The device plugin doesn't make the GPU work — it makes it *countable*.** The driver
  makes it work. The plugin's only job is advertising `nvidia.com/gpu` as an allocatable
  resource so the scheduler has something to match against.
- **No taint, no toleration, no nodeSelector needed on LKE.** Many GPU setups *taint*
  GPU nodes (so random pods don't land on expensive hardware), which then requires a
  matching toleration on your pod. **LKE does not auto-taint GPU nodes**, so the
  `nvidia.com/gpu: 1` limit *alone* is enough to steer the pod: it's the only node that
  advertises that resource, so the scheduler has exactly one candidate. That's why
  `vllm-gpu.yaml` has no toleration and no `linode.com/gpu` nodeSelector — and why adding
  one would be cargo-cult.

## Step 1 — Add a GPU node pool

If you opened a new terminal since lab-01, `$LKE_ID` and `$KUBECONFIG` are gone — re-derive
them before any `linode-cli lke` or `kubectl` command:

```bash
export KUBECONFIG=$PWD/lke-kubeconfig.yaml
export LKE_ID=$(linode-cli lke clusters-list --json | python3 -c \
  'import sys,json;print([c["id"] for c in json.load(sys.stdin) if c["label"]=="learn-k8s-platform"][0])')
```

```bash
# Discover the GPU plan type IDs available to your account:
linode-cli linodes types --text | grep -i gpu   # lists every g2-gpu-* plan + hourly price

linode-cli lke pool-create $LKE_ID \
  --type g2-gpu-rtx4000a1-s \      # the plan ID from the grep above — picks the hardware
  --count 1                        # one GPU node; this is the expensive line — keep it at 1
```

`pool-create` adds a *second* node pool to the existing cluster (`$LKE_ID`) rather than
replacing anything — your CPU nodes stay; the GPU node joins alongside them. The `--type`
is the plan you grepped for; `--count` is how many of that plan to provision.

`g2-gpu-rtx4000a1-s` is the smallest RTX 4000 Ada plan (1 GPU, 4 vCPU, 16 GB RAM) — the
cheapest deployable GPU node on LKE. (The older `g1-gpu-rtx6000-1` is **not** deployable
on LKE; use the `g2-gpu-rtx4000a1-*` family.) You add this pool *now*, late in the track,
on purpose: it's the most expensive thing you'll run, so it should exist for the shortest
time.

Wait for the node, then confirm the GPU pool's nodes registered:

```bash
kubectl get nodes -L lke.linode.com/pool-id    # the new GPU pool's nodes
# or, once the device plugin is up, label by GPU presence:
# kubectl get nodes -L nvidia.com/gpu.present
```

**What to look for:** a new node whose `POOL-ID` column matches the GPU pool. It joins as
`Ready` like any other node — at this moment Kubernetes still has *no idea* it has a GPU.
The card is present (step 1 of the under-the-hood chain) but unadvertised (step 2 is
missing). That gap is exactly what the next step closes.

## Step 2 — Install the NVIDIA device plugin

On a standard LKE cluster the **NVIDIA driver is already installed on the GPU node**
automatically (chain step 1). All that's missing is the device plugin to advertise the
GPU as a schedulable `nvidia.com/gpu` resource (chain step 2):

```bash
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.17.3/deployments/static/nvidia-device-plugin.yml
kubectl describe node -l nvidia.com/gpu.present | grep -A3 Capacity   # nvidia.com/gpu: 1
```

**What to look for:** under the node's `Capacity` block, a line `nvidia.com/gpu: 1`. That
line did not exist 30 seconds ago. The DaemonSet found the card and reported it to the
kubelet, which added it to the node's capacity — *that single line is chain step 2
completing.* Now the scheduler has a resource to match. If the line is absent, the plugin
pod isn't running on the GPU node; `kubectl -n kube-system get pods | grep nvidia` to
debug.

`kubectl apply` takes a URL, not just a local file — this is the NVIDIA-published DaemonSet
applied straight from their repo. This static manifest installs **only** the device plugin
DaemonSet — no GPU operator, no node feature discovery. That's all LKE needs, because the
driver ships with the node image; on clusters without a pre-installed driver you'd need the
full operator. The version (`v0.17.3`) matches the Akamai LKE docs; check the
[k8s-device-plugin releases](https://github.com/NVIDIA/k8s-device-plugin/releases) for newer pins.

## Step 3 — Create the Hugging Face token Secret

`vllm-gpu.yaml` runs `meta-llama/Llama-3.2-1B-Instruct`, which is a **gated** model on
Hugging Face — vLLM cannot pull it without a token. This is the **first gated model in the
track** (Phases 04/06 used non-gated TinyLlama/Qwen, so no token was ever needed). Request
access on the model page, then create a [user access token](https://huggingface.co/settings/tokens)
and store it as a Secret the Deployment mounts:

```bash
export HF_TOKEN=hf_xxx                  # paste the token you just created
kubectl create secret generic hf-token \           # name MUST be hf-token (the manifest's secretKeyRef.name)
  --from-literal=token="$HF_TOKEN"                  # key MUST be 'token' (the secretKeyRef.key) → the env var
```

The Secret `name` (`hf-token`) and the `--from-literal` key (`token`) are not arbitrary —
they are the exact `secretKeyRef.name` / `secretKeyRef.key` the Deployment looks up in
Step 4. Change either here and the pod can't find the value. `generic` is the Secret type
for arbitrary key/value data (as opposed to `docker-registry` or `tls`).

If `$HF_TOKEN` is empty, the Secret is created with a blank value and the pod 401s on the
model download — set it before running the command above.

The Deployment references this Secret with `optional: false` — so if it's missing the pod
fails fast instead of silently 401-ing on model download. **What to look for later:** if
you skip this step, the pod won't crash on a model 401 deep in the logs; it'll fail to
start because a required Secret key is absent — a faster, clearer failure by design.

> Prefer not to manage a token? Swap the `--model` arg in `manifests/vllm-gpu.yaml` for a
> non-gated small model (e.g. `Qwen/Qwen2.5-0.5B-Instruct`, the same model Phase 06 uses),
> delete the `hf-token` env block, and skip this step.

## Step 4 — Run vLLM on the GPU

`vllm-gpu.yaml` is `06-ai-gateway/manifests/vllm-deploy.yaml` adapted for real hardware —
same `Deployment` + `Service` shape you ran on kind, with exactly the fields that change
when the hardware stops being fake. Here is the whole object (`manifests/vllm-gpu.yaml`),
then the lines that earn their keep:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm-gpu                       # renamed from 'vllm' — the only contract change vs Phase 06
  namespace: default
  labels:
    app: vllm-gpu
spec:
  replicas: 1
  selector:
    matchLabels:
      app: vllm-gpu                    # must equal template labels below (lab-03 Phase 03's #1 trap)
  template:
    metadata:
      labels:
        app: vllm-gpu
    spec:
      containers:
        - name: vllm
          image: vllm/vllm-openai:latest   # the GPU/CUDA image (CPU run used vllm-openai-cpu:...)
          args:
            - "--model"
            - "meta-llama/Llama-3.2-1B-Instruct"   # a REAL gated model (CPU run used tiny Qwen)
            - "--dtype"
            - "bfloat16"               # bf16 — the GPU has a path for it; CPU was forced to float32
            - "--max-model-len"
            - "8192"                   # 8× the CPU run's 1024 — the GPU's VRAM holds a bigger KV cache
          env:
            - name: HUGGING_FACE_HUB_TOKEN   # the env var vLLM reads to authenticate the HF download
              valueFrom:
                secretKeyRef:
                  name: hf-token       # the Secret you made in Step 3
                  key: token
                  optional: false      # REQUIRED — missing Secret = pod fails to start, not a late 401
          ports:
            - containerPort: 8000      # same port as the CPU run — the Service contract is unchanged
          readinessProbe:
            httpGet: { path: /health, port: 8000 }
            initialDelaySeconds: 60    # don't probe for 60s — model download + load is slow
            periodSeconds: 10
            failureThreshold: 60       # tolerate ~10 min of not-ready before giving up on the pod
          resources:
            limits:
              nvidia.com/gpu: 1        # THE line that steers this pod to the GPU node (chain step 3)
              memory: 12Gi             # kept under the node's 16 GB so the pod stays schedulable
            requests:
              cpu: "2"
              memory: 8Gi
---
apiVersion: v1
kind: Service
metadata:
  name: vllm-gpu                       # Service name = the only thing callers must repoint at
  namespace: default
  labels:
    app: vllm-gpu
spec:
  selector:
    app: vllm-gpu                      # routes to the pods carrying app: vllm-gpu
  ports:
    - name: http
      port: 8000                       # same 8000 / OpenAI API as every earlier phase
      targetPort: 8000
```

The GPU-relevant diffs vs the CPU manifest are exactly four lines and nothing structural:
the **GPU image** (`vllm/vllm-openai:latest`), a **real gated model**
(`Llama-3.2-1B-Instruct`), **`dtype: bfloat16`** (CPU had no bf16 path so it used
`float32`), and **`resources.limits."nvidia.com/gpu": 1`**. Two gotchas:

- **`nvidia.com/gpu` belongs under `limits`, not `requests`.** Extended resources can only
  be set as limits (Kubernetes copies the value into requests for you). Put it under
  `requests` and the apiserver rejects the pod. This one limit is the *entire* scheduling
  story — there is **no** nodeSelector or toleration, because (under-the-hood section) the
  GPU node is the only one advertising `nvidia.com/gpu`, so the limit alone is a unique
  constraint. Adding a `linode.com/gpu` nodeSelector would be cargo-cult.
- **No CPU *limit*, on purpose.** Note `limits` caps memory and the GPU but not CPU — an
  unset CPU limit lets vLLM burst across all 4 vCPUs during model load instead of being
  throttled (lab-03 Phase 03: CPU over-limit = throttling). The `requests: cpu "2"` still
  reserves enough for the scheduler to place it.

Apply it and wait for the model to come up:

```bash
kubectl apply -f manifests/vllm-gpu.yaml
kubectl rollout status deploy/vllm-gpu --timeout=600s   # blocks until the pod is Ready (≤10 min)
```

- `apply -f` sends both objects (Deployment + Service) — the `---` splits one file into two.
- `--timeout=600s` is generous because the long pole is the Hugging Face **download + model
  load**, not the GPU. Without it, `rollout status` would give up at the default ~hang and
  you'd think the rollout failed when it's still pulling weights.

It needs **no** nodeSelector or toleration — for the reason you traced in the
under-the-hood section: the GPU node is the only one advertising `nvidia.com/gpu`, so the
limit alone is a unique scheduling constraint. **Same port (8000) and OpenAI API
contract** as before — only the Service *name* differs (`vllm-gpu`), so every gateway,
agent, and shim from earlier phases works unchanged once pointed at `vllm-gpu`.

**What to look for:** while it rolls out, `kubectl get pod -l app=vllm-gpu -o wide` shows
the pod landed on the GPU node (matching the pool-id from step 1) — the scheduler's match
on `nvidia.com/gpu` in action. The long timeout is the model download + load, not the GPU.

## Step 5 — Compare to the CPU run

```bash
kubectl port-forward svc/vllm-gpu 8000:8000 &       # tunnel localhost:8000 → the Service; & backgrounds it
time curl -s http://localhost:8000/v1/completions -H 'Content-Type: application/json' \
  -d '{"model":"meta-llama/Llama-3.2-1B-Instruct","prompt":"Explain Kubernetes Services:","max_tokens":128}' \
  | python3 -m json.tool                            # pretty-print the JSON response
kill %1 2>/dev/null                                 # stop the backgrounded port-forward (job %1)
```

- `port-forward svc/vllm-gpu 8000:8000` proxies your local 8000 through the apiserver to the
  Service's 8000 — no LoadBalancer needed for a quick test. The trailing `&` runs it in the
  background so the same shell can immediately `curl`; `kill %1` ends that background job after.
- `time` in front of `curl` is the whole point of this step — it prints how long the request
  took, which is the number you compare against the CPU run.
- The `model` in the body **must match** the `--model` arg from Step 4
  (`meta-llama/Llama-3.2-1B-Instruct`) — vLLM only serves the model it loaded; a mismatch 404s.

**What to look for:** a JSON body with a `choices[0].text` holding the generated completion,
and a `time` line — roughly a second or two for 128 tokens on this GPU, versus the many
seconds the same request took on the CPU run in Phase 06 lab-01 (you didn't record a number
there, but the order-of-magnitude difference is the point). The API call is byte-for-byte
the same OpenAI-protocol request — only the hardware under the Service changed. That's the
proof: the contract was hardware-agnostic all along; the GPU just made it fast.

## Break it, then read the error (Kelsey lens)

Ask for more GPUs than the node has. Set `resources.limits."nvidia.com/gpu": 2` on the
single-GPU node and re-apply:

```bash
kubectl get pod -l app=vllm-gpu
kubectl describe pod -l app=vllm-gpu | grep -A5 Events
```

**Read the error:** the pod stays `Pending`, and the event says
`0/N nodes are available: ... Insufficient nvidia.com/gpu`. Read that as a sentence: the
scheduler treated `nvidia.com/gpu` *exactly* like CPU or memory — a countable, finite
resource — and no node had 2 to give. This confirms the whole under-the-hood model:
the device plugin advertised a *count* (1), and the scheduler does integer accounting
against it. A GPU isn't special to the scheduler once it's advertised; it's just another
number that has to add up. Ask for more than exists and you wait forever — same failure
mode as requesting 64 CPUs on a 4-CPU node. Revert to `nvidia.com/gpu: 1` and re-apply.

## Checkpoint — you can now explain…

- [ ] **How a GPU becomes schedulable.** Driver (present on LKE) makes the card usable;
  the device-plugin DaemonSet discovers it and advertises `nvidia.com/gpu` to the kubelet,
  which adds it to node capacity; the scheduler then matches a pod's
  `resources.limits.nvidia.com/gpu` against that advertised count.
- [ ] **Why no toleration/nodeSelector is needed on LKE.** LKE doesn't auto-taint GPU
  nodes, and only the GPU node advertises `nvidia.com/gpu`, so the limit is already a
  unique, sufficient scheduling constraint.
- [ ] **Why the device plugin matters but the driver does the work.** The plugin makes the
  GPU *countable*; the driver makes it *functional*. Both must be present.
- [ ] **Why over-requesting GPUs hangs `Pending`.** The scheduler does integer accounting
  on `nvidia.com/gpu` like any resource; ask for more than the advertised count and no
  node satisfies it.
- [ ] **Why the vLLM contract didn't change.** Same port and OpenAI API — only the
  hardware (and the Service name, now `vllm-gpu`) changed. The third "kind faked it" rung,
  now real.

## Next

→ `lab-04-capstone-teardown.md`: stack the *whole* platform — Gateway → AI gateway → vLLM
on GPU — on real infra, watch one request cross every floor, then tear it all down so the
meter stops.

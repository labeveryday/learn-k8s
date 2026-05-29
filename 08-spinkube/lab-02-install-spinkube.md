# Lab 02 — Install SpinKube: routing a pod to `wasmtime` instead of `runc`

**Goal:** make a Kubernetes node able to run the `.wasm` from lab-01, and understand the
exact mechanism that does it — a `RuntimeClass` whose handler routes a pod through the
`containerd-shim-spin` shim to `wasmtime`, *not* `runc`. By the end you can explain why a
stock kind node physically cannot run a SpinApp, and why the k3d image here can.

**Time:** ~25 min · **Cost:** free (local k3d)

## The problem (why this exists)

You have a `.wasm` and you've felt how cheap it is to run. Now you want Kubernetes to
schedule it next to your pods. But the kubelet doesn't know how to run a `.wasm`. When
it starts a normal pod it hands the work to **containerd**, which hands it to **`runc`**,
which sets up a Linux container. `runc` has no idea what a WebAssembly module is. So the
problem isn't "write a manifest" — it's "the node literally lacks the machinery to
execute this workload type." You need two things the cluster doesn't have yet: the
`wasmtime` engine *on the node*, and a way to tell the kubelet "for this pod, don't use
`runc`."

## What it replaces, and why a normal node is insufficient

A stock Kubernetes node has exactly one path: kubelet → containerd → `runc` →
Linux container. There's no slot for a second runtime. SpinKube doesn't replace that
path; it **adds a parallel one** and a switch to select it:

| Piece | What it adds | What it replaces / removes the limit on |
|---|---|---|
| `containerd-shim-spin` (the shim, on the node) | a containerd runtime that runs `.wasm` in `wasmtime` | removes "the node can only run `runc` containers" |
| `RuntimeClass wasmtime-spin-v2` (`handler: spin`) | a named switch pods can opt into | removes "every pod is forced onto the default runtime" |
| spin-operator + `SpinApp`/`SpinAppExecutor` CRDs | a controller + new nouns | removes "you must hand-write a Deployment with the right runtimeClassName" (lab-03) |
| cert-manager | TLS for the operator's admission webhooks | a dependency, not the feature |

The key idea: a node can carry **more than one** containerd runtime. `RuntimeClass` is
Kubernetes' built-in mechanism for *choosing* one per pod. SpinKube installs a second
runtime (`wasmtime` via the shim) and a `RuntimeClass` that selects it.

## 0. Prereqs

The `spin` CLI from lab-01, plus `kubectl` and `helm` (from `00-prep`). This phase also
needs **`k3d`** — a *new* tool the rest of the track didn't use (00-prep installed `kind`).
We switch to k3d here for one reason: it can boot a node image with the Spin shim baked in
(Step 1), and kind no longer publishes one. Install it now:

```bash
brew install k3d                                              # macOS
# Linux: curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
k3d version
```

(`k3d cluster create` needs Docker running.) This lab builds a **dedicated** cluster — see
Step 1 for why.

## 1. A cluster whose nodes carry the Spin shim

A normal node runs containers via containerd + `runc`. To run Wasm, the node needs the
`containerd-shim-spin` binary installed alongside containerd *and* containerd configured
to know about it. The cleanest local path — and the one the current SpinKube quickstart
uses — is a **k3d** cluster booted from an image that already has the shim baked in:

```bash
# k3d node image with the containerd-shim-spin pre-installed (pinned tag).
k3d cluster create spin-lab \
  --image ghcr.io/spinframework/containerd-shim-spin/k3d:v0.24.0 \
  --port "8081:80@loadbalancer" \
  --agents 2
```

`--agents 2` gives you two worker nodes (so you can see scheduling spread); `--port
"8081:80@loadbalancer"` exposes the cluster's built-in load balancer on host port 8081 —
boilerplate that lets you `curl` Services without a port-forward. This phase uses
`kubectl port-forward` instead (lab-03), so 8081 goes unused here; leave it, it's harmless.

**What to look for:** `k3d cluster create` finishes and `kubectl get nodes` shows the
server + 2 agents `Ready`. The whole reason this image exists is the shim baked into its
containerd — that's the load-bearing detail of this lab.

> **Why k3d and not kind here?** This is the mechanism, not a preference. The shim must
> be a *binary on the node* and registered in that node's `containerd` config. A stock
> kind node's containerd only knows `runc` — there is no `spin` runtime for the
> `RuntimeClass` handler to resolve to, so a SpinApp pod would never start (you'll prove
> exactly that in "Break it"). kind has no SpinKube-published node image anymore — the
> old `ghcr.io/spinkube/containerd-shim-spin/node` image is gone (the project moved from
> the `spinkube` org to `spinframework`). You *can* add the shim to a stock kind cluster
> with the Runtime Class Manager (KWasm), but that's not the documented quickstart path
> today, so we take the supported, stable route: a pinned k3d shim image that ships the
> shim pre-wired.

> If you're staying on your Phase 05 `gateway-lab` cluster, its nodes likely lack the
> shim. SpinKube needs the shim on the node, so a dedicated cluster is the clean path
> here; you rejoin the main cluster mentally in Phase 09 (LKE node pools can carry the
> shim too).

## 2. cert-manager (operator dependency)

The spin-operator runs an admission webhook (an HTTP callback the apiserver invokes to
check or modify an object before it's stored — the `SpinApp`, here) and Kubernetes only
calls webhooks over TLS. cert-manager issues and rotates that cert.

```bash
helm repo add jetstack https://charts.jetstack.io && helm repo update
helm install cert-manager jetstack/cert-manager \
  --namespace cert-manager --create-namespace \
  --version v1.20.0 \
  --set crds.enabled=true
kubectl -n cert-manager rollout status deploy/cert-manager-webhook
```

**What to look for:** `rollout status` returns `successfully rolled out`. If it hangs,
the operator install in Step 3 will also fail — the webhook cert won't exist.

> The `--version` pin keeps cert-manager from floating to whatever's newest the day you
> run this (v1.20.0 is what the current SpinKube quickstart tests against). `crds.enabled=true`
> installs cert-manager's own CRDs as part of the chart — keep it.

cert-manager appeared in PLATFORM-TRACK.md's "supporting cast" ("TLS for your Gateways") —
here's where it earns its keep.

## 3. RuntimeClass + Spin Operator

```bash
# RuntimeClass + CRDs (org is spinframework, release is v0.6.1 — these move together)
kubectl apply -f https://github.com/spinframework/spin-operator/releases/download/v0.6.1/spin-operator.runtime-class.yaml
kubectl apply -f https://github.com/spinframework/spin-operator/releases/download/v0.6.1/spin-operator.crds.yaml

# The operator itself (chart version tracks the release: 0.6.1)
helm install spin-operator --namespace spin-operator --create-namespace \
  --version 0.6.1 \
  --wait \
  oci://ghcr.io/spinframework/charts/spin-operator

# The executor that ties SpinApps to the runtime class
kubectl apply -f https://github.com/spinframework/spin-operator/releases/download/v0.6.1/spin-operator.shim-executor.yaml
```

What each line installs, mapped to the mechanism:

- **`spin-operator.runtime-class.yaml`** → the `RuntimeClass wasmtime-spin-v2`. This is
  the entire switch, and it's only four load-bearing lines — don't apply it blind, read it
  (a reference copy lives at `manifests/runtime-class.yaml`):

  ```yaml
  apiVersion: node.k8s.io/v1   # RuntimeClass is a core k8s API, NOT a SpinKube CRD — the switch is stock Kubernetes
  kind: RuntimeClass
  metadata:
    name: wasmtime-spin-v2     # the name pods opt into via `runtimeClassName:` (lab-03's pods carry this)
  handler: spin                # ← THE WHOLE POINT: the containerd runtime name the kubelet passes over the CRI
  ```

  `handler: spin` is a *plain string*, and that's the gotcha: nothing here installs a
  runtime — it merely *names* one. It must match a runtime already registered in the
  node's containerd config (the shim from Step 1). Apply this onto a node without the shim
  and the object is created happily; the failure only shows up when a pod that uses it
  tries to start (that's the "Break it" section). Note `handler` is a *top-level* field,
  not under `spec` — a common copy-paste mistake.

- **`spin-operator.crds.yaml`** → teaches the cluster the nouns `SpinApp` and
  `SpinAppExecutor` (same idea as the Gateway API CRDs in Phase 05: nouns now, behavior
  when a controller watches them). This is a large generated CRD bundle — you apply it,
  you don't read it; the two `kind`s it registers are what matter.
- **`helm install spin-operator --wait`** → the controller that watches `SpinApp`s.
  `--wait` blocks until its pods are Ready, so a failure surfaces here, not later. The
  chart comes from an **OCI** registry (`oci://ghcr.io/...`) — note the `oci://` URL is the
  chart *location itself*, so there's no `helm repo add` step for it (unlike cert-manager).
- **`spin-operator.shim-executor.yaml`** → a `SpinAppExecutor` named
  `containerd-shim-spin`. This is the link between a `SpinApp` and `wasmtime-spin-v2` —
  and the field that does the linking is tiny:

  ```yaml
  apiVersion: core.spinkube.dev/v1alpha1   # a SpinKube CRD (registered by the crds.yaml above)
  kind: SpinAppExecutor
  metadata:
    name: containerd-shim-spin             # the name a SpinApp references in its `executor:` field (lab-03)
  spec:
    createDeployment: true                 # the operator generates a Deployment for each SpinApp using this executor
    deploymentConfig:
      runtimeClassName: wasmtime-spin-v2   # ← stamps THIS RuntimeClass onto every generated pod — closes the loop to the switch above
      installDefaultCACerts: true          # injects CA certs so the Wasm app can make outbound TLS calls (e.g. to vLLM in lab-03)
  ```

  The chain is now complete: a `SpinApp` names an `executor` → the `SpinAppExecutor` names
  a `runtimeClassName` → the `RuntimeClass` names a `handler` → containerd routes to the
  shim. The gotcha for lab-03: a `SpinApp` whose `executor:` doesn't match this name
  (`containerd-shim-spin`) gets no RuntimeClass stamped, so its pod silently lands on
  `runc` and fails exactly like the "Break it" probe below.

> The project moved from the `spinkube` GitHub org to `spinframework`, and the chart
> version moves in lockstep with the release tag — `spinframework/charts:0.4.0` does not
> exist, so you bump the org *and* the version together to `0.6.1`. The RuntimeClass name
> (`wasmtime-spin-v2`) and the CRDs (`SpinApp` / `SpinAppExecutor`) are unchanged.

## 4. Verify — read each piece, not just "it's green"

```bash
kubectl get runtimeclass wasmtime-spin-v2
kubectl -n spin-operator get pods
kubectl get crd | grep spin
```

**What to look for, and what each proves:**

- `kubectl get runtimeclass wasmtime-spin-v2` → confirms the *switch* exists. Add
  `-o jsonpath='{.handler}'` and you'll see `spin` — the containerd runtime name. That
  handler must match a runtime in the node's containerd config, which is precisely what
  the Step 1 image provides.
- operator pod `Running` → the *controller* that turns a `SpinApp` into a Deployment is
  live (lab-03 depends on it).
- the `spinapps` / `spinappexecutors` CRDs → the *nouns* are registered.

## Under the hood (MIT hat): what the RuntimeClass turns a pod into

Here is the whole machine. When a pod sets `runtimeClassName: wasmtime-spin-v2`, the
kubelet looks up that RuntimeClass, reads its `handler: spin`, and passes that handler to
containerd over the CRI. containerd maps the handler string to a registered runtime
(`io.containerd.spin.v1`, binary `containerd-shim-spin`) and hands the pod to **that
shim instead of `runc`**. The shim doesn't build a Linux container — it loads the pod's
OCI image (which is just your `.wasm`) into the **`wasmtime`** engine and runs it. Same
`spin up` you ran in lab-01, now driven by the kubelet:

```
                       default pod                 SpinApp pod
                            │                          │ runtimeClassName: wasmtime-spin-v2
        kubelet ── CRI ──► containerd ──┬── handler "" ─► runc  ──► Linux container
                                        │
                                        └── handler "spin" ─► containerd-shim-spin ─► wasmtime ─► .wasm
                                              ▲ added by the k3d image + RuntimeClass
```

The two facts that make this work, and where each comes from:

1. **The shim is on the node** (Step 1's k3d image). Without it, containerd has no
   runtime named for handler `spin`.
2. **The RuntimeClass selects it** (Step 3). `RuntimeClass` is a *stock Kubernetes*
   primitive — SpinKube didn't invent the switch, it just provides a shim to point it at
   and an operator (lab-03) that stamps `runtimeClassName` onto pods so you don't.

So "running Wasm on Kubernetes" is not a new scheduler or a new kubelet — it's the
existing kubelet → CRI → containerd path, *forked* at containerd to a second runtime.
Everything above containerd (scheduling, Services, the API) is the Phase 03 machinery you
already own.

## Break it, then read the error (Kelsey lens)

Prove the node capability is real by removing it. Create a plain cluster with **no**
shim, then try to schedule a SpinApp pod onto it:

```bash
k3d cluster create plain-lab          # default image — no containerd-shim-spin
kubectl apply -f https://github.com/spinframework/spin-operator/releases/download/v0.6.1/spin-operator.runtime-class.yaml
kubectl run wasm-probe --image=ttl.sh/hello-spin:1h \
  --overrides='{"spec":{"runtimeClassName":"wasmtime-spin-v2"}}'   # patch the bare pod to opt into the switch
kubectl describe pod wasm-probe
```

We apply *only* the RuntimeClass (the switch) — not the shim, not the operator — so the
node has the switch but nothing to switch *to*. `--overrides` merges that raw JSON into
the pod spec `kubectl run` generates; we do it by hand here because there's no operator on
this throwaway cluster to stamp `runtimeClassName` for us. That one field is what forces
the pod onto handler `spin`, which this node can't resolve.

**Read the events at the bottom of `describe`.** The RuntimeClass exists (the API
accepted the pod), so this isn't a scheduling or admission error. It fails at the
*node*, during container creation, with a containerd/CRI error about the **handler**:
something like `failed to create containerd task: ... unknown runtime "spin"` /
`RunPodSandbox failed ... no runtime for "spin" is configured`. That message is the
lesson: the switch (`RuntimeClass`) is set, but the thing it points to (the shim) isn't
installed on this node. **Wasm scheduling is a node capability, not just a controller.**
This is the exact symptom you'll diagnose if an LKE node pool is missing the shim in
Phase 09 — and it looks nothing like an `ImagePullBackOff`, because the image pulled
fine; the *runtime* is what's missing.

Clean up the throwaway cluster (keep `spin-lab`):

```bash
k3d cluster delete plain-lab
```

## Checkpoint — you can now explain…

- **What problem does SpinKube solve?** A stock node can only run `runc` containers;
  SpinKube adds a second containerd runtime (`wasmtime` via `containerd-shim-spin`) and a
  `RuntimeClass` switch so specific pods route to it.
- **What does the `RuntimeClass` actually do?** Its `handler: spin` is passed by the
  kubelet through the CRI to containerd, which selects the Spin shim instead of `runc`.
  The shim runs the pod's `.wasm` in `wasmtime`.
- **Why can't a stock kind node run a SpinApp?** Its containerd has no runtime
  registered for handler `spin` — the shim binary isn't there — so pod creation fails at
  the node even though the RuntimeClass and image are fine.

You can now:
- [ ] Trace a SpinApp pod from kubelet → CRI → containerd → shim → wasmtime.
- [ ] Explain the division of labor: node provides the shim, RuntimeClass selects it,
      operator stamps `runtimeClassName` onto pods.
- [ ] Read a "no runtime for spin" error and name exactly which piece is missing.

## Next

→ `lab-03-deploy-spinapp.md`: push your `.wasm` to a registry and create a `SpinApp`. The
operator turns it into a Deployment whose pods carry `runtimeClassName: wasmtime-spin-v2`
— the switch you just installed — and you'll point it at vLLM.

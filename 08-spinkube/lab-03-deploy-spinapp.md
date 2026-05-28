# Lab 03 — Deploy a SpinApp: the operator turns one CRD into a routed Deployment

**Goal:** push the lab-01 `.wasm` to a registry, declare a `SpinApp`, and watch the
spin-operator compile that one object into a Deployment whose pods carry
`runtimeClassName: wasmtime-spin-v2` — the switch from lab-02. Then make the app earn its
place on an AI platform: a millisecond shim that shapes prompts in front of vLLM. By the
end you can say what a `SpinApp` *becomes* and why the Wasm sandbox forces you to declare
outbound hosts.

**Time:** ~30 min · **Cost:** free (local k3d)

## The problem (why this exists)

You have a `.wasm` (lab-01) and a node that can run it (lab-02). But you don't want to
hand-write a Deployment, remember to set `runtimeClassName` on every pod template, attach
the right Service, and keep them in sync. That's the same boilerplate the `Deployment`
controller saved you from in Phase 03 — except now there's an extra, easy-to-forget field
(`runtimeClassName`) that, if you miss it, silently routes your pod to `runc` and breaks
everything. You want to declare *what* (this `.wasm`, this many replicas) and have a
controller produce the *how*.

## What it replaces, and the mental model

`SpinApp` is to a `.wasm` what a `Deployment` is to a container image — but it also
encodes the Wasm-specific wiring you'd otherwise get wrong by hand:

| | Container world (Phase 03) | Spin world |
|---|---|---|
| Declare a workload | `Deployment` | `SpinApp` |
| What runs it | the default runtime (`runc`) | `wasmtime` via the shim |
| Who sets the runtime | nobody — it's the default | the operator stamps `runtimeClassName: wasmtime-spin-v2` for you |
| Artifact | OCI image with layers | OCI image that is just your `.wasm` |

The point of the CRD is that you never type `runtimeClassName` — the operator reads the
`SpinAppExecutor` (`containerd-shim-spin`, from lab-02) and applies it. That's the whole
reason a `SpinApp` is safer than a hand-rolled Deployment for Wasm.

## 0. Prereqs

`spin-lab` from lab-02 is your active context, the spin-operator pod is `Running`, and
you have the `hello-spin/` project from lab-01. For Step 4's vLLM shim you need a vLLM
Service reachable at `vllm.default.svc.cluster.local:8000` (Phase 06) — Steps 1–3 stand
alone without it.

## 1. Push the Wasm to an OCI registry

Spin apps distribute as OCI artifacts — the same registry mechanics as containers, so the
cluster pulls them the same way it pulls any image:

```bash
cd hello-spin
spin registry push ttl.sh/hello-spin:1h     # ttl.sh = anonymous, expiring registry
```

`spin registry push` packages the `.wasm` (and `spin.toml`) as an OCI image and uploads
it. **What to look for:** a digest prints and the push succeeds. `ttl.sh` needs no login
and auto-expires after the tag's TTL — perfect for a lab. In production you'd push to
your own registry (or an Akamai Object Storage-fronted one, Phase 09). Note the tag
expires in an hour; if a later step `ImagePullBackOff`s, the artifact aged out — re-push.

## 2. Run it as a SpinApp — and watch what the operator generates

```bash
cd ..                                       # back to 08-spinkube/ (manifests/ lives here, not in hello-spin/)
kubectl apply -f manifests/spinapp.yaml
kubectl get spinapp
kubectl get pods -l core.spinkube.dev/app-name=hello-spin
```

You applied **one** object (`SpinApp/hello-spin`). The operator, watching for `SpinApp`s,
reacts by creating a **Deployment** (and Service) on your behalf. Confirm the chain
actually happened:

```bash
kubectl get deploy,svc -l core.spinkube.dev/app-name=hello-spin
```

**What to look for:** a Deployment and Service you never wrote, both labeled with the app
name. This is the controller pattern from Phase 03 — high-level object in, lower-level
objects out — applied to a new workload type.

Now verify the one field that makes it Wasm and not a normal container:

```bash
kubectl get pod -l core.spinkube.dev/app-name=hello-spin \
  -o jsonpath='{.items[0].spec.runtimeClassName}'; echo
```

**What to look for:** `wasmtime-spin-v2`. The operator stamped that onto the pod template
— *that* is what routes the pod through the shim to `wasmtime` (lab-02's mechanism)
instead of `runc`. You didn't type it; the `SpinAppExecutor` told the operator to.

## 3. Call it

```bash
kubectl port-forward svc/hello-spin 3000:80 &
curl -s http://localhost:3000/
kill %1 2>/dev/null
```

**What to look for:** the same response you got from `spin up` in lab-01 — but now it's a
WebAssembly module served as a first-class Kubernetes workload, scheduled next to your
vLLM and kagent pods, addressable by a Service DNS name (Phase 03 machinery, unchanged).
The request path is ordinary: Service ClusterIP → kube-proxy DNAT → pod. Only the
*runtime inside the pod* changed.

## 4. Make it a real platform piece — a prompt-shaping shim in front of vLLM

This is the role Wasm plays best: a per-request shim that's too small and too bursty to
deserve a container. Edit the lab-01 handler so it reads the incoming JSON, prepends a
house system prompt (or strips a banned field), then forwards to the vLLM Service at
`http://vllm.default.svc.cluster.local:8000/v1/chat/completions`. Rebuild, re-push under
a **fresh tag**, point `spinapp.yaml`'s `image:` at it, and re-apply.

> **Two `spin.toml` gates the handler depends on — easy to miss, both silent if wrong.
> Both come straight from lab-01's "the sandbox is shut by default" lesson:**
>
> 1. **Variable must be declared, not just supplied.** Any variable you pass via
>    `spinapp.yaml`'s `variables:` (e.g. `vllm_url`) must *also* be declared in the app's
>    `spin.toml` under `[variables]` and bound into the component
>    (`[component.<name>.variables]`). The SpinApp `variables` field only supplies a
>    *value* to a variable spin.toml already knows about — it does not create one. Supply
>    a value for a variable spin.toml never declared and Spin ignores it; the handler reads
>    an empty string and forwards nowhere.
> 2. **The vLLM host must be allow-listed.** It must appear in that component's
>    `allowed_outbound_hosts` in `spin.toml`
>    (e.g. `allowed_outbound_hosts = ["http://vllm.default.svc.cluster.local:8000"]`).
>    This is the lab-01 sandbox showing up in production: Spin denies outbound network by
>    default, so without this line the forward to vLLM is blocked *inside `wasmtime`* —
>    the YAML is valid, the pod is healthy, the call simply never leaves the module. The
>    failure is at the runtime layer, not the Kubernetes layer, which is why nothing in
>    `kubectl describe` will point at it.

```bash
# After editing the handler + spin build, push a FRESH tag — re-pushing the same
# :1h tag won't change the SpinApp's image reference, so the pod would keep running
# the OLD .wasm (the classic "I redeployed and nothing changed" trap):
spin registry push ttl.sh/hello-spin:1h-v2
# point image: in manifests/spinapp.yaml at ttl.sh/hello-spin:1h-v2, then re-apply:
kubectl apply -f manifests/spinapp.yaml
```

Now you have the full request-shaping story from the PLATFORM-TRACK diagram: a
millisecond Wasm shim in front of the model. Put it *behind* your Phase 06 gateway and
the layers stack exactly as drawn — gateway routes to the shim's Service, the shim shapes
the prompt, the shim calls vLLM.

## Break it, then read the error (Kelsey lens)

Two failures worth inducing, because they fail at *different layers* and teaching that
distinction is the point:

**(a) Wrong image — fails at Kubernetes.** Point `spinapp.yaml`'s `image` at a tag you
never pushed and re-apply:

```bash
kubectl describe pod -l core.spinkube.dev/app-name=hello-spin | tail -20
```

The pod sits `ImagePullBackOff` — identical to a missing container image. **The lesson:**
"the artifact isn't in the registry" looks the same whether the artifact is a `.wasm` or
a container. The familiar Phase 03 failures transfer unchanged; only the runtime did.

**(b) Missing `allowed_outbound_hosts` — fails inside the runtime.** If you did Step 4 but
forgot to allow-list the vLLM host, the pod is `Running`, the Service answers, `kubectl
describe` is clean — yet the forward to vLLM never happens. **The lesson:** this failure
is invisible to Kubernetes because it's the Wasm *sandbox* denying the socket, not the
kubelet failing the pod. You debug it in the app's logs / response, not in pod events.
That's the cost of the sandbox isolation you traded the kernel namespace for in lab-01.

## Checkpoint — you can now explain…

- **What is a `SpinApp` to a `.wasm`?** What a `Deployment` is to a container image — and
  it additionally guarantees the `runtimeClassName: wasmtime-spin-v2` field you'd
  otherwise have to remember by hand.
- **What does the operator turn a `SpinApp` into?** A Deployment + Service; the
  Deployment's pod template carries `runtimeClassName: wasmtime-spin-v2`, which routes
  pods through the shim to `wasmtime`. The request path above the pod is plain Phase 03
  Service → kube-proxy → pod.
- **Why does the vLLM forward need `allowed_outbound_hosts`?** The Wasm sandbox denies
  outbound network by default (lab-01); the allow-list is the host granting that one
  capability. Forget it and the failure hides below Kubernetes' view.

You can now:
- [ ] Trace `SpinApp` → operator → Deployment(+Service) → pod with `runtimeClassName`.
- [ ] Distinguish a registry failure (`ImagePullBackOff`, visible to k8s) from a sandbox
      failure (silent, inside `wasmtime`).
- [ ] Place the shim in the platform: gateway → Wasm shim → vLLM.

## What you proved in Phase 08

You built a Wasm module (lab-01), taught a cluster to schedule it via a `RuntimeClass` +
shim + operator (lab-02), ran it as a `SpinApp` that the operator compiled into a routed
Deployment, and used it as a real prompt-shaping shim in front of vLLM — a workload type
that costs a fraction of a container for glue work, with the failures split cleanly
between "Kubernetes can see it" and "the sandbox swallowed it."

## Next

→ **Phase 09**: take everything — gateway, AI gateway, vLLM, kagent, Spin — to a real
Akamai LKE cluster with NodeBalancers, Block Storage, and GPUs. The lab-02 shim that was
baked into a k3d image becomes a node-pool capability you provision yourself.

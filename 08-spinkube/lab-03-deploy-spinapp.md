# Lab 03: Deploy a SpinApp, where the operator turns one CRD into a routed Deployment

**Goal:** push the lab-01 `.wasm` to a registry, declare a `SpinApp`, and watch the
spin-operator compile that one object into a Deployment whose pods carry
`runtimeClassName: wasmtime-spin-v2`, the switch from lab-02. Then make the app earn its
place on an AI platform: a millisecond shim that shapes prompts in front of vLLM. By the
end you can say what a `SpinApp` *becomes* and why the Wasm sandbox forces you to declare
outbound hosts.

**Time:** ~30 min · **Cost:** free (local k3d)

## The problem (why this exists)

You have a `.wasm` (lab-01) and a node that can run it (lab-02). But you don't want to
hand-write a Deployment, remember to set `runtimeClassName` on every pod template, attach
the right Service, and keep them in sync. That's the same boilerplate the `Deployment`
controller saved you from in Phase 03, except now there's an extra, easy-to-forget field
(`runtimeClassName`) that, if you miss it, silently routes your pod to `runc` and breaks
everything. You want to declare *what* (this `.wasm`, this many replicas) and have a
controller produce the *how*.

## What it replaces, and the mental model

`SpinApp` is to a `.wasm` what a `Deployment` is to a container image, and it also
encodes the Wasm-specific wiring you'd otherwise get wrong by hand:

| | Container world (Phase 03) | Spin world |
|---|---|---|
| Declare a workload | `Deployment` | `SpinApp` |
| What runs it | the default runtime (`runc`) | `wasmtime` via the shim |
| Who sets the runtime | nobody; it's the default | the operator stamps `runtimeClassName: wasmtime-spin-v2` for you |
| Artifact | OCI image with layers | OCI image that is just your `.wasm` |

The point of the CRD is that you never type `runtimeClassName`: the operator reads the
`SpinAppExecutor` (`containerd-shim-spin`, from lab-02) and applies it. That's the whole
reason a `SpinApp` is safer than a hand-rolled Deployment for Wasm.

## 0. Prereqs

`spin-lab` from lab-02 is your active context, the spin-operator pod is `Running`, and
you have the `hello-spin/` project from lab-01. Steps 1–3 stand alone. Step 4 is
illustrative on `spin-lab`: it forwards to a vLLM Service at
`vllm.default.svc.cluster.local:8000`, but that vLLM lives on your *Phase 06* cluster,
not on `spin-lab`, so don't expect to `curl` a working model here without redeploying
vLLM onto this cluster (Step 4 explains).

## 1. Push the Wasm to an OCI registry

Spin apps distribute as OCI artifacts, the same registry mechanics as containers, so the
cluster pulls them the same way it pulls any image:

```bash
cd hello-spin
spin registry push ttl.sh/hello-spin:1h     # ttl.sh = anonymous, expiring registry
```

`spin registry push` packages the `.wasm` (and `spin.toml`) as an OCI image and uploads
it. **What to look for:** a digest prints and the push succeeds. `ttl.sh` needs no login
and auto-expires after the tag's TTL, which suits a lab. In production you'd push to
your own registry (or an Akamai Object Storage-fronted one, Phase 09). Note the tag
expires in an hour; if a later step `ImagePullBackOff`s, the artifact aged out, so re-push.

## 2. Run it as a SpinApp and watch what the operator generates

Here is the whole object you're about to apply (`manifests/spinapp.yaml`), short because
the operator fills in everything you'd otherwise hand-write:

```yaml
apiVersion: core.spinkube.dev/v1alpha1   # the SpinKube CRD group (NOT apps/v1 - this is a custom type)
kind: SpinApp                            # the operator watches for this kind and reconciles it
metadata:
  name: hello-spin                       # becomes the Deployment/Service name AND the app-name label below
  namespace: default
spec:
  image: "ttl.sh/hello-spin:1h"          # the OCI artifact from Step 1 - the .wasm, not a container image
  executor: containerd-shim-spin         # names the SpinAppExecutor (lab-02); THIS is what makes the
                                         #   operator stamp runtimeClassName: wasmtime-spin-v2 on the pod
  replicas: 1                            # desired pods - same meaning as a Deployment's replicas
```

The two load-bearing fields, and the beginner traps in each:

- **`executor: containerd-shim-spin`** is the whole point. It references the `SpinAppExecutor`
  you installed in lab-02; the operator reads it to learn *which* `runtimeClassName` to put on
  the pod (`wasmtime-spin-v2`). Name an executor that doesn't exist and the operator can't
  resolve a runtime class: the pod never gets the shim and your Wasm won't run. You never type
  `runtimeClassName` yourself; this field is how the operator derives it.
- **`image:` points at an OCI artifact that is your `.wasm`** (Step 1 pushed it), not a
  layered container image. The classic trap: re-push under the same `:1h` tag and re-apply, and
  nothing changes, because the image *reference* in this manifest is identical, so the pod keeps the
  old artifact. To roll out new code you must bump the tag (Step 4) and edit `image:` here.

> The manifest also carries a commented-out `variables:` block and notes, the Step 4
> wiring (passing `vllm_url` to a prompt-shaping handler). Leave it commented for Steps 2–3;
> Step 4 explains why supplying a value there isn't enough on its own.

Apply it and watch one object fan out into several:

```bash
cd ..                                       # back to 08-spinkube/ (manifests/ lives here, not in hello-spin/)
kubectl apply -f manifests/spinapp.yaml     # send the SpinApp to the apiserver; the operator does the rest
kubectl get spinapp
kubectl get pods -l core.spinkube.dev/app-name=hello-spin
```

You applied **one** object (`SpinApp/hello-spin`). The operator, watching for `SpinApp`s,
reacts by creating a **Deployment** (and Service) on your behalf. The operator stamps
everything it generates with the label `core.spinkube.dev/app-name=<your SpinApp name>`,
the selector you'll use throughout this lab to find the Deployment/Service/pods it
created. (The API group is still `core.spinkube.dev` even though the GitHub org moved to
`spinframework` in lab-02: the org name changed, the API didn't.) Confirm the chain
happened:

```bash
kubectl get deploy,svc -l core.spinkube.dev/app-name=hello-spin
```

**What to look for:** a Deployment and Service you never wrote, both labeled with the app
name. This is the controller pattern from Phase 03 (high-level object in, lower-level
objects out) applied to a new workload type.

Now verify the one field that makes it Wasm and not a normal container:

```bash
kubectl get pod -l core.spinkube.dev/app-name=hello-spin \
  -o jsonpath='{.items[0].spec.runtimeClassName}'; echo
```

**What to look for:** `wasmtime-spin-v2`. The operator stamped that onto the pod template;
*that* is what routes the pod through the shim to `wasmtime` (lab-02's mechanism)
instead of `runc`. You didn't type it; the `SpinAppExecutor` told the operator to.

## 3. Call it

```bash
kubectl port-forward svc/hello-spin 3000:80 &   # tunnel localhost:3000 → the Service's port 80; & backgrounds it
curl -s http://localhost:3000/                  # hit the app through that tunnel
kill %1 2>/dev/null                             # close the tunnel (%1 = the backgrounded port-forward job)
```

`port-forward svc/hello-spin 3000:80` maps a local port to the **Service** (the operator made
it), so the request walks the same Service ClusterIP → kube-proxy → pod path as any workload,
with no special Wasm routing. The `&` runs it in the background so the same shell can `curl`; `kill
%1` tears it down after.

**What to look for:** the same response you got from `spin up` in lab-01, but now it's a
WebAssembly module served as an ordinary Kubernetes workload, scheduled next to your
vLLM and kagent pods, addressable by a Service DNS name (Phase 03 machinery, unchanged).
The request path is ordinary: Service ClusterIP → kube-proxy DNAT → pod. Only the
*runtime inside the pod* changed.

## 4. Make it a real platform piece: a prompt-shaping shim in front of vLLM

> **Illustrative on `spin-lab`, and it requires writing handler code.** Your vLLM lives on
> the *Phase 06* cluster, not this dedicated `spin-lab` k3d cluster, so the forward below
> won't reach a model here unless you redeploy vLLM onto `spin-lab` first. Read
> this for the pattern; the layers stack (gateway → shim → vLLM, same cluster) in
> Phase 09 on LKE. This step also needs you to write a JSON-parsing, prompt-injecting
> handler in your chosen SDK. There's no starter snippet, so treat it as "wire it up in
> your language," not copy-paste.

This is the role Wasm plays best: a per-request shim that's too small and too bursty to
deserve a container. Five beats, each silent if you skip it:

1. **Edit the lab-01 handler** so it reads the incoming JSON, prepends a house system
   prompt (or strips a banned field), then forwards to the vLLM Service at
   `http://vllm.default.svc.cluster.local:8000/v1/chat/completions`.
2. **Declare the variable** in `spin.toml` (the gate below, point 1).
3. **Allow-list the vLLM host** in `spin.toml` (the gate below, point 2).
4. **`spin build`, then re-push under a fresh tag** (the snippet below; a same-tag push
   won't change anything).
5. **Point `spinapp.yaml`'s `image:` at the fresh tag and re-apply.**

> **Two `spin.toml` gates the handler depends on, easy to miss and both silent if wrong.
> Both come straight from lab-01's "the sandbox is shut by default" lesson:**
>
> 1. **Variable must be declared, not just supplied.** Any variable you pass via
>    `spinapp.yaml`'s `variables:` (e.g. `vllm_url`) must *also* be declared in the app's
>    `spin.toml` under `[variables]` and bound into the component
>    (`[component.<name>.variables]`). The SpinApp `variables` field supplies a
>    *value* to a variable spin.toml already knows about; it does not create one. Supply
>    a value for a variable spin.toml never declared and Spin ignores it; the handler reads
>    an empty string and forwards nowhere.
> 2. **The vLLM host must be allow-listed.** It must appear in that component's
>    `allowed_outbound_hosts` in `spin.toml`
>    (e.g. `allowed_outbound_hosts = ["http://vllm.default.svc.cluster.local:8000"]`).
>    This is the lab-01 sandbox showing up in production: Spin denies outbound network by
>    default, so without this line the forward to vLLM is blocked *inside `wasmtime`*.
>    The YAML is valid, the pod is healthy, and the call never leaves the module. The
>    failure is at the runtime layer, not the Kubernetes layer, which is why nothing in
>    `kubectl describe` will point at it.

```bash
# After editing the handler + spin build, push a FRESH tag - re-pushing the same
# :1h tag won't change the SpinApp's image reference, so the pod would keep running
# the OLD .wasm (the classic "I redeployed and nothing changed" trap):
spin registry push ttl.sh/hello-spin:1h-v2
# point image: in manifests/spinapp.yaml at ttl.sh/hello-spin:1h-v2, then re-apply:
kubectl apply -f manifests/spinapp.yaml
```

Now you have the full request-shaping story from the PLATFORM-TRACK diagram: a
millisecond Wasm shim in front of the model. Put it *behind* your Phase 06 gateway and
the layers stack exactly as drawn: gateway routes to the shim's Service, the shim shapes
the prompt, the shim calls vLLM.

## Break it, then read the error

Two failures worth inducing, because they fail at *different layers* and teaching that
distinction is the point:

**(a) Wrong image, fails at Kubernetes.** Point `spinapp.yaml`'s `image` at a tag you
never pushed and re-apply:

```bash
kubectl describe pod -l core.spinkube.dev/app-name=hello-spin | tail -20
```

The pod sits `ImagePullBackOff`, identical to a missing container image. **The lesson:**
"the artifact isn't in the registry" looks the same whether the artifact is a `.wasm` or
a container. The familiar Phase 03 failures transfer unchanged; only the runtime did.

**(b) Missing `allowed_outbound_hosts`, fails inside the runtime.** If you did Step 4 but
forgot to allow-list the vLLM host, the pod is `Running`, the Service answers, and `kubectl
describe` is clean, yet the forward to vLLM never happens. **The lesson:** this failure
is invisible to Kubernetes because it's the Wasm *sandbox* denying the socket, not the
kubelet failing the pod. You debug it in the app's logs / response, not in pod events.
That's the cost of the sandbox isolation you traded the kernel namespace for in lab-01.

## Checkpoint: you can now explain…

- **What is a `SpinApp` to a `.wasm`?** What a `Deployment` is to a container image, and
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

## What you proved so far

You built a Wasm module (lab-01), taught a cluster to schedule it via a `RuntimeClass` +
shim + operator (lab-02), ran it as a `SpinApp` that the operator compiled into a routed
Deployment, and used it as a real prompt-shaping shim in front of vLLM: a workload type
that costs a fraction of a container for glue work, with the failures split cleanly
between "Kubernetes can see it" and "the sandbox swallowed it."

## Next

→ `lab-04-akamai-functions.md`: you just ran this `.wasm` *yourself* on SpinKube; now let
**Akamai run the same module for you**, serverless, with one `spin aka deploy`. Then lab-05
turns a function into a RAG agent. (Phase 09 takes the self-managed half, SpinKube plus your
gateway and vLLM, onto real Akamai LKE.)

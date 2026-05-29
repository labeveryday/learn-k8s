# Lab 03 — Probes, Resources, Autoscaling

**What you'll build:** the *operational* layer that turns "a Pod that runs an LLM" into "a
service that stays up." You'll add three **health probes** so Kubernetes knows when the model
is loading vs. ready vs. wedged, set **requests/limits** so the scheduler places a big Pod
correctly, attach a CPU-based **HorizontalPodAutoscaler (HPA)** to the `ollama` Deployment from
Lab 02, then **load-test** it and watch the replica count move on its own. The point isn't the
autoscaler number going up — it's the *mechanism*: every production AI workload needs the same
probe/resource/HPA hygiene, and an LLM server is just a (big) HTTP Pod that earns no exemption.

> **The one idea (Kelsey):** *"An LLM server is just a Pod. A big Pod, but a Pod."* The only
> thing the AI label changes is the *numbers* — startup is measured in minutes, memory in GiB.
> The controls (probes, limits, HPA) are identical to any web service. This lab is those
> controls, tuned for slow-starting, memory-hungry workloads.

## 1. Probes for LLM servers

A probe is a periodic health check the **kubelet** runs against your container. There are three,
and they answer three different questions:

- **`startupProbe`** — "has it finished booting yet?" While this is failing, the *other two
  probes are suspended.* This is the one that matters for LLMs.
- **`readinessProbe`** — "should it receive traffic right now?" Failing → the Pod is pulled from
  its Service's endpoints (no traffic), but **not** killed.
- **`livenessProbe`** — "is it wedged and need a restart?" Failing past its threshold → the
  kubelet **kills and restarts** the container.

Gotcha: LLM startup is *slow* — loading model weights can take minutes. If the `livenessProbe`
starts checking too early, the kubelet decides the Pod is dead *while it's still loading*, kills
it, and you get a crash loop that never serves a single request. The `startupProbe` exists
precisely to buy that warmup time: until it passes, liveness can't fire.

Here is the pattern, as the kubelet sees it — a generous startup window gating a fast liveness
check:

```yaml
startupProbe:
  httpGet: { path: /v1/models, port: 8000 }
  failureThreshold: 60         # 60 * 10s = 10 min tolerance
  periodSeconds: 10
readinessProbe:
  httpGet: { path: /v1/models, port: 8000 }
  periodSeconds: 10
livenessProbe:
  httpGet: { path: /v1/models, port: 8000 }
  periodSeconds: 30
```

- `httpGet { path, port }` — the kubelet does an HTTP GET; any `2xx`/`3xx` is "healthy." For
  vLLM, `/v1/models` is a cheap endpoint that only answers once the engine is up, so it's a good
  proxy for "actually ready," not just "process started."
- `failureThreshold: 60` × `periodSeconds: 10` = the kubelet will tolerate **10 minutes** of a
  failing startup probe before giving up. That's the model-load budget. Liveness stays dormant
  the whole time.
- Liveness polls *slower* (`periodSeconds: 30`) than readiness (`10`) on purpose: you want to
  notice "stop sending traffic" fast, but "kill and restart" should be reluctant — a 30s poll
  avoids restarting a Pod that's just briefly busy.

**Pattern to internalize: startup gates liveness; readiness gates traffic.** Get the startup
budget wrong and a healthy-but-slow model looks dead.

> **The probes in the real manifest differ — and that's the lesson.** The vLLM manifest
> (`manifests/vllm.yaml`) is even more generous (`failureThreshold: 120` → 20 min), and the
> `ollama` Deployment you'll autoscale below probes a *different* endpoint/port because ollama's
> API isn't OpenAI-shaped. From `manifests/ollama.yaml`:
>
> ```yaml
> startupProbe:
>   httpGet: { path: /api/tags, port: 11434 }   # ollama's "list models" endpoint, not /v1/models
>   failureThreshold: 60
>   periodSeconds: 5                              # 60 * 5s = 5 min (ollama boots faster than vLLM)
> readinessProbe:
>   httpGet: { path: /api/tags, port: 11434 }
>   periodSeconds: 10
> livenessProbe:
>   httpGet: { path: /api/tags, port: 11434 }
>   periodSeconds: 30
> ```
>
> Same three-probe shape, different probe path/port/budget per server. The takeaway: probe the
> endpoint that *actually* proves your server is up, and size the startup budget to *your*
> worst-case load.

## 2. Resources

LLM Pods are big. The two resource fields decide *where* the Pod lands and *how hard* it's
capped:

- **`requests`** — what the **scheduler** reserves to place the Pod ("I need at least this").
  Set too low and the scheduler overcommits the node; the kernel then fights over RAM and your
  Pod gets OOM-killed under load.
- **`limits`** — the hard ceiling the **kernel cgroup** enforces at runtime. Exceed the memory
  limit → **OOM-kill**. Exceed the CPU limit → **throttling** (slowed, not killed).

This block is from the real vLLM Deployment (`manifests/vllm.yaml`):

```yaml
resources:
  requests:
    cpu: "2"              # reserve 2 vCPU to even schedule this Pod
    memory: "4Gi"         # reserve 4 GiB — below this the model won't load
  limits:
    cpu: "4"              # may burst to 4 vCPU, then CPU throttling kicks in
    memory: "8Gi"         # exceed 8 GiB and the container is OOM-killed
```

- CPU `"2"` = two whole vCPU (`"2"` is the same as `2000m`). `requests = ~average` you actually
  need; `limits = ~peak` you'll tolerate.
- Memory is the dangerous one: an LLM that requests `4Gi` but spikes past the `8Gi` limit during
  a long-context request is killed mid-flight. Size the limit to your worst-case context length,
  not your idle footprint.

On real GPU clusters you additionally request `nvidia.com/gpu: 1` — a *limit-only* resource
(see Lab 04). CPU/memory still apply; the GPU is just an extra schedulable resource on top.

## 3. HPA (CPU-based for now)

An **HPA** is a controller that watches a metric and edits a Deployment's `replicas` for you —
the same `replicas` field you set by hand in Phase 3, now moved automatically. Attach one to the
`ollama` Deployment:

```bash
kubectl -n llm autoscale deploy/ollama --min=1 --max=3 --cpu-percent=70
kubectl -n llm get hpa -w
```

- `autoscale deploy/ollama` creates an HPA targeting that Deployment (it imperatively generates
  the HPA object — equivalent to writing an `autoscaling/v2` manifest).
- `--min=1 --max=3` — the floor and ceiling. The HPA will never scale below 1 or above 3 Pods.
- `--cpu-percent=70` — the **target**: average CPU across Pods, *as a percent of the Pod's CPU
  `request`*. This is why section 2's `requests` matter for autoscaling, not just scheduling —
  "70%" is meaningless without a request to measure against. (No request → the HPA can't compute
  utilization and reports `<unknown>`.)
- `get hpa -w` — `-w` (watch) streams updates so you can see `TARGETS` and `REPLICAS` change live
  instead of re-running the command.

**What you should see:** a row like `ollama   Deployment/ollama   <current>%/70%   1   3   1`.
With no load, `TARGETS` sits low and `REPLICAS` stays at the `--min` of 1.

CPU is a *poor* proxy for LLM "load" — a model can be saturated (queue full) while CPU looks
modest, because the bottleneck is the GPU or memory bandwidth, not the CPU. You won't fix that
here, but for awareness, in production you'd scale on:

- **Custom metrics** (pending requests, queue depth, tokens/sec) via `prometheus-adapter` (an
  add-on that exposes Prometheus metrics to the HPA API) and an HPA whose `metrics:` type is
  `External` (a metric that lives outside K8s, not the built-in CPU/memory).
- **KEDA** — a separate add-on that scales on event sources (e.g., queue length), including down
  to zero replicas (which the CPU HPA's `--min=1` can't do).

Forward reference only; the lab below uses the CPU HPA you just created.

## 4. Load-test

This assumes the port-forward from Lab 02 Path B (`kubectl -n llm port-forward svc/ollama
11434:11434`) is still open in another terminal — otherwise these curls hit nothing (connection
refused).

Fire off concurrent requests and watch the HPA react:

```bash
# Simple ramp:
for i in $(seq 1 50); do
  curl -s http://localhost:11434/v1/chat/completions \
    -H 'Content-Type: application/json' \
    -d '{"model":"tinyllama","messages":[{"role":"user","content":"tell me a joke"}],"max_tokens":64}' &
done; wait

kubectl -n llm get hpa
kubectl -n llm top pods
```

- `for i in $(seq 1 50); do ... & done; wait` — launches 50 curls **in parallel** (`&`
  backgrounds each), then `wait` blocks until all 50 finish. Sequential requests wouldn't push
  CPU; concurrency is what creates the load spike the HPA needs to see.
- `--max_tokens: 64` keeps each generation short so the burst lands quickly rather than dribbling
  out over minutes.
- `kubectl top pods` reads live CPU/memory from the **metrics-server** — the same data source the
  HPA uses. If `top` errors with "metrics not available," metrics-server isn't installed, and the
  HPA won't scale either (it'll show `<unknown>`); that's a cluster add-on prerequisite, not a
  bug in your manifest.

**What you should see:** the HPA's `TARGETS` column climbing past `70%` and `REPLICAS` going
`1 -> 2 -> 3` over ~1–2 min. The HPA polls on a delay (it deliberately waits to avoid flapping),
so the scale-up lags the load — that's expected, not stuck. Caveat: if CPU never crosses 70%
(tinyllama is small; 50 short requests may not be enough), the HPA stays at 1 — **that's fine.**
The lesson is the mechanism (request → metric → replica change), not the specific number.

## 5. Production checklist (transfer these habits)

These are the habits that carry 1:1 to real GPU clusters — the K8s skills are the hard part; the
GPU bits (Lab 04) are a few extra fields.

- [ ] Startup probe covers worst-case model load. (Section 1 — the #1 cause of LLM crash loops.)
- [ ] Readiness gates traffic during warmup. (No requests routed until the model answers.)
- [ ] Requests + limits set (requests = ~avg; limits = ~peak). (Section 2 — and requests are what
      the HPA measures against.)
- [ ] Model weights on a PVC, not baked into the image (faster image, swap models easily — both
      manifests already mount a `PersistentVolumeClaim`).
- [ ] Graceful termination: `preStop` drains in-flight requests before SIGTERM (don't drop a
      half-generated response when a Pod is being replaced).
- [ ] Observability: request latency histogram, queue depth, GPU util (when applicable — vLLM
      emits these at `/metrics`; see Lab 04).
- [ ] Secrets out of env (HF tokens, API keys) — mount as files, not `env:` (env leaks into
      `describe`/logs).
- [ ] PodDisruptionBudget so node drains don't take you to zero replicas.

## Next

→ `lab-04-gpu-notes.md`: everything above is what's *portable*. Lab 04 is read-only — the handful
of fields that change when you finally have real GPUs (`nvidia.com/gpu` limits, taints/tolerations,
fast interconnects), so you know the shape before the hardware lands.

# Lab 03 — Probes, Resources, Autoscaling

## 1. Probes for LLM servers

Gotcha: startup is *slow* (model load can be minutes). If `livenessProbe` starts too early, the kubelet will kill the pod before it ever serves.

Use a generous `startupProbe`:

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

Pattern: startup gates liveness; readiness gates traffic.

## 2. Resources

LLM pods are big. Set requests accurately so the scheduler places them correctly.

```yaml
resources:
  requests:
    cpu: "2"
    memory: "4Gi"
  limits:
    cpu: "4"
    memory: "8Gi"
```

On real GPU clusters, you additionally request `nvidia.com/gpu: 1` (see Lab 04).

## 3. HPA (CPU-based for now)

```bash
kubectl -n llm autoscale deploy/ollama --min=1 --max=3 --cpu-percent=70
kubectl -n llm get hpa -w
```

CPU is a poor proxy for LLM "load." In production you'd scale on:

- **Custom metrics** (pending requests, queue depth, tokens/sec) via `prometheus-adapter` and a `HorizontalPodAutoscaler` with `metrics: External`.
- **KEDA** for event-driven scaling (e.g., queue length).

## 4. Load-test

Fire off concurrent requests and watch HPA react:

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

## 5. Production checklist (transfer these habits)

- [ ] Startup probe covers worst-case model load.
- [ ] Readiness gates traffic during warmup.
- [ ] Requests + limits set (requests = ~avg; limits = ~peak).
- [ ] Model weights on a PVC, not baked into the image (faster image, swap models easily).
- [ ] Graceful termination: preStop drains in-flight requests before SIGTERM.
- [ ] Observability: request latency histogram, queue depth, GPU util (when applicable).
- [ ] Secrets out of env (HF tokens, API keys) — mount as files.
- [ ] PodDisruptionBudget so node drains don't take you to zero replicas.

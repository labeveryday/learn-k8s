# Lab 08 — Probes, Lifecycle, PDBs

## 1. Three probes

| Probe | Fails → action | Use it for |
|-------|---------------|------------|
| `startupProbe` | kill + restart | slow-starting apps; disables liveness until started |
| `livenessProbe` | kill + restart | detect deadlocks/hangs |
| `readinessProbe` | remove from Service endpoints | "I'm alive but not ready to serve" |

Key distinction: **liveness kills, readiness gates traffic.**

Example:

```yaml
containers:
  - name: api
    image: myapp:0.1
    startupProbe:
      httpGet: { path: /healthz, port: 8000 }
      failureThreshold: 30
      periodSeconds: 2
    livenessProbe:
      httpGet: { path: /healthz, port: 8000 }
      periodSeconds: 10
      timeoutSeconds: 2
    readinessProbe:
      httpGet: { path: /ready, port: 8000 }
      periodSeconds: 5
```

Probe types: `httpGet`, `tcpSocket`, `exec`, `grpc`.

## 2. Lifecycle hooks

```yaml
lifecycle:
  postStart:
    exec: { command: ["sh", "-c", "echo started"] }
  preStop:
    exec: { command: ["sh", "-c", "sleep 10"] }   # drain
```

`preStop` runs before SIGTERM. Use it to deregister from service registries / drain connections.

## 3. Graceful termination

1. kubelet removes Pod from Service endpoints.
2. kubelet runs `preStop`.
3. kubelet sends **SIGTERM**.
4. kubelet waits `terminationGracePeriodSeconds` (default 30).
5. kubelet sends **SIGKILL**.

Your app must handle SIGTERM. This is why Phase 1 signal lab mattered.

## 4. PodDisruptionBudget

Protects you from voluntary disruptions (node drain, upgrades):

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata: { name: web-pdb }
spec:
  minAvailable: 2
  selector: { matchLabels: { app: web } }
```

K8s won't voluntarily evict below the budget.

## 5. Resources redux

Set sane probes + resources together:

```yaml
resources:
  requests: { cpu: "100m", memory: "128Mi" }
  limits:   { cpu: "500m", memory: "256Mi" }
```

Without requests, the scheduler can overcommit your node and murder you later.

## 6. Practice

1. Add readiness to the FastAPI app. Break Redis. Observe pods become NotReady → removed from Service → re-added when Redis is back.
2. Set liveness that always fails. Watch the pod enter `CrashLoopBackOff`.
3. Add `preStop: sleep 15` and verify termination duration with `kubectl delete pod -v=4`.

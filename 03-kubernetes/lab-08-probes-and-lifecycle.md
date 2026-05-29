# Lab 08 — Probes, Lifecycle, PDBs

**What you'll build:** the health and shutdown contract a Pod signs with Kubernetes. You'll
formalize the three probes (startup, liveness, readiness) — the same readiness gate from lab-07
that decided whether your `web` Pods joined the Service's Endpoints — then add lifecycle hooks,
walk the exact graceful-termination sequence, and protect a Deployment from being drained below a
floor with a **PodDisruptionBudget**. The point isn't health checks for their own sake; it's that
**Kubernetes only knows what your container tells it.** A Pod is `Running` the instant its process
starts — that says nothing about whether it can serve. Probes are how you close that gap.

> **The one idea (Stanford):** `Running` is a *process* fact; `Ready` is a *traffic* fact. The
> kubelet can't read your app's mind, so you hand it a probe — a tiny check it runs on a loop — and
> it acts on the result. Liveness failures get the Pod **killed**; readiness failures get it
> **pulled from the Service**. Same loop, two very different blast radii.

## 1. Three probes

| Probe | Fails → action | Use it for |
|-------|---------------|------------|
| `startupProbe` | kill + restart | slow-starting apps; disables liveness until started |
| `livenessProbe` | kill + restart | detect deadlocks/hangs |
| `readinessProbe` | remove from Service endpoints | "I'm alive but not ready to serve" |

Key distinction: **liveness kills, readiness gates traffic.** Get them backwards and you build
outage amplifiers: a liveness probe that checks a dependency (Redis, a DB) will *restart your
whole app* the moment that dependency hiccups — turning one slow backend into a crash loop. The
probe that's allowed to depend on the outside world is **readiness**, because its only consequence
is "stop sending me traffic until I recover."

The readiness gate isn't new — you already shipped one in lab-07. Here is the real probe on the
`web` Deployment (`manifests/deploy-web.yaml`), the one that decided Endpoints membership:

```yaml
          readinessProbe:
            httpGet: { path: /, port: 80 }   # GET http://<pod-ip>:80/ — 2xx/3xx = ready
            periodSeconds: 5                  # re-check every 5s; in/out of Endpoints follows this
```

- `httpGet` makes the kubelet issue an HTTP GET *to the Pod's own IP* on the named port. Any
  `2xx`/`3xx` status passes; anything else (or a connection refusal) fails. Endpoints membership
  tracks the result — fail and the Service stops routing to this Pod within ~`periodSeconds`.
- No `path` host is set because the kubelet always targets the Pod IP, not a Service or DNS name.

Now the fuller, illustrative shape with all three probes. (`myapp:0.1`/`/ready` are stand-ins;
apply real probes against the capstone FastAPI app below, which serves `/healthz`.)

```yaml
containers:
  - name: api
    image: myapp:0.1
    startupProbe:
      httpGet: { path: /healthz, port: 8000 }
      failureThreshold: 30        # allow 30 failures before giving up...
      periodSeconds: 2            # ...×2s = up to 60s for a slow boot, then kill
    livenessProbe:
      httpGet: { path: /healthz, port: 8000 }
      periodSeconds: 10           # liveness checks DON'T start until startupProbe first passes
      timeoutSeconds: 2           # a probe taking >2s counts as a failure
    readinessProbe:
      httpGet: { path: /ready, port: 8000 }
      periodSeconds: 5
```

Two gotchas this hides:

- **`startupProbe` is the slow-boot escape hatch.** While it's still failing, liveness and
  readiness are *suspended* — so a 45-second JVM/model load won't get killed by an impatient
  liveness probe. `failureThreshold × periodSeconds` is your total startup budget; size it to your
  worst cold start. Without a startup probe you'd instead crank `initialDelaySeconds` on liveness
  and guess.
- **Liveness and readiness usually want *different* endpoints.** Liveness = "is the process
  wedged?" (cheap, no dependencies). Readiness = "can I serve a real request right now?" (may check
  Redis/DB). Pointing both at the same heavy `/ready` is the classic mistake that makes a
  dependency outage trigger restarts.

Probe types: `httpGet`, `tcpSocket`, `exec`, `grpc`. (`exec` runs a command *inside* the
container and treats exit 0 as pass — that's how the capstone's Redis is probed, next.)

## 2. The capstone probes — the real thing

The illustrative block above is a teaching shape. Here is what's actually deployed in
`manifests/fastapi-redis.yaml` — the Phase 2 Compose app re-expressed in K8s — with both
containers' real probes:

```yaml
        - name: redis
          image: redis:7-alpine
          readinessProbe:
            exec: { command: ["redis-cli", "ping"] }   # exit 0 (PONG) = ready
          livenessProbe:
            exec: { command: ["redis-cli", "ping"] }   # in-process check, no network dependency
```

```yaml
        - name: api
          image: learn-k8s/api:0.1
          readinessProbe:
            httpGet: { path: /healthz, port: 8000 }
            periodSeconds: 5
          livenessProbe:
            httpGet: { path: /healthz, port: 8000 }
            periodSeconds: 10
            initialDelaySeconds: 10   # don't probe liveness for the first 10s (boot grace)
```

- **`exec` probe** runs `redis-cli ping` *inside* the redis container; a `PONG` exits 0 and passes.
  No HTTP server needed — `exec` is the right tool for processes that don't speak HTTP.
- The `api` here uses `/healthz` for *both* liveness and readiness (a deliberate simplification for
  the capstone — section 1's warning is what you'd fix at scale). `initialDelaySeconds: 10` is the
  no-startup-probe alternative: liveness simply waits 10s before its first check.

> **Gotcha:** `image: learn-k8s/api:0.1` with `imagePullPolicy: IfNotPresent` means the cluster
> uses your *locally built* image — it never hits a registry. If the Pod is stuck in
> `ErrImageNeverPull`/`ImagePullBackOff`, you skipped the prereq in the manifest header:
> `docker build -t learn-k8s/api:0.1 .` then `kind load docker-image learn-k8s/api:0.1 --name learn`.

## 3. Lifecycle hooks

```yaml
lifecycle:
  postStart:
    exec: { command: ["sh", "-c", "echo started"] }   # runs right after container start
  preStop:
    exec: { command: ["sh", "-c", "sleep 10"] }        # drain — runs BEFORE SIGTERM
```

`preStop` runs before SIGTERM. Use it to deregister from service registries / drain connections.

Two gotchas:

- **`preStop` eats into your grace period.** A `preStop` that takes 10s leaves only ~20s of the
  default 30s `terminationGracePeriodSeconds` for the app to finish — they share the same clock,
  they don't add (section 4).
- **`postStart` runs concurrently with the container's entrypoint and has no ordering guarantee** —
  it may run before your app's main process is up. Don't use it for "wait until ready" logic;
  that's what probes are for.

## 4. Graceful termination

1. kubelet removes Pod from Service endpoints.
2. kubelet runs `preStop`.
3. kubelet sends **SIGTERM**.
4. kubelet waits `terminationGracePeriodSeconds` (default 30).
5. kubelet sends **SIGKILL**.

The order matters: step 1 happens *first*, so the Pod stops receiving new traffic the instant
deletion begins — before your app even gets the SIGTERM. That's the readiness gate working in
reverse, and it's why a `preStop: sleep` buys in-flight requests time to finish.

Your app must handle SIGTERM. This is why Phase 1 signal lab mattered: if your app ignores SIGTERM,
deletes take the full `terminationGracePeriodSeconds` (30s) and then a hard kill — the practice #3
`-v=4` trick lets you watch that wait.

## 5. PodDisruptionBudget

Protects you from **voluntary** disruptions (node drain, cluster upgrades) — *not* from crashes or
the things in section 4. The distinction is the whole point: a PDB caps how many Pods the *control
plane* is allowed to evict on purpose at once. It does nothing about a node that dies on its own.

```yaml
apiVersion: policy/v1                              # PDBs live in the policy/v1 API group
kind: PodDisruptionBudget
metadata: { name: web-pdb }
spec:
  minAvailable: 2                                  # keep ≥2 of the selected Pods Ready, always
  selector: { matchLabels: { app: web } }          # targets the lab-03/07 `web` Deployment's Pods
```

K8s won't voluntarily evict below the budget.

Gotcha: **`minAvailable` counts *Ready* Pods, not running ones** — so it's wired straight to your
readiness probe (section 1). If `minAvailable: 2` but only 1 Pod is Ready, a `kubectl drain`
**blocks** rather than evicting the last healthy one. Set `minAvailable` below your replica count
(here `web` has 3, so `2` permits draining one node at a time); set it *equal* to replicas and no
drain can ever proceed.

## 6. Resources redux

Set sane probes + resources together — a probe tells K8s *whether* a Pod is healthy; resources
tell the scheduler *where* it fits and the kernel *how hard* to cap it (lab-03, section 7).

```yaml
resources:
  requests: { cpu: "100m", memory: "128Mi" }   # scheduler reserves this to place the Pod
  limits:   { cpu: "500m", memory: "256Mi" }   # cgroup cap: mem over → OOM-kill, cpu over → throttle
```

Without requests, the scheduler can overcommit your node and murder you later.

## 7. Practice

1. Add readiness to the FastAPI app. Break Redis. Observe pods become NotReady → removed from
   Service → re-added when Redis is back.

   ```bash
   kubectl scale deploy/cache -n demo --replicas=0   # "break" Redis by removing its Pod
   kubectl get pods -n demo -l app=api -w            # watch the api Pods flip READY 1/1 → 0/1
   kubectl get endpoints api -n demo                 # the api Service's Endpoints list shrinks/empties
   kubectl scale deploy/cache -n demo --replicas=1   # bring Redis back; readiness recovers
   ```

   **What you should see:** with Redis gone, the api readiness probe starts failing, the Pods go
   `0/1` READY (still `Running` — liveness didn't kill them), and they *drop out of the Service's
   Endpoints* so no traffic is routed to them. Restore Redis and within ~`periodSeconds` they go
   `1/1` and rejoin Endpoints. That's "liveness kills, readiness gates traffic" made visible.

2. Set liveness that always fails. Watch the pod enter `CrashLoopBackOff`.

   ```bash
   kubectl get pods -w                  # watch the RESTARTS column climb, status → CrashLoopBackOff
   kubectl describe pod <name>          # Events show "Liveness probe failed" + the Killing entry
   ```

   **What you should see:** a failing liveness probe makes the kubelet kill and restart the
   container on a loop. Restarts increment, and after a few quick failures the kubelet backs off
   (10s → 20s → … capped), which is the `CrashLoopBackOff` status — the runtime telling you "I keep
   restarting this and it keeps dying."

3. Add `preStop: sleep 15` and verify termination duration with `kubectl delete pod -v=4`.

   ```bash
   time kubectl delete pod <name> -v=4   # -v=4 prints the API round-trips; time it end-to-end
   ```

   **What you should see:** the delete takes ~15s+ — the `preStop` `sleep 15` runs before SIGTERM
   (section 4, steps 2→3), so the kubelet holds the terminating Pod for the full hook. `-v=4`'s
   verbose log shows the DELETE request and the polling while the Pod drains. Drop the `preStop`
   (or have the app ignore SIGTERM) and the wait stretches to the full 30s `terminationGracePeriodSeconds`
   before the SIGKILL instead.

## Next

→ `lab-09-rbac-and-security.md`: your Pods are now healthy and drain cleanly — but every one of
them runs as a ServiceAccount with *some* set of permissions. RBAC is how you decide what a
workload is allowed to ask the apiserver for, and Pod Security is how you stop a container from
escalating beyond it.

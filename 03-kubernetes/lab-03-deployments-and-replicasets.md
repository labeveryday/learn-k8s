# Lab 03 — Deployments, ReplicaSets, Rolling Updates

**What you'll build:** a `Deployment` running three nginx Pods — then watch Kubernetes
self-heal it when a Pod dies, roll out a new version with no downtime, roll that back, and
scale it up and down. The point isn't nginx; it's the **controller pattern** every workload
in this curriculum (vLLM, gateways, agents) is built on. By the end you can read a Deployment
manifest field-by-field and predict exactly what the controller will do with it.

> **The one idea (Stanford):** you never tell Kubernetes "start a Pod." You *declare desired
> state* ("I want 3 of these") and a **controller** loops forever making reality match. Every
> section below is that loop in action.

## 1. The hierarchy — the shape before the commands

One object you create fans out into three layers:

```
Deployment ──owns──► ReplicaSet ──owns──► Pod (×3 replicas)
   (you)              (rolling updates)     (your containers)
```

- A **Pod** is the atom: one or more containers sharing a network namespace + IP (lab-02).
- A **ReplicaSet** is a controller with one job: *keep exactly N Pods matching a selector
  alive.* Delete one, it makes another.
- A **Deployment** sits on top and manages ReplicaSets so it can do **rolling updates** —
  bring up a new ReplicaSet while draining the old one.

You almost always create Deployments; the ReplicaSet is created *for* you and you rarely
touch it directly. Watching all three appear from one `apply` is the first exercise.

## 2. The Deployment — what each piece does

A Deployment is your declarative request: *"keep 3 nginx Pods running, and roll updates with
no downtime."* You write the desired state; the controller makes reality match. Here is the
whole object (`manifests/deploy-web.yaml`), then the fields that matter:

```yaml
apiVersion: apps/v1          # Deployments live in the apps/v1 API group (Pods are core v1)
kind: Deployment
metadata:
  name: web                  # the Deployment's name; the ReplicaSet/Pods derive names from it
spec:
  replicas: 3                # DESIRED Pod count — the ReplicaSet keeps exactly this many alive
  selector:
    matchLabels:
      app: web               # which Pods this Deployment OWNS (it finds them by this label)
  template:                  # the Pod blueprint the ReplicaSet stamps out, replicas times
    metadata:
      labels:
        app: web             # MUST match the selector above — the #1 beginner trap (see below)
    spec:
      containers:
        - name: nginx
          image: nginx:1.27-alpine
          ports:
            - containerPort: 80
          resources:
            requests:        # what the SCHEDULER reserves to place the Pod ("need at least")
              cpu: 50m
              memory: 64Mi
            limits:          # the hard cap the kernel cgroup enforces (exceed mem → OOM-kill)
              cpu: 200m
              memory: 128Mi
```

Two fields beginners get wrong, and both fail *silently*:

- **`selector.matchLabels` must equal `template.metadata.labels`.** The Deployment finds the
  Pods it owns *by label*. If they don't match, the Deployment manages **zero** Pods and the
  apiserver rejects it (`selector does not match template labels`). This is the single most
  common Deployment error.
- **`replicas` is *desired*, not *current*.** You're not "starting 3 Pods" — you're telling
  the controller "3 is the truth." Delete one and it comes back (section 3). Set it to 0 and
  they all leave.

Apply it and watch the 1→3 hierarchy that one object created:

```bash
kubectl apply -f manifests/deploy-web.yaml   # create/update the cluster from desired state
kubectl get deploy,rs,pods -l app=web        # one Deployment → one ReplicaSet → three Pods
kubectl rollout status deploy/web            # blocks until all 3 Pods are Ready, then returns
```

- `apply -f` sends the manifest to the apiserver as the desired state (declarative — re-running
  it is safe and idempotent, unlike `create`).
- `-l app=web` filters by the label, so you see only this app's objects.
- `rollout status` is the "are we there yet?" command — it waits for the rollout to finish
  instead of you eyeballing `get pods` in a loop.

**What you should see:** one `deployment.apps/web`, one `replicaset.apps/web-<hash>`, and
three `pod/web-<hash>-<rand>`, all `Running`/`Ready`. That hierarchy is the controller pattern
made real: you declared one thing, controllers built three layers to satisfy it.

## 3. Self-healing — desired state in action

Kill a Pod and watch the gap close itself:

```bash
kubectl delete pod -l app=web --grace-period=0 --force   # yank one Pod instantly
kubectl get pods -l app=web                               # a replacement is already appearing
```

- `--grace-period=0 --force` skips the normal graceful-shutdown wait so the Pod dies *now* —
  we only do this to make the demo instant; lab-08 covers the graceful path these flags skip
  (and why you normally want it).
- You deleted one Pod, so current (2) < desired (3). The **ReplicaSet controller** sees the
  gap on its next loop and stamps out a replacement from the same `template`.

**What you should see:** briefly 2 Pods, then a new `web-...` Pod `ContainerCreating` → `Running`
within seconds. You didn't run a "restart" command — *desired state* did it. This is why "is
the Pod up?" is rarely the right question; "does desired match current?" is.

## 4. Rolling update — change the image with no downtime

Change the container image and watch the Deployment migrate traffic gradually:

```bash
kubectl set image deploy/web nginx=nginx:1.26-alpine   # patch the 'nginx' container's image
kubectl rollout status deploy/web                      # watch the new ReplicaSet come up
kubectl get rs -l app=web                              # now TWO ReplicaSets: old (0) + new (3)
kubectl rollout history deploy/web                     # the revision list you can roll back to
```

- `set image deploy/web nginx=...` edits the `template` of the Deployment. Changing the
  template is what *triggers* a rollout — the Deployment now needs Pods that don't exist yet.
- The Deployment's strategy (section 5) is to **create a new ReplicaSet** for the new template
  and scale it up while scaling the old one down — so there are always healthy Pods serving.
  That's why `get rs` now shows two: the old at `0` replicas (kept for rollback) and the new at `3`.
- `rollout history` records each template change as a numbered **revision**.

**What you should see:** `get rs` lists two ReplicaSets, old `DESIRED 0` / new `DESIRED 3`.
No request ever hit zero Pods — that's the "no downtime" the Deployment buys you over editing
Pods by hand. Undo it:

```bash
kubectl rollout undo deploy/web        # scale the previous ReplicaSet back up, new one down
```

`undo` doesn't delete anything — it re-scales the *old* ReplicaSet (still sitting at 0) back to
3 and the current one to 0. Rollback is instant because the old ReplicaSet was never thrown away.

## 5. Update strategies — how the rollout is paced

The rollout above was gradual because of the Deployment's default **strategy**. You can tune it:

```yaml
spec:
  strategy:
    type: RollingUpdate      # the default
    rollingUpdate:
      maxUnavailable: 25%    # during a rollout, at most 25% of desired Pods may be DOWN
      maxSurge: 25%          # ...and at most 25% EXTRA Pods may exist temporarily
```

- `maxUnavailable` protects availability: with 3 replicas and 25%, at most ~1 Pod is missing
  at a time, so you keep serving.
- `maxSurge` controls how fast you roll: it lets the new ReplicaSet add Pods *before* the old
  ones leave, trading a little extra capacity for a faster, safer rollout.
- The other type, `Recreate`, kills *all* old Pods then starts new ones — **downtime**, but the
  right choice for non-HA stateful apps that can't run two versions at once.

## 6. Scaling

```bash
kubectl scale deploy/web --replicas=5   # change DESIRED from 3 to 5
kubectl get pods -l app=web             # two new Pods appear
```

`scale` just edits `replicas`. The ReplicaSet sees current (3) < desired (5) and adds two —
the same self-healing loop from section 3, this time because *you* moved the target. (In a
later phase an **HPA** moves this number for you based on CPU/load.)

## 7. requests vs limits — what each does

These two fields decide *where* a Pod runs and *how hard* it's capped:

- **`requests`** — what the **scheduler** uses to place the Pod. "I need at least this much."
  The scheduler only puts the Pod on a node with that much free. Set too high → Pod won't
  schedule (`Pending`); too low → the node gets overcommitted.
- **`limits`** — what the **kernel cgroup** enforces at runtime. "You may not exceed this."

CPU is in **millicores**: `1000m` = 1 vCPU, so `50m` = 5% of a core. Memory is `Mi`/`Gi`
(mebibytes/gibibytes). The two limits behave very differently when hit:

- **Memory over limit → OOM-kill** (the container is killed and restarted).
- **CPU over limit → throttling** (the container is slowed, not killed).

**Rule of thumb (Kelsey):** *always* set requests (or the scheduler is guessing); *often* set
limits (to stop one Pod starving its neighbors).

## 8. Labels and selectors — the glue that wires Kubernetes together

Labels are arbitrary key/value tags on objects; **selectors** match them. This is how the
Deployment found its Pods (section 2), and how Services, NetworkPolicies, and more find theirs.

```bash
kubectl get pods --show-labels                       # see every Pod's labels
kubectl get pods -l app=web                           # equality selector
kubectl get pods -l 'app in (web,api),env!=prod'      # set-based selector
kubectl label pod <name> tier=frontend                # add a label to a live object
```

- `-l app=web` is the same selector syntax the Deployment's `matchLabels` uses.
- The set-based form (`in (...)`, `!=`) lets you slice across many apps/environments.

Internalize this: in Kubernetes, things don't reference each other by name as much as by
**label match**. A Service doesn't list Pod IPs — it selects `app: web` and the system keeps
the membership current. That indirection is why a Pod can die and be replaced without anything
upstream needing to change.

## 9. Practice

1. Scale `web` to 10. Watch Pods spawn (`kubectl get pods -l app=web -w`). Scale back to 2.
   Watch them terminate. (You're moving *desired*; the controller does the rest.)
2. Update the image to a nonexistent tag (`nginx:does-not-exist`). What happens, and *where*
   does it show up? (Hint: `kubectl get pods` shows `ImagePullBackOff`; `kubectl describe pod`
   shows the why in its Events.)
3. Roll back to the prior revision (`kubectl rollout undo deploy/web`).
4. Add `resources.limits.memory: 8Mi` (absurdly low) and re-apply. Watch the Pod's fate
   (`describe` shows the OOM-kill) — section 7's "memory over limit → OOM-kill," live.

## Next

→ `lab-04-services-and-networking.md`: your 3 Pods have churning IPs and no stable address.
A **Service** gives them one — and you'll trace exactly how a request reaches a Pod.

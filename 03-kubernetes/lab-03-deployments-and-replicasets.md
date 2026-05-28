# Lab 03 — Deployments, ReplicaSets, Rolling Updates

## 1. The hierarchy

```
Deployment ──owns──► ReplicaSet ──owns──► Pod (x replicas)
```

- **ReplicaSet**: keeps N Pods matching a selector alive.
- **Deployment**: orchestrates ReplicaSets to do rolling updates.

You almost always interact with Deployments. ReplicaSets exist under the hood.

## 2. A Deployment

`manifests/deploy-web.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 3
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
        - name: nginx
          image: nginx:1.27-alpine
          ports:
            - containerPort: 80
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
```

Apply:

```bash
kubectl apply -f manifests/deploy-web.yaml
kubectl get deploy,rs,pods -l app=web
kubectl rollout status deploy/web
```

Notice the 1→2→3 hierarchy: one Deployment, one ReplicaSet, three Pods.

## 3. Self-healing

```bash
kubectl delete pod -l app=web --grace-period=0 --force
kubectl get pods -l app=web    # a new one is already being created
```

The ReplicaSet controller noticed current<desired and spawned a replacement.

## 4. Rolling update

Change the image:

```bash
kubectl set image deploy/web nginx=nginx:1.26-alpine
kubectl rollout status deploy/web
kubectl get rs -l app=web       # you now have TWO rs: old (replicas 0) and new (replicas 3)
kubectl rollout history deploy/web
```

Rollback:

```bash
kubectl rollout undo deploy/web
```

## 5. Update strategies

```yaml
spec:
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxUnavailable: 25%
      maxSurge: 25%
```

- `RollingUpdate` (default): gradual.
- `Recreate`: kill all, then start new (downtime; use for non-HA state).

## 6. Scaling

```bash
kubectl scale deploy/web --replicas=5
kubectl get pods -l app=web
```

## 7. Resources: requests vs limits

- **requests**: what the scheduler uses to place the Pod. "I need at least this."
- **limits**: what the kernel cgroup enforces. "You may not exceed this."

Memory overrun = OOM-kill. CPU overrun = throttle. **Always set requests. Often set limits.**

## 8. Labels and selectors

Labels are key/value on objects; selectors match them.

```bash
kubectl get pods --show-labels
kubectl get pods -l app=web
kubectl get pods -l 'app in (web,api),env!=prod'
kubectl label pod <name> tier=frontend
```

Services, Deployments, NetworkPolicies — all use selectors to glue things together.

## 9. Practice

1. Scale `web` to 10. Watch pods spawn. Scale back to 2. Watch them terminate.
2. Update the image to a nonexistent tag. What happens? Where does it show up? (Hint: events + `ImagePullBackOff`.)
3. Rollback to the prior revision.
4. Add `resources.limits.memory: 8Mi` (absurdly low). Watch the pod's fate.

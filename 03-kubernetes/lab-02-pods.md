# Lab 02 — Pods: the Atom

## 1. What is a Pod?

A Pod is **one or more containers that share a network namespace and (optional) volumes**. They are scheduled together, live together, die together.

Why not just "container"? Because real workloads often want a sidecar (log shipper, proxy) co-located with the main app, sharing `localhost` and volumes, but isolated by process (mnt/pid namespaces).

You saw this exact pattern in Phase 2 Lab 04: `--network container:<other>`.

## 2. A minimal Pod

`manifests/pod-nginx.yaml`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: web
  labels:
    app: web
spec:
  containers:
    - name: nginx
      image: nginx:1.27-alpine
      ports:
        - containerPort: 80
```

Apply and inspect:

```bash
kubectl apply -f manifests/pod-nginx.yaml
kubectl get pods
kubectl describe pod web
kubectl logs web
kubectl exec -it web -- sh
kubectl port-forward pod/web 8080:80   # tunnel host:8080 → pod:80
# in another terminal:
curl http://localhost:8080
```

## 3. A two-container Pod (shared net + volume)

`manifests/pod-sidecar.yaml`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: sidecar-demo
spec:
  volumes:
    - name: shared
      emptyDir: {}
  containers:
    - name: writer
      image: busybox:1.36
      command: ["sh", "-c", "i=0; while true; do echo hello-$i > /data/msg; i=$((i+1)); sleep 2; done"]
      volumeMounts:
        - name: shared
          mountPath: /data
    - name: reader
      image: busybox:1.36
      command: ["sh", "-c", "while true; do cat /data/msg 2>/dev/null; sleep 2; done"]
      volumeMounts:
        - name: shared
          mountPath: /data
```

```bash
kubectl apply -f manifests/pod-sidecar.yaml
kubectl logs sidecar-demo -c reader -f
```

Both containers in the same net ns (`curl localhost` would reach peer), sharing `/data`.

## 4. Pod lifecycle

Phases: `Pending → Running → (Succeeded | Failed)`. Also `Unknown`.

You'll see lots of `Pending` while images pull or resources are unavailable. `describe` shows why via **Events**:

```bash
kubectl describe pod web | tail -30
```

## 5. Pods are (mostly) not what you create directly

In real life, you don't `kubectl apply` bare Pods — you create a **Deployment** that manages Pods for you. Bare Pods don't get restarted if a node dies. Next lab.

## 6. Practice

1. Apply the sidecar Pod. Use `kubectl exec` to enter each container; prove they share `/data` but have separate `/proc`.
2. Add a second `containerPort` and verify `kubectl describe`.
3. Delete the Pod (`kubectl delete pod web`). Does it come back? Why not?

# Lab 02: Pods, the Atom

**What you'll build:** two Pods. First, a one-container nginx Pod you exec into, log, and reach
over a port-forward tunnel; then a two-container Pod whose containers share a volume and a
network namespace. The **Pod is the smallest thing Kubernetes schedules**. Every higher object
in this curriculum (Deployments, vLLM servers, gateways, agents) is a wrapper that produces
Pods. By the end you can read a Pod manifest field-by-field and explain why containers inside
one Pod can talk over `localhost`.

> **The one idea:** the unit of deployment in Kubernetes is the **Pod**, not the container. A Pod
> is a group of containers that share a network namespace + IP and are scheduled, live, and die
> as one. Everything else in K8s is built to create and manage Pods.

## 1. What is a Pod?

A Pod is **one or more containers that share a network namespace and (optional) volumes**. They are scheduled together, live together, die together.

Why a Pod and not a single container? Real workloads often want a sidecar (log shipper, proxy) co-located with the main app, sharing `localhost` and volumes, but isolated by process (mnt/pid namespaces).

You saw this pattern in Phase 2 Lab 04: `--network container:<other>`. A Pod formalizes that
trick; the shared network namespace is its defining property.

## 2. A minimal Pod

A Pod is the smallest object you can hand the apiserver. Here is the whole thing
(`manifests/pod-nginx.yaml`), then the fields that matter:

```yaml
apiVersion: v1               # Pods are CORE v1 - no API group prefix (Deployments are apps/v1)
kind: Pod
metadata:
  name: web                  # the Pod's name; unique within the namespace
  labels:
    app: web                 # arbitrary tag - how Services/selectors will find this Pod later
spec:
  containers:                # a LIST - a Pod can hold more than one (section 3)
    - name: nginx            # container name, unique within the Pod (used by logs -c, exec -c)
      image: nginx:1.27-alpine
      ports:
        - containerPort: 80  # DOCUMENTS the port nginx listens on; does NOT publish it (see gotcha)
```

Two things beginners get wrong here:

- **`containerPort` does not "open" or "expose" anything.** It's informational
  metadata. The container listens on 80 whether or not you list it; removing this line changes
  nothing about reachability. You reach the port via `port-forward` (below) or a Service
  (lab-04), never because of this field.
- **A bare Pod has no controller.** Nothing is watching to restart it. If the node dies or you
  delete it, it's gone for good; no replacement appears. That's why you almost
  never create bare Pods in production (section 5).

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

- `describe pod web` prints the full object *plus* the **Events** at the bottom: the
  scheduling/pull/start timeline. This is the first place to look when a Pod misbehaves.
- `logs web` streams the main process's stdout/stderr (nginx's access/error log here). No `-c`
  needed because this Pod has only one container.
- `exec -it web -- sh` opens an interactive shell *inside* the container: `-i` keeps stdin
  open, `-t` allocates a TTY. You're now in nginx's filesystem and process namespace.
- `port-forward pod/web 8080:80` opens a tunnel from your laptop's `localhost:8080` straight to
  the Pod's port 80, bypassing all cluster networking. It runs in the foreground and holds the
  terminal open, so the `curl` goes in a *second* terminal.

**What you should see:** `get pods` shows `web` `1/1 Running` (1 of 1 containers ready); the
`curl` returns nginx's "Welcome to nginx!" HTML. The tunnel proves the Pod serves traffic with no
Service in front: `port-forward` is your debug backdoor to any single Pod.

## 3. A two-container Pod (shared net + volume)

This is the sidecar pattern made literal: two containers in *one* Pod, sharing a volume. Here
is the whole object (`manifests/pod-sidecar.yaml`), then what wires the two containers together:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: sidecar-demo
spec:
  volumes:                   # Pod-level volume - declared ONCE, mounted by BOTH containers below
    - name: shared
      emptyDir: {}           # an empty dir created with the Pod, gone when the Pod dies (ephemeral)
  containers:
    - name: writer
      image: busybox:1.36
      command: ["sh", "-c", "i=0; while true; do echo hello-$i > /data/msg; i=$((i+1)); sleep 2; done"]
      volumeMounts:
        - name: shared       # mounts the 'shared' volume above...
          mountPath: /data   # ...at /data inside THIS container
    - name: reader
      image: busybox:1.36
      command: ["sh", "-c", "while true; do cat /data/msg 2>/dev/null; sleep 2; done"]
      volumeMounts:
        - name: shared       # same volume...
          mountPath: /data   # ...also at /data, so writer's /data/msg IS reader's /data/msg
```

The two load-bearing ideas:

- **One `volumes:` entry, two `volumeMounts:`.** The volume is owned by the *Pod*, not a
  container. Each container opts in by referencing it by `name`. The writer's writes to
  `/data/msg` are immediately visible to the reader because both `/data` paths point at the
  same `emptyDir`. This is how a sidecar shares files with the main app.
- **`emptyDir: {}` is ephemeral.** It exists only as long as the Pod does; delete the Pod and
  the data is gone. It survives a *container* restart but not a *Pod* deletion. (Persistent
  storage that outlives the Pod is a later lab; `emptyDir` is for intra-Pod scratch space.)

```bash
kubectl apply -f manifests/pod-sidecar.yaml
kubectl logs sidecar-demo -c reader -f   # -c picks the container, -f follows (tail) the stream
```

- `-c reader` is now *required*: with two containers, `logs` won't guess which one, so name it.
- `-f` follows the log live, like `tail -f`. Ctrl-C to stop.

**What you should see:** a stream of `hello-0`, `hello-1`, `hello-2`..., the reader printing
what the writer wrote two seconds earlier, through the shared volume. Both containers also sit
in the same network namespace, so `curl localhost` from one would reach a server in the
other. That shared `localhost` is what makes them a Pod and not two unrelated containers.

## 4. Pod lifecycle

Every Pod moves through a small set of **phases**: `Pending → Running → (Succeeded | Failed)`.
There's also `Unknown` (the node stopped reporting).

`Succeeded`/`Failed` only happen for run-to-completion Pods (Jobs/batch); a long-running server like nginx stays `Running`. It never "succeeds" because there's nothing to finish.

You'll see lots of `Pending` while images pull or resources are unavailable. `Pending` means
the Pod is accepted but not yet running: it has not been scheduled, or its image is still
pulling. `describe` shows *why* via the **Events** at the bottom:

```bash
kubectl describe pod web | tail -30   # the Events section is last - the scheduling/pull timeline
```

- `| tail -30` trims the long object dump down to the Events, the part that tells
  you the *story* (Scheduled → Pulling → Pulled → Created → Started, or where it got stuck).

**What you should see:** a chronological Events list. For a healthy Pod the last event is
`Started`. For a stuck one, the failing step (e.g. `Failed to pull image`, `Insufficient cpu`)
is right there. Events answer "why is it `Pending`/`CrashLoopBackOff`?" far better than `get`.

## 5. Pods are (mostly) not what you create directly

In real life, you don't `kubectl apply` bare Pods; you create a **Deployment** that manages Pods for you. Bare Pods don't get restarted if a node dies, and nothing re-creates one you
delete. A Deployment adds the missing piece: a **controller** that keeps reality matching your
declared desired state. That's the next lab, and the pattern every workload is built on.

## 6. Practice

1. Apply the sidecar Pod. Use `kubectl exec` to enter each container; prove they share `/data` but have separate `/proc`.
   (`exec -it sidecar-demo -c writer -- sh`, then again with `-c reader`: same file under
   `/data/msg`, different PIDs in `/proc`.)
2. Add a second `containerPort` and verify `kubectl describe`. (It shows up as metadata only;
   reachability doesn't change, proving section 2's gotcha.)
3. Delete the Pod (`kubectl delete pod web`). Does it come back? Why not? (It doesn't: a bare
   Pod has no controller watching it. This is the gap lab-03's Deployment closes.)

## Next

→ `lab-03-deployments-and-replicasets.md`: you just proved a bare Pod, once deleted, is gone for
good. A **Deployment** wraps Pods in a controller that keeps your desired count alive and rolls
updates with no downtime, the pattern every workload in this curriculum is built on.

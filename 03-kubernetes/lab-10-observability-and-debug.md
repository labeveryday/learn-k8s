# Lab 10 — Observability and Debugging

**What you'll build:** not an object this time — a **method**. A repeatable loop for answering
"why isn't this working?" without guessing, plus the one cluster add-on (`metrics-server`) that
turns resource usage from invisible into a number. You'll drive every tool against the `web`
Deployment from lab-03 (`manifests/deploy-web.yaml`) — its Pods, its events, its logs — then
learn to inject a debug container into a Pod that has no shell, read live CPU/memory, and map
the seven failure states you'll actually hit in production to their causes. The point isn't the
commands; it's the *order* you run them in.

> **The one idea (Kelsey):** Kubernetes is loud — it writes down everything it does as **events**
> and exposes every container's stdout as **logs**. Debugging is not detective work, it's reading.
> The cluster already told you what's wrong; the skill is knowing which command surfaces it. So
> the rule is: **never guess, always `describe`.**

## 1. The debug loop (memorize)

This is the spine of the whole lab. Run these in order, top to bottom, every time something is
broken. Each rung narrows the question:

```bash
kubectl get <kind>                    # does it exist?
kubectl describe <kind> <name>        # what's its status and events?
kubectl logs <pod> [-c <container>]   # what is it saying?
kubectl logs <pod> --previous         # what did the crashed container say?
kubectl exec -it <pod> -- sh          # look inside
kubectl get events --sort-by=.lastTimestamp
```

- `describe` is the workhorse: it merges the object's spec, its current status, *and* the recent
  **Events** the controllers wrote about it — all in one screen. The Events block at the bottom is
  where the real cause almost always lives.
- `logs --previous` reads the **prior** container instance's stdout — critical for a
  `CrashLoopBackOff`, because by the time you look, the current container has already been killed
  and the message you need is in the dead one.
- `-c <container>` targets a specific container; a Pod can have several (init containers, sidecars),
  and `logs`/`exec` default to the first one, which is often not the one that's failing.

**What you should see:** `describe`'s output ending in an `Events:` table with lines like
`Failed to pull image` or `Liveness probe failed`. That line is the answer. The rest of this lab
is just learning which rung of this ladder catches which failure.

Never guess. `describe` and events tell you 80% of the truth.

## 2. Logs

`kubectl logs` reads a container's stdout/stderr — whatever your process prints. The non-obvious
part is *which* Pod(s) you get, since a Deployment has many:

```bash
kubectl logs deploy/web              # one pod of the deployment
kubectl logs -l app=web --tail=100   # all pods matching a selector
kubectl logs -f pod/web-abc          # follow
kubectl logs pod/web --all-containers
```

- `logs deploy/web` is a shortcut: it resolves the Deployment to **one** of its Pods and tails
  that — handy, but you're seeing a single replica, not the fleet.
- `-l app=web` uses the same label selector from lab-03 to fan out across **every** matching Pod —
  this is how you read all replicas at once. `--tail=100` caps each to its last 100 lines so the
  output stays readable.
- `-f` follows (streams) like `tail -f`; `--all-containers` pulls stdout from every container in
  the Pod, not just the default first one.

**What you should see:** nginx's access/error log lines (the `web` Pods run `nginx:1.27-alpine`).
If a selector returns nothing, the Pods either don't exist or don't carry `app=web` — which is
itself a finding.

K8s has no built-in log aggregation. In real clusters: Loki, ELK, Datadog, etc. `kubectl logs`
reads from the node's disk, so once a Pod is deleted its logs are **gone** — which is exactly why
production runs a log shipper.

## 3. Events

Events are the cluster's running narration — every scheduling decision, image pull, probe failure,
and OOM-kill, time-stamped:

```bash
kubectl get events -A --sort-by=.lastTimestamp
kubectl get events --field-selector involvedObject.name=web
```

- `-A` spans all namespaces; `--sort-by=.lastTimestamp` orders them chronologically (the default
  ordering is *not* by time, which makes the raw list nearly useless during an incident).
- `--field-selector involvedObject.name=web` filters to events about one object by name — the
  surgical version of scrolling the whole list.

**What you should see:** a chronological feed. Two gotchas hide here: events are **namespaced**
(forget `-n`/`-A` and you'll miss the relevant ones), and they **expire** — the default retention
is about one hour, so a failure from this morning may already be gone. That short TTL is the other
reason real clusters ship events somewhere durable.

`describe` prints recent events at the bottom; that's where the truth usually lives (ImagePullBackOff, OOMKilled, FailedScheduling, ...).

## 4. `kubectl debug`

`exec` only works if the target container ships a shell and tools. Production images often don't —
a **distroless** image is just your binary, no `sh`, no `ps`, no `netstat`. `kubectl debug` solves
this by attaching a *new* container (with the tools you need) **alongside** the target, inside the
same Pod, sharing its process namespace — so from busybox you can see and poke the real process:

```bash
kubectl debug -it pod/web --image=busybox:1.36 --target=nginx -- sh
# now you can ps, netstat, nsenter into the nginx container
```

- `--image=busybox:1.36` is the toolbox you're bringing — pinned, so the demo is reproducible.
- `--target=nginx` shares the **PID namespace** of the `nginx` container specifically, so `ps`
  inside busybox lists nginx's processes and you can `nsenter` into them. Drop `--target` and you
  get an isolated debug container in the Pod's network namespace but *not* its PIDs.
- `-it -- sh` allocates an interactive TTY and drops you into a shell, exactly like `exec`.

Or create a "copy" of a problematic pod to poke at safely:

```bash
kubectl debug pod/web --copy-to=web-debug --container=nginx --image=busybox:1.36 -- sh
```

- `--copy-to=web-debug` clones the Pod spec into a **new, separate** Pod named `web-debug` and adds
  the debug container there. The original `web` Pod keeps serving traffic untouched — you experiment
  on the copy. Gotcha: the copy is a bare Pod, not owned by the ReplicaSet, so **clean it up
  yourself** (`kubectl delete pod web-debug`) when done.

**What you should see:** a busybox `#` prompt. Inside it `ps` shows the nginx master/worker
processes (the proof the PID namespace is shared), even though nginx-alpine itself has no debug
tools.

## 5. Resource metrics

Nothing so far told you how much CPU or memory a Pod is *using* — only what it requested. That data
comes from `metrics-server`, which kind doesn't install by default:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
# kind's kubelet serves metrics over a self-signed cert that metrics-server won't trust by
# default, so we append the --kubelet-insecure-tls flag. (This JSON-patch syntax just adds one
# arg to the container — the array-index path means "append to the args list"; you rarely hand-write these.)
kubectl patch deploy metrics-server -n kube-system --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'

kubectl top nodes
kubectl top pods
```

- `apply -f <url>` installs metrics-server straight from its GitHub release — a bundle of
  Deployment + RBAC + an APIService that registers the `metrics.k8s.io` API the cluster previously
  lacked.
- `patch --type=json` applies a **JSON Patch (RFC 6902)** — a list of typed operations, not a merge.
  Read the path piece by piece: `/spec/template/spec/containers/0/args/` walks into the **first**
  container's `args` list, and the trailing `/-` is JSON Patch's "append to the end of the array"
  token. So the op `add` tacks `--kubelet-insecure-tls` onto the container's args. Without that flag
  metrics-server refuses kind's self-signed kubelet cert and `kubectl top` stays empty forever.
- `top nodes` / `top pods` read live usage *from* metrics-server — the first commands in this lab
  that show consumption rather than configuration.

**What you should see:** `top` initially errors with `metrics not available yet` — metrics-server
needs ~15-30s after the patch reschedules its Pod to collect a first sample. Then `top nodes` shows
per-node CPU(cores)/MEM and `top pods` shows per-Pod usage. Gotcha: `top` reports **usage**, which
is unrelated to the `requests`/`limits` you set in lab-03 — usage is what's happening now, requests
are what you reserved.

Metrics-server powers `kubectl top` and **HPA** (HorizontalPodAutoscaler), which you'll wire up in the capstone (`exercises.md` step 7). There's no separate HPA lab — this install is the prerequisite for it. Without metrics-server, an HPA shows `TARGETS: <unknown>` and never scales.

## 6. Common failure modes (cheatsheet)

Every row maps a symptom (from `kubectl get pods`) to the rung of the section-1 loop that proves
the cause. Memorize the left column; the right column is always confirmed by `describe` + logs.

| Symptom | Likely cause |
|---------|--------------|
| `ImagePullBackOff` | wrong image name/tag, private registry no creds |
| `CrashLoopBackOff` | process exits; check logs + previous logs |
| `Pending` forever | no node has resources; describe → events |
| `CreateContainerConfigError` | ConfigMap/Secret missing or key mismatch |
| `OOMKilled` | memory limit too low; bump or fix leak |
| `0/1 READY` but `Running` | readiness probe failing |
| Service returns no response | endpoints empty (selector mismatch) or readiness failing |

Two of these you can already trigger on `web`. The `0/1 READY but Running` row is the
`readinessProbe` from `manifests/deploy-web.yaml` doing its job — `Running` is a process fact,
`READY` is a traffic fact (lab-08):

```yaml
          readinessProbe:
            httpGet: { path: /, port: 80 }   # kubelet GETs the Pod's own IP:80; non-2xx/3xx = not ready
            periodSeconds: 5                  # re-checked every 5s — READY flips on this cadence
```

And the last row chains to that probe: the `web` Service selects `app: web`, so if a Pod fails
readiness the endpoints controller **pulls it from the Service's Endpoints** and traffic dries up —
the same `selector: { app: web }` wiring you traced in lab-04. "No response from the Service" and
"`0/1 READY`" are usually the same bug seen from two angles.

## 7. Practice

1. Break a Deployment (wrong image). Diagnose from events only, without looking at YAML.
   (`kubectl set image deploy/web nginx=nginx:does-not-exist`, then `describe` → `ImagePullBackOff`
   in the Events block — section 6, row 1, proven by section 1's loop.)
2. `kubectl top pods` after installing metrics-server. Which pod uses most memory?
3. Use `kubectl debug` to launch a busybox sidecar in a running pod and tcpdump its traffic.

## Next

→ `lab-11-helm-and-kustomize.md`: you've been applying raw YAML one file at a time. Now package
and template a whole app — and stop hand-editing manifests for every environment.

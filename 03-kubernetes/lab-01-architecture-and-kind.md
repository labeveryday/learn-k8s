# Lab 01 — Architecture and your first cluster (kind)

**What you'll build:** a real, single-node Kubernetes cluster running on your laptop — then
you'll take it apart with your hands. By the end you can point at the control-plane boxes from
the README diagram (apiserver, etcd, scheduler, controller-manager) and say *"that one is this
Pod, here are its logs, and here is the exact HTTP call `kubectl` made to it"* — and know which
parts (kubelet, kube-proxy) run per-node instead. No app yet; the goal is to make the control
plane concrete so every later lab sits on a model you've actually touched.

> **The one idea (MIT):** Kubernetes *is* an API. The control plane is just a set of processes
> that read and write objects through one REST front door (the apiserver). `kubectl` is an HTTP
> client. Every section below is you watching that client talk to that server.

## 1. Spin it up

`kind` = Kubernetes IN Docker. Each "node" is a Docker container running the K8s components —
so a whole cluster is a few containers on your host, disposable and free.

```bash
kind create cluster --name learn --image kindest/node:v1.30.0   # one Docker container = the node
kubectl cluster-info                                            # where the apiserver lives + its URL
kubectl get nodes
kubectl get pods -A                 # all namespaces
```

- `--name learn` labels the cluster; it's how you target it later (`kind delete cluster --name learn`).
- `--image kindest/node:v1.30.0` **pins the Kubernetes version**. Pinning matters: a control
  plane that drifts under you breaks reproducibility — every reader of this lab gets the same
  K8s. The image *is* the node: control-plane components run as Pods inside that one container.
- `cluster-info` prints the apiserver URL `kubectl` will hit; that URL is the "REST front door"
  the README's MIT note means.
- `-A` is shorthand for `--all-namespaces` — without it you only see the `default` namespace,
  and the control plane lives in `kube-system`, so you'd see nothing interesting.

**What you should see:** one node named `learn-control-plane`, `Ready`. In `kube-system`, Pods
for `etcd`, `kube-apiserver`, `kube-controller-manager`, `kube-scheduler`, `kube-proxy`,
`coredns`, and `kindnet`. **Four of these — apiserver, etcd, scheduler, controller-manager —
are the control-plane boxes from the README diagram**, now running as real Pods you can `logs`
and `describe`; `kube-proxy` and `kindnet` (the CNI — the plugin that gives every Pod an IP,
explained in lab-04) are the per-node networking add-ons, and `coredns` is cluster DNS. (The
README diagram also shows `kubelet` — that one is **not** a Pod; it's a process on the node
itself, which you'll find via `ps` in the Practice.) The fact that the control plane *itself*
runs as Pods is the first big idea: Kubernetes manages Kubernetes.

## 2. Map the diagram to real pods

```bash
kubectl get pods -n kube-system -o wide                              # which node, which IP
kubectl describe pod -n kube-system -l component=kube-apiserver | head -40
kubectl logs -n kube-system -l component=kube-apiserver --tail=20
```

- `-o wide` adds the node and Pod IP columns — proof these control-plane Pods are real
  scheduled workloads, not magic.
- `-l component=kube-apiserver` selects by **label**, not name. The apiserver Pod's name has a
  cluster-specific suffix (`kube-apiserver-learn-control-plane`), but its `component` label is
  stable — so the selector works without you knowing the exact name. This label/selector
  indirection is everywhere in K8s (lab-03 leans on it hard).
- `describe` shows the full object + its recent **Events**; `logs` shows the process's stdout.

**What you should see:** `describe` lists the apiserver's container, its image, the flags it was
started with, and an Events section. `logs` shows live apiserver request traffic. Each
control-plane component you read about in the README is a Pod here you can inspect — the diagram
is now greppable.

## 3. The API itself

The MIT lens says Kubernetes is an API. Here's how to read it directly.

```bash
kubectl api-resources              # every kind in this cluster
kubectl api-versions               # API group versions
kubectl explain pod                # docs
kubectl explain pod.spec.containers
```

- `api-resources` lists every object **kind** the apiserver understands (Pod, Deployment,
  Service, ...), with its API group and whether it's namespaced. This is the cluster telling you
  its own vocabulary.
- `api-versions` lists the group/versions (`v1`, `apps/v1`, ...) — the `apiVersion:` line at the
  top of every manifest you'll write comes from this list.
- `explain` is your **offline textbook**: it reads the live API's schema, so it documents
  *exactly* the fields this cluster's version accepts. `explain pod.spec.containers` drills into a
  nested field — the same `<kind>.<field>` path syntax the Kelsey note in the README recommends.

**What you should see:** dozens of resource kinds, a handful of API group versions, and field-by-
field docs for `pod` and `pod.spec.containers`. Internalize that these docs come *from the
cluster*, not the internet — they can never be out of date for your version.

Raw API:

```bash
kubectl get --raw /api/v1/namespaces | jq .          # the apiserver's raw JSON, no kubectl pretty-printing
kubectl -v=8 get pods 2>&1 | head -40                # see the raw HTTPS calls
```

- `--raw <path>` bypasses `kubectl`'s table formatting and hits an apiserver REST path directly,
  returning the raw JSON object — proof that `kubectl get` is just a friendly wrapper over HTTP
  GETs. `| jq .` pretty-prints that JSON.
- `-v=8` cranks `kubectl`'s log verbosity to level 8, which prints the **actual HTTP requests and
  responses**. `2>&1` merges stderr (where the verbose logs go) into stdout so `head` can see them.

**What you should see:** in the `-v=8` output, lines starting `GET https://...` and
`Response Status: 200 OK` — that's `kubectl` talking REST to the apiserver, the exact point the
MIT note makes. Once you've seen this, "Kubernetes is an API" stops being a slogan: every command
in every later lab is one of these calls.

## 4. Namespaces

A namespace is a scoping boundary for names and RBAC — two teams can each have a `web` Deployment
without collision, and you can grant access per-namespace.

```bash
kubectl get ns
kubectl create namespace demo
kubectl -n demo get all
kubectl config set-context --current --namespace=demo     # makes demo your default ns
kubectl config set-context --current --namespace=default  # switch back
```

- `-n demo` scopes a single command to the `demo` namespace (it's empty, so `get all` shows
  nothing — that's expected).
- `set-context --current --namespace=...` edits your **kubeconfig** so bare `kubectl` commands
  default to that namespace from now on. `--current` means "the context I'm using now."

> Heads up: `set-context --namespace` is sticky — it changes where bare `kubectl get pods` lands
> for the rest of your session. **The rest of Phase 3 assumes you're in `default`**, so we switch
> back here. (The capstone manifest pins everything to `demo` explicitly with `-n demo`, so it
> doesn't care.) Check with `kubectl config view --minify | grep namespace`.

**What you should see:** `get ns` lists `default`, `kube-system`, `kube-public`,
`kube-node-lease`, and your new `demo`. After the first `set-context`, `config view --minify`
shows `namespace: demo`; after the second, it's back to `default` — confirm this before moving on
or later labs will silently look in the wrong place.

Convention: one namespace per environment/team/app.

## 5. Contexts

Your `~/.kube/config` holds *contexts* (cluster + user + namespace). A context is one named
"who am I, talking to which cluster, defaulting to which namespace" bundle — switching contexts is
how you jump between clusters.

```bash
kubectl config get-contexts                 # the * marks your current context
kubectl config use-context kind-learn       # kind names its context kind-<cluster-name>
```

- `get-contexts` lists every cluster/user/namespace bundle in your kubeconfig; the `*` column
  marks the active one.
- `use-context kind-learn` switches to it. Note the `kind-` prefix: kind names its context
  `kind-<cluster-name>`, so the `learn` cluster's context is `kind-learn`.

**What you should see:** `kind-learn` in the list, marked current. If you later spin up more
clusters, this is the command that moves between them.

Install `kubectx`/`kubens` if you want fast switching.

## 6. k9s: your TUI dashboard

```bash
k9s
# ':pods' to view pods, ':ns' for namespaces, 'l' for logs, 'd' for describe
# '?' for help
```

`k9s` is a terminal UI over the same API `kubectl` uses — `:pods` is a live `get pods`, `l` is
`logs`, `d` is `describe`. It auto-refreshes, so it's a great way to *watch* the reconcile loop in
later labs.

**What you should see:** a live, auto-refreshing table of Pods. Extremely useful for learning. Use
it alongside raw `kubectl` — never as a replacement: the point of Phase 3 is that you can do it by
hand. Press `:q` to quit.

## 7. Teardown

Keep this cluster running through Phase 3 — every later lab applies to it. When you're fully done:

```bash
kind delete cluster --name learn        # removes the node container and this kubeconfig context
```

`delete cluster --name learn` tears down the Docker container *and* removes the `kind-learn`
context from your kubeconfig — clean slate, nothing left running on your host.

## 8. Practice

1. Which process on your host is the apiserver? (It's inside the kind node container — on a Mac
   that's inside the Colima/Docker VM; on Linux it's a container directly on your host.) Run
   `docker ps | grep control-plane` and then `docker exec -it <kind-container> bash` →
   `ps -ef | grep kube-apiserver`. (You're proving the apiserver Pod from section 2 is a real OS
   process inside the node container.)
2. `kubectl explain deployment.spec.strategy` — what are the two types? (You'll use one in lab-03.)
3. `kubectl get events -A --sort-by=.lastTimestamp` — events are how K8s narrates itself; the
   `--sort-by` puts the most recent last so you read the story in order.

## Next

→ `lab-02-pods.md`: now that the control plane is real to you, create the first thing you'll
actually run on it — a Pod, the atom every other object is built from.

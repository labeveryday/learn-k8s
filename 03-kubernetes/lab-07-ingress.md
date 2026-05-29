# Lab 07 — Ingress (L7 routing)

**What you'll build:** a single HTTP entry point — one IP, one controller — that routes by
**hostname and URL path** to different Services behind it. You'll recreate your kind cluster
with host ports exposed, install the **ingress-nginx controller**, point an `Ingress` object at
your `web` Service, and `curl` it from your laptop. Then you'll layer on multi-path routing and
TLS. The point isn't nginx; it's the split between a **declarative routing object** (the
`Ingress`) and the **proxy that enforces it** (the controller) — the same control-plane /
data-plane split every gateway in this curriculum repeats.

> **The one idea (Stanford):** an `Ingress` is *config, not a server*. It's a routing table you
> declare; a separate **Ingress Controller** watches every Ingress in the cluster and rewrites a
> real proxy's config to match. No controller installed → your Ingress is inert YAML that routes
> nothing. Every section below is that watch-and-reconfigure loop in action.

## 1. Ingress vs Service — the layer it operates at

A Service (lab-04) and an Ingress solve different problems at different layers:

- **Service is L4** (TCP/UDP). One virtual IP per Service; external exposure means a NodePort
  or a cloud LoadBalancer — i.e. *one public entry point per app*.
- **Ingress is L7** (HTTP/HTTPS). One IP fronts **many** hostnames and paths, with TLS
  termination — i.e. *one public entry point for the whole cluster*, fanned out by HTTP rules.

The thing to internalize: **an Ingress is an object, not a process.** It does nothing on its own.
It needs an **Ingress Controller** (ingress-nginx, Traefik, HAProxy, etc.) — a Pod running an
actual reverse proxy — that *watches* Ingress objects and reconfigures that proxy. The Ingress is
the desired routing state; the controller is the loop that makes a real proxy match it. Install
the controller first (section 2), then the Ingress (section 3) has something to act on.

## 2. Install ingress-nginx on kind

> **Heads up — this recreates your cluster from scratch and deletes everything from labs 02-06** (Deployments, Services, ConfigMaps, PVCs). kind can only add the host port mappings at create time. After it comes back up, re-apply your `web` Deployment + Service (both live in `manifests/deploy-web.yaml` — it's a two-document file) before the section 3 Ingress will have a backend to route to.

Why the teardown is unavoidable: kind runs your "node" as a container, and a container's
published ports are fixed **at `docker run` time** — there's no way to add `-p 80:80` to a
running container. The controller listens on the node's :80/:443, so those ports must be mapped
into the node container, which means the node container must be recreated. Hence: delete cluster,
recreate with port mappings.

```bash
# Needs host:80 and host:443 exposed — recreate cluster with mappings:
cat <<'EOF' > /tmp/kind-ingress.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  kubeadmConfigPatches:
  - |
    kind: InitConfiguration
    nodeRegistration:
      kubeletExtraArgs:
        # ingress-ready=true: the ingress-nginx kind manifest only schedules
        # the controller onto nodes carrying this label.
        node-labels: "ingress-ready=true"
  # extraPortMappings: forward host 80/443 into the node so curl from your
  # laptop reaches the controller (80 = HTTP, 443 = HTTPS).
  extraPortMappings:
  - containerPort: 80
    hostPort: 80
    protocol: TCP
  - containerPort: 443
    hostPort: 443
    protocol: TCP
EOF

kind delete cluster --name learn
kind create cluster --name learn --config /tmp/kind-ingress.yaml --image kindest/node:v1.30.0

# This installs the ingress-nginx controller (its own namespace, RBAC, a Deployment,
# and admission webhooks) — expect a long list of "created" lines.
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.10.1/deploy/static/provider/kind/deploy.yaml
# If offline, use the image pre-pulled in 00-prep and the yaml from a local clone.

# Blocks until the controller Pod is Ready — up to ~3 min on first image pull
# (it isn't hung). A "pod ... condition met" line means success.
kubectl wait --namespace ingress-nginx --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller --timeout=180s
```

The kind config has two load-bearing pieces, and they work together:

- **`node-labels: "ingress-ready=true"`** stamps a label onto the kubelet's node registration.
  The `provider/kind` flavor of the ingress-nginx manifest pins its controller to nodes with a
  `nodeSelector` of `ingress-ready=true`. Skip this label and the controller Pod sits **`Pending`
  forever** ("0 nodes match node selector") — a silent stall with no error on `apply`.
- **`extraPortMappings`** publishes the node container's :80/:443 to your host's :80/:443.
  `containerPort` is inside the node, `hostPort` is on your laptop. This is the *only* reason a
  `curl` from your Mac reaches the controller — without it the controller listens, but nothing
  forwards the host's traffic to it.

Two beginner gotchas these fields hide:

- **The image tag and the manifest version must match.** `--image kindest/node:v1.30.0` pins the
  Kubernetes version; the `controller-v1.10.1` manifest is the one verified against it. Mixing a
  newer controller with an older node (or vice-versa) can fail the admission webhook on first
  Ingress apply.
- **`hostPort: 80` needs :80 free on your machine.** If anything local already owns port 80 (a
  dev server, another Docker container), `kind create` fails with a bind error. Free the port or
  the cluster never comes up.

The command flags worth knowing:

- `kind delete cluster --name learn` removes the old node container *and its volumes* — this is
  what wipes labs 02-06.
- `kubectl apply -f https://.../kind/deploy.yaml` pulls the **kind-specific** provider manifest
  (it uses a `hostPort`-based DaemonSet/Deployment instead of a cloud LoadBalancer). Using the
  generic `cloud` manifest here would leave the controller's Service `<pending>` with no external IP.
- `kubectl wait --for=condition=ready pod --selector=app.kubernetes.io/component=controller` blocks
  until the controller Pod reports `Ready` rather than you re-running `get pods`. `--timeout=180s`
  is generous on purpose — the first run pulls the controller image.

**What you should see:** a fresh `learn` cluster, a long list of `... created` objects in the
`ingress-nginx` namespace, and finally `pod/ingress-nginx-controller-... condition met`. That
last line means the proxy is up and watching — Ingress objects you create now will actually route.

Re-apply the backend the Ingress will point at (it was wiped with the cluster). Here is the real
`manifests/deploy-web.yaml` — note it's **two documents**: the Deployment *and* the `web` Service
(the Service is that second document, which is what the heads-up box above meant):

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
          readinessProbe:
            httpGet: { path: /, port: 80 }   # Pod only joins Endpoints once / returns 200
            periodSeconds: 5                  # re-checks readiness every 5s
---
apiVersion: v1
kind: Service
metadata:
  name: web                  # the Ingress backend below routes to this exact name
spec:
  selector:
    app: web                 # live membership: any Pod labeled app: web becomes an Endpoint
  ports:
    - port: 80               # the port the Ingress backend dials (service:80)
      targetPort: 80         # the container port it forwards to
```

```bash
kubectl apply -f manifests/deploy-web.yaml   # recreate the web Deployment + Service
kubectl get deploy,svc,endpoints web         # confirm 3/3 ready and Endpoints populated
```

The gotcha that bites people right here: the **Ingress routes to the Service, and the Service
routes only to *ready* Endpoints.** Because the Service has a `readinessProbe`, if the Pods aren't
ready yet (or the `endpoints web` list is empty) the controller has nowhere to send traffic and
you'll get a `503` from nginx — not because the Ingress is wrong, but because the backend has zero
ready Pods. Confirm `endpoints web` is non-empty before blaming the Ingress.

**What you should see:** `deployment.apps/web` at `3/3`, `service/web` with a ClusterIP, and
`endpoints/web` listing three Pod IPs on `:80`. Now the Ingress has a live backend.

## 3. A simple Ingress

The `Ingress` is your declarative routing table: *"requests for host `web.localtest.me` path `/`
go to the `web` Service on port 80."* You write the rule; the controller rewrites its proxy to
enforce it. Here is the whole object (`manifests/ingress-web.yaml`), then the fields that matter:

```yaml
apiVersion: networking.k8s.io/v1   # Ingress is GA in networking.k8s.io/v1 (older guides used extensions/v1beta1 — gone)
kind: Ingress
metadata:
  name: web
spec:
  ingressClassName: nginx          # WHICH controller owns this Ingress — must match an installed IngressClass
  rules:
    - host: web.localtest.me      # public DNS wildcard: *.localtest.me → 127.0.0.1 (needs DNS; offline, add `127.0.0.1 web.localtest.me` to /etc/hosts)
      http:
        paths:
          - path: /
            pathType: Prefix       # match this path AND everything beneath it (/, /foo, /foo/bar)
            backend:
              service:
                name: web          # target Service name (must exist in this namespace)
                port:
                  number: 80       # the Service's PORT (svc port), not the container port
```

Two fields beginners get wrong, and both fail *silently* (the Ingress applies fine, traffic just
goes nowhere):

- **`ingressClassName: nginx` must name an installed IngressClass.** The controller only acts on
  Ingresses whose class it owns. Install ingress-nginx and you get an IngressClass named `nginx`;
  omit or misspell this field and **no controller claims the Ingress** — it shows an empty
  `ADDRESS` and routes nothing. Check `kubectl get ingressclass` to see the real name.
- **`backend.service.port.number` is the *Service* port, not the container port.** Here both are
  80 so the distinction hides, but the Ingress dials the Service's `port:` (lab-04's
  `port` vs `targetPort` split); the Service then forwards to its `targetPort`. Point the Ingress
  at a port the Service doesn't expose and you get a `503` with no Endpoints.

A third trap, about names: `host: web.localtest.me` only works because `*.localtest.me` is a
**public DNS wildcard that resolves to 127.0.0.1**. Your laptop's :80 is mapped into the node
(section 2), so the request reaches the controller, which matches the `Host:` header against the
rule. Offline (no DNS), add `127.0.0.1 web.localtest.me` to `/etc/hosts` or the name won't resolve.

```bash
kubectl apply -f manifests/ingress-web.yaml
curl http://web.localtest.me/      # → nginx welcome
```

- `apply -f` registers the routing rule; the controller picks it up within a second or two and
  rewrites its nginx config. (Run `kubectl get ingress web` and wait for the `ADDRESS` column to
  populate — that's the controller acknowledging ownership.)
- `curl http://web.localtest.me/` sends `Host: web.localtest.me` to 127.0.0.1:80 → node :80 →
  controller → `web` Service → a `web` Pod.

**What you should see:** the nginx welcome HTML. That full path — laptop → host port → node →
controller → Service → Pod — is L7 routing working end to end. A `503` here means the backend has
no ready Endpoints (section 2 gotcha); a `404` from the controller means the `Host`/path didn't
match any rule (usually DNS resolving to the wrong place or a typo'd host).

## 4. Multi-path routing

The payoff of L7: one host, multiple paths, each to a different Service. This is how `/api` and
`/` live behind a single IP:

```yaml
spec:
  ingressClassName: nginx
  rules:
    - host: app.localtest.me
      http:
        paths:
          - path: /api
            pathType: Prefix
            backend: { service: { name: api,  port: { number: 8000 } } }   # /api/* → api Service:8000
          - path: /
            pathType: Prefix
            backend: { service: { name: web,  port: { number: 80   } } }   # everything else → web Service:80
```

- **Order doesn't decide the match — specificity does.** ingress-nginx matches the *longest*
  matching prefix, so `/api/orders` hits the `api` backend even though `/` would also match. List
  the catch-all `/` last for readability, but the controller picks `/api` because it's more specific.
- The `api` backend points at the FastAPI app from `manifests/fastapi-redis.yaml` (Service `api`,
  port 8000). That Service lives in the `demo` namespace — an **Ingress can only route to Services
  in its own namespace.** To front `api`, either put this Ingress in `demo` or run `api` in the
  default namespace; cross-namespace backends are not allowed in core Ingress.

**What you should see (once both backends exist):** `curl http://app.localtest.me/` returns the
nginx welcome, and `curl http://app.localtest.me/api/...` returns the FastAPI JSON — same IP, same
host, split by path. That's the one-front-door-for-many-apps property L7 buys you.

## 5. TLS

Add a `tls:` block to terminate HTTPS at the controller:

```yaml
spec:
  tls:
    - hosts: [app.example.com]   # which host(s) this cert covers — must match a rule's host
      secretName: app-tls        # a Secret of type kubernetes.io/tls holding tls.crt + tls.key
```

- `secretName` references a **`kubernetes.io/tls` Secret** with two keys, `tls.crt` and `tls.key`.
  The controller loads that cert and terminates TLS at the proxy; traffic to the backend Pod is
  then plain HTTP inside the cluster.
- The `hosts:` list must match the `host:` in your `rules:` — the controller picks the cert by
  SNI/host. A mismatch means the connection falls back to the controller's default self-signed
  cert and clients see a name error.

In real life you don't hand-craft that Secret — use **cert-manager + Let's Encrypt**, which
watches your Ingresses, requests certs over ACME, and writes the `kubernetes.io/tls` Secrets for
you (auto-renewing them). That's a Phase-later topic; for now know the shape: cert lives in a
Secret, Ingress references it by name.

## 6. Gateway API (what's next)

Ingress is `networking.k8s.io/v1` and showing its age — annotations sprawl, no clean way to split
ownership between platform and app teams. **Gateway API** (also in `networking.k8s.io`) is the
successor: richer routing (header/method matching, traffic splitting), explicit role separation
(`GatewayClass` / `Gateway` / `HTTPRoute`), and multi-tenant friendliness. Learn it next, after
Ingress is comfortable — the mental model (routing object + controller that enforces it) carries
straight over.

## 7. Practice

1. Deploy `web` + Service + Ingress. Hit it via `http://web.localtest.me`.
2. Add a second Deployment `api` (the FastAPI/Redis app), a Service, and an Ingress path `/api/`.
3. Inspect the ingress-nginx controller logs while you curl — observe per-request logging.

> **Debugging tip:** for #3, `kubectl logs -n ingress-nginx deploy/ingress-nginx-controller -f`
> tails the proxy's access log; each `curl` prints a line with the matched host, path, backend, and
> status. A `503` there with an empty upstream confirms a "no ready Endpoints" backend; a `404`
> confirms a host/path that matched no rule — the two failure modes from section 3, now visible.

## Next

→ `lab-08-probes-and-lifecycle.md`: the readiness gate that decided whether your `web` Pods joined
the Service's Endpoints — formalized. Startup, liveness, and readiness probes, plus the graceful
shutdown and PodDisruptionBudgets that keep an Ingress backend serving during churn.

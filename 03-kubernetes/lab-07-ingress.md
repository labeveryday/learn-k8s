# Lab 07 — Ingress (L7 routing)

## 1. Ingress vs Service

- Service is **L4** (TCP/UDP). One IP per service; external exposure is NodePort or LB.
- Ingress is **L7** (HTTP/HTTPS). One IP for many hostnames/paths, with TLS termination.

Ingress is an object. It does nothing without an **Ingress Controller** (nginx-ingress, Traefik, etc.) that watches Ingresses and configures an actual proxy.

## 2. Install ingress-nginx on kind

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
        node-labels: "ingress-ready=true"
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

kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.10.1/deploy/static/provider/kind/deploy.yaml
# If offline, use the image pre-pulled in 00-prep and the yaml from a local clone.

kubectl wait --namespace ingress-nginx --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller --timeout=180s
```

## 3. A simple Ingress

`manifests/ingress-web.yaml`:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: web
spec:
  ingressClassName: nginx
  rules:
    - host: web.localtest.me      # resolves to 127.0.0.1 for any subdomain
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: web
                port:
                  number: 80
```

```bash
kubectl apply -f manifests/ingress-web.yaml
curl http://web.localtest.me/      # → nginx welcome
```

## 4. Multi-path routing

```yaml
spec:
  ingressClassName: nginx
  rules:
    - host: app.localtest.me
      http:
        paths:
          - path: /api
            pathType: Prefix
            backend: { service: { name: api,  port: { number: 8000 } } }
          - path: /
            pathType: Prefix
            backend: { service: { name: web,  port: { number: 80   } } }
```

## 5. TLS

```yaml
spec:
  tls:
    - hosts: [app.example.com]
      secretName: app-tls
```

Secrets of type `kubernetes.io/tls` with `tls.crt` and `tls.key`. In real life use cert-manager + Let's Encrypt.

## 6. Gateway API (what's next)

Ingress is v1 and showing its age. Gateway API (also in `networking.k8s.io`) is the successor — richer routing, multi-tenant friendly. Learn it next, after Ingress is comfortable.

## 7. Practice

1. Deploy `web` + Service + Ingress. Hit it via `http://web.localtest.me`.
2. Add a second Deployment `api` (the FastAPI/Redis app), a Service, and an Ingress path `/api/`.
3. Inspect the ingress-nginx controller logs while you curl — observe per-request logging.

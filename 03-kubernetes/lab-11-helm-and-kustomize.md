# Lab 11 — Helm and Kustomize

You've written raw YAML. Now package it.

## Helm: templated charts

Helm is a templating engine + release manager. A **chart** is a directory of templated YAML + values.

```bash
helm create mychart             # scaffold
tree mychart
# Chart.yaml, values.yaml, templates/*, ...

helm install dev ./mychart
helm list
helm upgrade dev ./mychart --set replicaCount=3
helm rollback dev 1
helm uninstall dev
```

Template syntax uses Go templates + Sprig functions:

```yaml
# templates/deployment.yaml
spec:
  replicas: {{ .Values.replicaCount }}
  template:
    spec:
      containers:
        - name: {{ .Chart.Name }}
          image: "{{ .Values.image.repo }}:{{ .Values.image.tag }}"
```

Debug:

```bash
helm template ./mychart --values values.yaml    # render without installing
helm install --dry-run --debug dev ./mychart
```

**Kelsey's warning:** Helm is powerful and can make simple apps hard to reason about. Use it when you need the reusability; avoid it when a single manifest will do. If you can't `helm template | kubectl apply -f -` and eyeball the output, you're in trouble.

## Using upstream charts

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update
helm search repo postgres
helm install pg bitnami/postgresql --values my-values.yaml
```

## Kustomize: overlays, no templates

Built into `kubectl`. Pattern: a `base/` directory with plain manifests + `overlays/{dev,prod}/` that patch them.

```
app/
├── base/
│   ├── kustomization.yaml
│   ├── deployment.yaml
│   └── service.yaml
└── overlays/
    ├── dev/
    │   ├── kustomization.yaml
    │   └── replicas.yaml
    └── prod/
        └── kustomization.yaml
```

`base/kustomization.yaml`:

```yaml
resources:
  - deployment.yaml
  - service.yaml
```

`overlays/prod/kustomization.yaml`:

```yaml
resources:
  - ../../base
namespace: prod
replicas:
  - name: web
    count: 5
images:
  - name: myapp
    newTag: "1.2.3"
```

Apply:

```bash
kubectl apply -k overlays/prod
kubectl kustomize overlays/prod    # just render
```

## When to use which

| Need | Tool |
|------|------|
| Install third-party apps (Postgres, Prometheus) | Helm |
| Manage your own small/medium apps across envs | Kustomize |
| Complex parameterization + conditionals | Helm |
| GitOps with cleanly rendered manifests | Kustomize (or Helm rendered via `helm template` into ArgoCD) |

## Practice

1. Convert the FastAPI/Redis stack to a tiny Helm chart with configurable replicas + image tag.
2. Also convert it to Kustomize with dev/prod overlays (dev: replicas=1, prod: replicas=3, different namespace).
3. `helm template` the chart and `diff` against `kubectl kustomize`. Which is easier to read?

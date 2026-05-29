# Lab 11 — Helm and Kustomize

**What you'll build:** the same app, packaged two ways. First a **Helm chart** — a directory of
*templated* YAML you install, upgrade, roll back, and uninstall as one named **release**. Then a
**Kustomize** layout — plain, un-templated manifests in a `base/` that per-environment
**overlays** patch (`dev` gets 1 replica, `prod` gets 5 and its own namespace). You've been
running `kubectl apply -f` one file at a time; this lab is about turning a pile of YAML into a
*unit you can version, parameterize, and ship*. The point isn't nginx or FastAPI — it's the two
dominant ways the ecosystem answers "how do I deploy the same thing to three environments without
copy-pasting YAML?"

> **The one idea (Kelsey):** both tools exist to kill duplicated YAML, but they take opposite
> bets. **Helm templates** — it *generates* manifests from variables, so the source is no longer
> valid YAML and you must render it to see the truth. **Kustomize patches** — the base is always
> real, applyable YAML and overlays layer changes on top. Templating buys power; patching buys
> readability. Every section below is one of those two bets in action.

## 1. Helm — templated charts

Helm is a templating engine + release manager. A **chart** is a directory of templated YAML +
a `values.yaml` of defaults. You don't `apply` a chart — you `install` it, which renders the
templates with your values and tracks the result as a **release** you can later upgrade or
delete as one atomic thing.

```bash
helm create mychart             # scaffold a fully-working example chart
tree mychart
# Chart.yaml, values.yaml, templates/*, ...

helm install dev ./mychart           # render + apply; 'dev' is the RELEASE name, not an env
helm list                            # show installed releases and their current revision
helm upgrade dev ./mychart --set replicaCount=3   # re-render with an overridden value
helm rollback dev 1                  # revert the 'dev' release to revision 1
helm uninstall dev                   # delete every object Helm created for this release
```

- `helm create mychart` scaffolds a **complete, deployable** chart (a Deployment, Service,
  ServiceAccount, HPA, ingress, tests) — not an empty skeleton. It's meant to be edited down,
  not built up from nothing.
- `helm install dev ./mychart` — `dev` is the **release name**, the handle Helm uses to track
  this installation. It is *not* an environment; you could install the same chart twice as
  `dev` and `staging` into the same cluster and Helm keeps them separate.
- `--set replicaCount=3` overrides one value inline for this upgrade without editing
  `values.yaml`. For more than a key or two, use `--values myfile.yaml` instead.
- `helm rollback dev 1` works because Helm stores **every revision's rendered manifest** (in a
  Secret in-cluster by default). Rollback re-applies an old revision's output — like Lab 03's
  `rollout undo`, but for the whole release, not one Deployment.

**What you should see:** `helm list` shows one release named `dev` with `REVISION 1`; after the
`upgrade` it's `REVISION 2`; after `rollback dev 1` it's `REVISION 3` (rollback creates a *new*
revision whose content equals revision 1 — it doesn't rewind the counter). `helm uninstall`
removes all of it. The mental model: a release is a *versioned deployment of a chart*, and the
revision number is your undo history.

Template syntax uses Go templates + Sprig functions (Sprig = a library of extra template helpers
Helm bundles in; you don't install it):

```yaml
# templates/deployment.yaml
spec:
  replicas: {{ .Values.replicaCount }}                      # pulled from values.yaml (or --set)
  template:
    spec:
      containers:
        - name: {{ .Chart.Name }}                           # built-in: the chart's own name
          image: "{{ .Values.image.repo }}:{{ .Values.image.tag }}"   # repo + tag composed from values
```

What the `{{ }}` are doing, and the traps they hide:

- **`.Values.X`** reads from `values.yaml`, overridable by `--set`/`--values`. **`.Chart.X`**
  reads chart metadata (name, version). These are the two objects you'll touch 95% of the time;
  there's also `.Release` (the release name/namespace) and `.Files`.
- **A rendered template is no longer valid YAML.** Open `templates/deployment.yaml` in an editor
  and it won't parse — the `{{ }}` aren't YAML. This is Helm's core tradeoff: you can't read the
  source to know what gets applied; you have to *render* it (next block). That's the price of
  templating power.
- **Gotcha — quote your string values.** `image: "{{ .Values.image.tag }}"` is quoted for a
  reason: a tag like `1.27` renders to the YAML number `1.27` (so `1.30` would become `1.3`),
  and `latest` is fine but `y`/`no`/`on` parse as booleans. Quoting forces a string. This is the
  single most common chart bug for beginners.

Debug — never install blind:

```bash
helm template ./mychart --values values.yaml    # render to stdout WITHOUT touching the cluster
helm install --dry-run --debug dev ./mychart     # full install simulation, prints rendered YAML + notes
```

- `helm template` runs the templating engine locally and prints the resulting manifests — no
  apiserver involved. This is how you *see the truth* a template hides, and it's the bridge to
  GitOps (render to YAML, commit that).
- `--dry-run --debug` goes further: it asks the apiserver to validate (but not persist) the
  release, so it catches schema errors `helm template` alone misses, and `--debug` prints the
  computed values and rendered output.

**What you should see:** both commands print fully-resolved YAML — `replicas: 1`,
`image: "nginx:..."` — with no `{{ }}` left. If you see `<no value>` somewhere, a `.Values` key
your template referenced is missing from `values.yaml`. Eyeball this output before every install.

**Kelsey's warning:** Helm is powerful and can make simple apps hard to reason about. Use it when
you need the reusability; avoid it when a single manifest will do. If you can't
`helm template | kubectl apply -f -` and eyeball the output, you're in trouble.

## 2. Using upstream charts

The biggest reason to learn Helm isn't packaging *your* apps — it's installing *other people's*.
Postgres, Prometheus, cert-manager, ingress controllers all ship as charts. A **repo** is just a
URL serving an index of charts; you add it, then install from it by `repo/chart` name.

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami   # register a chart repo under the alias 'bitnami'
helm repo update                                           # refresh the local index of all added repos
helm search repo postgres                                  # find charts matching 'postgres' in added repos
helm install pg bitnami/postgresql --values my-values.yaml  # install chart 'postgresql' as release 'pg'
```

- `helm repo add <alias> <url>` is a one-time registration; the alias is how you'll reference
  charts (`bitnami/postgresql`).
- `helm repo update` re-downloads each repo's index — run it before searching/installing if you
  want the latest chart versions, same as `apt update`.
- `--values my-values.yaml` is how you configure an *upstream* chart: you don't edit their
  templates, you supply your own values file overriding their defaults (passwords, storage size,
  replica count). Read the chart's `values.yaml` to learn what knobs exist.

**What you should see:** `helm search repo postgres` lists matching charts with their chart and
app versions; `helm install pg ...` prints a `NOTES.txt` block (the chart's post-install
instructions — connection strings, how to get the password). That `NOTES` output is a chart
feature; well-made charts tell you exactly how to use what they just deployed.

## 3. Kustomize — overlays, no templates

Kustomize is built into `kubectl` (the `-k` flag, and `kubectl kustomize`). It takes the opposite
bet from Helm: **no templates, no variables — every file is real, applyable YAML.** The pattern
is a `base/` of plain manifests plus `overlays/{dev,prod}/` that *patch* the base for each
environment. Because the base is valid YAML, you can `kubectl apply -f base/` directly and it
works — the overlay only layers changes on top.

```
app/
├── base/
│   ├── kustomization.yaml        # lists which manifests make up the base
│   ├── deployment.yaml           # plain, applyable YAML (no {{ }})
│   └── service.yaml
└── overlays/
    ├── dev/
    │   ├── kustomization.yaml     # points at ../../base, applies dev tweaks
    │   └── replicas.yaml
    └── prod/
        └── kustomization.yaml     # points at ../../base, applies prod tweaks
```

The `kustomization.yaml` is the control file Kustomize looks for in any directory you build. The
base's just declares its members:

```yaml
# base/kustomization.yaml
resources:                  # the set of manifests this base is composed of
  - deployment.yaml
  - service.yaml
```

The overlay imports the base, then declares the differences — no patch file needed for the
common cases, because Kustomize has built-in transformers:

```yaml
# overlays/prod/kustomization.yaml
resources:
  - ../../base              # pull in the entire base as the starting point
namespace: prod             # rewrite EVERY resource's namespace to 'prod' on build
replicas:                   # override the replica count of a named workload...
  - name: web               # ...the Deployment named 'web'...
    count: 5                # ...to 5 (the base might say 1)
images:                     # swap a container image without editing the base manifest
  - name: myapp             # match containers using image 'myapp'...
    newTag: "1.2.3"         # ...and pin them to tag 1.2.3
```

What each transformer is doing, and the gotchas:

- **`resources: [../../base]`** makes the overlay *include* the base — the overlay's output is
  the base plus every transform below it. This is composition, not copy: fix a typo in the base
  and both overlays inherit it.
- **`namespace: prod`** rewrites the `metadata.namespace` of every resource on build. Note your
  base manifests should usually *omit* `namespace` so the overlay can set it — hardcode it in the
  base and you're fighting the overlay.
- **`replicas` / `images`** target resources **by name/image, not by patch file**. `name: web`
  must match an existing Deployment's `metadata.name` in the base; `name: myapp` must match the
  *current image name* a container uses. A typo here is silent — Kustomize matches nothing and
  changes nothing, no error.
- **Gotcha — `newTag` is quoted** for the same reason as Helm's tag: `1.2.3` is fine but a bare
  `1.30` would be read as a number. Quote tags.

Build and apply:

```bash
kubectl apply -k overlays/prod      # build the overlay AND apply the result to the cluster
kubectl kustomize overlays/prod     # build (render) the overlay to stdout — apply nothing
```

- `apply -k <dir>` is `kubectl kustomize <dir> | kubectl apply -f -` in one step: it builds the
  overlay (base + transforms) and applies the rendered output. The `-k` is the Kustomize-aware
  cousin of `-f`.
- `kubectl kustomize <dir>` is the **render-only** command — Kustomize's answer to
  `helm template`. Always run this first to *see* what `-k` would apply.

**What you should see:** `kubectl kustomize overlays/prod` prints the full base manifests with
the prod transforms baked in — every object now has `namespace: prod`, the `web` Deployment shows
`replicas: 5`, and matched containers show tag `1.2.3`. No `{{ }}`, no `<no value>` — it's just
final YAML, which is exactly Kustomize's selling point: the rendered output is trivially diffable
against what's in `base/`.

## 4. When to use which

| Need | Tool |
|------|------|
| Install third-party apps (Postgres, Prometheus) | Helm |
| Manage your own small/medium apps across envs | Kustomize |
| Complex parameterization + conditionals | Helm |
| GitOps with cleanly rendered manifests | Kustomize (or Helm rendered via `helm template` into ArgoCD) |

(GitOps = deploy by committing manifests to git and letting a tool like ArgoCD sync them to the
cluster.) The dividing line is the bet from the top of the lab: reach for **Helm** when you need
real logic — conditionals, loops, dozens of knobs, or you're consuming someone else's chart.
Reach for **Kustomize** when the output needs to stay readable and diffable and the variation
between environments is "a few fields differ." Many teams use both: Helm for upstream
dependencies, Kustomize for their own services.

## 5. Practice

These convert the Lab-03→10 capstone (`manifests/fastapi-redis.yaml` — the `demo` namespace with
`api` at 2 replicas, `cache`, their Services, and the `api-config` ConfigMap) into each tool, so
you internalize the difference by doing the same job twice.

1. Convert the FastAPI/Redis stack to a tiny Helm chart with configurable replicas + image tag.
   (Move `api`'s `replicas: 2` and `image: learn-k8s/api:0.1` into `values.yaml`; reference them
   as `{{ .Values.replicaCount }}` and `"{{ .Values.image.repo }}:{{ .Values.image.tag }}"` —
   quote that image string.)
2. Also convert it to Kustomize with dev/prod overlays (dev: replicas=1, prod: replicas=3,
   different namespace). (Base = the manifests with `namespace` removed; each overlay sets
   `namespace:` and a `replicas:` entry targeting `name: api`.)
3. `helm template` the chart and `diff` against `kubectl kustomize`. Which is easier to read?
   (You're directly comparing the templating bet against the patching bet — the whole point of
   the lab in one `diff`.)

## Next

→ You've finished the core Kubernetes track: pods, controllers, services, config, storage,
ingress, probes, RBAC, observability, and now packaging. From here the curriculum builds *on*
this foundation — gateways, AI inference, and agents — all of which ship as the charts and
overlays you just learned to read.

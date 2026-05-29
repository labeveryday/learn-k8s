# Lab 02 — Deploy on Kubernetes

**What you'll build:** an LLM inference server reachable at a stable Kubernetes
Service name (`ollama.llm.svc.cluster.local`) — by **two different routes**.
**Path A** points the Service at the ollama already running on your Mac (zero image pulls,
zero model re-downloads — perfect for hotel wifi). **Path B** runs ollama or vLLM as real
Pods inside the cluster. The deliverable looks the same to a client either way; what changes
is what sits *behind* the Service. That swap-without-the-client-noticing is the whole lesson.

> **The one idea (Kelsey):** "An LLM server is just a Pod. A big Pod, but a Pod." A Service
> is an *indirection* — clients talk to a name, and the name can resolve to a process on your
> Mac (Path A) or to in-cluster Pods (Path B). The AI-ness is irrelevant to Kubernetes; it's
> a routing problem you already know how to solve.

You have two paths. **Path A (recommended for hotel wifi / no re-downloads): proxy to your Mac's ollama.** Path B runs ollama or vLLM as Pods inside the cluster.

---

## Path A — Use your host's ollama (no image pulls)

Conceptually: an `ExternalName` Service inside K8s is just cluster-DNS pointing at `host.docker.internal`, which Colima/Docker maps back to your Mac. Pods inside the cluster talk to your already-running ollama on `localhost:11434` via a normal K8s Service name.

The shape — a request leaving a Pod and ending up at your Mac:

```
Pod ──► ollama.llm.svc.cluster.local ──(CNAME)──► host.docker.internal ──► your Mac :11434
        (a normal Service name)        (ExternalName)   (Docker/Colima maps host→Mac)
```

Nothing inside the cluster actually runs ollama. The Service is pure DNS — a signpost, not a destination.

### 1. Make ollama listen on all interfaces

By default ollama binds to `127.0.0.1`, which Pods inside the VM can't reach. Bind to `0.0.0.0`:

This lab's Path A assumes macOS. Pick one:

```bash
# macOS menubar app: launchctl setenv sets a system-wide env var the app reads on launch.
launchctl setenv OLLAMA_HOST 0.0.0.0:11434

# Starting ollama from a terminal (any OS): just set the env var inline.
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

- `127.0.0.1` (loopback) accepts connections *only* from the same machine's own stack — the
  kind/Colima VM is a separate network namespace, so it sees a closed port. `0.0.0.0` means
  "bind every interface," which is what lets the VM-to-host hop land.
- The two forms do the same thing differently: `launchctl setenv` sets the var system-wide so
  the menubar **app** reads it at launch; the inline form sets it for one `ollama serve` you
  start yourself. Use whichever matches how you run ollama — not both.

Restart the ollama app/process. Verify it's reachable:

```bash
# From your Mac:
curl http://localhost:11434/api/tags

# From a container (proves the VM can reach the host):
docker run --rm curlimages/curl:latest \
  curl -s http://host.docker.internal:11434/api/tags
```

- The first curl proves ollama is up *on the host*. The second is the real test: it runs
  inside a throwaway container (`--rm` deletes it on exit) and hits `host.docker.internal` —
  the magic hostname Docker/Colima resolves to "the Mac running this VM." If *that* one
  returns models, the VM→host path works and the Service will too.

**What you should see:** both curls return the same JSON list of your already-pulled models.
If the host one works but the container one hangs, ollama is still bound to `127.0.0.1` —
go back and set `OLLAMA_HOST` (the #1 Path A failure).

### 2. Apply the Service

This is the entire Path A manifest (`04-vllm/manifests/ollama-host.yaml`) — the part that matters is tiny:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: llm                       # scope all Phase 4 objects to their own namespace
---
apiVersion: v1
kind: Service
metadata:
  name: ollama                    # the DNS name Pods will use: ollama.llm.svc.cluster.local
  namespace: llm
spec:
  type: ExternalName              # NOT a normal Service — no Pods, no Endpoints, just a CNAME
  externalName: host.docker.internal   # cluster DNS resolves the Service name to THIS instead
  ports:
    - port: 11434                 # advisory only for ExternalName (DNS doesn't carry ports)
      targetPort: 11434
      protocol: TCP
```

- **`type: ExternalName`** is the unusual bit. A normal Service has a cluster IP and a set of
  Pod **Endpoints**; an ExternalName Service has *neither* — it's a DNS CNAME. Resolving
  `ollama.llm.svc.cluster.local` returns `host.docker.internal`, which then resolves to your Mac.
- **Gotcha:** the `ports` block is decorative for ExternalName Services — DNS records carry no
  port, so the client still has to dial `:11434` explicitly (the curls below do). Don't expect
  the Service to "redirect" the port for you.

```bash
kubectl apply -f 04-vllm/manifests/ollama-host.yaml   # create the namespace + ExternalName Service
kubectl get svc -n llm                                # confirm it exists; note TYPE=ExternalName
```

**What you should see:** one Service of `TYPE ExternalName`, with `EXTERNAL-IP` showing
`host.docker.internal` and **no** `CLUSTER-IP` (it's `<none>`). That blank CLUSTER-IP is the
tell that this Service is DNS-only.

### 3. Test from inside the cluster

```bash
kubectl -n llm run tmp --rm -it --image=curlimages/curl:latest -- sh
# inside the pod:
curl http://ollama:11434/api/tags
curl http://ollama.llm.svc.cluster.local:11434/v1/models

curl http://ollama:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"<your-model-name>","messages":[{"role":"user","content":"hello"}]}'
```

- `kubectl run tmp --rm -it` launches a one-off Pod, attaches your terminal to it (`-it`),
  and deletes it the moment you exit (`--rm`) — a disposable shell *inside* the cluster, so
  these curls test real Pod→Service DNS, not your Mac's.
- `http://ollama:...` and `http://ollama.llm.svc.cluster.local:...` reach the **same** Service.
  The short form works because the Pod is in the `llm` namespace, so DNS auto-completes the
  rest. The FQDN is what you'd use from *another* namespace (Practice 1).
- The third curl is the actual inference call — same OpenAI-compatible `/v1/chat/completions`
  shape you used in Lab 01, just aimed at the Service name instead of `localhost`.

(Substitute one of the model names you saw in `/api/tags`.)

**What you should see:** `/api/tags` lists your models, `/v1/models` lists them in OpenAI
format, and the chat call returns a completion — all served by ollama *on your Mac*, reached
through a cluster Service. Type `exit` to drop the throwaway Pod.

You just used a Kubernetes Service to abstract a backend that lives outside the cluster. This is the same pattern teams use to gradually migrate workloads — point the Service at an existing VM, then later replace it with in-cluster Pods. Clients never change.

### 4. What this taught you

- **ExternalName Service:** cluster DNS as a CNAME to anything.
- **Service-as-abstraction:** clients use `ollama.llm.svc.cluster.local`; the *backend* is replaceable.
- **`host.docker.internal`:** the standard hostname Docker/Colima exposes for "the Mac running this VM."

When you later get GPU hardware, you swap `ollama-host.yaml` for a real `Deployment + Service` and clients keep working.

---

## Path B — Run the LLM server as Pods (do this when you have bandwidth)

Two variants in `manifests/`:

- `ollama.yaml` — full Deployment running `ollama/ollama`, with PVC for models. Image is ~500 MB; you'll re-pull models inside the pod. Use this when you want to learn Pod/PVC/probes for stateful AI workloads.
- `vllm.yaml` — vLLM CPU mode. Image is ~12 GB. Demo value only on Mac; real fit is GPUs (Lab 04).

### The in-cluster ollama manifest, dissected

`04-vllm/manifests/ollama.yaml` is three objects: a **PVC** (where model weights live), a
**Deployment** (the Pod blueprint + probes), and a **Service** (stable name). The fields that
carry weight:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim       # durable disk for models, so a Pod restart doesn't re-download
metadata:
  name: ollama-models
  namespace: llm
spec:
  accessModes: [ReadWriteOnce]    # RWO = mountable by ONE node at a time (drives Recreate below)
  resources:
    requests:
      storage: 10Gi
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ollama
  namespace: llm
spec:
  replicas: 1                     # one inference Pod (RWO volume can't be shared anyway)
  selector: { matchLabels: { app: ollama } }
  strategy:
    type: Recreate                # PVC is RWO; avoid dual-attach
  template:
    metadata:
      labels: { app: ollama }     # MUST match the selector above (lab-03 trap)
    spec:
      containers:
        - name: ollama
          image: ollama/ollama:latest
          imagePullPolicy: IfNotPresent   # use the image you kind-loaded; don't re-pull
          ports:
            - containerPort: 11434
          env:
            - name: OLLAMA_HOST
              value: "0.0.0.0:11434"       # bind all interfaces so the Service can reach it
          volumeMounts:
            - name: models
              mountPath: /root/.ollama     # where ollama stores models → backed by the PVC
          resources:
            requests: { cpu: "1",   memory: "2Gi" }   # scheduler reserves at least this
            limits:   { cpu: "4",   memory: "6Gi" }   # cgroup hard cap (mem over → OOM-kill)
          startupProbe:
            httpGet: { path: /api/tags, port: 11434 }
            failureThreshold: 60           # 60 * 5s = up to 5 min for model load before failing
            periodSeconds: 5
          readinessProbe:
            httpGet: { path: /api/tags, port: 11434 }   # gates whether the Service sends traffic
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /api/tags, port: 11434 }   # restarts the Pod if it wedges
            periodSeconds: 30
      volumes:
        - name: models
          persistentVolumeClaim:
            claimName: ollama-models       # wire the volume to the PVC above
---
apiVersion: v1
kind: Service
metadata:
  name: ollama                    # SAME name as Path A's ExternalName — clients don't change
  namespace: llm
spec:
  selector: { app: ollama }       # now a REAL selector: routes to Pods labeled app: ollama
  ports:
    - port: 11434
      targetPort: 11434
```

- **`strategy: Recreate`** is the consequence of `ReadWriteOnce`: an RWO volume attaches to one
  node at a time, so the default RollingUpdate (which briefly runs old + new Pods together)
  would deadlock on the volume. `Recreate` kills the old Pod *before* starting the new one —
  you accept a few seconds of downtime to avoid a dual-attach hang.
- **`startupProbe` gates the others.** Model load is slow; if `livenessProbe` ran during
  startup it would see a not-yet-serving port and kill the Pod in a loop. The startup probe's
  `failureThreshold: 60 * periodSeconds: 5` = ~5 min of grace before liveness/readiness even
  begin. **Gotcha:** that's why a quiet first boot is normal — don't Ctrl-C it.
- **Same Service name as Path A** (`ollama` in `llm`). That's the punchline: the *only* change
  between paths is what the name resolves to (an ExternalName CNAME vs. a real Pod selector).
  Clients that hit `ollama.llm.svc.cluster.local` never know the difference (Practice 3).

```bash
# In-cluster ollama:
docker pull ollama/ollama
kind load docker-image ollama/ollama --name learn
kubectl apply -f 04-vllm/manifests/ollama.yaml
kubectl -n llm rollout status deploy/ollama
kubectl -n llm exec deploy/ollama -- ollama pull tinyllama
kubectl -n llm port-forward svc/ollama 11434:11434
curl http://localhost:11434/v1/models
```

- `kind load docker-image ... --name learn` copies the image from your host's Docker into the
  kind node's image store. Without this, the Pod's `IfNotPresent` policy finds nothing locally
  and a kind cluster (offline by default) can't pull it — you'd get `ErrImagePull`.
- `rollout status` blocks until the Pod is Ready, so the next command doesn't race ahead.
- `exec deploy/ollama -- ollama pull tinyllama` pulls the **model** *inside* the running Pod
  (the image ships the engine, not weights) — it lands on the PVC, so it survives restarts.
- `port-forward svc/ollama 11434:11434` tunnels your Mac's `localhost:11434` to the Service,
  so the final curl can reach the in-cluster Pod from outside. Leave it running in its own
  terminal — Lab 03's load test reuses it.

Expect a JSON list with `tinyllama` in it. First startup can take a few minutes while the model downloads — the manifest's `startupProbe` allows for this, so don't Ctrl-C if it seems quiet.

### The vLLM variant (heavier; CPU demo only)

`04-vllm/manifests/vllm.yaml` is the same PVC + Deployment + Service shape, with two
LLM-specific twists: an **initContainer** that downloads weights before the server starts, and
**vLLM serving flags** tuned for CPU. The load-bearing parts:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vllm
  namespace: llm
spec:
  replicas: 1
  selector: { matchLabels: { app: vllm } }
  strategy:
    type: Recreate                # same RWO reasoning as ollama
  template:
    metadata:
      labels: { app: vllm }
    spec:
      initContainers:
        - name: fetch-model       # runs to COMPLETION before the vllm container starts
          image: python:3.11-slim
          command:
            - sh
            - -c
            - |
              set -e
              if [ ! -d /models/tinyllama/snapshots ]; then   # idempotent: skip if already fetched
                pip install --no-cache-dir huggingface_hub
                python -c "from huggingface_hub import snapshot_download; snapshot_download('TinyLlama/TinyLlama-1.1B-Chat-v1.0', local_dir='/models/tinyllama')"
              fi
          volumeMounts:
            - name: models
              mountPath: /models  # writes weights onto the shared PVC the server will read
      containers:
        - name: vllm
          image: vllm/vllm-openai:latest
          imagePullPolicy: IfNotPresent
          args:
            - --model=/models/tinyllama   # load by PATH (the initContainer's download dir)
            - --dtype=float32             # CPU can't do float16 well → full precision
            - --device=cpu                # no GPU on a Mac kind cluster
            - --max-model-len=1024        # cap context to 1024 tokens so it fits in RAM
            - --host=0.0.0.0
            - --port=8000
          ports:
            - containerPort: 8000         # vLLM serves on 8000 (ollama used 11434)
          volumeMounts:
            - name: models
              mountPath: /models
          resources:
            requests: { cpu: "2", memory: "4Gi" }
            limits:   { cpu: "4", memory: "8Gi" }
          startupProbe:
            httpGet: { path: /v1/models, port: 8000 }
            failureThreshold: 120         # 120 * 10s = up to 20 min — vLLM CPU boot is SLOW
            periodSeconds: 10
          readinessProbe:
            httpGet: { path: /v1/models, port: 8000 }
            periodSeconds: 10
          livenessProbe:
            httpGet: { path: /v1/models, port: 8000 }
            periodSeconds: 30
      volumes:
        - name: models
          persistentVolumeClaim:
            claimName: vllm-models
---
apiVersion: v1
kind: Service
metadata:
  name: vllm
  namespace: llm
spec:
  selector: { app: vllm }
  ports:
    - port: 8000
      targetPort: 8000
```

- **The initContainer pattern:** Kubernetes runs every initContainer to completion *before*
  any app container starts. Here it downloads the TinyLlama weights to the PVC, so the `vllm`
  container finds them at `/models/tinyllama` on boot. The `if [ ! -d .../snapshots ]` guard
  makes a re-create skip the download — the weights already sit on the persistent volume.
- **vLLM loads by path, not by name** (`--model=/models/tinyllama`) — same quirk as Lab 01, so
  the `model` field in chat requests echoes that path. The CPU flags (`--dtype=float32`,
  `--device=cpu`, `--max-model-len=1024`) are the trio that makes vLLM runnable without a GPU;
  on real hardware you'd drop them (Lab 04).
- **Gotcha:** `failureThreshold: 120 * 10s` = a **20-minute** startup window. vLLM CPU boot is
  genuinely that slow on a Mac — the long startup probe is deliberate, not a typo.

> The vLLM image is large — this lab's note above says **~12 GB**, while the Phase 4 README
> quotes **~5–10 GB**. Either way it's the heavy path; deploy it only when you have bandwidth.
> (Flagged inconsistency — not changing either number here.)

---

## Mapping back to what you know

Whichever path you took, you're using:

- **Service** for stable DNS + decoupling.
- **Namespace** for scoping.
- **ConfigMap / probes / resources** (in Path B) — same patterns as the FastAPI/Redis app.

The "AI-ness" of the workload is irrelevant to K8s. It's just another HTTP server.

## Practice

1. **Path A:** from a pod in another namespace (e.g., `default`), curl `ollama.llm.svc.cluster.local:11434/api/tags`. Cross-namespace DNS works because the FQDN includes the namespace.
2. **Path A:** `kubectl describe svc -n llm ollama`. Note: no Endpoints (ExternalName Services don't use endpoints — they're DNS-only).
3. **Switch backends:** delete the ExternalName Service. Apply `ollama.yaml` (in-cluster). Clients using the same DNS name keep working — that's the point of Services.
4. Point the Python `openai` client at the Service:
   ```python
   # from a pod with internet to pip install openai, or run on your Mac through port-forward:
   from openai import OpenAI
   c = OpenAI(base_url="http://ollama.llm.svc.cluster.local:11434/v1", api_key="none")
   print(c.chat.completions.create(model="<your-model>", messages=[{"role":"user","content":"hi"}]))
   ```

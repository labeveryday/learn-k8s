# Lab 02 — Deploy on Kubernetes

You have two paths. **Path A (recommended for hotel wifi / no re-downloads): proxy to your Mac's ollama.** Path B runs ollama or vLLM as Pods inside the cluster.

---

## Path A — Use your host's ollama (no image pulls)

Conceptually: an `ExternalName` Service inside K8s is just cluster-DNS pointing at `host.docker.internal`, which Colima/Docker maps back to your Mac. Pods inside the cluster talk to your already-running ollama on `localhost:11434` via a normal K8s Service name.

### 1. Make ollama listen on all interfaces

By default ollama binds to `127.0.0.1`, which Pods inside the VM can't reach. Bind to `0.0.0.0`:

```bash
# Persistent (re-applied on next launch of the ollama app):
launchctl setenv OLLAMA_HOST 0.0.0.0:11434

# Or, if you run ollama from the CLI:
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

Restart the ollama app/process. Verify it's reachable:

```bash
# From your Mac:
curl http://localhost:11434/api/tags

# From a container (proves the VM can reach the host):
docker run --rm curlimages/curl:latest \
  curl -s http://host.docker.internal:11434/api/tags
```

You should see your already-pulled models in both.

### 2. Apply the Service

```bash
kubectl apply -f 04-vllm/manifests/ollama-host.yaml
kubectl get svc -n llm
```

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

(Substitute one of the model names you saw in `/api/tags`.)

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

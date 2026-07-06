# Phase 3 Exercises & Capstone

## Drills (do without peeking)

1. Create a namespace `lab`. Deploy nginx with 3 replicas. Expose via a ClusterIP Service. From a throwaway `curlimages/curl` pod, reach it by DNS.
2. Rolling-update the nginx image. Watch two ReplicaSets during the transition.
3. Set `resources.limits.memory: 16Mi` on the nginx container. Observe OOMKills.
4. Create a ConfigMap with a key `GREETING=hi`. Mount it as env and as a file. Verify both inside a pod.
5. Create a Secret with `PASSWORD=s3cret`. Mount it as a file. `cat` it from inside.
6. Create a PVC, mount it in a Pod, write a file, delete the Pod, recreate, read the file.
7. Write a Deployment that fails readiness but passes liveness. Watch endpoints update.
8. Create a ServiceAccount `viewer` with list/get on pods only. Mount it in a pod using `bitnami/kubectl`. Confirm it can list but not delete.
9. Enable the `restricted` PodSecurity label on a namespace. Try to deploy a pod running as root. Confirm rejection.

## Capstone: Redeploy the FastAPI/Redis app on Kubernetes

Goal: take your Phase 2 Compose project and run it on kind.

Steps:

1. Build the image in the local Docker.
   ```bash
   cd 02-docker/project-fastapi-redis
   docker build -t learn-k8s/api:0.1 .
   ```
2. Load it into the kind cluster so it's visible without a registry:
   ```bash
   kind load docker-image learn-k8s/api:0.1 --name learn
   ```
3. Apply the manifest:
   ```bash
   kubectl apply -f 03-kubernetes/manifests/fastapi-redis.yaml
   kubectl -n demo get pods,svc
   ```
4. Port-forward and test:
   ```bash
   kubectl -n demo port-forward svc/api 8000:8000
   curl http://localhost:8000/hits
   ```
5. Break Redis: `kubectl -n demo delete pod -l app=cache`. Observe API readiness flip until Redis recovers.
6. Scale: `kubectl -n demo scale deploy/api --replicas=5`. Hit `/` repeatedly and note `host` changes. Each Pod returns its own hostname (= Pod name) in `host`; seeing it vary proves the Service spread requests across the 5 replicas. It's not strict round-robin; kube-proxy picks an endpoint at random per connection.
7. Add an HPA. An **HPA** (HorizontalPodAutoscaler) is a controller that watches a metric and adds/removes replicas to hit a target: here, keep average CPU near 50% of each Pod's CPU *request*, between 2 and 10 replicas. It needs metrics-server (installed in lab-10); without it, `kubectl get hpa` shows `TARGETS: <unknown>/50%` and never scales. The capstone Pods already set CPU requests, so once metrics-server is up you'll see a real percentage.
   ```bash
   kubectl -n demo autoscale deploy/api --min=2 --max=10 --cpu-percent=50
   kubectl -n demo get hpa
   ```
   To see it scale, generate load (e.g. a busybox pod looping `wget -q -O- http://api.demo.svc.cluster.local:8000/` in the `demo` namespace) and watch `kubectl -n demo get hpa -w`.
8. Put it behind an Ingress (if you did Lab 07).

## Self-check

- Explain what a Pod is, and why it's the atom instead of a container.
- Explain the reconcile loop: spec, status, controller.
- Trace a packet from `curl http://api.demo.svc.cluster.local:8000` to the app.
- Explain: probe types, signals on termination, graceful period.
- Given a stuck `Pending` pod, list 3 likely causes and how to diagnose each.

If all of the above feel obvious, you're ready for Phase 4.

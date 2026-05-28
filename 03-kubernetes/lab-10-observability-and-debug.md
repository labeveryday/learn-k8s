# Lab 10 — Observability and Debugging

## 1. The debug loop (memorize)

```bash
kubectl get <kind>                    # does it exist?
kubectl describe <kind> <name>        # what's its status and events?
kubectl logs <pod> [-c <container>]   # what is it saying?
kubectl logs <pod> --previous         # what did the crashed container say?
kubectl exec -it <pod> -- sh          # look inside
kubectl get events --sort-by=.lastTimestamp
```

Never guess. `describe` and events tell you 80% of the truth.

## 2. Logs

```bash
kubectl logs deploy/web              # one pod of the deployment
kubectl logs -l app=web --tail=100   # all pods matching a selector
kubectl logs -f pod/web-abc          # follow
kubectl logs pod/web --all-containers
```

K8s has no built-in log aggregation. In real clusters: Loki, ELK, Datadog, etc.

## 3. Events

```bash
kubectl get events -A --sort-by=.lastTimestamp
kubectl get events --field-selector involvedObject.name=web
```

`describe` prints recent events at the bottom; that's where the truth usually lives (ImagePullBackOff, OOMKilled, FailedScheduling, ...).

## 4. `kubectl debug`

Inject a debug container into a running pod (shares PID namespace with target):

```bash
kubectl debug -it pod/web --image=busybox:1.36 --target=nginx -- sh
# now you can ps, netstat, nsenter into the nginx container
```

Or create a "copy" of a problematic pod to poke at safely:

```bash
kubectl debug pod/web --copy-to=web-debug --container=nginx --image=busybox:1.36 -- sh
```

## 5. Resource metrics

Install metrics-server (kind doesn't by default):

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
# for kind: patch --kubelet-insecure-tls
kubectl patch deploy metrics-server -n kube-system --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'

kubectl top nodes
kubectl top pods
```

Metrics-server powers `kubectl top` and **HPA** (next lab).

## 6. Common failure modes (cheatsheet)

| Symptom | Likely cause |
|---------|--------------|
| `ImagePullBackOff` | wrong image name/tag, private registry no creds |
| `CrashLoopBackOff` | process exits; check logs + previous logs |
| `Pending` forever | no node has resources; describe → events |
| `CreateContainerConfigError` | ConfigMap/Secret missing or key mismatch |
| `OOMKilled` | memory limit too low; bump or fix leak |
| `0/1 READY` but `Running` | readiness probe failing |
| Service returns no response | endpoints empty (selector mismatch) or readiness failing |

## 7. Practice

1. Break a Deployment (wrong image). Diagnose from events only, without looking at YAML.
2. `kubectl top pods` after installing metrics-server. Which pod uses most memory?
3. Use `kubectl debug` to launch a busybox sidecar in a running pod and tcpdump its traffic.

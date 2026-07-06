# kubectl Cheatsheet

## Context / namespace
```
kubectl config get-contexts
kubectl config use-context kind-learn
kubectl config set-context --current --namespace=demo
kubectl get ns
```

## Get / describe (the daily workflow)
```
kubectl get pods                       in current ns
kubectl get pods -A                    all namespaces
kubectl get pods -o wide               include node + ip
kubectl get pods -l app=web            label selector
kubectl get pods -w                    watch
kubectl get pod NAME -o yaml
kubectl get pod NAME -o jsonpath='{.status.podIP}'
kubectl describe pod NAME
kubectl get events --sort-by=.lastTimestamp
kubectl api-resources
kubectl explain deployment.spec.strategy
```

## Logs / exec / debug
```
kubectl logs POD [-c CONTAINER] [-f] [--tail=100] [--previous]
kubectl logs -l app=web --all-containers
kubectl exec -it POD -- sh
kubectl exec -it POD -c CONTAINER -- bash
kubectl debug -it POD --image=busybox --target=CONTAINER
kubectl cp POD:/path/in/pod ./local
kubectl port-forward svc/NAME LOCAL:REMOTE
```

## Apply / delete / scale / rollout
```
kubectl apply -f file.yaml
kubectl apply -k overlays/dev
kubectl delete -f file.yaml
kubectl delete pod NAME --grace-period=0 --force
kubectl scale deploy/NAME --replicas=5
kubectl set image deploy/NAME container=IMG:TAG
kubectl rollout status deploy/NAME
kubectl rollout history deploy/NAME
kubectl rollout undo deploy/NAME [--to-revision=N]
kubectl rollout restart deploy/NAME
```

## Create one-off
```
kubectl run tmp --rm -it --image=curlimages/curl:latest -- sh
kubectl create deployment web --image=nginx
kubectl expose deploy web --port=80 --type=ClusterIP
kubectl create configmap c --from-literal=K=V --from-file=./f
kubectl create secret generic s --from-literal=K=V
kubectl autoscale deploy web --min=1 --max=5 --cpu-percent=70
```

## RBAC checks
```
kubectl auth can-i create deploy
kubectl auth can-i list pods --as=system:serviceaccount:NS:SA
kubectl auth can-i --list
```

## Resources / metrics
```
kubectl top nodes
kubectl top pods
kubectl describe node NAME
```

## Output / scripting
```
-o yaml | json | wide | name
-o jsonpath='{.items[*].metadata.name}'
-o custom-columns=NAME:.metadata.name,IP:.status.podIP
--no-headers
```

## Useful one-liners
```
# All images running in cluster
kubectl get pods -A -o jsonpath='{range .items[*]}{range .spec.containers[*]}{.image}{"\n"}{end}{end}' | sort -u

# Pods not Ready
kubectl get pods -A --no-headers | awk '$3 != "Running" && $3 != "Completed"'

# Last 20 events sorted
kubectl get events -A --sort-by=.lastTimestamp | tail -20

# Show secrets decoded (single key)
kubectl get secret S -o jsonpath='{.data.PASS}' | base64 -d ; echo

# Force re-pull image (no tag change) - restart triggers new pull only if imagePullPolicy=Always
kubectl rollout restart deploy/NAME
```

## kind helpers
```
kind create cluster --name learn --image kindest/node:v1.30.0
kind delete cluster --name learn
kind load docker-image my:tag --name learn
kind get clusters
kind export logs ./logs --name learn
```

## Troubleshooting flow
```
1. kubectl get pods                  is it there?
2. kubectl describe pod NAME         events at the bottom
3. kubectl logs POD --previous       what did the dead one say?
4. kubectl get endpoints SVC         is the Service wired up?
5. kubectl exec into a curl pod      can you reach it from the cluster?
6. kubectl auth can-i ...            is RBAC blocking?
```

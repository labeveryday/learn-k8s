# Lab 05 — ConfigMaps and Secrets

Code in image. Config outside.

## 1. ConfigMap

Key-value or file data, injected into Pods.

```bash
kubectl create configmap app-config \
  --from-literal=LOG_LEVEL=debug \
  --from-literal=GREETING=hello
kubectl get cm app-config -o yaml
```

Or from file:

```bash
kubectl create cm app-files --from-file=./nginx.conf
```

Consume as env:

```yaml
spec:
  containers:
    - name: api
      image: myapp:0.1
      env:
        - name: LOG_LEVEL
          valueFrom:
            configMapKeyRef:
              name: app-config
              key: LOG_LEVEL
      envFrom:
        - configMapRef:
            name: app-config
```

Consume as file:

```yaml
spec:
  volumes:
    - name: cfg
      configMap:
        name: app-files
  containers:
    - name: nginx
      image: nginx:1.27-alpine
      volumeMounts:
        - name: cfg
          mountPath: /etc/nginx/conf.d
```

## 2. Secret

Same shape as ConfigMap, values base64-encoded at rest, handled slightly more carefully.

```bash
kubectl create secret generic db-creds \
  --from-literal=USER=postgres \
  --from-literal=PASS=s3cret
kubectl get secret db-creds -o yaml        # values are base64
echo "czNjcmV0" | base64 -d                 # → s3cret
```

**Base64 is not encryption.** Secrets are obfuscated at rest unless you enable etcd encryption. Use external managers (Vault, SOPS, cloud KMS) for anything serious.

Inject as env (be wary — env leaks into child processes and `ps`):

```yaml
env:
  - name: DB_PASS
    valueFrom:
      secretKeyRef:
        name: db-creds
        key: PASS
```

Or as file (better for most secrets):

```yaml
volumes:
  - name: creds
    secret:
      secretName: db-creds
volumeMounts:
  - name: creds
    mountPath: /etc/creds
    readOnly: true
```

## 3. Immutable configs (perf)

For large/frequently-mounted ConfigMaps, `immutable: true` prevents updates and makes the kubelet skip watch overhead.

## 4. Practice

1. Create a ConfigMap `greeting` with a key `MESSAGE`. Mount it as env in a new Deployment that just does `env && sleep`. Verify with `kubectl exec ... env`.
2. Create a Secret for Redis password. Mount as file. `exec` in and `cat` it.
3. Update the ConfigMap. Does the running Pod see the new value? (Answer: env — no; mounted file — eventually, yes. Try it.)

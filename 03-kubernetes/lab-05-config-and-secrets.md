# Lab 05: ConfigMaps and Secrets

**What you'll build:** two configuration objects, a `ConfigMap` (plaintext key/values) and a
`Secret` (the same shape, base64-encoded), then learn the four ways to feed them into a Pod:
as individual env vars, as a bulk env import, and as files mounted into a volume. This is the
**separation of config from code**. The same container image should run in dev, staging, and
prod with nothing changed but the config injected at runtime.

> **The one idea (12-factor):** *code in image, config outside.* You bake the app into an
> immutable image once, then vary its behavior per environment by injecting config and
> secrets at the boundary, never by editing the image or hard-coding values inside it. A
> ConfigMap is that injection point for non-sensitive data; a Secret for sensitive.

## 1. The two objects: same shape, different handling

A ConfigMap and a Secret are both maps of keys to values that live in the apiserver. The
difference is *handling*, not structure:

```
ConfigMap ──► plaintext in etcd, shown in clear, for non-sensitive config
Secret    ──► base64 in etcd, redacted in describe, mounted as tmpfs (RAM)
```

- A **ConfigMap** holds non-sensitive config: log levels, hostnames, feature flags, whole
  config files.
- A **Secret** holds sensitive data: passwords, tokens, TLS keys. Values are base64-encoded
  (see the gotcha in section 3: that is *not* encryption), and when mounted as files they land
  on a RAM-backed `tmpfs` instead of disk.

You consume both the same four ways. Learn the mechanics once and they apply to both.

## 2. ConfigMap: create it, then read it back

`kubectl create configmap` builds the object imperatively (no YAML file needed) from literals
you pass on the command line:

```bash
kubectl create configmap app-config \
  --from-literal=LOG_LEVEL=debug \
  --from-literal=GREETING=hello
kubectl get cm app-config -o yaml
```

- `create configmap app-config` names the object `app-config`.
- `--from-literal=KEY=VALUE` adds one key/value pair to the map; repeat the flag per pair.
  (Imperative create is fine for throwaway/learning; in real repos you'd commit a YAML file so
  the config is version-controlled.)
- `-o yaml` dumps the full object the apiserver stored, so you can see what `create` actually
  built.

**What you should see:** a `ConfigMap` whose `data:` block contains your two keys in *plaintext*
(`LOG_LEVEL: debug`, `GREETING: hello`). That plaintext is the tell: a ConfigMap is not for
secrets, and anyone with read access sees the values as-is.

Or build one from a file instead of literals (make a throwaway one first, since there's no
`nginx.conf` in the repo):

```bash
echo "server { listen 80; }" > nginx.conf
kubectl create cm app-files --from-file=./nginx.conf
```

- `--from-file=./nginx.conf` makes the **filename the key** (`nginx.conf`) and the file's
  contents the value. This is how you ship a whole config file into a Pod, not just
  scalar values.

**Gotcha:** `--from-file` keys on the *basename* of the path. `--from-file=./nginx.conf` creates
the key `nginx.conf`, not `./nginx.conf`. When you mount this later (section 5), that key becomes
the filename inside the container, so the file lands at `<mountPath>/nginx.conf`.

## 3. Secret: same idea, base64 at rest

A Secret is created the same way; `generic` is the type for arbitrary key/value secrets (other
types exist for TLS and docker registry creds):

```bash
kubectl create secret generic db-creds \
  --from-literal=USER=postgres \
  --from-literal=PASS=s3cret
kubectl get secret db-creds -o yaml        # values are base64
echo "czNjcmV0" | base64 -d                 # czNjcmV0 is what -o yaml printed for PASS; this decodes it back → s3cret
```

- `secret generic db-creds`: `generic` (a.k.a. `Opaque`) is the catch-all type for your own
  key/values; `--from-literal` works exactly as it did for the ConfigMap.
- `get secret -o yaml` shows the `data:` values **base64-encoded**, not in clear.
- `echo "..." | base64 -d` decodes one of those values back to plaintext, proving base64 is
  trivially reversible.

**What you should see:** the `-o yaml` output shows `PASS: czNjcmV0` (base64), and the decode
prints `s3cret`. That round-trip sets up the next warning.

**Base64 is not encryption.** It's reversible by anyone, with no key. Secrets are only
*obfuscated* at rest unless you enable etcd encryption-at-rest on the cluster. For anything
real, use an external manager (Vault, SOPS, cloud KMS). The Secret object alone is a *handling
convention*, not a security boundary.

## 4. Consume as environment variables

Now feed config into a Pod. Two env styles: pick keys one at a time, or import the whole map.
This is an illustrative snippet (`myapp:0.1` is a stand-in for "your app"; the runnable version
is the capstone `manifests/fastapi-redis.yaml`, not something you copy-paste and apply):

```yaml
spec:
  containers:
    - name: api
      image: myapp:0.1
      env:
        - name: LOG_LEVEL              # the env var name the container will see
          valueFrom:
            configMapKeyRef:
              name: app-config         # which ConfigMap to read from
              key: LOG_LEVEL           # which key in it - value becomes $LOG_LEVEL
      envFrom:
        - configMapRef:
            name: app-config           # import EVERY key as an env var (key = var name)
```

- **`env` + `valueFrom.configMapKeyRef`** pulls *one* key and lets you *rename* it. Use this
  when the env var name differs from the key, or you only want a few keys.
- **`envFrom.configMapRef`** bulk-imports *all* keys in the ConfigMap as env vars named after
  their keys. Less typing, but you get whatever the map contains.

**Gotcha:** env vars are read **once, at container start**. Update the ConfigMap later and the
running Pod keeps the *old* value until it's restarted (you prove this in the Practice section).
A second trap: if a referenced ConfigMap/key doesn't exist, `configMapKeyRef` makes the Pod fail
to start, though you can make missing refs optional with `optional: true`.

The capstone uses the `envFrom` pattern: look at `manifests/fastapi-redis.yaml`, where its
`api` container does `envFrom: [configMapRef: { name: api-config }]` to pull `REDIS_HOST` and
`REDIS_PORT` in one shot. The snippet above is that pattern, isolated.

## 5. Consume as a mounted file

The other path: project the data into the filesystem as files. A ConfigMap (or Secret) becomes
a **volume**, and each key becomes a file under `mountPath`:

```yaml
spec:
  volumes:
    - name: cfg                        # volume name, referenced by volumeMounts below
      configMap:
        name: app-files                # the ConfigMap to project as files
  containers:
    - name: nginx
      image: nginx:1.27-alpine
      volumeMounts:
        - name: cfg                    # must match the volume name above
          mountPath: /etc/nginx/conf.d # each key in app-files lands as a file here
```

- **`volumes[].configMap.name`** declares the ConfigMap as a volume source at the Pod level.
- **`volumeMounts[].mountPath`** is where it appears in the container. Every key in `app-files`
  becomes a file at `<mountPath>/<key>`; so `nginx.conf` from section 2 lands at
  `/etc/nginx/conf.d/nginx.conf`, exactly where nginx auto-loads vhost configs.

**Gotcha:** mounting a volume at `mountPath` **shadows** whatever was already in that directory
in the image; the existing contents are hidden, not merged. Mount at a directory that's safe to
replace, or use `subPath` to project a single file without masking its neighbors.

The Secret version is the same shape, and is **the preferred way to inject secrets** (env vars
leak, per the gotcha):

```yaml
volumes:
  - name: creds
    secret:
      secretName: db-creds             # secret source uses 'secretName', not 'name'
volumeMounts:
  - name: creds
    mountPath: /etc/creds
    readOnly: true                     # secrets should never be writable by the app
```

- **`secret.secretName`**: note the field is `secretName` here, *not* `name` like the ConfigMap
  volume. Easy to fumble.
- **`readOnly: true`**: the app reads creds, never writes them. Secret volumes are also backed
  by `tmpfs` (RAM), so they never touch the node's disk.

And the env form of a Secret, for completeness (be wary: env leaks into child processes and
`ps`, and into many logging/crash-reporting tools):

```yaml
env:
  - name: DB_PASS
    valueFrom:
      secretKeyRef:                    # secretKeyRef, the Secret analog of configMapKeyRef
        name: db-creds
        key: PASS
```

**Why files beat env for secrets:** an env var is inherited by every child process the
container spawns and is visible in `/proc/<pid>/environ` and crash dumps; a mounted file is read
only by code that explicitly opens it. For passwords and tokens, prefer the file mount above.

## 6. Immutable configs (perf)

For large or frequently-mounted ConfigMaps/Secrets, set `immutable: true`:

```yaml
immutable: true                        # object can no longer be edited; only deleted/recreated
```

This does two things: it prevents accidental updates (you must delete and recreate to change
it), and it lets the kubelet **stop watching** the object for changes, removing per-Pod watch
overhead that, at scale, meaningfully loads the apiserver. The trade-off is the auto-update
behavior from section 5 goes away: to change an immutable config you replace it and roll the
Pods.

## 7. Practice

1. Create a ConfigMap `greeting` with a key `MESSAGE`. Mount it as env in a new Deployment that
   just does `env && sleep`. Verify with `kubectl exec ... env` (look for `MESSAGE` in the
   output): that's section 4's env path, live.
2. Create a Secret for a Redis password. Mount it as a file (section 5). `exec` in and `cat`
   the file under your `mountPath`; confirm it's the plaintext value, not base64 (the kubelet
   decodes it when projecting the file).
3. Update the ConfigMap, then check whether the running Pod sees the new value. (Answer: env,
   **no**, not until restart; mounted file, **eventually yes**, the kubelet refreshes it on a
   sync loop, typically within a minute. This is the section 4 vs. section 5 gotcha made real;
   try both.)

## Next

→ `lab-06-storage.md`: ConfigMaps and Secrets inject *config*; they're not where your app's
*data* lives. A **PersistentVolume** gives a Pod durable storage that survives a restart, and
you'll trace the PVC → PV → StorageClass binding that provisions it.

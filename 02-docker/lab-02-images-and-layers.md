# Lab 02 — Images and Layers

## 1. Images vs containers

- An **image** is a read-only filesystem snapshot + metadata (entrypoint, env, exposed ports).
- A **container** is a running (or stopped) instance: image + a thin writable layer on top.

```bash
docker images                  # local images
docker pull alpine:3.19
docker image inspect alpine:3.19 | less
```

## 2. Layers

Each Dockerfile instruction (`RUN`, `COPY`, ...) creates a *layer*. Layers are content-addressed (SHA256) and cached. This is why a good Dockerfile order matters:

```bash
docker history nginx:1.27-alpine
```

You'll see ~10 layers, each with size and the command that built it.

## 3. The cache rule

Docker reuses a layer if (a) the previous layer matches AND (b) the instruction text + inputs match. Order matters:

```dockerfile
# BAD — invalidates cache on every code change
COPY . /app
RUN pip install -r requirements.txt

# GOOD — deps cached separately
COPY requirements.txt /app/
RUN pip install -r requirements.txt
COPY . /app
```

You'll feel this in lab 03.

## 4. Tags and registries

An image reference is `[registry/]repo[:tag][@digest]`:

- `nginx` → `docker.io/library/nginx:latest`
- `gcr.io/foo/bar:v1`
- `myimage@sha256:abc123...` (digest = immutable)

`:latest` is a *convention*, not magic — it's just whatever was last pushed with that tag. Avoid it in production; pin versions.

## 5. Save / load (offline transfer)

```bash
docker save nginx:1.27-alpine -o nginx.tar
docker load -i nginx.tar
```

Useful when moving images between machines without a registry.

## 6. Cleanup

```bash
docker image prune              # dangling images
docker image prune -a           # ALL unused images (careful!)
docker system df                # disk usage
docker system prune             # everything dangling
```

## 7. Practice

1. List your local images sorted by size.
   `docker images --format '{{.Size}}\t{{.Repository}}:{{.Tag}}' | sort -h`
2. Inspect `nginx:1.27-alpine` and find its declared `EXPOSE`d ports and `CMD`.
3. Pull `alpine:3.19` and `alpine:3.18`. Compare their layer counts and total sizes with `docker history`.
4. Save `alpine:3.19` to a tarball. How big is it vs `docker images` reported size? Why might they differ? (Hint: layer dedup.)

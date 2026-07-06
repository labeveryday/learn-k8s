# Lab 02: Images and Layers

**What you'll build:** nothing new gets created here. You take an image apart.
You'll pull an image, crack it open with `inspect` and `history` to see the stack of
read-only layers it's made of, learn the one cache rule that decides whether a rebuild takes
2 seconds or 2 minutes, decode an image reference (`registry/repo:tag@digest`), and move an
image between machines with no registry. The commands matter less than the mental model
everything downstream rides on: a Kubernetes Pod pulls these exact layers, and a slow or
bloated image is a slow Pod start.

> **The one idea:** an image is not a blob. It's an ordered stack of
> content-addressed layers, and a container is that stack plus one thin writable layer on
> top. Once you see images as layers, caching, registry pulls, and image size all make sense.
> Every section below is that one idea from a different angle.

## 1. Images vs containers: the shape before the commands

- An **image** is a read-only filesystem snapshot plus metadata (entrypoint, env, exposed ports, defined in lab-03).
- A **container** is a running (or stopped) instance: image plus a thin writable layer on top.

The image is the cookie cutter; the container is the cookie. The image never changes. Every
container made from it shares the same read-only layers and gets its own writable top layer,
which is why ten containers from one image cost almost no extra disk. Delete the container, the
writable layer goes; the image stays.

```bash
docker images                  # local images - what's cached on this machine
docker pull alpine:3.19        # fetch each missing layer from the registry (Docker Hub by default)
docker image inspect alpine:3.19 | less   # full config + layer digests as JSON; q to quit
```

- `pull` downloads layer by layer; re-pull and already-present layers are skipped ("Already
  exists"). This is the same dedup a node uses when starting a Pod.
- `inspect` dumps the metadata an image carries: its `Cmd`, `Env`, `ExposedPorts`, and under
  `RootFS.Layers` the SHA256 digest of every layer. `| less` pages it; `inspect` outputs JSON.

**What you should see:** `docker images` lists `alpine 3.19` with a size around 7-8 MB after the
pull. That small footprint is the whole appeal of Alpine, and why later labs lean on `-alpine` tags.

## 2. Layers

Each Dockerfile instruction (`RUN`, `COPY`, ...) creates a *layer*. Layers are content-addressed (SHA256) and cached. This is why a good Dockerfile order matters:

```bash
docker history nginx:1.27-alpine   # show every layer, newest on top, with the command that built it
```

You'll see ~10 layers, each with size and the command that built it:

```
IMAGE          CREATED      CREATED BY                              SIZE    COMMENT
e8c…           2 weeks ago  CMD ["nginx" "-g" "daemon off;"]        0B
<missing>      2 weeks ago  COPY docker-entrypoint.sh / # buildkit   …B
<missing>      3 weeks ago  /bin/sh -c #(nop) ADD file:… in /        7MB
```

Reading it line by line:

- **`<missing>` in the IMAGE column is normal.** It means that layer came from the base
  image and has no standalone ID. Only the top, locally-built layer keeps a real ID.
- **`0B` layers are metadata-only.** They set config (the `CMD`, `ENV`, `EXPOSE`) rather than
  files, so they add no size. The bottom `ADD file:…` is the actual root filesystem (~7MB here).
- **`# buildkit` / `#(nop)`** are build-engine markers; `#(nop)` means "no operation on the
  filesystem," a pure-metadata instruction.
- **`CREATED BY` is truncated by default;** use `docker history --no-trunc nginx:1.27-alpine` to
  see full commands.

**What you should see:** a stack of ~10 rows, newest (the `CMD`) on top, oldest (the base
filesystem) on the bottom. Sum the SIZE column and you've got the image's on-disk footprint,
and you can spot which instruction is fat. That's how you debug a bloated image.

## 3. The cache rule

Docker reuses a layer if (a) the previous layer matches AND (b) the instruction text + inputs match. Order matters:

```dockerfile
# BAD - invalidates cache on every code change
COPY . /app
RUN pip install -r requirements.txt

# GOOD - deps cached separately
COPY requirements.txt /app/
RUN pip install -r requirements.txt
COPY . /app
```

Both conditions are AND-ed, and they cascade: the moment one layer misses the cache, **every
layer after it rebuilds too** (its "previous layer" no longer matches). So:

- In the **BAD** order, `COPY . /app` brings in *all* your source. Change one line of code and
  that layer's input changed → cache miss → the expensive `pip install` below it reruns every
  build, even though your deps didn't move.
- In the **GOOD** order, `requirements.txt` is copied alone first. Edit your code and only the
  last `COPY . /app` misses; the `pip install` layer above it is untouched and reused.

The rule of thumb: order instructions **rarely-changing → frequently-changing**. Deps change
rarely; code changes constantly. You'll feel this in lab 03.

## 4. Tags and registries

An image reference is `[registry/]repo[:tag][@digest]`:

- `nginx` → `docker.io/library/nginx:latest` (`library/` is the namespace Docker Hub uses for official images, so bare names like `nginx` resolve there)
- `gcr.io/foo/bar:v1`
- `myimage@sha256:abc123...` (digest = immutable)

How the defaults fill in: drop the registry and you get `docker.io`; drop the tag and you get
`:latest`; a bare official name like `nginx` also gets the `library/` namespace inserted. So
`nginx` and `docker.io/library/nginx:latest` are the same image, the short form being
shorthand for the long one.

`:latest` is a convention: it's whatever was last pushed with that tag. Avoid
it in production; pin versions. A **tag** is a mutable pointer (it can move to a new image
tomorrow); a **digest** (`@sha256:…`) is the layer content's hash, so it can never point at
anything but those exact bytes. That's why a digest is what you pin when you need certainty.
Kubernetes records both, and lab-02 (k8s) Pods can pin by digest for reproducible rollouts.

## 5. Save / load (offline transfer)

```bash
docker save nginx:1.27-alpine -o nginx.tar   # serialize the image (all layers + metadata) to a tarball
docker load -i nginx.tar                      # rehydrate it into another machine's image store
```

- `save -o nginx.tar` writes the whole image (every layer and its config) into one tar
  file. (Don't confuse it with `docker export`, which flattens a container's filesystem and
  throws away the layer history.)
- `load -i nginx.tar` reads that tarball back into the local image store, layers intact.

**What you should see:** an `nginx.tar` on disk, and after `load` on the target box,
`docker images` lists `nginx:1.27-alpine` with no registry round-trip. Useful when moving
images between machines without a registry: air-gapped hosts, a flaky network, or shipping a
build to a node directly.

## 6. Cleanup

```bash
docker image prune              # delete only DANGLING images (untagged <none> leftovers) - safe
docker image prune -a           # delete ALL images not used by a container (careful!)
docker system df                # disk usage broken down: images, containers, volumes, cache
docker system prune             # remove dangling images + stopped containers + unused networks
```

- A **dangling** image is one with no tag, usually an old layer set orphaned when you rebuilt a
  tag. `prune` (no `-a`) only removes those, so it's safe.
- `prune -a` is aggressive: it removes every image not currently backing a container,
  including ones you'd have to re-pull. Read the prompt before you confirm.
- `system df` is your "where did my disk go" command. Run it first to see whether images,
  stopped containers, or the build cache is eating space.

**What you should see:** each command prints what it reclaimed (e.g. `Total reclaimed space:
…`). `system df` shows a table with a `RECLAIMABLE` column, which is how much a prune would free.

## 7. Practice

1. List your local images sorted by size.
   `docker images --format '{{.Size}}\t{{.Repository}}:{{.Tag}}' | sort -h`
2. Inspect `nginx:1.27-alpine` and find its declared `EXPOSE`d ports and `CMD`.
3. Pull `alpine:3.19` and `alpine:3.18`. Compare their layer counts and total sizes with `docker history`.
4. Save `alpine:3.19` to a tarball. How big is it vs `docker images` reported size? Why might they differ? (Hint: layer dedup.)

## Next

→ `lab-03-dockerfile.md`: you've read images apart; now you'll build one, and the cache rule
from section 3 stops being theory the moment your `pip install` reruns on every code edit.

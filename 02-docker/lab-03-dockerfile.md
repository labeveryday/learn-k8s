# Lab 03: Build an Image

**What you'll build:** your own image, from scratch: a tiny Python web server baked into a
Dockerfile, built with `docker build`, run with `docker run`, then squeezed down with a
multi-stage build and locked down with a `.dockerignore` and non-root user. The web server is
the vehicle; the lesson is the Dockerfile as a recipe for layers (lab-02's mental model, now from
the author's seat) and the cache rule that decides whether your rebuild takes 2 seconds or
2 minutes. By the end you can read a Dockerfile instruction-by-instruction and predict
which layers a build will reuse and which it will rebuild. A Kubernetes Pod pulls the exact
image you produce here.

> **The one idea:** a Dockerfile is a declarative, ordered recipe. Each
> instruction is one cacheable layer, and the order you write them in sets your build speed and
> your image size. Everything below is that one idea: order rarely-changing before
> frequently-changing, ship only what runtime needs, and never run as root.

## 1. Anatomy of a Dockerfile: the shape before you build

Before building anything, read a full Dockerfile top to bottom. Each instruction is one layer
(lab-02, section 2); the order is deliberate (lab-02, section 3). Here is the whole recipe, then
the instructions that matter:

```dockerfile
FROM python:3.11-slim                # base image (a layer chain) - everything stacks on this
WORKDIR /app                         # cd /app for subsequent commands (creates it if missing)
COPY requirements.txt .              # add file from build context (the folder you point `docker build` at)
RUN pip install --no-cache-dir -r requirements.txt   # COPIED ALONE FIRST so this layer caches across code edits
COPY . .                             # now bring in the source - the layer that changes on every edit
EXPOSE 8000                          # documentation, not enforcement (does NOT publish the port)
ENV PYTHONUNBUFFERED=1               # env var, set at build AND runtime
USER 1000:1000                       # run as non-root. UID:GID; 1000:1000 is the conventional first non-root user/group on Linux (need not exist by name in the image). Limits blast radius if the app is compromised.
CMD ["python", "-m", "http.server", "8000"]   # default command (PID 1) - exec form, no shell
```

Two things that look cosmetic but aren't, and both bite later:

- **The order of `COPY requirements.txt` → `RUN pip install` → `COPY . .` is the cache rule from
  lab-02 in action.** Deps are copied alone first so the expensive `pip install` layer stays
  cached when you edit code; only the final `COPY . .` misses. Flip the two `COPY`s and every
  source edit reruns `pip install` (you'll feel this in section 2's practice).
- **`EXPOSE` does NOT open or publish a port.** It's metadata only: documentation for humans and
  a hint for `-P`. The port that reaches the container is the `-p` flag on `docker run`
  (section 2). This is the top "I EXPOSEd it, why can't I reach it?" trap.

Key instructions:

| Instruction | Purpose |
|-------------|---------|
| `FROM` | base image |
| `RUN` | execute at build time → new layer |
| `COPY` / `ADD` | bring files in (prefer `COPY`; `ADD` also auto-extracts tars and fetches URLs) |
| `WORKDIR` | set cwd |
| `ENV` | env var (build + runtime) |
| `ARG` | build-only arg |
| `EXPOSE` | document ports |
| `USER` | drop root |
| `ENTRYPOINT` | exec'd binary |
| `CMD` | default args (or full command if no entrypoint) |

`ENTRYPOINT` vs `CMD` mental model: `ENTRYPOINT` is the verb, `CMD` are default arguments. `docker run image arg1` replaces `CMD` but keeps `ENTRYPOINT`. (The `["...", "..."]` exec form runs your binary directly as PID 1, with no shell wrapper to swallow signals: the PID-1 problem from lab-02.)

## 2. Build a tiny app

Make a folder. This folder becomes the **build context** (the trailing `.` you hand to
`docker build`), so everything `COPY` can see must live inside it:

```bash
mkdir -p /tmp/myapp && cd /tmp/myapp   # this dir = the build context Docker will tar and ship
```

`app.py`, a bare-stdlib HTTP server that prints its own hostname and PID (so you can later see
each container/Pod is a distinct process):

```python
from http.server import BaseHTTPRequestHandler, HTTPServer
import os, socket
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(f"hi from {socket.gethostname()} pid={os.getpid()}\n".encode())
HTTPServer(("0.0.0.0", 8000), H).serve_forever()   # bind 0.0.0.0 (all interfaces), NOT 127.0.0.1, or -p can't reach it
```

> **Gotcha:** the server binds `0.0.0.0`, not `127.0.0.1`. Inside a container, `127.0.0.1` is the
> container's own loopback; bind there and the published `-p` port hits nothing. Bind `0.0.0.0`
> so the port is reachable from outside the container.

`Dockerfile` (no `requirements.txt` here, stdlib only, so the recipe is shorter than section 1's):

```dockerfile
FROM python:3.11-slim                # small Debian + Python; -slim drops build tooling/docs
WORKDIR /app                         # work and COPY target = /app
COPY app.py .                        # bring the one source file into /app
EXPOSE 8000                          # documents the port (still need -p to publish)
USER 1000:1000                       # drop root before the app runs
CMD ["python", "app.py"]             # PID 1 = your server, exec form
```

Build and run:

```bash
docker build -t myapp:0.1 .              # -t names:tags the image; . = build context (this folder)
docker run --rm -p 8000:8000 myapp:0.1   # foreground (no -d) - occupies this terminal
# in ANOTHER terminal:
curl http://localhost:8000               # → hi from <hostname> pid=<n>
```

- `docker build -t myapp:0.1 .`: `-t name:tag` labels the image so you can refer to it; the final
  `.` is the **build context**, the folder Docker tars and ships to the daemon (so `COPY` can only
  see files inside it). The daemon runs each instruction, caching every layer (lab-02, section 3).
- `docker run --rm -p 8000:8000`: `--rm` deletes the container on exit (no stopped-container
  litter); `-p HOST:CONTAINER` publishes container port 8000 to host port 8000. This `-p`, not
  `EXPOSE`, is what makes `curl` reach in. No `-d`, so it runs in the foreground and holds the
  terminal, which is why `curl` goes in a second terminal. Ctrl-C stops it (and `--rm` cleans up).

**What you should see:** the build prints a line per instruction (`[1/4]`, `[2/4]`, … or
`CACHED`), then `curl` returns `hi from <container-id> pid=1`. The `pid=1` is there because your `CMD` is PID 1
inside the container (the exec-form payoff from section 1). The hostname is the container's short
ID. The build context is the trailing `.`, the folder Docker tars and ships to the daemon, so
`COPY` can only see files inside it.

## 3. Multi-stage builds (smaller images)

When you need build tools at compile time but not at runtime, split the build into stages and copy
only the artifacts forward, so the compiler never ships:

```dockerfile
# Stage 1: build
FROM python:3.11 AS builder          # FULL image (has gcc etc.), named 'builder' so stage 2 can COPY from it
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir=/wheels -r requirements.txt   # a wheel (.whl) is a pre-built Python package; building them here means stage 2 needs no compiler

# Stage 2: runtime - only has the wheels, no compilers
FROM python:3.11-slim                # SLIM image - the only stage that ends up in the final image
WORKDIR /app
COPY --from=builder /wheels /wheels  # pull ONLY the built wheels across the stage boundary
RUN pip install --no-cache /wheels/*.whl   # install from local wheels - no network, no build tools
COPY . .
USER 1000:1000
CMD ["python", "app.py"]
```

- `FROM python:3.11 AS builder`: the heavy image (compilers, headers) gets a name. Only the
  last `FROM` in the file produces the shipped image; earlier stages exist only to feed it.
- `COPY --from=builder /wheels /wheels` is the key move: reach into the build stage and copy out
  the compiled wheels. gcc, the source, the apt cache: none of it crosses over.
- `pip install --no-cache /wheels/*.whl` installs from those local files, so the runtime stage
  needs no compiler and no network. Smaller image, fewer moving parts.

Result: smaller, fewer CVEs (known security vulnerabilities; fewer packages means fewer to patch), faster pulls. (Smaller also means faster Pod starts, since a node pulls these layers before the container runs, lab-02, section 1.)

## 4. `.dockerignore`

Like `.gitignore` for the build context. Keep it tight:

```
.git              # repo history - huge, never needed at runtime
__pycache__       # compiled bytecode - regenerated, pure bloat
*.pyc
node_modules      # reinstallable, often gigabytes
.env              # SECRETS - keep them OUT of the context entirely
```

A bloated context means slow builds and accidental secret leaks. Remember section 2: `docker build`
tars the whole context and ships it to the daemon before any `COPY` runs, so anything not
ignored is sent (and a `COPY . .` can pull a `.env` straight into a layer). `.dockerignore` trims
what's sent in the first place.

## 5. Best practices

1. **Pin base images** by tag or digest. `:latest` is for demos. (A tag is a mutable pointer; a digest is immutable, lab-02, section 4.)
2. **Run as non-root**. `USER 1000:1000`.
3. **One process per container.** Your `CMD` runs as PID 1, which must reap zombie children and forward signals (Phase 1 Lab 02). Use an init like `tini` if your app spawns children; it does that for you.
4. **Don't put secrets in images.** Use env at runtime / mounted secrets.
5. **Order instructions** for cache: rarely-changing → frequently-changing. (Section 1's `COPY requirements.txt` → `RUN pip install` → `COPY . .`, exactly.)
6. **Use `--no-cache-dir`, `--no-install-recommends`** to keep images lean.
7. **Healthcheck** when it makes sense (`HEALTHCHECK CMD curl -f localhost:8000 || exit 1`).

## 6. Practice

1. Add a `HEALTHCHECK` to the Dockerfile above. Verify with `docker ps` (STATUS column shows `(healthy)`).
2. Modify `app.py` and rebuild. Which layers were re-used vs rebuilt? Why? (Hint: watch for `CACHED` lines; only layers at/after the changed `COPY` rebuild, section 1's cache rule, live.)
3. Convert the build to multi-stage and compare image sizes (`docker images myapp`).
4. Try `docker run --read-only` with your image. What breaks? Why? (Hint: `/tmp`.)

## Next

→ `lab-04-networking.md`: your image runs, but a container's IP is private and churning. You'll
wire containers together on a user-defined network and reach one from another by **name**.

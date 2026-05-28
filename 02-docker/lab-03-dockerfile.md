# Lab 03 — Build an Image

## 1. Anatomy of a Dockerfile

```dockerfile
FROM python:3.11-slim                # base image (a layer chain)
WORKDIR /app                         # cd /app for subsequent commands
COPY requirements.txt .              # add file from build context
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000                          # documentation, not enforcement
ENV PYTHONUNBUFFERED=1               # env var
USER 1000:1000                       # run as non-root
CMD ["python", "-m", "http.server", "8000"]
```

Key instructions:

| Instruction | Purpose |
|-------------|---------|
| `FROM` | base image |
| `RUN` | execute at build time → new layer |
| `COPY` / `ADD` | bring files in (prefer `COPY`; `ADD` has tar/URL magic) |
| `WORKDIR` | set cwd |
| `ENV` | env var (build + runtime) |
| `ARG` | build-only arg |
| `EXPOSE` | document ports |
| `USER` | drop root |
| `ENTRYPOINT` | exec'd binary |
| `CMD` | default args (or full command if no entrypoint) |

`ENTRYPOINT` vs `CMD` mental model: `ENTRYPOINT` is the verb, `CMD` are default arguments. `docker run image arg1` *replaces* `CMD` but keeps `ENTRYPOINT`.

## 2. Build a tiny app

Make a folder:

```bash
mkdir -p /tmp/myapp && cd /tmp/myapp
```

`app.py`:

```python
from http.server import BaseHTTPRequestHandler, HTTPServer
import os, socket
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(f"hi from {socket.gethostname()} pid={os.getpid()}\n".encode())
HTTPServer(("0.0.0.0", 8000), H).serve_forever()
```

`Dockerfile`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY app.py .
EXPOSE 8000
USER 1000:1000
CMD ["python", "app.py"]
```

Build and run:

```bash
docker build -t myapp:0.1 .
docker run --rm -p 8000:8000 myapp:0.1
# in another terminal:
curl http://localhost:8000
```

## 3. Multi-stage builds (smaller images)

When you need build tools at compile time but not at runtime:

```dockerfile
# Stage 1: build
FROM python:3.11 AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --wheel-dir=/wheels -r requirements.txt

# Stage 2: runtime — only has the wheels, no compilers
FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache /wheels/*.whl
COPY . .
USER 1000:1000
CMD ["python", "app.py"]
```

Result: smaller, fewer CVEs, faster pulls.

## 4. `.dockerignore`

Like `.gitignore` for the build context. Keep it tight:

```
.git
__pycache__
*.pyc
node_modules
.env
```

A bloated context = slow builds and accidental secret leaks.

## 5. Best practices (Kelsey's list)

1. **Pin base images** by tag or digest. `:latest` is for demos.
2. **Run as non-root**. `USER 1000:1000`.
3. **One process per container.** Use init like `tini` if you fork.
4. **Don't put secrets in images.** Use env at runtime / mounted secrets.
5. **Order instructions** for cache: rarely-changing → frequently-changing.
6. **Use `--no-cache-dir`, `--no-install-recommends`** to keep images lean.
7. **Healthcheck** when it makes sense (`HEALTHCHECK CMD curl -f localhost:8000 || exit 1`).

## 6. Practice

1. Add a `HEALTHCHECK` to the Dockerfile above. Verify with `docker ps` (STATUS column shows `(healthy)`).
2. Modify `app.py` and rebuild. Which layers were re-used vs rebuilt? Why?
3. Convert the build to multi-stage and compare image sizes.
4. Try `docker run --read-only` with your image. What breaks? Why? (Hint: `/tmp`.)

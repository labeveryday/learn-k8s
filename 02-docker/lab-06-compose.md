# Lab 06: Docker Compose

**What you'll build:** a multi-container app described in a single `compose.yaml`: `docker run`
flags promoted into a declarative file, where one `docker compose up` creates the network, the
volumes, and the containers, wired together by name. You'll stand up a three-service stack
(nginx + python + redis), then drive the runnable stack in `project-fastapi-redis/`:
a FastAPI app talking to Redis over a Compose-managed network. The apps are the vehicle; the
lesson is that Compose is the on-ramp to Kubernetes manifests. Every Compose concept here maps to a
K8s object in section 7, and once you can write Compose, K8s YAML is mostly verbose Compose
with stricter typing.

> **The one idea:** in lab-01 through lab-05 you typed imperative `docker run -d -p ... -v ...`
> commands: do this, now. Compose flips that: you declare desired state in a file ("these
> services, this network, these volumes") and `up` reconciles reality to match. That declarative
> shift is the leap Phase 3 makes from `docker` to `kubectl apply -f`.

## 1. The model

A `compose.yaml` describes:

- **services** (containers)
- **networks** (links between them)
- **volumes** (persistence)

Compose creates a private network for the project automatically; service names are DNS names.
This last point is the load-bearing one: in the stack below, the `web` service reaches the `api`
service at the hostname `api`. Compose runs an embedded DNS resolver on the project network and
each service name resolves to that container's IP. No links, no IPs to hardcode. (This is the
same name-based service discovery K8s gives you with Services, section 7.)

## 2. Minimal example

`compose.yaml`:

```yaml
services:
  web:
    image: nginx:1.27-alpine   # pull this image (no build) - same as `docker run nginx:1.27-alpine`
    ports:
      - "8080:80"              # HOST:CONTAINER - publish container :80 to localhost:8080
    depends_on:
      - api                    # start `api` first (start-order only - NOT "wait until ready"; see §3)
  api:
    image: python:3.11-slim
    command: python -m http.server 8000   # override the image's default CMD for this service
    expose:
      - "8000"                 # reachable to OTHER services on the project network, NOT published to host
  cache:
    image: redis:7-alpine
    volumes:
      - cachedata:/data        # mount the named volume `cachedata` at /data inside the container

volumes:
  cachedata:                   # declare the named volume (Docker-managed; survives `down`, see below)
```

Two distinctions beginners trip on, both in the YAML above:

- **`ports` vs `expose`.** `ports: "8080:80"` publishes to the host: you can `curl localhost:8080`.
  `expose: "8000"` only opens the port to other services on the project network; the host can't
  reach it. `web` can curl `api:8000`, you cannot curl `localhost:8000`. (Bare `expose` is mostly
  documentation now; container-to-container traffic works regardless on the shared network.)
- **`depends_on` (the bare list form) is start-ORDER only.** It guarantees `api`'s container is
  started before `web`'s, not that `api` is ready to serve. The fix is the long form with a
  health condition (§3).

Run:

```bash
docker compose up -d        # build (if needed) + create network/volumes/containers, run detached
docker compose ps           # status of this project's services
docker compose logs -f api  # tail (-f = follow) just the api service's logs
docker compose exec web sh  # open a shell inside the RUNNING web container (exec, not a new container)
docker compose down         # stop + remove containers AND the project network (volumes survive)
docker compose down -v      # ...and ALSO delete named volumes (-v) - destroys persisted data
```

- `up -d` is the declarative apply: it diffs desired (the file) against running and creates only
  what's missing. Re-running it is safe; unchanged services are left alone. `-d` detaches so your
  terminal returns; drop it to stream all logs in the foreground.
- `exec` runs a command in an already-running container (like `docker exec`); `run` would start a
  new one-off container. Use `exec` to poke at a live service.
- `down` vs `down -v` is the gotcha: plain `down` keeps your named volumes (your data is safe), but
  `down -v` wipes them. Reach for `-v` only when you want a clean slate.

**What you should see:** `up -d` prints network/volume/container creation lines; `ps` should show
all three services running:

```
NAME            IMAGE                COMMAND                  STATUS
proj-api-1      python:3.11-slim     "python -m http.ser…"    Up 5 seconds
proj-cache-1    redis:7-alpine       "docker-entrypoint.s…"   Up 5 seconds
proj-web-1      nginx:1.27-alpine    "/docker-entrypoint.…"   Up 5 seconds
```

Note the name shape: `<project>-<service>-<index>`. The project name (`proj`) defaults to the
directory name; the `-1` index is the replica number (Compose can run more than one). That naming
is how `compose down` knows which containers belong to this project and nothing else.

## 3. Healthchecks and `depends_on`

(Illustrative fragments, not runnable as-is; the one runnable stack is `project-fastapi-redis/`.)

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: secret
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]   # the probe - exit 0 = healthy. CMD-SHELL runs it via /bin/sh
      interval: 5s                                     # how often to run the probe
      timeout: 3s                                      # fail the probe if it runs longer than this
      retries: 5                                       # consecutive failures before status flips to "unhealthy"
  api:
    image: myapp:0.1
    depends_on:
      db:
        condition: service_healthy   # WAIT until db's healthcheck passes before starting api (not just "started")
```

`depends_on: condition: service_healthy` is the reason healthchecks exist. Without it, `depends_on` only waits for "started," not "ready."

This is the precise difference from §2's bare-list `depends_on`: there it meant order; here, with
`condition: service_healthy`, it means readiness. The race it prevents is real: without it, `api`
boots, tries to connect to a Postgres that's still initializing, and crashes. (Note `pg_isready`,
not "is the process up": a probe should test the thing you depend on. In Phase 3 this
exact pattern becomes a `readinessProbe` + initContainers, §7.)

## 4. Env, configs, secrets

```yaml
services:
  api:
    image: myapp:0.1
    environment:
      DB_HOST: db          # inline env var - db is a service name, resolvable via the project DNS (§1)
      DB_USER: postgres
    env_file:
      - .env               # load additional vars from a file (keep secrets here, out of the committed yaml)
```

Don't commit `.env`. Add it to `.gitignore` and `.dockerignore`.

The `.dockerignore` half matters as much as `.gitignore`: without it, a `.env` sitting in the build
context can get baked into an image layer by a `COPY . .`, leaking secrets into the image even if
git never saw them. (`environment:` here maps to env / `envFrom` from a ConfigMap or Secret in K8s, §7.)

## 5. Build vs image

```yaml
services:
  api:
    build:
      context: ./api          # the build context - the dir sent to the daemon, relative to compose.yaml
      dockerfile: Dockerfile  # which Dockerfile within that context (defaults to ./Dockerfile)
    image: myorg/api:dev      # tag to GIVE the built image (and the name to reuse on the next `up`)
```

`docker compose build` builds; `up` will build automatically if the image doesn't exist.

The subtlety: with both `build` and `image` set, `image` is the output tag of your build, not a
registry pull. Once that tag exists locally, a later `up` reuses it instead of rebuilding, so when
you change code you need `docker compose up --build` (or `compose build` first) to force a rebuild.
The project in §6 uses this `build` + `image` pairing.

## 6. Practice

Use the project in `project-fastapi-redis/`. It's a real Compose stack: Python API + Redis. Bring
it up, hit the endpoints, break a service, debug it. This stack ties together everything above plus
the `HEALTHCHECK` from lab-03. Read it before you run it.

The `compose.yaml`:

```yaml
services:
  api:
    build: .                          # build from the Dockerfile in THIS dir (shorthand for build.context: .)
    image: learn-k8s/api:0.1          # tag the built image; reused on next `up` unless you pass --build
    ports:
      - "8000:8000"                   # publish the API to localhost:8000
    environment:
      REDIS_HOST: cache               # the app reads this (app.py) - `cache` resolves to the redis service via DNS
      REDIS_PORT: "6379"              # quoted: Compose env values must be strings (a bare 6379 is fine, but quoting avoids type surprises)
    depends_on:
      cache:
        condition: service_healthy    # don't start api until redis answers `redis-cli ping` (the readiness pattern from §3)

  cache:
    image: redis:7-alpine             # pulled, not built
    volumes:
      - cachedata:/data               # persist redis's data dir across restarts (the visit counter survives)
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]   # CMD (no shell) - exec redis-cli directly; replies PONG when ready
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  cachedata:                          # named volume backing /data above
```

Read it top to bottom and notice every concept from this lab is here: `build`+`image` (§5),
`ports` to publish (§2), `environment` with a service name as a value (§1, §4), `depends_on:
condition: service_healthy` (§3), a `healthcheck` (§3), and a named `volume` (§2). One difference
from §3's `CMD-SHELL`: this probe uses `["CMD", ...]`, which execs `redis-cli ping` directly without
a shell, slightly faster and with no shell-quoting surprises.

The Dockerfile it builds (`Dockerfile`):

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .                       # copy deps FIRST, on its own layer...
RUN pip install --no-cache-dir -r requirements.txt   # ...so this layer is cached unless requirements change (lab-02 layer caching)

COPY app.py .                                 # app code copied AFTER deps - code changes don't bust the pip cache

ENV PYTHONUNBUFFERED=1                         # don't buffer stdout/stderr → logs appear live in `compose logs`
EXPOSE 8000                                    # documents the port (doesn't publish - compose ports: does that)
USER 1000:1000                                 # drop root: run as an unprivileged UID:GID (lab-03 hardening)

HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz').read()" || exit 1   # image-level probe: hits /healthz

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]   # run uvicorn serving the `app` object in app.py; 0.0.0.0 = listen on all interfaces (required in a container)
```

Two gotchas worth internalizing:

- **`--host 0.0.0.0` is mandatory in a container.** Bind to `127.0.0.1` and the server only listens
  on the container's loopback: the published port forwards to nothing and `curl localhost:8000`
  hangs/refuses. `0.0.0.0` listens on all interfaces so Compose's port mapping can reach it.
- **The COPY ordering is deliberate.** `requirements.txt` is copied and installed before `app.py`,
  so editing `app.py` reuses the cached pip-install layer (lab-02). Flip the order and every code
  edit reinstalls every dependency.

The `app.py` it serves is a FastAPI app with three routes: `GET /` (hello + hostname),
`GET /hits` (increments a Redis counter via `r.incr("hits")`), and `GET /healthz` (returns 503 if
Redis is unreachable, which is what the healthcheck above probes).

Run it:

```bash
cd 02-docker/project-fastapi-redis
docker compose up --build -d        # --build forces a rebuild even if learn-k8s/api:0.1 exists (you changed code)
curl http://localhost:8000/         # {"hello":"world","host":"...","pid":1}
curl http://localhost:8000/hits     # {"hits":1}  (increments each call)
docker compose logs -f api
```

- `--build` is the flag from §5's gotcha: without it, `up` reuses the existing `learn-k8s/api:0.1`
  tag and your code change never lands in the running container.
- `pid":1` in the response is a teaching detail: the app is PID 1 inside the container
  (`01-linux/lab-02`, processes & signals). It is the container's init process, which is
  why signal handling and `--init` matter.

**What you should see:** `up --build` streams the image build, then creates the network, the
`cachedata` volume, and both containers: `cache` first (api waits on its healthcheck), then `api`.
`curl /` returns the JSON hello with the container's hostname; each `curl /hits` returns a number
one higher than the last, because the count lives in Redis, not in the API process.

Then break it on purpose:

```bash
docker compose stop cache           # stop only the redis service (containers stay defined, just halted)
curl http://localhost:8000/hits     # now returns HTTP 503: {"detail":"redis down: ..."}
```

**What this means:** the API is up but its dependency is down, so `/hits` and `/healthz` fail with
503, the dependency-failure mode `depends_on: condition: service_healthy` exists to avoid
at startup. The app's own try/except is what turns a Redis outage into a clean 503 instead of a
crash. Restart it (`docker compose start cache`) and `/hits` recovers, the counter resuming
from where it left off, because `cachedata` persisted the data through the stop.

The project's full README has the rest of the exercises (multi-stage build, pinning tags, `.dockerignore`).

## 7. Mapping Compose → Kubernetes

This is your bridge to Phase 3. Preview only: don't memorize the right column yet (those K8s terms are taught in Phase 3); revisit this table at the end of Phase 3.

| Compose | Kubernetes |
|---------|------------|
| `services:` entry | Deployment + Pod |
| `ports: 8080:80` | Service (type=NodePort or LoadBalancer) + container `containerPort` |
| `depends_on` | initContainers / readiness probes |
| `volumes:` (named) | PersistentVolumeClaim |
| project network | Pod network (cluster-wide flat) |
| `environment:` | env / envFrom (ConfigMap, Secret) |
| `healthcheck:` | livenessProbe / readinessProbe |
| `restart: always` | Deployment (always restarts via ReplicaSet) |

If you can write Compose, K8s YAML is mostly verbose Compose with stricter typing. The deeper
parallel: both are declarative reconcilers. `docker compose up` and `kubectl apply -f` each take a
file of desired state and make reality match it, the one idea from the top of this lab, and the
spine of the rest of the curriculum.

## Next

→ **Phase 03** (`03-kubernetes/`): Compose orchestrates containers on ONE host; Kubernetes does it across many. Same idea (declare desired state, a controller reconciles), bigger blast radius.

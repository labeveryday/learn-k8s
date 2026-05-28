# Lab 06 — Docker Compose

Compose = declarative multi-container apps. It's the on-ramp to K8s manifests.

## 1. The model

A `compose.yaml` describes:

- **services** (containers)
- **networks** (links between them)
- **volumes** (persistence)

Compose creates a private network for the project automatically; service names are DNS names.

## 2. Minimal example

`compose.yaml`:

```yaml
services:
  web:
    image: nginx:1.27-alpine
    ports:
      - "8080:80"
    depends_on:
      - api
  api:
    image: python:3.11-slim
    command: python -m http.server 8000
    expose:
      - "8000"
  cache:
    image: redis:7-alpine
    volumes:
      - cachedata:/data

volumes:
  cachedata:
```

Run:

```bash
docker compose up -d        # start all
docker compose ps           # status
docker compose logs -f api  # tail
docker compose exec web sh  # shell into a service
docker compose down         # stop + remove (keeps volumes)
docker compose down -v      # also remove volumes
```

## 3. Healthchecks and `depends_on`

```yaml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: secret
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 3s
      retries: 5
  api:
    image: myapp:0.1
    depends_on:
      db:
        condition: service_healthy
```

`depends_on: condition: service_healthy` is the reason healthchecks exist. Without it, `depends_on` only waits for "started," not "ready."

## 4. Env, configs, secrets

```yaml
services:
  api:
    image: myapp:0.1
    environment:
      DB_HOST: db
      DB_USER: postgres
    env_file:
      - .env
```

Don't commit `.env`. Add it to `.gitignore` and `.dockerignore`.

## 5. Build vs image

```yaml
services:
  api:
    build:
      context: ./api
      dockerfile: Dockerfile
    image: myorg/api:dev
```

`docker compose build` builds; `up` will build automatically if the image doesn't exist.

## 6. Practice

Use the project in `project-fastapi-redis/`. It's a real Compose stack: Python API + Redis. Bring it up, hit the endpoints, break a service, debug it.

## 7. Mapping Compose → Kubernetes

This is your bridge to Phase 3. Memorize the analogues:

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

If you can write Compose, K8s YAML is mostly verbose Compose with stricter typing.

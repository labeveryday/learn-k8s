# Project: FastAPI + Redis

A small HTTP API with a Redis-backed visit counter. Builds your image, wires it to a Redis service, exposes it on `localhost:8000`.

## Layout

```
project-fastapi-redis/
├── README.md
├── app.py
├── requirements.txt
├── Dockerfile
└── compose.yaml
```

## Run

```bash
cd 02-docker/project-fastapi-redis
docker compose up --build -d
curl http://localhost:8000/
curl http://localhost:8000/hits
docker compose logs -f api
```

## Endpoints

- `GET /` — hello + hostname
- `GET /hits` — increments and returns the counter
- `GET /healthz` — used by Compose healthcheck

## Exercises

1. **Break it on purpose.** Stop the `cache` service: `docker compose stop cache`. Hit `/hits`. Read the error. Fix the API to return a sensible 503 if Redis is unreachable.
2. **Make it persist.** The current Compose mounts a named volume for Redis. Verify counter survives `docker compose restart cache`.
3. **Tighten the image.** Convert the Dockerfile to multi-stage. Compare sizes.
4. **Pin everything.** Replace `:latest` and floating tags with specific versions.
5. **Production hygiene.** Add `USER`, `HEALTHCHECK`, drop unused build deps, `.dockerignore`.

This stack will be re-deployed in Phase 3 as Kubernetes manifests. Same app, more YAML.

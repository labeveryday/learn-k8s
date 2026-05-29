# Project: FastAPI + Redis

A small HTTP API with a Redis-backed visit counter. Builds your image, wires it to a Redis service, exposes it on `localhost:8000`.

## What's new here

Up to now the only web app you'd seen was the raw `http.server` from lab-03. This adds three things:

- **FastAPI** — a Python web framework (defines the routes in `app.py`).
- **uvicorn** — the ASGI server that runs it. The `CMD ["uvicorn", "app:app", ...]` means "in `app.py`, serve the FastAPI object named `app`" (module:variable).
- the **`redis`** Python package — the client library that talks to the Redis service.

This project reuses `HEALTHCHECK` (lab-03) in the Dockerfile and `depends_on: condition: service_healthy` (lab-06) in `compose.yaml` — that pairing is why `api` waits for `cache` to be ready, not just started. Recap, not new material.

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
curl http://localhost:8000/        # {"hello":"world","host":"...","pid":1}
curl http://localhost:8000/hits    # {"hits":1}  (increments each call)
docker compose logs -f api
```

## Endpoints

- `GET /` — hello + hostname → `{"hello":"world","host":"<id>","pid":1}`
- `GET /hits` — increments and returns the counter → `{"hits":N}`
- `GET /healthz` — used by Compose healthcheck → `{"ok":true}`

With Redis down (`docker compose stop cache`), `/hits` and `/healthz` return HTTP 503 with body `{"detail":"redis down: ..."}` — that's the failure exercise 1 asks you to induce.

## Exercises

1. **Break it on purpose.** Stop the `cache` service: `docker compose stop cache`. Hit `/hits`. Read the error. Fix the API to return a sensible 503 if Redis is unreachable.
2. **Make it persist.** The current Compose mounts a named volume for Redis. Verify counter survives `docker compose restart cache`.
3. **Tighten the image.** Convert the Dockerfile to multi-stage. Compare sizes.
4. **Pin everything.** Replace `:latest` and floating tags with specific versions.
5. **Production hygiene.** Add `USER`, `HEALTHCHECK`, drop unused build deps, `.dockerignore`.

This stack will be re-deployed in Phase 3 as Kubernetes manifests. Same app, more YAML.

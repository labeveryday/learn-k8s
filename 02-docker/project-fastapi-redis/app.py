import os
import socket
from fastapi import FastAPI, HTTPException
import redis

REDIS_HOST = os.getenv("REDIS_HOST", "cache")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

app = FastAPI()
r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, socket_timeout=2)


@app.get("/")
def root():
    return {"hello": "world", "host": socket.gethostname(), "pid": os.getpid()}


@app.get("/healthz")
def healthz():
    try:
        r.ping()
        return {"ok": True}
    except redis.RedisError as e:
        raise HTTPException(status_code=503, detail=f"redis down: {e}")


@app.get("/hits")
def hits():
    try:
        n = r.incr("hits")
        return {"hits": n}
    except redis.RedisError as e:
        raise HTTPException(status_code=503, detail=f"redis down: {e}")

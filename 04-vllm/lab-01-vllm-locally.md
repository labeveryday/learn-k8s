# Lab 01 — Run vLLM (or ollama) in Docker First

Before K8s: prove the container runs.

## Option A: vLLM CPU

```bash
docker run --rm -it \
  -p 8000:8000 \
  -v $HOME/models/tinyllama:/models/tinyllama \
  vllm/vllm-openai:latest \
  --model /models/tinyllama \
  --dtype float32 \
  --device cpu \
  --max-model-len 1024
```

First startup is slow (model load). Once ready:

```bash
# OpenAI-compatible endpoints:
curl http://localhost:8000/v1/models

curl http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "/models/tinyllama",
    "messages": [{"role": "user", "content": "Say hi in 5 words."}],
    "max_tokens": 32
  }'
```

Note the same API shape as OpenAI's — you can point LangChain, llama-index, Python `openai` SDK at it with `base_url=http://localhost:8000/v1`.

## Option B: ollama (fallback / lighter)

You already have ollama. It also speaks an OpenAI-compatible API:

```bash
# If ollama runs on your Mac (not in Docker):
ollama pull tinyllama
ollama run tinyllama "hello"

# Its HTTP API:
curl http://localhost:11434/v1/models
curl http://localhost:11434/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "tinyllama", "messages": [{"role":"user","content":"hi"}]}'
```

For K8s, you'll want ollama *in a container* instead. There's an official image:

```bash
docker run -d --name ollama -p 11434:11434 -v ollama:/root/.ollama ollama/ollama
docker exec ollama ollama pull tinyllama
curl http://localhost:11434/api/tags
```

Pick whichever you can comfortably run. The rest of Phase 4 assumes one of the two is working locally.

## Practice

1. Send a request with `max_tokens=256`. Time it. What's your tokens/sec?
2. Open two concurrent request streams. Does throughput scale or collapse? (Hint: with CPU vLLM, batching helps; ollama is single-stream by default.)
3. Look at container RAM usage (`docker stats`). How much does the model occupy?

# 06 — AI Gateway: vLLM behind a smart front door

> Floor 2. The *same* gateways from Phase 05 — now routing to LLMs, with the controls
> that only matter for AI traffic: token-based rate limits, multi-model routing, prompt
> guards, and provider abstraction.

## Why this phase exists

In Phase 05 you built a front door that counts **requests** and forwards **bytes**. Put
that in front of an LLM and it falls short in three concrete ways, none of which are
fixable with more HTTP routing:

- **It can't meter spend.** LLM cost and capacity are priced in **tokens**, not requests.
  A 5-token "hi" and a 4,000-token essay are one request each — so a request limit caps
  the wrong thing.
- **It can't route by intent.** The thing that says *which model the caller wants* is the
  `model` field inside the JSON body. Path/host/header routing never reads the body, so it
  can't fan one URL out to several models.
- **It can't guard the prompt.** PII and jailbreak filtering need to inspect the prompt —
  again, in the body — before it reaches any model.

The fix is a gateway that **understands the protocol**, not just HTTP: its data plane
*parses* the OpenAI request and response. That single capability — reading the body —
unlocks token metering (it sees `usage`), model routing (it sees `model`), prompt guards
(it sees the prompt), and upstream auth the caller never sees. That's an "AI gateway."
Both kgateway and Kong ship one. You'll put the vLLM from `04-vllm` behind it.

## Prereqs

- Phase 04 (vLLM) and Phase 05 (kgateway/Kong) done.
- vLLM (or Ollama, from `04-vllm/manifests`) running and serving an OpenAI-compatible
  `/v1/chat/completions` endpoint on the cluster.

## Objectives

1. Route OpenAI-style requests to your in-cluster vLLM through a gateway.
2. Enforce a **token-per-minute** limit (not just requests).
3. Route by model name to **two backends** (e.g. vLLM + a fallback).
4. Add a basic **prompt guard** / header-based auth in front of the model.
5. Compare the kgateway AI Gateway path vs Kong's `ai-proxy`.

## Labs

| Lab | Idea |
|---|---|
| 01 | `lab-01-vllm-backend.md` — deploy vLLM as a Deployment + Service, confirm the OpenAI `/v1` API answers before any gateway is in front of it |
| 02 | `lab-02-kgateway-ai.md` — kgateway/agentgateway AI Gateway: `AgentgatewayBackend` CRD, route to vLLM, meter callers by **tokens** |
| 03 | `lab-03-kong-ai.md` — Kong `ai-proxy` (+ `ai-rate-limiting-advanced`), the same AI-gateway pattern in Kong's plugin idiom |
| 04 | `lab-04-multimodel-guards.md` — fan **one endpoint out to two models** by the request body's `model` value, then **block bad prompts** with a prompt guard |

## The one idea to carry out

Every lab is a variation on a single sentence: **a plain gateway forwards bytes; an AI
gateway parses the OpenAI body.** From that one difference comes token metering
(`usage`), model routing (`model`), and prompt guards (the prompt) — all of it acting on
the payload, not the envelope.

## The payoff

By the end, a client hits **one stable endpoint**, you swap models behind it, cap token
spend, and never expose vLLM directly. That's the architecture of a real inference
platform — and a strong Akamai Cloud + AI demo.

# Lab 05: A RAG agent as a serverless function

**Goal:** build a Spin (JavaScript) function that is a lightweight **RAG agent**: it
retrieves over a small **in-memory** corpus and calls an LLM that is *provider-agnostic*, a
hosted OpenAI-compatible API **or** your own Phase 06 gateway, by changing one variable. Run
it locally, then deploy it to Akamai Functions. This is the **third operational model** for
an agent in this repo, after a Strands process and a kagent object.

**Time:** ~50 min · **Cost:** free tier (preview) + your LLM/embeddings API usage

## The problem (why this exists)

In Phase 07 you ran an agent two ways: as a **Python process** (your `agents/` Strands
framework, lab-04) and as a **Kubernetes object** (kagent, lab-02/03). Both assume a
long-lived runtime you keep warm. But a lot of agent work is *bursty and tiny*: a Q&A
webhook, a per-request RAG lookup, a "summarize this and reply" hook. Keeping a Pod or a
process alive for that is paying for idle. A Wasm function (millisecond cold start, scales
to zero) is the right home. Two constraints shape what you build here, and both are honest:

1. **It's stateless and ephemeral.** No long-lived process holds your data between requests.
2. **Wasm means JS / TS / Rust / Go, not Python.** So this is *not* your Strands framework;
   it's a **hand-rolled agent loop** in JavaScript. That's the point of seeing the third
   model: agent logic is a pattern (retrieve → prompt → call model → answer), not a library.

## What it replaces / the two contrasts

**Three operational models for one idea (an LLM + a loop + tools):**

| | Strands process (07/lab-04) | kagent object (07/lab-02–03) | Akamai Function (here) |
|---|---|---|---|
| Lives as | a process (laptop / AgentCore) | a Pod a controller reconciles | a `.wasm`, **scale-to-zero** |
| Language | Python | any (model-agnostic CR) | JS / TS / Rust / Go |
| Warm cost | a running process | a running Pod | ~zero when idle |
| Best for | building, content, rich SDK | always-on, multi-tenant, RBAC'd | bursty, tiny, serverless glue |

**In-memory RAG vs Phase 10's vector DB:**

| | In-memory (this lab) | Vector DB / Qdrant (Phase 10) |
|---|---|---|
| Store | corpus + vectors **baked into the module** | a running Qdrant service |
| Search | brute-force cosine in JS | HNSW/ANN index |
| Good for | small, **static** corpus; cheap, no infra | large or changing corpus |
| Scales? | no; O(n) per query, memory-bound | yes |

The teaching point: **in-memory brute-force RAG is the right tool when the corpus is small
and static** (a product FAQ, a runbook) and you want zero infra. You graduate to Phase 10's
vector DB when it grows. A function with a baked-in corpus is "RAG without a database."

> **The 4-sentence RAG primer (Phase 10 teaches this in depth; here's what you need now).**
> An **embedding** is a list of numbers (a vector) that represents a piece of text's
> meaning, produced by an embeddings model. **Cosine similarity** measures how close two
> such vectors point, on a 0–1 scale (1 = same meaning); you score your question's vector
> against every chunk's vector. **Top-k** keeps the *k* highest-scoring chunks; a
> **relevance floor** drops any whose score is too low to be worth feeding the model.
> **RAG** = retrieve those chunks, paste them into the prompt, and let the LLM answer
> *from them* instead of from memory. (**ANN / HNSW** in the table above = "approximate
> nearest-neighbor" search via a "hierarchical navigable small world" index, a data
> structure that finds close vectors fast without scoring all of them; Phase 10's Qdrant
> uses it, this lab doesn't need it.)

## Under the hood: the agent loop, and where each hop goes

```
  HTTP request ──► the function (one .wasm, cold-started in ms)
        │
        │ 1. embed the QUERY  ── outbound fetch ─► embeddings API   (corpus already embedded, offline)
        ▼
   in-memory cosine over corpus.json (baked in) ──► top-k chunk texts
        │
        │ 2. build a grounded prompt (chunks + question)
        ▼
        │ 3. chat completion ── outbound fetch ─► LLM_BASE_URL
        ▼
   grounded answer ──► HTTP response
```

Two mechanisms to hold onto:

- **The corpus is embedded *offline*; only the query is embedded at runtime.** A build step
  calls the embeddings API once over your docs and writes `corpus.json` (chunks + vectors)
  into the bundle. At request time you embed only the one query (one outbound call) and do
  cosine in plain JS. No vector DB, no embedding model running.
- **Provider-agnostic means it's an OpenAI-protocol client.** The function reads
  `LLM_BASE_URL` + `LLM_API_KEY` from Spin variables. Point them at `https://api.openai.com/v1`
  (hosted, no GPU) **or** at your Phase 06 gateway (`/v1`, your own vLLM, inherits your token
  limits): the exact `base_url` swap from 07/lab-04, now inside a function. *(Anthropic's API
  is `/v1/messages`, a different shape; to use Claude directly you'd build that request body,
  or front Anthropic behind your gateway so the function still speaks OpenAI.)*

> **Reachability, the wrinkle that teaches "where does it run."** Outbound hosts must be in
> `spin.toml`'s `allowed_outbound_hosts`, **and** be reachable from where the function runs.
> Local `spin up` runs on your laptop, so it can reach `localhost:8080` (your port-forwarded
> gateway) *and* public APIs. Once **deployed** to Akamai Functions, the function runs in
> Akamai's cloud: it can reach `api.openai.com` (public) but **cannot** reach your laptop's
> port-forward. To call *your* model from a deployed function, your gateway needs a public
> address, which is exactly Phase 09's LKE **NodeBalancer** URL. Hosted-vs-sovereign is not
> a key swap; it's a reachability decision about where each piece lives.

## 0. Prereqs

- The `spin` CLI and the `hello-spin` toolchain from lab-01; the `aka` plugin + preview
  access from lab-04 (only needed for Step 5's deploy; Steps 1–4 run locally).
- **An OpenAI API key** exported as `OPENAI_API_KEY`; `build-corpus.mjs` (Step 1) and the
  local-test commands (Step 4) read it. (Or substitute your Phase 10 `vllm-embed` endpoint
  + your gateway; swap the URLs/models accordingly.) Sign up at platform.openai.com if you
  don't have one.
- **Node.js installed**; `build-corpus.mjs` is a one-time Node script that embeds the
  corpus offline.
- **Cost note:** every embeddings call (corpus build + each query) and every chat call is
  billed by OpenAI. The corpus here is three lines, so it's pennies, though not free.

## 1. Scaffold the function and bake the corpus

```bash
spin new -t http-js rag-fn --accept-defaults   # -t = template; http-js scaffolds a JS HTTP handler + spin.toml
cd rag-fn
```

- `-t http-js` picks the JavaScript HTTP component template (the same toolchain you fetched in
  lab-01). `--accept-defaults` skips the interactive prompts (name/description) so it scaffolds
  non-interactively.
- **What you should see:** a new `rag-fn/` dir with `spin.toml`, `package.json`, and a
  `src/index.js` stub; that stub is where the Step 2 handler goes, and the `spin.toml` is what you
  edit in Step 3.

Precompute embeddings for a tiny corpus **offline** (run once; needs an embeddings endpoint,
hosted, or your Phase 10 `vllm-embed` via port-forward) and write `corpus.json`:

```js
// build-corpus.mjs  - run with: node build-corpus.mjs   (one-time, at build)
import { writeFileSync } from "node:fs";
const EMBED = process.env.EMBED_URL ?? "https://api.openai.com/v1/embeddings";
const KEY   = process.env.EMBED_KEY ?? process.env.OPENAI_API_KEY;
const MODEL = process.env.EMBED_MODEL ?? "text-embedding-3-small";  // or BAAI/bge-small-en-v1.5 on your vllm-embed

const chunks = [
  "Acme Cloud's Osaka region code is as-07.",
  "Block Storage volumes on Acme Cloud cap at 10 TiB each.",
  "Acme Cloud bills GPU nodes per second with a one-minute minimum.",
];
async function embed(text) {
  const r = await fetch(EMBED, { method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${KEY}` },
    body: JSON.stringify({ input: text, model: MODEL }) });
  return (await r.json()).data[0].embedding;
}
const corpus = [];
for (const text of chunks) corpus.push({ text, vec: await embed(text) });
writeFileSync("corpus.json", JSON.stringify(corpus));
console.log(`baked ${corpus.length} chunks`);
```

Run it once. With only `OPENAI_API_KEY` exported it uses the hosted defaults; the three
`process.env.* ??` fallbacks are the *one place* you redirect it at your own platform.
Override them to embed against your Phase 10 `vllm-embed` instead:

```bash
node build-corpus.mjs                                 # hosted: reads OPENAI_API_KEY, text-embedding-3-small
# - or point it at your own embeddings model (Phase 10), port-forwarded to localhost:8081 -
EMBED_URL=http://localhost:8081/v1/embeddings \
  EMBED_MODEL=BAAI/bge-small-en-v1.5 EMBED_KEY=unused node build-corpus.mjs
```

- `EMBED_URL` / `EMBED_MODEL` / `EMBED_KEY` map 1:1 to the script's three env reads; the
  vectors must be produced by the **same model** you'll query with at runtime, or cosine
  scores are meaningless (you can't compare vectors from different embedding spaces).
- **What you should see:** `baked N chunks` printed and a new `corpus.json` on disk. Open it:
  each entry is `{ text, vec: [ ...floats ] }`, your chunks plus their embeddings, computed
  *now* so the function never has to embed the corpus at request time.

`corpus.json` ships *inside* the `.wasm` bundle, your in-memory "DB."

## 2. Write the RAG agent handler

Inside the handler the `http-js` template scaffolds (see the
[Spin JS SDK](https://spinframework.dev/v3/javascript-components) for the exact entry
signature and the variables/KV imports), the agent logic is plain JS: `fetch` for outbound,
cosine in a few lines.

> **This block is illustrative, not copy-paste-runnable.** `spinVar(...)` is a stand-in for
> the SDK's real variable accessor (you import it; see the SDK link), and the template's
> request-handler signature that calls `ragAnswer(query)` isn't shown. Wire those two from
> the SDK docs; the retrieval/prompt/call logic in between is the part to study.

```js
import corpus from "./corpus.json";   // baked-in chunks + vectors (step 1)

// --- config from Spin variables (set in spin.toml; see SDK for the exact import) ---
const LLM_BASE = spinVar("llm_base_url");   // e.g. https://api.openai.com/v1  OR your gateway /v1
const LLM_KEY  = spinVar("llm_api_key");
const EMB_BASE = spinVar("embed_base_url"); // embeddings endpoint
const MODEL    = "gpt-4o-mini";             // or Qwen/Qwen2.5-0.5B-Instruct via your gateway

const dot = (a, b) => a.reduce((s, x, i) => s + x * b[i], 0);
const cos = (a, b) => dot(a, b) / (Math.hypot(...a) * Math.hypot(...b));

async function post(url, key, body) {
  const r = await fetch(url, { method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
    body: JSON.stringify(body) });
  return r.json();
}

async function ragAnswer(question) {
  // 1. embed the query (the corpus is already embedded, offline)
  const qvec = (await post(`${EMB_BASE}/embeddings`, LLM_KEY,
                 { input: question, model: "text-embedding-3-small" })).data[0].embedding;
  // 2. in-memory cosine search, top-2, with a relevance floor (covered in Phase 10)
  //    0.3 is a low bar on cosine's 0..1 scale: "drop chunks not even loosely related."
  //    Tune per corpus - raise it to be stricter. (gpt-4o-mini below is just an example
  //    OpenAI model; swap it, like text-embedding-3-small, for any you prefer.)
  const hits = corpus.map(c => ({ ...c, score: cos(qvec, c.vec) }))
                     .sort((a, b) => b.score - a.score).slice(0, 2)
                     .filter(h => h.score >= 0.3);
  const context = hits.map(h => h.text).join("\n") || "(no relevant context)";
  // 3. grounded generation through the provider-agnostic base_url
  const out = await post(`${LLM_BASE}/chat/completions`, LLM_KEY, {
    model: MODEL, max_tokens: 200,
    messages: [
      { role: "system", content: "Answer ONLY from the context. If it's not there, say you don't know." },
      { role: "user", content: `Context:\n${context}\n\nQuestion: ${question}` },
    ],
  });
  return out.choices[0].message.content;
}
// call ragAnswer(query) from the template's request handler and return its text as the response.
```

## 3. Declare permissions in `spin.toml`

The single most important lines, the Phase 08 lab-03 sandbox lesson now load-bearing:

```toml
[component.rag-fn]
# WITHOUT these the fetch calls fail with "Destination not allowed" - Spin denies
# outbound by default. List your LLM host AND your embeddings host (and your gateway,
# if you point there).
allowed_outbound_hosts = [
  "https://api.openai.com:443",
  # "https://<your-lke-gateway-host>:443",   # for the sovereign variant (step 6, Phase 09)
]
key_value_stores = ["default"]              # optional: cache query embeddings / session state

[variables]
llm_api_key = { required = true }
[component.rag-fn.variables]
llm_base_url   = "https://api.openai.com/v1"
embed_base_url = "https://api.openai.com/v1"
llm_api_key    = "{{ llm_api_key }}"
```

> **Stateful option:** for caching repeated query embeddings or holding short conversation
> history, open the Akamai **KV store** (`key_value_stores = ["default"]` →
> `Kv.openDefault()`). It's persisted and globally replicated but **app-scoped** and **not**
> strongly consistent: a read isn't guaranteed to see the latest write (`wasi:keyvalue/atomic`,
> the interface for safe compare-and-set, is unsupported), so it's fine as a cache, not for a
> counter you need to be exact. See the
> [KV store docs](https://techdocs.akamai.com/akamai-functions/docs/use-the-key-value-store).

## 4. Test locally

```bash
SPIN_VARIABLE_LLM_API_KEY=$OPENAI_API_KEY spin build && \
SPIN_VARIABLE_LLM_API_KEY=$OPENAI_API_KEY spin up &
curl -s -X POST http://127.0.0.1:3000/ -d '{"q":"What is the Osaka region code?"}'
```

- `SPIN_VARIABLE_LLM_API_KEY=...` is how Spin sets the **`llm_api_key`** variable you marked
  `required = true` in Step 3; the env-var name is `SPIN_VARIABLE_` + the variable name,
  upper-cased. Leave it unset and `spin up` refuses to start (the required variable has no
  value). It must be present for both `build` (compiles the module + bundles `corpus.json`)
  and `up` (runs it), hence repeated on each.
- `&&` runs `up` only if `build` succeeds; the trailing `&` backgrounds `spin up` so the same
  shell can `curl` it. (When done: `kill %1`, or `fg` then Ctrl-C.)
- `spin up` serves on **`127.0.0.1:3000`** by default; `-d '{"q":...}'` is the request body
  your handler parses for the question. `-X POST` because the handler reads the body.

**What to look for:** an answer containing **`as-07`**, pulled from your baked corpus, not
the model's memory (the facts are fictional on purpose). Ask `"What is 2+2?"` and the
grounding instruction makes it abstain or answer plainly: retrieval found nothing relevant
(score floor), and the prompt says don't invent.

## 5. Deploy to Akamai Functions

```bash
spin aka deploy        # prints your stable public URL (lab-04)
curl -s -X POST https://<your-fn-url>/ -d '{"q":"What is the block storage volume cap?"}'
```

- `spin aka deploy` is the `aka` plugin (lab-04): it pushes the built `.wasm` (with
  `corpus.json` baked in) to Akamai Functions and returns a **stable, public HTTPS URL**, no
  port-forward, reachable from anywhere. The deploy carries the variable values from
  `spin.toml`; the `required` `llm_api_key` is set the same way at deploy time (see lab-04).
- The URL is `https` and public, the inverse of the localhost reachability rule above: a
  *caller* anywhere can now reach your function, but the *function* still can only reach hosts
  it's allow-listed AND that are publicly reachable (this is the Step 6 / Phase 09 hinge).

**What to look for:** the same grounded answer (**`10 TiB`**), now from a serverless function
that costs nothing at idle. You have a RAG agent with no server, no vector DB, no GPU, until
you want one.

## 6. The provider swap: hosted → your own platform

Change one variable to point at your model instead of OpenAI, but it's **three
coordinated edits** in `spin.toml`, because the variable swap alone would be denied by the
sandbox and answered by the wrong model:

```toml
[component.rag-fn]
allowed_outbound_hosts = [
  "https://<your-lke-gateway-host>:443",   # 1. allow the gateway - else "Destination not allowed"
]

[component.rag-fn.variables]
llm_base_url = "https://<your-lke-gateway-host>/v1"   # 2. point the base_url at the Phase 06 gateway on LKE (Phase 09)
```

```js
const MODEL = "Qwen/Qwen2.5-0.5B-Instruct";   // 3. the model your vLLM serves (in the handler)
```

All three change together: (1) the host must be **allow-listed** (Step 3's deny-by-default,
note the `:443`), (2) `llm_base_url` repoints the OpenAI-protocol client, and (3) `MODEL` must
name a model your vLLM serves (`gpt-4o-mini` isn't on your gateway). Then `spin aka deploy`
again. **Same function, now sovereign:** it calls *your* vLLM through *your* gateway and
inherits its token budget and prompt guards. That's the 07/lab-04 bridge inside a serverless
function. (Remember the reachability rule: a *deployed* function needs your gateway's
*public* LKE URL, not a localhost port-forward.)

## Break it, then read the error

Remove your LLM host from `allowed_outbound_hosts`, rebuild, and call it:

```toml
allowed_outbound_hosts = []   # forbid all outbound
```

**Read it.** The function still starts and accepts the request, then the LLM `fetch` fails
with **"Destination not allowed."** The Wasm *sandbox* denied the socket; nothing about the
deploy or the platform is broken. This is the lab-03 sandbox lesson at production stakes:
your function's network is *deny-by-default*, the failure is invisible to Akamai's control
plane (the function is "healthy"), and it surfaces only in the function's response/logs. Put
the host back. The capability you grant is exactly the capability it has, no more.

## Checkpoint: you can now explain…

1. **The three operational models for an agent.** Process (Strands), Kubernetes object
   (kagent), serverless function (Akamai Functions): same loop, different runtime and cost
   curve; you choose by warmth and ops, not by what the agent *is*.
2. **When in-memory RAG beats a vector DB.** Small, static corpus + zero-infra + scale-to-zero
   → bake vectors in, brute-force cosine; graduate to Phase 10's Qdrant when it grows.
3. **Why provider-agnostic is one `base_url`.** The function is an OpenAI-protocol client;
   point it at a hosted API or your gateway. (Anthropic's `/v1/messages` needs its own shape
   or to sit behind your gateway.)
4. **Why `allowed_outbound_hosts` is load-bearing, and the reachability rule.** Spin denies
   outbound by default; and a *deployed* function can only reach *public* hosts, so calling
   your own model means giving the gateway a public LKE address (Phase 09).

You can now:
- [ ] Build a serverless RAG agent in Spin JS with a baked-in in-memory corpus.
- [ ] Make its LLM call provider-agnostic and swap hosted ↔ your own gateway.
- [ ] Allow-list outbound hosts and explain the deny-by-default sandbox + reachability.
- [ ] Place an agent in the right runtime: process, k8s object, or function.

## What you proved across Phase 08

You built a `.wasm` (01), taught a cluster to run it (02), ran it as a `SpinApp` shaping vLLM
(03), deployed the same module managed on **Akamai Functions** (04), and turned it into a
**serverless RAG agent** that calls a hosted model *or* your own platform (05). The Spin floor
now spans self-managed *and* managed, adds the **third home for an agent**, and shows RAG
without a database, all on Akamai Cloud, all from one portable module.

## Next

→ **Phase 09**: take the self-managed half (SpinKube, your gateway, vLLM) onto real Akamai
**LKE**, where a NodeBalancer gives your gateway the *public* URL this function needs to call
your own model from the cloud.

## Sources

- Akamai Functions: [welcome/onboarding](https://techdocs.akamai.com/akamai-functions/docs/welcome) ·
  [KV store](https://techdocs.akamai.com/akamai-functions/docs/use-the-key-value-store) ·
  [quotas & limits](https://techdocs.akamai.com/akamai-functions/docs/quotas-and-limits)
- Spin JS/TS SDK (handler, variables, KV, outbound):
  <https://spinframework.dev/v3/javascript-components> ·
  outbound + `allowed_outbound_hosts`: <https://spinframework.dev/v3/http-outbound>

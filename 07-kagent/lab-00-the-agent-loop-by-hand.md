# Lab 00: The agent loop by hand, sixty lines and no framework

**What you'll build:** an agent in about sixty lines of plain Python. A loop that sends
messages to the vLLM server you already run, lets the model request a tool, executes that
tool, feeds the result back, and repeats until the model answers. No framework, no SDK, no
operator. Every agent stack in Phases 07 through 13 (Strands, kagent, MCP servers, the
deploy and sandbox phases) is a convenience wrapper around this loop. After this lab you
know what they wrap, so when a framework misbehaves you can name the layer that failed.

**Time:** ~45 min active, plus up to 15 min once for a vLLM restart · **Cost:** free (local kind)

## The problem (why this exists)

This course builds everything from primitives: Phase 01 built a container out of kernel
namespaces before you ever typed `docker run`. The agent phases deserve the same
treatment. If you start at lab-01, the first agent you meet is already a CRD reconciled by
a controller, and the loop inside it stays a black box. So before the controller, the
CRDs, and the protocols, you build the loop itself, against the same vLLM the rest of the
phase uses:

```
  user task ──► messages[] ──► POST /v1/chat/completions ──► finish_reason?
                   ▲                                              │
                   │  append the tool result                      │ "tool_calls"
                   └────── your code runs the tool ◄──────────────┘
                                          ("stop" ──► print the answer, done)
```

The model is a text-in, text-out function on the other side of an HTTP call. Everything
that makes it an agent (memory, action, recovery) lives in your loop.

One expectation to set now: you are on CPU vLLM with a 0.5B-parameter model. Single calls
can take tens of seconds, and a model this small is a clumsy tool caller. Both facts are
useful here. The slowness gives you time to watch each turn, and the clumsiness forces you
to write the defensive code every real agent runtime contains.

## 1. Setup: a tunnel to your model

Prereq: the vLLM backend from `06-ai-gateway/lab-01` is running (Deployment and Service
named `vllm` in the `default` namespace, serving `Qwen/Qwen2.5-0.5B-Instruct` on port
8000). If `kubectl get deploy vllm` comes back empty, do that lab first.

Forward the Service to your Mac and confirm the model answers:

```bash
kubectl port-forward svc/vllm 8000:8000 &   # tunnel localhost:8000 → the Service's 8000; '&' backgrounds it

curl -s http://localhost:8000/v1/models | python3 -m json.tool
```

> **What you should see:** a JSON list with one entry whose `id` is
> `Qwen/Qwen2.5-0.5B-Instruct`. That exact string goes in every request's `model` field.

### Check whether the server will parse tool calls

A model emits tool calls as text in its own format; the server needs a parser to turn
that text into structured JSON, and vLLM ships with that parser switched off. Test yours
by sending a request with a `tools` field:

```bash
curl -s http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen/Qwen2.5-0.5B-Instruct","messages":[{"role":"user","content":"hi"}],"max_tokens":8,"tools":[{"type":"function","function":{"name":"noop","description":"does nothing","parameters":{"type":"object","properties":{}}}}]}'
```

> **What you should see:** with the stock Phase 06 manifest, a `400` whose error message
> says `"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be
> set`. The server told you the fix. If you instead got a normal completion, your
> Deployment already has the flags; skip the patch below.

Qwen2.5 was trained to write tool calls in the Hermes format, so `hermes` is the parser
to enable. Append the two flags to the Deployment's args:

```bash
kill %1 2>/dev/null   # the patch replaces the Pod, which kills the tunnel anyway

kubectl patch deploy vllm --type=json -p='[
  {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--enable-auto-tool-choice"},
  {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--tool-call-parser"},
  {"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"hermes"}]'

kubectl rollout status deploy/vllm --timeout=900s   # new Pod re-downloads weights; budget up to 15 min
```

The rollout takes as long as the first deploy did: the Phase 06 manifest has no PVC, so
the replacement Pod pulls the weights from Hugging Face again. Leave the flags in place
after this lab; anything else that asks this server for tool calls (including kagent later
in the phase) needs them too.

Re-open the tunnel and re-run the `tools` probe:

```bash
kubectl port-forward svc/vllm 8000:8000 &
```

> **What you should see:** the probe now returns a normal completion instead of a 400.
> The model ignores the useless `noop` tool and answers; all you needed was the server to
> stop rejecting the field.

Last piece of setup: this lab is plain Python plus `requests`, which the repo's venv
already has. The point of skipping the `openai` SDK is that you see the wire format; the
SDK would hide the exact JSON this lab is about.

```bash
cd 07-kagent
source ../.venv/bin/activate
python -c "import requests; print(requests.__version__)"
```

## 2. One call, no loop: the API is a stateless function

Start the script. `agent.py`, first version, is one helper and two calls:

```python
# agent.py, version 1: two calls, no loop yet
import json
import requests

BASE = "http://localhost:8000/v1"
MODEL = "Qwen/Qwen2.5-0.5B-Instruct"   # must match the server's --model arg exactly

def chat(messages, tools=None):
    body = {"model": MODEL, "messages": messages, "max_tokens": 200}
    if tools:
        body["tools"] = tools
    r = requests.post(f"{BASE}/chat/completions", json=body, timeout=300)
    r.raise_for_status()
    return r.json()

if __name__ == "__main__":
    reply = chat([{"role": "user", "content": "My favorite number is 41. Reply with only: ok"}])
    print(json.dumps(reply["choices"][0], indent=2))
    reply = chat([{"role": "user", "content": "What is my favorite number?"}])
    print(reply["choices"][0]["message"]["content"])
```

```bash
python agent.py   # two CPU inference calls; expect tens of seconds each
```

> **What you should see:** the first call prints a `choices[0]` object with
> `"finish_reason": "stop"` and a `message` of role `assistant`. The second call has no
> idea what your favorite number is.

Read the shape of `choices[0]` carefully; the whole lab lives in two of its fields.
`message` is what the model said, and `finish_reason` is why it stopped saying it. Right
now `finish_reason` is always `"stop"` (or `"length"` if it ran out of tokens). Section 3
adds a third value.

The second call is the finding. You told the server your favorite number thirty seconds
ago and it has no memory of that, because there is no session. Each request carries the
entire conversation in `messages`, and the response is computed from that request alone.
Chat interfaces fake continuity by resending history every time. Your loop will have to do
the same, which is why the `messages` list is about to become the central data structure
of this file.

## 3. Offer the model a tool

A tool, on the wire, is two things: a plain function your process can run, and a JSON
schema that describes it to the model. Add both to `agent.py`, above `chat()`:

```python
import os   # add to the imports at the top

def read_file(path):
    root = os.path.realpath(os.getcwd())
    full = os.path.realpath(path)
    if not full.startswith(root + os.sep):
        return f"error: {path} is outside the working directory"
    try:
        with open(full) as f:
            return f.read()[:800]    # cap it: the server's --max-model-len is 1024 tokens total
    except OSError as e:
        return f"error: {e}"

TOOLS = [{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a text file from the current directory and return its contents.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "relative path to the file"},
            },
            "required": ["path"],
        },
    },
}]
```

Then replace the `__main__` block with a single call that offers the tool:

```python
if __name__ == "__main__":
    messages = [{"role": "user", "content": "Use the read_file tool to read notes.txt."}]
    reply = chat(messages, tools=TOOLS)
    print(json.dumps(reply["choices"][0], indent=2))
```

The task phrasing is deliberate. "Use the read_file tool" names the tool outright, because
a 0.5B model given a subtle hint will often answer from imagination instead. Bigger models
need less steering; the wire format is identical either way.

```bash
python agent.py
```

> **What you should see:** `finish_reason` changed value, and `content` is empty:
>
> ```json
> {
>   "finish_reason": "tool_calls",
>   "message": {
>     "role": "assistant",
>     "content": null,
>     "tool_calls": [
>       {
>         "id": "chatcmpl-tool-9f2ab41c",
>         "type": "function",
>         "function": {
>           "name": "read_file",
>           "arguments": "{\"path\": \"notes.txt\"}"
>         }
>       }
>     ]
>   }
> }
> ```
>
> Your `id` and formatting will differ; the shape is what matters.

Three observations before you write another line of code:

- **Nothing was executed.** No file was read. The model emitted a request, your process
  received it, and both are now waiting. The model has no hands; your code is the hands,
  and it hasn't moved yet.
- **`arguments` is a string, and the model wrote it.** That is JSON the model typed
  token by token, not a structure the server validated for you. It happens to be
  well-formed here. It will not always be, and section 5 makes that your problem on
  purpose.
- **The schema is prompt engineering.** Compare `usage.prompt_tokens` with your section 2
  calls: it went up, because the chat template pasted your tool schema into the prompt as
  text. A tool definition is instructions to the model with a wire format attached, which
  is also why a system prompt can talk the model out of using it.

## 4. Close the loop

Now the agent. The contract has four steps: append the assistant message exactly as
received, execute what it asked for, append the result as a `role: "tool"` message tagged
with the `tool_call_id`, and call the model again. Repeat until `finish_reason` is
`"stop"`.

Give the tool something to read:

```bash
cat > notes.txt <<'EOF'
The cluster runs kind on Colima.
vLLM serves Qwen2.5-0.5B-Instruct from the default namespace.
Do not delete the llm namespace.
EOF
```

Add a dispatcher above the `__main__` block. It owns the two failure modes a model-written
argument string can have: invalid JSON, and valid JSON with the wrong keys. In both cases
it returns error text instead of raising, a choice the Break-it sections justify:

```python
def run_tool(name, raw_args):
    if name != "read_file":
        return f"error: unknown tool {name}"
    try:
        args = json.loads(raw_args)
        return read_file(**args)
    except (json.JSONDecodeError, TypeError) as e:
        return f"error: bad arguments {raw_args!r}: {e}"
```

Replace the `__main__` block with the loop. This is the whole agent:

```python
import sys   # add to the imports at the top

SYSTEM = ("You are a file assistant. You cannot see a file's contents until you "
          "call the read_file tool. Base your answers on tool results.")

if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or \
        "Use the read_file tool to read notes.txt, then say in one sentence what it says."
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": task}]

    turn = 0
    while True:
        turn += 1
        if turn > 6:
            print("giving up: too many turns")
            break
        print(f"--- turn {turn}: sending {len(messages)} messages, "
              f"{len(json.dumps(messages))} bytes of history ---")
        choice = chat(messages, tools=TOOLS)["choices"][0]
        msg = choice["message"]
        messages.append(msg)                       # the model's turn joins the history
        if choice["finish_reason"] != "tool_calls":
            print("assistant:", msg["content"])
            break
        for call in msg["tool_calls"]:
            name = call["function"]["name"]
            raw_args = call["function"]["arguments"]
            print(f"model asked for: {name}({raw_args})")
            result = run_tool(name, raw_args)
            print(f"tool returned: {result[:120]}")
            messages.append({"role": "tool",              # your turn joins it too
                             "tool_call_id": call["id"],
                             "content": result})
```

```bash
python agent.py
```

> **What you should see:** two turns, tens of seconds each on CPU:
>
> ```
> --- turn 1: sending 2 messages, 391 bytes of history ---
> model asked for: read_file({"path": "notes.txt"})
> tool returned: The cluster runs kind on Colima.
> --- turn 2: sending 4 messages, 941 bytes of history ---
> assistant: The file says the cluster runs kind on Colima with vLLM serving
> Qwen2.5-0.5B-Instruct, and the llm namespace must not be deleted.
> ```
>
> Your byte counts and the exact sentence will differ. If the model answers on turn 1
> without calling the tool, run it again; a 0.5B model is inconsistent, and that
> inconsistency is real data about what frameworks have to absorb.

Watch the numbers in the turn headers. The history grew from 2 messages to 4, and every
one of those bytes was re-sent on turn 2, because section 2 taught you the server keeps
nothing. This is the quiet cost of the loop: context grows every turn, tokens are billed
on the whole history every call, and on this server `--max-model-len=1024` is a hard
wall. Let a conversation run long enough and vLLM returns a 400 about maximum context
length. Real agent runtimes summarize, truncate, or window the history; your loop keeps
everything, so you can see exactly what they are managing.

The `turn > 6` guard is two lines of harness. Without it, a model that keeps requesting
tools would loop forever at your expense. Budgets like this are the subject of lab-05.

## 5. The schema is a suggestion

The JSON schema in `TOOLS` looks like a contract. Test how much it enforces.

First, ask for something the tool must refuse:

```bash
python agent.py "Use the read_file tool to read /etc/passwd, then summarize it."
```

> **What you should see:** the model calls `read_file({"path": "/etc/passwd"})`, your
> guard returns `error: /etc/passwd is outside the working directory`, and the model's
> final answer reports it could not read the file.

Nothing in the schema said "current directory only"; the description did, and the model
ignored it. The model asked anyway and the three-line `realpath` check in `read_file` was
the enforcement. This split shows up at every scale: the schema shapes what the model
tends to write, and your code decides what runs. (`../README.md` gets the same refusal;
`realpath` resolves the `..` before the check.)

Second, feed the dispatcher the argument strings a sloppy model produces. You cannot
force the model to write bad JSON on demand, so exercise the branch directly:

```bash
python - <<'EOF'
from agent import run_tool
print(run_tool("read_file", '{"path": notes.txt}'))        # invalid JSON: unquoted value
print(run_tool("read_file", '{"filename": "notes.txt"}'))  # valid JSON, wrong key
EOF
```

> **What you should see:** two `error: bad arguments ...` lines, one from
> `json.loads` and one from calling `read_file()` with a keyword it doesn't accept.
> No traceback. Both strings are things a small model will eventually send you.

Third, steer the choice from the system prompt. Change `SYSTEM` in `agent.py` to:

```python
SYSTEM = "Never use tools. Answer from your own knowledge."
```

Run the default task again. The model usually answers on turn 1 with `finish_reason`
`"stop"` and an invented summary of a file it never read; a model this small may still
call the tool sometimes. Either outcome makes the point: the `tools` field is an offer,
the system prompt is steering, and neither is control. Put the original `SYSTEM` back
before continuing.

## The whole agent

The complete file you built, just over eighty lines with comments and whitespace:

```python
# agent.py: the agent loop with no framework
import json
import os
import sys
import requests

BASE = "http://localhost:8000/v1"
MODEL = "Qwen/Qwen2.5-0.5B-Instruct"   # must match the server's --model arg exactly

SYSTEM = ("You are a file assistant. You cannot see a file's contents until you "
          "call the read_file tool. Base your answers on tool results.")

def read_file(path):
    root = os.path.realpath(os.getcwd())
    full = os.path.realpath(path)
    if not full.startswith(root + os.sep):
        return f"error: {path} is outside the working directory"
    try:
        with open(full) as f:
            return f.read()[:800]    # cap it: the server's --max-model-len is 1024 tokens total
    except OSError as e:
        return f"error: {e}"

TOOLS = [{
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read a text file from the current directory and return its contents.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "relative path to the file"},
            },
            "required": ["path"],
        },
    },
}]

def chat(messages, tools=None):
    body = {"model": MODEL, "messages": messages, "max_tokens": 200}
    if tools:
        body["tools"] = tools
    r = requests.post(f"{BASE}/chat/completions", json=body, timeout=300)
    r.raise_for_status()
    return r.json()

def run_tool(name, raw_args):
    if name != "read_file":
        return f"error: unknown tool {name}"
    try:
        args = json.loads(raw_args)
        return read_file(**args)
    except (json.JSONDecodeError, TypeError) as e:
        return f"error: bad arguments {raw_args!r}: {e}"

if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) or \
        "Use the read_file tool to read notes.txt, then say in one sentence what it says."
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": task}]

    turn = 0
    while True:
        turn += 1
        if turn > 6:
            print("giving up: too many turns")
            break
        print(f"--- turn {turn}: sending {len(messages)} messages, "
              f"{len(json.dumps(messages))} bytes of history ---")
        choice = chat(messages, tools=TOOLS)["choices"][0]
        msg = choice["message"]
        messages.append(msg)
        if choice["finish_reason"] != "tool_calls":
            print("assistant:", msg["content"])
            break
        for call in msg["tool_calls"]:
            name = call["function"]["name"]
            raw_args = call["function"]["arguments"]
            print(f"model asked for: {name}({raw_args})")
            result = run_tool(name, raw_args)
            print(f"tool returned: {result[:120]}")
            messages.append({"role": "tool",
                             "tool_call_id": call["id"],
                             "content": result})
```

An agent is a while loop, an HTTP call, a list, and a dispatch function. Hold onto that
when the phase starts stacking controllers and protocols on top.

## Break it: kill the tunnel mid-loop

The loop's model calls ride your port-forward. Take it away while the agent is thinking.
Start a run, and while turn 1 is in flight (CPU inference gives you a window of tens of
seconds), kill the tunnel from a second terminal:

```bash
python agent.py                          # terminal 1: start a run

pkill -f "port-forward svc/vllm"         # terminal 2: cut the tunnel while turn 1 runs
```

> **What you should see:** a Python traceback ending in
> `requests.exceptions.ConnectionError` (a connection reset or refusal on
> `localhost:8000`). The process is dead.

Read what died with it. The `messages` list, the only memory this agent had, lived in
process RAM, so the conversation is unrecoverable; nothing restarts the loop, because the
loop's runtime is one terminal on your Mac. Every "agent platform" feature is aimed at
this traceback: retries around the HTTP call, a supervisor that restarts the loop (kagent
runs it in a Pod a controller keeps alive, lab-01), and state that survives the process
(12-agent-deploy). Re-open the tunnel before continuing:

```bash
kubectl port-forward svc/vllm 8000:8000 &
```

Re-running `agent.py` starts a blank conversation. That is the correct behavior of the
code you wrote, and the problem later phases exist to fix.

## Break it again: ask for a file that does not exist

```bash
python agent.py "Use the read_file tool to read missing.txt, then say what it contains."
```

> **What you should see:** on turn 1 the tool returns
> `error: [Errno 2] No such file or directory: '/.../07-kagent/missing.txt'`, and on turn 2 the model
> answers that the file does not exist. No crash, and the loop ended normally with
> `finish_reason` `"stop"`.

The design choice doing the work is in `read_file`: an `OSError` becomes a string, and
that string goes back to the model as an ordinary tool result. The model reads the error
the same way it reads file contents, as context, and gets a chance to recover: report the
problem, try a different path, or ask you for help. Swap that `except OSError` for a bare
`raise` and the same run is a traceback on turn 1 with the conversation lost. Errors as
tool results keep the loop alive; kagent's runtime makes the same choice when a tool call
fails (you'll read that log line in lab-03).

## Where this loop goes next

Everything ahead of you in this phase and beyond is one of these sixty lines, grown up.
The `while` loop with its dispatch and budget is what Strands calls its event loop, the
thing `agents/` and lab-04 hand you pre-built with retries and hooks. The `read_file`
function plus its JSON schema is a tool server in miniature: MCP turns that pairing into a
protocol, and kagent models it as a `RemoteMCPServer` with `toolNames` doing the job of
your `run_tool` allow-list (lab-03). And the `messages` list is session state, which is
why 12-agent-deploy spends its time persisting conversations: you watched that state die
with a `pkill`, and a platform's job is to make sure it doesn't.

## Checkpoint: you can now explain…

1. **Why does an agent loop have to resend the whole conversation every call?** The chat
   completions API is stateless: each response is computed from the `messages` in that
   request alone. Whatever memory the agent has is the list your code maintains, so
   context grows every turn and something eventually has to trim it.
2. **What happens between `finish_reason: "tool_calls"` and the next model call?** Your
   code, and only your code: it reads the `tool_calls` array, parses the argument string
   the model wrote, decides whether to honor it, runs the function, and appends a
   `role: "tool"` message carrying the `tool_call_id` and the result. The model never
   executes anything.
3. **What does the tool schema enforce?** The model's tendencies, nothing more. It is
   text pasted into the prompt. The model can ignore the description, write malformed
   JSON, or use wrong keys; enforcement is your dispatcher's parsing and your function's
   guards.
4. **Why return error text to the model instead of raising?** An exception kills the loop
   and the conversation with it. An error string is context the model can reason over and
   recover from. Tool failures are conversation, crashes are data loss.

You can now:
- [ ] Write the wire shape of a tool call request, a `tool_calls` response, and a
      `role: "tool"` result message from memory.
- [ ] Point at the line where "the model decides" ends and "your code decides" begins.
- [ ] Explain what `--enable-auto-tool-choice` and `--tool-call-parser hermes` switch on
      in vLLM, and how you'd discover they were missing.
- [ ] Say what a framework would have to add to this file before you'd trust it with a
      real task (retries, history management, persistence, supervision).

## Next

→ `lab-01-install-kagent.md`: you built the loop and watched it die with its terminal.
Now install the controller that runs this same loop as a Kubernetes object it keeps
alive, and meet the CRDs that describe it.

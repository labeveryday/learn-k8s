"""The process Kubernetes runs.

An agent is a client, not a server: it dials out to a model and (optionally)
to Discord, and nothing dials in. Kubernetes still needs an HTTP surface to
probe, and you still want a way to trigger and inspect reviews. Flask
provides both on port 8080:

    GET  /healthz   liveness: the process is up and can respond
    GET  /readyz    readiness: the repo is cloned; Discord (if enabled) is connected
    GET  /status    what the agent has done: last review, feedback count
    POST /review    run a review now; returns the report

Discord is optional. When DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID are set,
a gateway client runs in a daemon thread: reviews get posted to the channel,
and channel messages get conversational replies. Without them the HTTP
surface is the whole interface.
"""

import asyncio
import datetime
import os
import threading

from flask import Flask, jsonify

import agent
import tools

app = Flask(__name__)
review_lock = threading.Lock()

STATE = {
    "last_review_at": None,
    "last_report": None,
    "reviews_run": 0,
    "discord_enabled": bool(os.getenv("DISCORD_BOT_TOKEN") and os.getenv("DISCORD_CHANNEL_ID")),
    "discord_ready": False,
}


def run_review_now() -> str:
    report = agent.run_review()
    STATE["last_review_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    STATE["last_report"] = report
    STATE["reviews_run"] += 1
    _post_to_discord(f"🔍 **Review**\n{report[:1800]}")
    return report


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/readyz")
def readyz():
    problems = []
    if not tools.repo_ready():
        problems.append("repository not cloned at " + str(tools.REPO_DIR))
    if STATE["discord_enabled"] and not STATE["discord_ready"]:
        problems.append("discord gateway not connected")
    if problems:
        return jsonify({"ready": False, "problems": problems}), 503
    return {"ready": True}


@app.get("/status")
def status():
    return {
        "repo": str(tools.REPO_DIR),
        "reviews_run": STATE["reviews_run"],
        "last_review_at": STATE["last_review_at"],
        "last_report": STATE["last_report"],
        "feedback_entries": tools.feedback_entries(),
        "discord": "connected" if STATE["discord_ready"] else
                   ("enabled" if STATE["discord_enabled"] else "disabled"),
    }


@app.post("/review")
def review():
    if not review_lock.acquire(blocking=False):
        return jsonify({"error": "a review is already running"}), 409
    try:
        return {"report": run_review_now()}
    finally:
        review_lock.release()


# ---------------------------------------------------------------------------
# Optional Discord gateway, in a daemon thread with its own event loop.
# ---------------------------------------------------------------------------

_discord_loop = None
_discord_channel_id = int(os.getenv("DISCORD_CHANNEL_ID", "0"))


def _post_to_discord(text: str) -> None:
    if _discord_loop is None:
        return
    asyncio.run_coroutine_threadsafe(_send(text), _discord_loop)


if STATE["discord_enabled"]:
    import discord

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    async def _send(text: str) -> None:
        channel = client.get_channel(_discord_channel_id) or await client.fetch_channel(_discord_channel_id)
        await channel.send(text[:1900])

    @client.event
    async def on_ready():
        global _discord_loop
        _discord_loop = asyncio.get_running_loop()
        STATE["discord_ready"] = True
        await _send("👋 Review agent online. Message me about the repo, or POST /review to the service.")

    @client.event
    async def on_message(message):
        if message.author.bot or message.channel.id != _discord_channel_id:
            return
        reply = await asyncio.to_thread(agent.chat, message.content.strip())
        await message.channel.send(reply[:1900] or "(no reply)")

    def _run_discord():
        asyncio.run(client.start(os.getenv("DISCORD_BOT_TOKEN")))

    threading.Thread(target=_run_discord, daemon=True, name="discord").start()


if __name__ == "__main__":
    # Flask's built-in server is fine for a single-replica internal workload.
    # Front it with a WSGI server (gunicorn) if this ever serves real traffic.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), threaded=True)

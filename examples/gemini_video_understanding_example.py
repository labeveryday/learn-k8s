#!/usr/bin/env python3
"""
Gemini Video Understanding Example (Strands Agent)

Demonstrates using the `understand_video` tool (Gemini 3 Pro/Flash) for:
- YouTube URL video understanding (preview)
- Local file understanding via Files API upload
- Local small file understanding via inline bytes (<20MB by default)

Includes hub integration for session tracking and metrics.
"""

import os
import sys
import argparse
import time
from pathlib import Path

# Add repo root to path for imports
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv()

from strands import Agent
from src.tools.gemini_video_understanding import understand_video
from src.models import gemini_model
from src.hub import (
    create_session_manager,
    MetricsExporter,
    AgentRegistry,
)
from src.hub.session import generate_run_id


AGENT_ID = "gemini-video-understanding"
AGENT_NAME = "Gemini Video Understanding"


def _apply_gemini3_thought_signature_workaround() -> None:
    """
    Workaround for Gemini 3 + Strands tool calling.

    Gemini 3 requires "thought signatures" to be circulated for function calling.
    Some Strands versions do not yet propagate the signature on tool/function_call parts
    into the next request, which can cause:
      400 INVALID_ARGUMENT: Function call is missing a thought_signature ...
      400 INVALID_ARGUMENT: Corrupted thought signature.

    IMPORTANT: The signature must be preserved as raw bytes throughout - any string
    encoding/decoding round-trips will corrupt it.
    """
    import base64

    try:
        from google import genai as _genai  # type: ignore
        from strands.models import gemini as strands_gemini  # type: ignore
        from strands.event_loop import streaming as strands_streaming  # type: ignore
    except Exception:
        return

    # Patch GeminiModel._format_chunk: include signature on toolUse start blocks
    if not hasattr(strands_gemini.GeminiModel, "_orig_format_chunk"):
        strands_gemini.GeminiModel._orig_format_chunk = strands_gemini.GeminiModel._format_chunk  # type: ignore[attr-defined]

        def _patched_format_chunk(self, event):  # type: ignore[no-untyped-def]
            if event.get("chunk_type") == "content_start" and event.get("data_type") == "tool":
                part = event.get("data")
                sig = getattr(part, "thought_signature", None) if part is not None else None
                if sig:
                    # Preserve signature as base64 to avoid corruption during transport
                    if isinstance(sig, (bytes, bytearray)):
                        sig_b64 = base64.b64encode(sig).decode("ascii")
                    else:
                        # If it's already a string, assume it might be base64 or preserve as-is
                        sig_b64 = str(sig)
                    return {
                        "contentBlockStart": {
                            "start": {
                                "toolUse": {
                                    "name": part.function_call.name,
                                    "toolUseId": part.function_call.name,
                                    "signature": sig_b64,
                                    "signature_is_b64": True,
                                }
                            }
                        }
                    }
            return strands_gemini.GeminiModel._orig_format_chunk(self, event)  # type: ignore[attr-defined]

        strands_gemini.GeminiModel._format_chunk = _patched_format_chunk  # type: ignore[method-assign]

    # Patch streaming handlers: preserve signature while constructing ToolUse blocks
    if not hasattr(strands_streaming, "_orig_handle_content_block_start"):
        strands_streaming._orig_handle_content_block_start = strands_streaming.handle_content_block_start  # type: ignore[attr-defined]

        def _patched_handle_content_block_start(event):  # type: ignore[no-untyped-def]
            current = strands_streaming._orig_handle_content_block_start(event)  # type: ignore[attr-defined]
            start = event.get("start", {})
            tool_use = start.get("toolUse")
            if tool_use and isinstance(current, dict):
                if tool_use.get("signature"):
                    current["signature"] = tool_use["signature"]
                if tool_use.get("signature_is_b64"):
                    current["signature_is_b64"] = tool_use["signature_is_b64"]
            return current

        strands_streaming.handle_content_block_start = _patched_handle_content_block_start  # type: ignore[assignment]

    if not hasattr(strands_streaming, "_orig_handle_content_block_stop"):
        strands_streaming._orig_handle_content_block_stop = strands_streaming.handle_content_block_stop  # type: ignore[attr-defined]

        def _patched_handle_content_block_stop(state):  # type: ignore[no-untyped-def]
            # Capture signature before stop finalizes and resets state
            sig = None
            sig_is_b64 = False
            try:
                current_tool = state.get("current_tool_use") or {}
                sig = current_tool.get("signature")
                sig_is_b64 = current_tool.get("signature_is_b64", False)
            except Exception:
                sig = None
            new_state = strands_streaming._orig_handle_content_block_stop(state)  # type: ignore[attr-defined]
            if sig and new_state.get("message") and new_state["message"].get("content"):
                last = new_state["message"]["content"][-1]
                if "toolUse" in last and isinstance(last["toolUse"], dict) and "signature" not in last["toolUse"]:
                    last["toolUse"]["signature"] = sig
                    last["toolUse"]["signature_is_b64"] = sig_is_b64
            return new_state

        strands_streaming.handle_content_block_stop = _patched_handle_content_block_stop  # type: ignore[assignment]

    # Patch GeminiModel request formatting: re-inject signature into function_call parts
    if not hasattr(strands_gemini.GeminiModel, "_orig_format_request_content_part"):
        strands_gemini.GeminiModel._orig_format_request_content_part = (  # type: ignore[attr-defined]
            strands_gemini.GeminiModel._format_request_content_part
        )

        def _patched_format_request_content_part(self, content):  # type: ignore[no-untyped-def]
            if "toolUse" in content:
                tool_use = content["toolUse"]
                sig = tool_use.get("signature")
                if sig:
                    # Decode base64 back to original bytes if needed
                    sig_is_b64 = tool_use.get("signature_is_b64", False)
                    if sig_is_b64 and isinstance(sig, str):
                        try:
                            sig_bytes = base64.b64decode(sig)
                        except Exception:
                            sig_bytes = sig.encode("utf-8")
                    elif isinstance(sig, (bytes, bytearray)):
                        sig_bytes = bytes(sig)
                    else:
                        sig_bytes = str(sig).encode("utf-8")

                    return _genai.types.Part(
                        function_call=_genai.types.FunctionCall(
                            args=tool_use["input"],
                            id=tool_use["toolUseId"],
                            name=tool_use["name"],
                        ),
                        thought_signature=sig_bytes,
                    )
            return strands_gemini.GeminiModel._orig_format_request_content_part(self, content)  # type: ignore[attr-defined]

        strands_gemini.GeminiModel._format_request_content_part = _patched_format_request_content_part  # type: ignore[method-assign]


def _build_single_turn_prompt(args: argparse.Namespace) -> str:
    source_parts = []
    if args.youtube_url:
        source_parts.append(f'youtube_url="{args.youtube_url}"')
    if args.video_path:
        source_parts.append(f'video_path="{args.video_path}"')

    # Escape quotes in the prompt for safe embedding
    escaped_prompt = args.prompt.replace('"', '\\"')

    tool_kwargs = [
        f'prompt="{escaped_prompt}"',
        f'model="{args.model}"',
        f"use_file_api={str(args.use_file_api).lower()}",
    ]
    if args.start_offset_seconds is not None:
        tool_kwargs.append(f"start_offset_seconds={args.start_offset_seconds}")
    if args.end_offset_seconds is not None:
        tool_kwargs.append(f"end_offset_seconds={args.end_offset_seconds}")
    if args.fps is not None:
        tool_kwargs.append(f"fps={args.fps}")
    if args.media_resolution is not None:
        tool_kwargs.append(f'media_resolution="{args.media_resolution}"')
    if args.thinking_level is not None:
        tool_kwargs.append(f'thinking_level="{args.thinking_level}"')

    source_str = ", ".join(source_parts)
    kwargs_str = ", ".join(tool_kwargs)

    return f"""Use the understand_video tool exactly once with {source_str}, {kwargs_str}.

Then answer the user with the tool result, as plain text.

User question/instructions:
{args.prompt}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini 3 video understanding with Strands Agents")
    parser.add_argument(
        "--prompt",
        type=str,
        default="Please summarize the video in 3 sentences. Then create a 5-question quiz with an answer key.",
        help="What to ask Gemini about the video (you can include timestamps like 00:42).",
    )
    parser.add_argument(
        "--youtube-url",
        type=str,
        default=None,
        help="Public YouTube URL to analyze (preview feature).",
    )
    parser.add_argument(
        "--video-path",
        type=str,
        default=None,
        help="Local path to a video file to analyze.",
    )
    parser.add_argument(
        "--model",
        type=str,
        default="gemini-3-flash-preview",
        choices=["gemini-3-flash-preview", "gemini-3-pro-preview"],
        help="Gemini model used inside the tool call.",
    )
    parser.add_argument(
        "--agent-model",
        type=str,
        default="gemini-2.0-flash",
        help=(
            "Model used by the Strands Agent to orchestrate tool calls. "
            "Default uses Gemini 2.x to avoid Gemini 3 thought_signature strictness for function calling."
        ),
    )
    parser.add_argument(
        "--use-file-api",
        action="store_true",
        help="Use Files API upload for local videos (recommended).",
    )
    parser.add_argument(
        "--inline",
        action="store_true",
        help="Force inline video bytes for local videos (<20MB). Implies --use-file-api=False.",
    )
    parser.add_argument(
        "--start-offset-seconds",
        type=int,
        default=None,
        help="Optional clip start offset in seconds.",
    )
    parser.add_argument(
        "--end-offset-seconds",
        type=int,
        default=None,
        help="Optional clip end offset in seconds.",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=None,
        help="Optional frame sampling rate (frames/sec).",
    )
    parser.add_argument(
        "--media-resolution",
        type=str,
        default=None,
        choices=[
            "media_resolution_low",
            "media_resolution_medium",
            "media_resolution_high",
            "media_resolution_ultra_high",
        ],
        help="Optional Gemini 3 media_resolution (v1alpha per docs).",
    )
    parser.add_argument(
        "--thinking-level",
        type=str,
        default=None,
        choices=["minimal", "low", "medium", "high"],
        help="Optional Gemini 3 thinking_level for the tool call.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Interactive agent mode (type 'exit' to quit).",
    )
    parser.add_argument(
        "--no-hub",
        action="store_true",
        help="Disable hub tracking.",
    )

    args = parser.parse_args()

    if not os.getenv("GOOGLE_API_KEY"):
        raise SystemExit("Please set GOOGLE_API_KEY.")

    # Apply workaround early so Gemini 3 can tool-call without missing thought signatures.
    _apply_gemini3_thought_signature_workaround()

    if bool(args.youtube_url) == bool(args.video_path):
        raise SystemExit("Provide exactly one of --youtube-url or --video-path.")

    if args.inline:
        args.use_file_api = False

    # Initialize hub components (unless disabled)
    run_id = None
    registry = None
    metrics = None
    session_manager = None

    if not args.no_hub:
        run_id = generate_run_id(AGENT_ID)

        registry = AgentRegistry()
        registry.register(
            agent_id=AGENT_ID,
            description="Analyze videos using Gemini 3 (upload/inline/YouTube URL)",
            tags=["gemini", "video", "understanding", "multimodal"],
            model_id=args.model,
        )

        metrics = MetricsExporter(agent_id=AGENT_ID, run_id=run_id)
        print(f"Run ID: {run_id}")

        # Create session manager (tracks tool calls + conversation) for both one-shot and interactive
        session_manager = create_session_manager(agent_id=AGENT_ID, run_id=run_id)

    start_time = time.time()
    success = False

    # Agent model (separate from the tool's internal model).
    # Gemini 3 enforces thought signatures for function calling; some wrappers may not yet circulate them correctly.
    # The tool itself still uses Gemini 3 (args.model).
    agent_llm = gemini_model(model_id=args.agent_model)

    agent = Agent(
        model=agent_llm,
        tools=[understand_video],
        session_manager=session_manager,
        system_prompt=f"""You are a video understanding assistant.

You have one tool:
- understand_video: Analyze a video (YouTube URL or local file) and return text answers.

Rules:
- Use the understand_video tool when the user asks about video content.
- Put the user's question AFTER the video part (the tool already does this correctly).
- Return the final answer as plain text.
""",
        name=AGENT_NAME,
    )

    if args.interactive:
        print(f"\n{AGENT_NAME}")
        print("Tool: understand_video")
        print("Tip: Ask questions with timestamps like 00:42.")
        print("Type 'exit' to quit\n")

        question_count = 0
        while True:
            try:
                user_input = input("\nYou: ").strip()
                if user_input.lower() in ["exit", "quit"]:
                    success = True
                    break
                if not user_input:
                    continue

                # In interactive mode we keep the video source fixed from CLI flags,
                # and let the user change only the prompt/question.
                args.prompt = user_input
                response = agent(_build_single_turn_prompt(args))
                print(f"\nAgent: {response}")
                question_count += 1

            except KeyboardInterrupt:
                success = True
                break
        if metrics:
            metrics.set_stats("question_count", question_count)
    else:
        response = agent(_build_single_turn_prompt(args))
        print(response)
        success = True
        if metrics:
            metrics.set_stats("question_count", 1)

    if metrics:
        metrics.set_stats("tool_model", args.model)
        metrics.set_stats("use_file_api", bool(args.use_file_api))
        metrics.set_stats("source_type", "youtube_url" if args.youtube_url else "video_path")
        if args.youtube_url:
            metrics.set_stats("youtube_url", args.youtube_url)
        if args.video_path:
            metrics.set_stats("video_path", args.video_path)
        if args.start_offset_seconds is not None:
            metrics.set_stats("start_offset_seconds", args.start_offset_seconds)
        if args.end_offset_seconds is not None:
            metrics.set_stats("end_offset_seconds", args.end_offset_seconds)
        if args.fps is not None:
            metrics.set_stats("fps", args.fps)
        if args.media_resolution is not None:
            metrics.set_stats("media_resolution", args.media_resolution)
        if args.thinking_level is not None:
            metrics.set_stats("thinking_level", args.thinking_level)

    if metrics:
        elapsed = time.time() - start_time
        metrics.set_timing("total_duration", elapsed)
        metrics_path = metrics.export()
        print(f"Metrics saved to: {metrics_path}")

    if registry:
        registry.record_run(agent_id=AGENT_ID, run_id=run_id, success=success)


if __name__ == "__main__":
    main()



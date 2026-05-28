#!/usr/bin/env python3
"""
Gemini Meme Remix Example (Strands + Hub)

Workflow:
1) Understand an input meme image (describe template, layout, tone, and extract caption)
2) Generate a brand-new, unique meme "in the likeness" (same vibe/layout) using reference image guidance

Notes:
- Uses `understand_image` for analysis and `generate_image` for generation.
- `generate_image` supports reference images (Nano Banana / Gemini 3 Pro Image Preview).
- Hub integration is enabled by default; disable with --no-hub.
"""

from __future__ import annotations

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
from src.hub import AgentRegistry, MetricsExporter, create_session_manager
from src.hub.session import generate_run_id
from src.models import gemini_model
from src.tools import understand_image, generate_image


AGENT_ID = "gemini-meme-remix"
AGENT_NAME = "Gemini Meme Remix"


def _apply_gemini3_thought_signature_workaround() -> None:
    """
    See `examples/gemini_video_understanding_example.py` for the full rationale.
    This enables Gemini 3 agents to use tools without failing on missing/corrupted thought signatures.
    """
    try:
        from examples.gemini_video_understanding_example import (  # type: ignore
            _apply_gemini3_thought_signature_workaround as _patch,
        )

        _patch()
    except Exception:
        # If import fails for any reason, continue; non-Gemini3 agents will still work.
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Remix a meme (analyze -> generate) with Strands + Gemini")
    parser.add_argument("--meme", required=True, help="Path to the input meme image (png/jpg/webp).")
    parser.add_argument(
        "--prompt",
        default="Generate a new, unique meme in the same style and comedic tone.",
        help="High-level instruction for the new meme (the agent will turn this into a concrete generation prompt).",
    )
    parser.add_argument(
        "--analysis-model",
        default="gemini-3-flash-preview",
        help="Model used for meme understanding (e.g. gemini-2.5-flash or gemini-3-flash-preview).",
    )
    parser.add_argument(
        "--image-model",
        default="gemini-3-pro-image-preview",
        help="Model used for image generation (default: gemini-3-pro-image-preview aka Nano Banana Pro).",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory to save the generated meme image.",
    )
    parser.add_argument("--interactive", action="store_true", help="Interactive mode (keep remixing the same meme).")
    parser.add_argument("--no-hub", action="store_true", help="Disable hub tracking.")

    args = parser.parse_args()

    if not os.getenv("GOOGLE_API_KEY"):
        raise SystemExit("Please set GOOGLE_API_KEY.")

    meme_path = Path(args.meme)
    if not meme_path.exists():
        raise SystemExit(f"Meme file not found: {args.meme}")

    # Enable Gemini 3 tool-calling signatures if user runs the agent on Gemini 3
    _apply_gemini3_thought_signature_workaround()

    # Hub integration
    run_id = None
    registry = None
    metrics = None
    session_manager = None

    if not args.no_hub:
        run_id = generate_run_id(AGENT_ID)
        registry = AgentRegistry()
        registry.register(
            agent_id=AGENT_ID,
            description="Analyze a meme image and generate a new meme in its likeness",
            tags=["gemini", "image", "meme", "remix", "nanobanana"],
            model_id="gemini-3-flash-preview",
        )
        metrics = MetricsExporter(agent_id=AGENT_ID, run_id=run_id)
        session_manager = create_session_manager(agent_id=AGENT_ID, run_id=run_id)
        print(f"Run ID: {run_id}")

    start_time = time.time()
    success = False
    remix_count = 0

    # Orchestrator model (agent). Gemini 3 is fine with workaround; Gemini 2.5 also fine.
    agent_llm = gemini_model(model_id="gemini-3-flash-preview")

    system_prompt = f"""You are a meme remix assistant.

Goal: Take an existing meme image, understand what makes it funny/recognizable, then generate a brand-new meme that is
"in the likeness" (same vibe/layout) but clearly original.

Rules:
- Do NOT copy the exact caption text; create a new caption and a new joke.
- Avoid reproducing watermarks/logos.
- If the meme contains copyrighted characters, create an original lookalike vibe rather than cloning the exact character.

Tools:
- understand_image(prompt, image_path, model): analyze the meme.
- generate_image(prompt, model, reference_images=[...], output_dir): generate a new image using reference guidance.

You MUST follow this procedure:
1) Call understand_image on the meme to extract: template/layout, caption placement, tone, and the current caption text.
2) Write a single, concrete image-generation prompt that includes:
   - the new (original) caption text
   - layout instructions (top/bottom text, etc.)
   - style cues matching the template
3) Call generate_image with reference_images=[meme_path] and output_dir="{args.output_dir}".
4) Return the saved file path and the new caption text.
"""

    agent = Agent(
        model=agent_llm,
        tools=[understand_image, generate_image],
        session_manager=session_manager,
        system_prompt=system_prompt,
        name=AGENT_NAME,
    )

    # The agent needs the meme path as data. Provide it in the user message and in the tool call instructions.
    def run_once(user_instruction: str) -> str:
        return agent(
            f"""Meme image path: "{str(meme_path)}"

Use understand_image(image_path="{str(meme_path)}", model="{args.analysis_model}") first.
Then generate_image(model="{args.image_model}", reference_images=["{str(meme_path)}"], output_dir="{args.output_dir}").

User instruction:
{user_instruction}
"""
        )

    if args.interactive:
        print(f"\n{AGENT_NAME}")
        print("Type a new idea/prompt for the next remix, or 'exit' to quit.\n")
        while True:
            try:
                user_input = input("\nYou: ").strip()
                if user_input.lower() in ("exit", "quit"):
                    success = True
                    break
                if not user_input:
                    continue
                print(run_once(user_input))
                remix_count += 1
            except KeyboardInterrupt:
                success = True
                break
    else:
        print(run_once(args.prompt))
        remix_count = 1
        success = True

    if metrics:
        metrics.set_stats("remix_count", remix_count)
        metrics.set_stats("analysis_model", args.analysis_model)
        metrics.set_stats("image_model", args.image_model)
        metrics.set_stats("meme_path", str(meme_path))
        metrics.set_timing("total_duration", time.time() - start_time)
        metrics_path = metrics.export()
        print(f"Metrics saved to: {metrics_path}")

    if registry:
        registry.record_run(agent_id=AGENT_ID, run_id=run_id, success=success)


if __name__ == "__main__":
    main()



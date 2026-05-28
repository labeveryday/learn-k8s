#!/usr/bin/env python3
"""
Gemini Image Generation Example

Demonstrates using the generate_image and edit_image tools
with Gemini 3 Pro Image Preview model.

Includes hub integration for session tracking and metrics.
"""

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
from src.tools.gemini_image import generate_image, edit_image
from src.models import gemini_model
from src.hub import (
    create_session_manager,
    MetricsExporter,
    AgentRegistry,
)
from src.hub.session import generate_run_id

# Agent configuration
AGENT_ID = "gemini-image-generator"
AGENT_NAME = "Gemini Image Generator"


def main():
    parser = argparse.ArgumentParser(description="Generate images with Gemini")
    parser.add_argument(
        "--prompt",
        type=str,
        default="A serene mountain landscape at sunset with a crystal clear lake reflection",
        help="Image generation prompt"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Output directory for generated images"
    )
    parser.add_argument(
        "--edit",
        type=str,
        default=None,
        help="Path to image to edit (enables edit mode)"
    )
    parser.add_argument(
        "--edit-prompt",
        type=str,
        default="Make it more vibrant and colorful",
        help="Edit prompt when using --edit"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Use interactive agent mode"
    )
    parser.add_argument(
        "--no-hub",
        action="store_true",
        help="Disable hub tracking"
    )

    args = parser.parse_args()

    # Ensure output directory exists
    Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    # Initialize hub components (unless disabled)
    run_id = None
    registry = None
    metrics = None

    if not args.no_hub:
        run_id = generate_run_id(AGENT_ID)

        # Register agent
        registry = AgentRegistry()
        registry.register(
            agent_id=AGENT_ID,
            description="Generate and edit images using Gemini 3 Pro Image Preview",
            tags=["gemini", "image", "generation"],
            model_id="gemini-3-pro-image-preview",
        )

        # Initialize metrics
        metrics = MetricsExporter(
            agent_id=AGENT_ID,
            run_id=run_id,
        )

        print(f"Run ID: {run_id}")

    start_time = time.time()
    success = False

    if args.interactive:
        # Interactive agent mode
        print(f"\n{AGENT_NAME}")
        print("Available tools: generate_image, edit_image")
        print("Type 'exit' to quit\n")

        # Create session manager for interactive mode
        session_manager = None
        if not args.no_hub:
            session_manager = create_session_manager(agent_id=AGENT_ID, run_id=run_id)

        model = gemini_model(model_id="gemini-2.0-flash")
        agent = Agent(
            model=model,
            tools=[generate_image, edit_image],
            session_manager=session_manager,
            system_prompt=f"""You are an image generation assistant using Gemini 3 Pro Image Preview.

You can:
- Generate new images from text descriptions using generate_image
- Edit existing images using edit_image

When generating images, suggest creative and detailed prompts that will produce high-quality results.
Always confirm the output file path after generation.
Save images to the '{args.output_dir}' directory.""",
        )

        generation_count = 0
        while True:
            try:
                user_input = input("\nYou: ").strip()
                if user_input.lower() in ["exit", "quit"]:
                    print("Goodbye!")
                    success = True
                    break
                if not user_input:
                    continue

                response = agent(user_input)
                print(f"\nAgent: {response}")
                generation_count += 1

            except KeyboardInterrupt:
                print("\nGoodbye!")
                success = True
                break

        if metrics:
            metrics.set_stats("generation_count", generation_count)

    elif args.edit:
        # Direct edit mode
        print(f"Editing image: {args.edit}")
        print(f"Edit prompt: {args.edit_prompt}")

        result = edit_image(
            prompt=args.edit_prompt,
            image_path=args.edit,
            output_dir=args.output_dir,
        )

        if result["success"]:
            print(f"Success! Edited image saved to: {result['file_path']}")
            success = True
            if metrics:
                metrics.set_stats("operation", "edit")
                metrics.set_stats("output_file", result["file_path"])
        else:
            print(f"Error: {result.get('error', 'Unknown error')}")
            if metrics:
                metrics.set_stats("error", result.get("error"))

    else:
        # Direct generation mode
        print(f"Generating image...")
        print(f"Prompt: {args.prompt}")

        result = generate_image(
            prompt=args.prompt,
            output_dir=args.output_dir,
        )

        if result["success"]:
            print(f"Success! Image saved to: {result['file_path']}")
            success = True
            if metrics:
                metrics.set_stats("operation", "generate")
                metrics.set_stats("output_file", result["file_path"])
                metrics.set_stats("prompt", args.prompt)
        else:
            print(f"Error: {result.get('error', 'Unknown error')}")
            if metrics:
                metrics.set_stats("error", result.get("error"))

    # Export metrics and record run
    if metrics:
        elapsed = time.time() - start_time
        metrics.set_timing("total_duration", elapsed)
        metrics_path = metrics.export()
        print(f"Metrics saved to: {metrics_path}")

    if registry:
        registry.record_run(agent_id=AGENT_ID, run_id=run_id, success=success)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Gemini Video Generation Example

Demonstrates using the generate_video and generate_video_from_image tools
with Veo 3.1 model.

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
from src.tools.gemini_video import generate_video, generate_video_from_image
from src.models import gemini_model
from src.hub import (
    create_session_manager,
    MetricsExporter,
    AgentRegistry,
)
from src.hub.session import generate_run_id

# Agent configuration
AGENT_ID = "gemini-video-generator"
AGENT_NAME = "Gemini Video Generator"


def main():
    parser = argparse.ArgumentParser(description="Generate videos with Veo 3.1")
    parser.add_argument(
        "--prompt",
        type=str,
        default="A drone shot flying over a beautiful coastal city at golden hour, cinematic",
        help="Video generation prompt"
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=6,
        choices=[4, 6],
        help="Video duration in seconds (4 or 6)"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        help="Output directory for generated videos"
    )
    parser.add_argument(
        "--image",
        type=str,
        default=None,
        help="Path to image for image-to-video generation"
    )
    parser.add_argument(
        "--max-wait",
        type=int,
        default=300,
        help="Maximum wait time in seconds"
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
            description="Generate videos using Veo 3.1 (text-to-video and image-to-video)",
            tags=["gemini", "video", "veo", "generation"],
            model_id="veo-3.1-generate-preview",
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
        print("Available tools: generate_video, generate_video_from_image")
        print("Note: Video generation takes 1-5 minutes")
        print("Type 'exit' to quit\n")

        # Create session manager for interactive mode
        session_manager = None
        if not args.no_hub:
            session_manager = create_session_manager(agent_id=AGENT_ID, run_id=run_id)

        model = gemini_model(model_id="gemini-2.0-flash")
        agent = Agent(
            model=model,
            tools=[generate_video, generate_video_from_image],
            session_manager=session_manager,
            system_prompt=f"""You are a video generation assistant using Veo 3.1.

You can:
- Generate videos from text descriptions using generate_video
- Animate images into videos using generate_video_from_image

When generating videos:
- Describe camera motion (pan, zoom, tracking shot, etc.)
- Include lighting and atmosphere details
- Mention style (cinematic, documentary, etc.)
- Video generation takes 1-5 minutes, so set expectations
- Duration can be 4 or 6 seconds

Always confirm the output file path after generation.
Save videos to the '{args.output_dir}' directory.""",
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

    elif args.image:
        # Image-to-video mode
        print(f"Generating video from image: {args.image}")
        print(f"Prompt: {args.prompt}")
        print("This may take 1-5 minutes...")

        result = generate_video_from_image(
            prompt=args.prompt,
            image_path=args.image,
            output_dir=args.output_dir,
            duration_seconds=args.duration,
            max_wait_seconds=args.max_wait,
        )

        if result["success"]:
            print(f"Success! Video saved to: {result['file_path']}")
            print(f"Duration: {result['duration']}s")
            success = True
            if metrics:
                metrics.set_stats("operation", "image_to_video")
                metrics.set_stats("output_file", result["file_path"])
                metrics.set_stats("source_image", args.image)
                metrics.set_stats("duration_seconds", result["duration"])
        else:
            print(f"Error: {result.get('error', 'Unknown error')}")
            if metrics:
                metrics.set_stats("error", result.get("error"))

    else:
        # Direct text-to-video mode
        print(f"Generating video...")
        print(f"Prompt: {args.prompt}")
        print(f"Duration: {args.duration}s")
        print("This may take 1-5 minutes...")

        result = generate_video(
            prompt=args.prompt,
            output_dir=args.output_dir,
            duration_seconds=args.duration,
            max_wait_seconds=args.max_wait,
        )

        if result["success"]:
            print(f"Success! Video saved to: {result['file_path']}")
            print(f"Duration: {result['duration']}s")
            success = True
            if metrics:
                metrics.set_stats("operation", "text_to_video")
                metrics.set_stats("output_file", result["file_path"])
                metrics.set_stats("prompt", args.prompt)
                metrics.set_stats("duration_seconds", result["duration"])
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
